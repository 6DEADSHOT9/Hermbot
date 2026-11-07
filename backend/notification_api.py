"""Owner-only inbox, policy, and private device registration."""

import base64
from datetime import time
import json
import os
import secrets
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import attention, db, notifications
from .secrets import credential_hash, encrypt, decrypt


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Observed(Strict):
    id: UUID
    version: int = Field(ge=1)


class ReadRequest(Strict):
    items: list[Observed] = Field(max_length=100)
    action: Literal["read", "unread", "dismiss"] = "read"


class Preferences(Strict):
    revision: int = Field(ge=1)
    push_enabled: bool
    categories: list[Literal["questions", "reviews", "results", "problems"]] = Field(max_length=4)
    timezone: str = Field(max_length=100)
    quiet_enabled: bool
    quiet_start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def validate_window(self):
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Choose a valid IANA timezone")
        if self.quiet_start == self.quiet_end:
            raise ValueError("Quiet start and end must differ")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("Categories must be distinct")
        return self


class Subscription(Strict):
    endpoint: str = Field(max_length=4096)
    keys: dict[str, str]
    label: str = Field(min_length=1, max_length=80)
    key_id: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def valid_keys(self):
        try:
            if set(self.keys) != {"auth", "p256dh"} or any(len(v) > 100 for v in self.keys.values()):
                raise ValueError()
            auth, point = (
                base64.b64decode(self.keys[k] + "=" * (-len(self.keys[k]) % 4), altchars=b"-_", validate=True)
                for k in ("auth", "p256dh")
            )
            if len(auth) != 16 or len(point) != 65 or point[0] != 4:
                raise ValueError()
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        except (ValueError, TypeError):
            raise ValueError("Browser push keys are invalid")
        return self


def public_settings(conn, device=None):
    pref = notifications.preferences(conn)
    return {
        **{
            k: pref[k]
            for k in ("revision", "push_enabled", "categories", "timezone", "quiet_enabled", "worker_error")
        },
        "quiet_start": pref["quiet_start"].strftime("%H:%M"),
        "quiet_end": pref["quiet_end"].strftime("%H:%M"),
        "worker_healthy": bool(
            pref["heartbeat_at"] and (notifications.service.now() - pref["heartbeat_at"]).total_seconds() < 30
        ),
        "heartbeat_at": pref["heartbeat_at"],
        "push": notifications.configuration(),
        "devices": conn.execute(
            """SELECT id,label,status,enabled,created_at,updated_at,expires_at,
            device_hash=%s AS current FROM push_subscriptions ORDER BY enabled DESC,device_hash=%s DESC,created_at DESC LIMIT 20""",
            (credential_hash(device) if device else "", credential_hash(device) if device else ""),
        ).fetchall(),
    }


