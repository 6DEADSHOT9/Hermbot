"""Push policy and outbox. Vendor acceptance is not proof of display or reading."""

import base64
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
import random
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from . import db, service
from .connector_http import ProviderFailure, request
from .secrets import decrypt

CATEGORIES = {"questions", "reviews", "results", "problems"}
GENERIC = "Hermbot has an update. Open Hermbot to view the current status."


def configuration():
    public = os.environ.get("VAPID_PUBLIC_KEY", "")
    return {
        "public_key": public,
        "key_id": hashlib.sha256(public.encode()).hexdigest() if public else "",
        "configured": bool(public and os.environ.get("VAPID_SUBJECT", "")),
    }


def preferences(conn):
    return conn.execute("SELECT * FROM notification_preferences WHERE owner='owner'").fetchone()


def quiet_until(pref, at):
    if not pref["quiet_enabled"]:
        return None
    zone = ZoneInfo(pref["timezone"])
    start, end = pref["quiet_start"], pref["quiet_end"]

    def quiet(value):
        local = value.astimezone(zone).time().replace(tzinfo=None)
        return start <= local < end if start < end else local >= start or local < end

    if not quiet(at):
        return None
    # Walk UTC minutes: nonexistent local times are skipped, repeated hours share one period end.
    candidate = at.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(2880):
        if not quiet(candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError("Quiet window has no allowed end")


def quiet_key(pref, at):
    local = at.astimezone(ZoneInfo(pref["timezone"]))
    day = local.date()
    if pref["quiet_start"] > pref["quiet_end"] and local.time().replace(tzinfo=None) < pref["quiet_end"]:
        day -= timedelta(days=1)
    return f"quiet:{pref['revision']}:{pref['timezone']}:{day}:{pref['quiet_start']}:{pref['quiet_end']}"


def enqueue(conn, item):
    pref = preferences(conn)
    if (
        not pref["push_enabled"]
        or item["category"] not in pref["categories"]
        or not configuration()["configured"]
    ):
        return
    if (
        not item["active"]
        or item["read_version"] >= item["version"]
        or item["dismissed_version"] >= item["version"]
    ):
        return
    at = service.now()
    end = quiet_until(pref, at)
    delay = (
        15
        if item["category"] == "results"
        else 30
        if item["category"] == "problems" and item["source_status"] not in ("uncertain", "unresolved")
        else 0
    )
    due = end or at + timedelta(seconds=delay)
    expires = min(at + timedelta(hours=24), item["expires_at"] or at + timedelta(hours=24))
    if due >= expires:
        return
    config = configuration()
    subscriptions = conn.execute(
        "SELECT id,generation FROM push_subscriptions WHERE enabled AND expires_at>now() AND key_id=%s AND origin=%s ORDER BY id",
        (config["key_id"], os.environ.get("PUBLIC_URL", "http://localhost:8780").rstrip("/")),
    ).fetchall()
    for subscription in subscriptions:
        batch = quiet_key(pref, at) if end else None
        count = conn.execute(
            "SELECT count(*) AS n FROM notification_deliveries WHERE subscription_id=%s AND status='pending'",
            (subscription["id"],),
        ).fetchone()["n"]
        overflow = count >= 999
        if overflow:
            due = end or (at + timedelta(minutes=1)).replace(second=0, microsecond=0)
            batch = f"overflow:{pref['revision']}:{due.isoformat()}"
        if overflow:
            prior = conn.execute(
                "SELECT purpose,due_at FROM notification_deliveries WHERE subscription_id=%s AND generation=%s AND purpose LIKE 'overflow:%%' AND status='pending' AND expires_at>now() ORDER BY due_at LIMIT 1",
                (subscription["id"], subscription["generation"]),
            ).fetchone()
            if prior:
                batch, due = prior["purpose"], prior["due_at"]
        purpose = batch if overflow else f"item:{item['id']}:{item['version']}:{pref['revision']}"
        conn.execute(
            """INSERT INTO notification_deliveries(id,subscription_id,generation,purpose,item_id,item_version,
            policy_revision,batch_key,due_at,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(subscription_id,generation,purpose) DO NOTHING""",
            (
                uuid4(),
                subscription["id"],
                subscription["generation"],
                purpose,
                None if overflow else item["id"],
                None if overflow else item["version"],
                pref["revision"],
                batch,
                due,
                expires,
            ),
        )


def eligible(conn, delivery):
    from . import attention

    pref = preferences(conn)
    if not pref["push_enabled"] or pref["revision"] != delivery["policy_revision"]:
        return None
    if delivery["item_id"]:
        candidates = conn.execute(
            "SELECT * FROM attention_items WHERE id=%s", (delivery["item_id"],)
        ).fetchall()
    elif delivery["purpose"].startswith("test:"):
        return {"id": None, "category": "results", "needs_owner": False, "expires_at": None}
    elif delivery["batch_key"] and not delivery["purpose"].startswith("overflow:"):
        candidates = conn.execute(
            """SELECT DISTINCT a.* FROM attention_items a JOIN notification_deliveries d ON d.item_id=a.id
            WHERE d.subscription_id=%s AND d.generation=%s AND d.batch_key=%s AND d.item_version=a.version
            AND d.status IN ('pending','covered') AND d.expires_at>now()
            AND a.updated_at>now()-interval '24 hours' ORDER BY a.updated_at DESC LIMIT 1000""",
            (delivery["subscription_id"], delivery["generation"], delivery["batch_key"]),
        ).fetchall()
    else:
        candidates = conn.execute(
            """SELECT * FROM attention_items WHERE active AND version>read_version
            AND dismissed_version<version AND updated_at>now()-interval '24 hours'
            AND category=ANY(%s) ORDER BY updated_at DESC LIMIT 1000""",
            (pref["categories"],),
        ).fetchall()
    for item in candidates:
        attention.project(conn, item["subject_type"], item["subject_id"])
        item = conn.execute("SELECT * FROM attention_items WHERE id=%s", (item["id"],)).fetchone()
        if (
            item["active"]
            and item["version"] > item["read_version"]
            and item["dismissed_version"] < item["version"]
            and item["category"] in pref["categories"]
            and (not item["expires_at"] or item["expires_at"] > service.now())
            and (not delivery["item_id"] or item["version"] == delivery["item_version"])
        ):
            return item
    return None


def claim():
    with db.connection() as conn:
        if not conn.execute("SELECT pg_try_advisory_xact_lock(887832154)").fetchone()[
            "pg_try_advisory_xact_lock"
        ]:
            return None
        rows = conn.execute("""SELECT d.* FROM notification_deliveries d JOIN push_subscriptions s ON s.id=d.subscription_id
            WHERE d.status IN ('pending','dispatching') AND d.due_at<=now() AND d.expires_at>now()
            AND (d.lease_until IS NULL OR d.lease_until<now()) ORDER BY d.due_at,d.id LIMIT 30""").fetchall()
        for row in rows:
            sub = conn.execute(
                "SELECT * FROM push_subscriptions WHERE id=%s", (row["subscription_id"],)
            ).fetchone()
            config = configuration()
            item = (
                eligible(conn, row)
                if sub["enabled"]
                and sub["expires_at"] > service.now()
                and sub["generation"] == row["generation"]
                and sub["key_id"] == config["key_id"]
                and sub["origin"] == os.environ.get("PUBLIC_URL", "http://localhost:8780").rstrip("/")
                else None
            )
            if not item:
                conn.execute(
                    "UPDATE notification_deliveries SET status='suppressed',updated_at=now() WHERE id=%s",
                    (row["id"],),
                )
                continue
            pref = preferences(conn)
            quiet = quiet_until(pref, service.now())
            if quiet:
                conn.execute(
                    "UPDATE notification_deliveries SET status='pending',due_at=%s,batch_key=%s,lease_until=NULL WHERE id=%s",
                    (quiet, quiet_key(pref, service.now()), row["id"]),
                )
                continue
            if row["batch_key"] and row["item_id"]:
                # One durable generic summary covers this quiet/burst period, including retries.
                until = conn.execute(
                    "SELECT max(expires_at) AS at FROM notification_deliveries WHERE subscription_id=%s AND generation=%s AND batch_key=%s AND status='pending'",
                    (row["subscription_id"], row["generation"], row["batch_key"]),
                ).fetchone()["at"]
                conn.execute(
                    """INSERT INTO notification_deliveries(id,subscription_id,generation,purpose,policy_revision,batch_key,expires_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(subscription_id,generation,purpose) DO UPDATE SET expires_at=GREATEST(notification_deliveries.expires_at,excluded.expires_at),status='pending' WHERE notification_deliveries.status='pending' OR (notification_deliveries.status IN ('expired','suppressed') AND notification_deliveries.attempts=0)""",
                    (
                        uuid4(),
                        row["subscription_id"],
                        row["generation"],
                        row["batch_key"],
                        row["policy_revision"],
                        row["batch_key"],
                        until,
                    ),
                )
                conn.execute(
                    "UPDATE notification_deliveries SET status='covered',updated_at=now() WHERE subscription_id=%s AND generation=%s AND batch_key=%s AND item_id IS NOT NULL AND status='pending'",
                    (row["subscription_id"], row["generation"], row["batch_key"]),
                )
                continue
            count = conn.execute(
                "SELECT count(*) AS n FROM notification_deliveries WHERE subscription_id=%s AND last_attempt_at>now()-interval '1 minute'",
                (row["subscription_id"],),
            ).fetchone()["n"]
            if count >= 3:
                due = (service.now() + timedelta(minutes=1)).replace(second=0, microsecond=0)
                conn.execute(
                    "UPDATE notification_deliveries SET status='pending',due_at=%s,batch_key=%s,lease_until=NULL WHERE id=%s",
                    (due, f"burst:{pref['revision']}:{due.isoformat()}", row["id"]),
                )
                continue
            token = uuid4()
            claimed = conn.execute(
                """UPDATE notification_deliveries SET status='dispatching',attempts=attempts+1,
                lease_token=%s,lease_until=now()+interval '60 seconds',last_attempt_at=now(),updated_at=now()
                WHERE id=%s AND status IN ('pending','dispatching') AND (lease_until IS NULL OR lease_until<now()) RETURNING *""",
                (token, row["id"]),
            ).fetchone()
            if claimed:
                ttl = min(
                    3600 if item["needs_owner"] else 14400,
                    max(
                        1,
                        int(
                            (
                                min(row["expires_at"], item["expires_at"] or row["expires_at"])
                                - service.now()
                            ).total_seconds()
                        ),
                    ),
                )
                return {
                    **claimed,
                    "subscription": sub,
                    "ttl": ttl,
                    "target": str(item["id"]) if row["item_id"] else "inbox",
                }
    return None


def validate_endpoint(endpoint, *, resolve=True):
    from .library import public_address

    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise HTTPException(422, "Invalid browser push endpoint")
    if (
        len(endpoint) > 4096
        or any(char.isspace() for char in endpoint)
        or parsed.scheme != "https"
        or port not in (None, 443)
        or not parsed.hostname
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(422, "A browser subscription requires a public HTTPS endpoint on port 443")
    if resolve:
        public_address(endpoint)


class PinnedSession:
    """Public pywebpush session hook; every actual socket uses Hermbot's SSRF boundary."""

    def post(self, url, *, data=None, headers=None, timeout=None, **kwargs):
        from requests import Response

        validate_endpoint(url, resolve=False)
        body, metadata, status = request(
            "POST",
            url,
            headers=headers,
            content=data,
            raw=True,
            mutation=True,
            max_seconds=15,
            response_metadata=True,
            accepted_statuses=tuple(range(300, 600)),
        )
        result = Response()
        result.status_code = status
        result._content = body
        result.headers.update(metadata)
        return result


def send(delivery):
    from pywebpush import webpush

    secret = json.loads(decrypt(delivery["subscription"]["secret_encrypted"]))
    topic = base64.urlsafe_b64encode(
        hashlib.sha256(
            ("item:" + str(delivery["item_id"]) if delivery.get("item_id") else delivery["purpose"]).encode()
        ).digest()
    ).decode()[:32]
    return webpush(
        secret,
        data=json.dumps({"message": GENERIC, "target": delivery["target"], "tag": topic}),
        vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
        vapid_claims={"sub": os.environ["VAPID_SUBJECT"]},
        ttl=delivery["ttl"],
        headers={"Topic": topic, "Urgency": "normal"},
        timeout=15,
        requests_session=PinnedSession(),
    )


def retry_seconds(value):
    try:
        return max(1, int(value))
    except (ValueError, TypeError):
        try:
            return max(1, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            return 0


def deliver(delivery):
    # A queued send may have been revoked or replaced after claim and before network dispatch.
    with db.connection() as conn:
        live = conn.execute(
            """SELECT 1 FROM notification_deliveries d JOIN push_subscriptions s ON s.id=d.subscription_id
            JOIN notification_preferences p ON p.owner='owner' WHERE d.id=%s AND d.lease_token=%s
            AND d.status='dispatching' AND d.lease_until>now() AND d.expires_at>now() AND s.enabled
            AND s.generation=d.generation AND s.expires_at>now() AND p.push_enabled AND p.revision=d.policy_revision""",
            (delivery["id"], delivery["lease_token"]),
        ).fetchone()
        if not live:
            return
    status, retry = None, 0
    try:
        response = send(delivery)
        status = response.status_code
    except ProviderFailure as exc:
        status, retry = exc.status, exc.retry_after or 0
    except Exception as exc:
        # Do not stringify push exceptions: they can contain endpoint/auth headers and bodies.
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        retry = retry_seconds(response.headers.get("Retry-After")) if response is not None else 0
    at = service.now()
    terminal = status in (400, 401, 403, 404, 410, 413) or (
        status is not None and 300 <= status < 500 and status != 429
    )
    accepted = status is not None and 200 <= status < 300
    delay = max(retry, min(1800, 5 * 2 ** min(delivery["attempts"], 8)) + random.randint(0, 5))
    due = at + timedelta(seconds=delay)
    state = (
        "accepted"
        if accepted
        else "failed"
        if terminal or delivery["attempts"] >= 5
        else "expired"
        if due >= delivery["expires_at"]
        else "pending"
    )
    with db.connection() as conn:
        row = conn.execute(
            """UPDATE notification_deliveries SET status=%s,result=%s,due_at=%s,lease_until=NULL,updated_at=now()
            WHERE id=%s AND lease_token=%s AND status='dispatching' RETURNING subscription_id""",
            (
                state,
                "accepted_by_push_service"
                if accepted
                else f"http_{status}"
                if status
                else "transport_unavailable",
                due,
                delivery["id"],
                delivery["lease_token"],
            ),
        ).fetchone()
        if row and status in (404, 410):
            conn.execute(
                "UPDATE push_subscriptions SET enabled=false,status='expired',secret_encrypted=NULL WHERE id=%s AND generation=%s",
                (row["subscription_id"], delivery["generation"]),
            )
        elif row and status in (401, 403):
            conn.execute(
                "UPDATE push_subscriptions SET status='server_key_error' WHERE id=%s AND generation=%s",
                (row["subscription_id"], delivery["generation"]),
            )

        elif row and accepted:
            conn.execute(
                "UPDATE push_subscriptions SET status='accepted_by_push_service',updated_at=now() WHERE id=%s AND generation=%s",
                (row["subscription_id"], delivery["generation"]),
            )