def routes(owner):
    router = APIRouter(dependencies=[Depends(owner)])

    @router.get("/api/v1/attention")
    def inbox(
        view: Literal["needs", "updates", "history"] = "needs",
        before: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
    ):
        with db.connection() as conn:
            return attention.page(conn, view, before, limit)

    @router.post("/api/v1/attention/read")
    def read(body: ReadRequest):
        with db.connection() as conn:
            attention.acknowledge(conn, body.items, body.action)
        return {"status": "recorded"}

    @router.get("/api/v1/attention/{identity}")
    def resolve(identity: UUID):
        with db.connection() as conn:
            item = conn.execute("SELECT * FROM attention_items WHERE id=%s", (identity,)).fetchone()
            if not item:
                raise HTTPException(404, "This attention item is no longer available")
            # Source reads precede attention locks. Refuse to race another projector.
            if not conn.execute("SELECT pg_try_advisory_xact_lock(887832154)").fetchone()[
                "pg_try_advisory_xact_lock"
            ]:
                raise HTTPException(409, "Attention is refreshing; try again shortly")
            attention.project(conn, item["subject_type"], item["subject_id"])
            item = conn.execute(
                f"SELECT {attention.PUBLIC_COLUMNS} FROM attention_items WHERE id=%s", (identity,)
            ).fetchone()
            refs = item["refs"]
            table = (
                "input_requests"
                if item["category"] == "questions"
                else "approvals"
                if item["category"] == "reviews"
                else None
            )
            item["request"] = (
                conn.execute(f"SELECT * FROM {table} WHERE id=%s", (refs["request_id"],)).fetchone()
                if item["active"] and table and refs.get("request_id")
                else None
            )
            item["work"] = (
                next(
                    iter(
                        attention.work_items.list_items(
                            conn, identities=[UUID(refs["work_item_id"])], canonical=True
                        )
                    ),
                    None,
                )
                if refs.get("work_item_id")
                else None
            )
            item["task"] = (
                conn.execute("SELECT * FROM tasks WHERE id=%s", (refs["task_id"],)).fetchone()
                if refs.get("task_id")
                else None
            )
            item["conversation"] = (
                conn.execute("SELECT * FROM conversations WHERE id=%s", (refs["conversation_id"],)).fetchone()
                if refs.get("conversation_id")
                else None
            )
            if item["conversation"]:
                item["conversation"]["bot_ids"] = [
                    r["bot_id"]
                    for r in conn.execute(
                        "SELECT bot_id FROM participants WHERE conversation_id=%s",
                        (item["conversation"]["id"],),
                    ).fetchall()
                ]
            item["thread"] = (
                conn.execute(
                    "SELECT * FROM messages WHERE id=%s AND conversation_id=%s",
                    (refs["thread_id"], refs["conversation_id"]),
                ).fetchone()
                if refs.get("thread_id") and refs.get("conversation_id")
                else None
            )
            return item

    @router.get("/api/v1/notifications/settings")
    def settings(hermbot_device: str | None = Cookie(None)):
        with db.connection() as conn:
            return public_settings(conn, hermbot_device)

    @router.put("/api/v1/notifications/settings")
    def save(body: Preferences, hermbot_device: str | None = Cookie(None)):
        with db.connection() as conn:
            changed = conn.execute(
                """UPDATE notification_preferences SET revision=revision+1,push_enabled=%s,categories=%s,
                timezone=%s,quiet_enabled=%s,quiet_start=%s,quiet_end=%s WHERE owner='owner' AND revision=%s RETURNING revision""",
                (
                    body.push_enabled,
                    Jsonb(body.categories),
                    body.timezone,
                    body.quiet_enabled,
                    time.fromisoformat(body.quiet_start),
                    time.fromisoformat(body.quiet_end),
                    body.revision,
                ),
            ).fetchone()
            if not changed:
                raise HTTPException(
                    409, "Notification preferences changed in another tab; reload before saving"
                )
            pending = conn.execute(
                "SELECT DISTINCT item_id FROM notification_deliveries WHERE status IN ('pending','covered') AND item_id IS NOT NULL AND expires_at>now() LIMIT 1000"
            ).fetchall()
            catchups = conn.execute("""SELECT subscription_id,generation,max(expires_at) AS expires_at
                FROM notification_deliveries WHERE status='pending' AND item_id IS NULL
                AND purpose LIKE 'overflow:%%' AND expires_at>now() GROUP BY subscription_id,generation""").fetchall()
            conn.execute(
                "UPDATE notification_deliveries SET status='suppressed',updated_at=now() WHERE status='pending'"
            )
            for row in pending:
                item = conn.execute("SELECT * FROM attention_items WHERE id=%s", (row["item_id"],)).fetchone()
                if item and item["updated_at"] > notifications.service.now() - notifications.timedelta(
                    hours=24
                ):
                    notifications.enqueue(conn, item)
            if body.push_enabled:
                pref = notifications.preferences(conn)
                at = notifications.service.now()
                due = notifications.quiet_until(pref, at) or at
                for row in catchups:
                    if (
                        due >= row["expires_at"]
                        or conn.execute(
                            "SELECT 1 FROM notification_deliveries WHERE subscription_id=%s AND generation=%s AND status='pending' AND purpose LIKE 'overflow:%%'",
                            (row["subscription_id"], row["generation"]),
                        ).fetchone()
                    ):
                        continue
                    conn.execute(
                        """INSERT INTO notification_deliveries(id,subscription_id,generation,purpose,
                        policy_revision,due_at,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(subscription_id,generation,purpose) DO NOTHING""",
                        (
                            uuid4(),
                            row["subscription_id"],
                            row["generation"],
                            f"overflow:policy:{pref['revision']}",
                            pref["revision"],
                            due,
                            row["expires_at"],
                        ),
                    )
            db.event(conn, "notifications.preferences")
            return public_settings(conn, hermbot_device)

    @router.post("/api/v1/notifications/subscriptions")
    def register(body: Subscription, response: Response, hermbot_device: str | None = Cookie(None)):
        config = notifications.configuration()
        if not config["configured"] or body.key_id != config["key_id"]:
            raise HTTPException(409, "Browser push is not configured or its server key changed")
        notifications.validate_endpoint(body.endpoint)
        device = hermbot_device or secrets.token_urlsafe(32)
        hashed, endpoint_hash = credential_hash(device), credential_hash(body.endpoint)
        with db.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(887832155)")
            old = conn.execute(
                "SELECT * FROM push_subscriptions WHERE device_hash=%s OR endpoint_hash=%s ORDER BY device_hash=%s DESC",
                (hashed, endpoint_hash, hashed),
            ).fetchall()
            if (
                conn.execute(
                    "SELECT count(*) AS n FROM push_subscriptions WHERE enabled AND device_hash<>%s AND endpoint_hash<>%s",
                    (hashed, endpoint_hash),
                ).fetchone()["n"]
                >= 10
            ):
                raise HTTPException(
                    409, "Disable an older browser before registering another (10 active browsers maximum)"
                )
            own = next((row for row in old if row["device_hash"] == hashed), None)
            same = (
                own
                and own["endpoint_hash"] == endpoint_hash
                and own["key_id"] == config["key_id"]
                and own["enabled"]
                and own["origin"] == os.environ.get("PUBLIC_URL", "http://localhost:8780").rstrip("/")
                and json.loads(decrypt(own["secret_encrypted"]))
                == {"endpoint": body.endpoint, "keys": body.keys}
            )
            generation = own["generation"] if same else uuid4()
            for row in old:
                if not own or row["id"] != own["id"]:
                    conn.execute(
                        "UPDATE push_subscriptions SET enabled=false,status='replaced',secret_encrypted=NULL,endpoint_hash=%s WHERE id=%s",
                        (credential_hash("retired:" + str(row["id"])), row["id"]),
                    )
            conn.execute(
                """INSERT INTO push_subscriptions(id,device_hash,endpoint_hash,secret_encrypted,generation,origin,key_id,label)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(device_hash) DO UPDATE SET endpoint_hash=excluded.endpoint_hash,
                secret_encrypted=excluded.secret_encrypted,generation=excluded.generation,origin=excluded.origin,key_id=excluded.key_id,
                label=excluded.label,enabled=true,status='registered',expires_at=now()+interval '90 days',updated_at=now()""",
                (
                    own["id"] if own else uuid4(),
                    hashed,
                    endpoint_hash,
                    encrypt(json.dumps({"endpoint": body.endpoint, "keys": body.keys})),
                    generation,
                    os.environ.get("PUBLIC_URL", "http://localhost:8780").rstrip("/"),
                    config["key_id"],
                    body.label,
                ),
            )
        response.set_cookie(
            "hermbot_device",
            device,
            max_age=90 * 86400,
            httponly=True,
            secure=os.environ.get("PUBLIC_URL", "").startswith("https://"),
            samesite="strict",
        )
        return {"status": "registered"}

    @router.delete("/api/v1/notifications/subscriptions/{identity}")
    def revoke(identity: UUID):
        with db.connection() as conn:
            conn.execute(
                "UPDATE push_subscriptions SET enabled=false,status='revoked',generation=%s,secret_encrypted=NULL,updated_at=now() WHERE id=%s",
                (uuid4(), identity),
            )
        return {"status": "revoked"}

    @router.post("/api/v1/notifications/test")
    def test(hermbot_device: str | None = Cookie(None)):
        with db.connection() as conn:
            sub = conn.execute(
                "SELECT * FROM push_subscriptions WHERE device_hash=%s AND enabled AND expires_at>now()",
                (credential_hash(hermbot_device or ""),),
            ).fetchone()
            pref = notifications.preferences(conn)
            if not sub or not pref["push_enabled"]:
                raise HTTPException(409, "Enable push and register this browser before sending a test")
            recent = conn.execute(
                "SELECT 1 FROM notification_deliveries WHERE subscription_id=%s AND purpose LIKE 'test:%%' AND created_at>now()-interval '1 minute'",
                (sub["id"],),
            ).fetchone()
            if recent:
                raise HTTPException(429, "Wait one minute between test notifications")
            conn.execute(
                """INSERT INTO notification_deliveries(id,subscription_id,generation,purpose,policy_revision,expires_at)
                VALUES(%s,%s,%s,%s,%s,now()+interval '1 hour')""",
                (uuid4(), sub["id"], sub["generation"], "test:" + str(uuid4()), pref["revision"]),
            )
        return {
            "status": "queued",
            "note": "Push service acceptance does not confirm display. Quiet hours still apply.",
        }

    return router
