from datetime import datetime, time, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import attention, db, inputs, notifications, service
from backend.notification_api import Observed
from backend.secrets import encrypt


def project_task(task):
    with db.connection() as conn:
        attention.project(conn, "task", task["id"])
        return attention.page(conn)


def ask(task):
    with db.connection() as conn:
        return inputs.ask(
            conn,
            service.locked_task(conn, task["id"]),
            {"prompt": "Which dataset should I use?", "answer_type": "text", "choices": []},
        )


def policy(monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:owner@example.com")
    monkeypatch.setenv("PUBLIC_URL", "http://localhost:8780")
    with db.connection() as conn:
        conn.execute("UPDATE notification_preferences SET push_enabled=true")
        identity, generation = uuid4(), uuid4()
        conn.execute(
            """INSERT INTO push_subscriptions(id,device_hash,endpoint_hash,secret_encrypted,generation,origin,key_id,label)
            VALUES(%s,'device','endpoint',%s,%s,'http://localhost:8780',%s,'Browser')""",
            (identity, encrypt("{}"), generation, notifications.configuration()["key_id"]),
        )
    return identity, generation


def ready_deliveries():
    with db.connection() as conn:
        conn.execute("UPDATE notification_deliveries SET due_at=now()-interval '1 second'")


def client():
    from backend.app import app
    from backend.settings import settings

    return TestClient(app, headers={"Authorization": "Bearer " + settings.owner_token})


def test_question_read_is_not_answer_and_closes_on_expiry(make_task):
    task = make_task()
    ask(task)
    first = project_task(task)
    assert first["needs_owner"] == 1 and first["unread"] == 1
    item = first["items"][0]
    with db.connection() as conn:
        attention.acknowledge(conn, [Observed(id=item["id"], version=item["version"])], "read")
        assert attention.page(conn)["needs_owner"] == 1
        assert attention.page(conn)["unread"] == 0
        assert conn.execute("SELECT status FROM input_requests").fetchone()["status"] == "pending"
        conn.execute("UPDATE input_requests SET expires_at=now()-interval '1 second'")
    assert project_task(task)["needs_owner"] == 0


def test_read_observed_version_does_not_read_new_material(make_task):
    task = make_task()
    ask(task)
    item = project_task(task)["items"][0]
    with db.connection() as conn:
        conn.execute("UPDATE input_requests SET prompt='A new question',question_hash='new'")
        attention.project(conn, "task", task["id"])
        attention.acknowledge(conn, [Observed(id=item["id"], version=1)], "read")
        assert attention.page(conn)["unread"] == 1
        assert attention.page(conn)["items"][0]["version"] == 2


def test_open_and_dismiss_cannot_resolve_live_question(make_task):
    task = make_task()
    ask(task)
    item = project_task(task)["items"][0]
    response = client().get("/api/v1/attention/" + item["id"])
    assert response.status_code == 200 and response.json()["request"]["prompt"]
    assert (
        client()
        .post(
            "/api/v1/attention/read", json={"items": [{"id": item["id"], "version": 1}], "action": "dismiss"}
        )
        .status_code
        == 200
    )
    assert project_task(task)["needs_owner"] == 1
    assert project_task(task)["unread"] == 1


def test_discussion_has_no_result_notification(make_task):
    task = make_task()
    with db.connection() as conn:
        conn.execute(
            "UPDATE tasks SET status='completed',message_intent='discussion',output='Thanks' WHERE id=%s",
            (task["id"],),
        )
    project_task(task)
    with db.connection() as conn:
        assert not attention.page(conn, "updates")["items"]


def test_replay_and_batch_read_preserve_new_arrivals(make_task):
    one = make_task()
    ask(one)
    item = project_task(one)["items"][0]
    two = make_task(bot="writer")
    ask(two)
    project_task(two)
    with db.connection() as conn:
        attention.project(conn, "task", one["id"])
        attention.acknowledge(conn, [Observed(id=item["id"], version=1)], "read")
        value = attention.page(conn)
        assert value["needs_owner"] == 2 and value["unread"] == 1
        assert conn.execute("SELECT count(*) AS n FROM attention_items").fetchone()["n"] == 2


def test_direct_sql_and_rollback_dirty_are_transactional(make_task):
    task = make_task()
    with db.connection() as conn:
        conn.execute("DELETE FROM attention_pending")
    try:
        with db.connection() as conn:
            conn.execute("UPDATE tasks SET status='failed' WHERE id=%s", (task["id"],))
            raise ValueError()
    except ValueError:
        pass
    with db.connection() as conn:
        assert not conn.execute("SELECT * FROM attention_pending").fetchall()
        conn.execute("UPDATE tasks SET status='failed' WHERE id=%s", (task["id"],))
        assert conn.execute("SELECT * FROM attention_pending").fetchone()


def test_dirty_generation_cannot_ack_a_new_hint(make_task):
    task = make_task()
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM attention_pending WHERE subject_type='task'").fetchone()
        attention.dirty(conn, "task", task["id"])
        conn.execute(
            "DELETE FROM attention_pending WHERE subject_type=%s AND subject_id=%s AND generation=%s",
            ("task", task["id"], row["generation"]),
        )
        assert (
            conn.execute("SELECT generation FROM attention_pending WHERE subject_type='task'").fetchone()[
                "generation"
            ]
            > row["generation"]
        )


@pytest.mark.parametrize("kind", ["connector", "runtime", "lifecycle", "skill", "memory", "input"])
def test_review_kinds_and_authority(make_task, kind):
    task = make_task(manager=True)
    with db.connection() as conn:
        identity = uuid4()
        attempt = uuid4() if kind == "runtime" else None
        if attempt:
            conn.execute(
                "INSERT INTO attempts(id,task_id,bot_id,number,session_id,status,idempotency_key,input) VALUES(%s,%s,%s,1,'test','running',%s,'test')",
                (attempt, task["id"], task["bot_id"], str(uuid4())),
            )
        conn.execute(
            "INSERT INTO approvals(id,task_id,attempt_id,kind,payload,payload_hash,expires_at) VALUES(%s,%s,%s,%s,'{}','hash',now()+interval '1 hour')",
            (identity, task["id"], attempt, kind),
        )
    assert project_task(task)["needs_owner"] == 1
    with db.connection() as conn:
        conn.execute("UPDATE grants SET revoked_at=now() WHERE id=%s", (task["grant_id"],))
    current = project_task(task)
    assert current["needs_owner"] == 1
    assert all(item["category"] != "reviews" for item in current["items"])
    assert current["items"][0]["category"] == "problems"


def test_outcome_acceptance_waits_for_settlement(make_task):
    from backend import outcomes

    task = make_task()
    with db.connection() as conn:
        outcomes.register(
            conn, task, [{"key": "report", "title": "Report", "kind": "answer", "required": True}]
        )
        conn.execute("UPDATE work_outcomes SET status='fulfilled',evidence_strength='answer'")
        conn.execute("UPDATE tasks SET status='running' WHERE id=%s", (task["id"],))
        attention.project(conn, "work", task["work_item_id"])
        assert not attention.page(conn, "updates")["items"]
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task["id"],))
        attention.project(conn, "work", task["work_item_id"])
        assert attention.page(conn, "updates")["items"][0]["title"] == "Results ready"


def test_old_results_bootstrap_read_without_push(make_task, monkeypatch):
    policy(monkeypatch)
    task = make_task()
    with db.connection() as conn:
        conn.execute(
            "UPDATE tasks SET status='completed',created_at=now()-interval '2 days' WHERE id=%s",
            (task["id"],),
        )
        conn.execute(
            "UPDATE work_items SET created_at=now()-interval '2 days' WHERE id=%s", (task["work_item_id"],)
        )
        attention.project(conn, "work", task["work_item_id"])
        assert attention.page(conn, "updates")["unread"] == 0
        assert not conn.execute("SELECT * FROM notification_deliveries").fetchall()


def test_quiet_cross_midnight_and_dst():
    pref = {
        "quiet_enabled": True,
        "timezone": "America/New_York",
        "quiet_start": time(22),
        "quiet_end": time(8),
    }
    at = datetime(2026, 11, 1, 4, 30, tzinfo=timezone.utc)
    assert notifications.quiet_until(pref, at) == datetime(2026, 11, 1, 13, tzinfo=timezone.utc)
    pref["quiet_end"] = time(2, 30)
    at = datetime(2026, 3, 8, 6, tzinfo=timezone.utc)
    assert notifications.quiet_until(pref, at) == datetime(2026, 3, 8, 7, tzinfo=timezone.utc)
    assert notifications.quiet_until(pref, datetime(2026, 3, 8, 18, tzinfo=timezone.utc)) is None


def test_push_disabled_by_default(make_task):
    task = make_task()
    ask(task)
    project_task(task)
    with db.connection() as conn:
        assert not conn.execute("SELECT * FROM notification_deliveries").fetchall()


def test_push_dedup_and_read_before_claim(make_task, monkeypatch):
    policy(monkeypatch)
    task = make_task()
    ask(task)
    item = project_task(task)["items"][0]
    project_task(task)
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM notification_deliveries").fetchone()["n"] == 1
        attention.acknowledge(conn, [Observed(id=item["id"], version=1)], "read")
    ready_deliveries()
    assert notifications.claim() is None


def test_push_provider_acceptance_is_not_read(make_task, monkeypatch):
    policy(monkeypatch)
    task = make_task()
    ask(task)
    project_task(task)
    ready_deliveries()
    claimed = notifications.claim()
    assert claimed and claimed["ttl"] <= 3600
    monkeypatch.setattr(notifications, "send", lambda d: type("Accepted", (), {"status_code": 201})())
    notifications.deliver(claimed)
    with db.connection() as conn:
        assert attention.page(conn)["unread"] == 1
        assert conn.execute("SELECT status FROM notification_deliveries").fetchone()["status"] == "accepted"


@pytest.mark.parametrize(
    "status,retired", [(404, True), (410, True), (401, False), (403, False), (413, False), (503, False)]
)
def test_provider_errors_retire_only_invalid_endpoint(make_task, monkeypatch, status, retired):
    policy(monkeypatch)
    task = make_task()
    ask(task)
    project_task(task)
    ready_deliveries()
    claimed = notifications.claim()

    def failed(d):
        raise notifications.ProviderFailure("private canary", status=status, retry_after=120)

    monkeypatch.setattr(notifications, "send", failed)
    notifications.deliver(claimed)
    with db.connection() as conn:
        sub = conn.execute("SELECT * FROM push_subscriptions").fetchone()
        assert sub["enabled"] is not retired
        delivery = conn.execute("SELECT * FROM notification_deliveries").fetchone()
        assert "canary" not in str(delivery)
        if status == 503:
            assert delivery["status"] == "pending" and delivery["due_at"] > service.now() + timedelta(
                seconds=110
            )


def test_quiet_summary_is_single_and_not_self_merged(make_task, monkeypatch):
    policy(monkeypatch)
    task = make_task()
    ask(task)
    project_task(task)
    with db.connection() as conn:
        conn.execute(
            "UPDATE notification_deliveries SET batch_key='quiet:period',due_at=now()-interval '1 second'"
        )
    assert notifications.claim() is None
    claimed = notifications.claim()
    assert claimed and claimed["purpose"] == "quiet:period"
    assert notifications.claim() is None


def test_api_requires_owner_and_enforces_observed_bounds():
    from backend.app import app

    assert TestClient(app).get("/api/v1/attention").status_code == 401
    assert (
        client()
        .post("/api/v1/attention/read", json={"items": [{"id": str(uuid4()), "version": 1}] * 101})
        .status_code
        == 422
    )
    settings = client().get("/api/v1/notifications/settings").json()
    payload = {
        k: settings[k]
        for k in [
            "revision",
            "push_enabled",
            "categories",
            "timezone",
            "quiet_enabled",
            "quiet_start",
            "quiet_end",
        ]
    }
    payload["timezone"] = "No/SuchPlace"
    assert client().put("/api/v1/notifications/settings", json=payload).status_code == 422
    payload["timezone"] = "UTC"
    assert client().put("/api/v1/notifications/settings", json=payload).status_code == 200
    assert client().put("/api/v1/notifications/settings", json=payload).status_code == 409


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://push.example.com/a",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://example.com:8443/x",
        "https://user:pass@example.com/x",
        "https://example.com/x#fragment",
    ],
)
def test_subscription_ssrf_boundary(endpoint):
    with pytest.raises(HTTPException):
        notifications.validate_endpoint(endpoint)


def test_transport_uses_public_session_hook_and_generic_payload(make_task, monkeypatch):
    import base64
    import json
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from requests import Response

    key = ec.generate_private_key(ec.SECP256R1())

    def enc(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    monkeypatch.setenv(
        "VAPID_PRIVATE_KEY",
        enc(
            key.private_bytes(
                serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        ),
    )
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:owner@example.com")
    called = []

    def post(self, url, **kwargs):
        called.append((url, kwargs))
        response = Response()
        response.status_code = 201
        return response

    monkeypatch.setattr(notifications.PinnedSession, "post", post)
    secret = {
        "endpoint": "https://push.example.com/private-canary",
        "keys": {
            "auth": enc(b"a" * 16),
            "p256dh": enc(
                key.public_key().public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                )
            ),
        },
    }
    delivery = {
        "subscription": {"secret_encrypted": encrypt(json.dumps(secret))},
        "purpose": "item:test:1",
        "target": "inbox",
        "ttl": 3600,
    }
    assert notifications.send(delivery).status_code == 201
    assert len(called) == 1 and called[0][1]["headers"]["ttl"] == "3600"
    assert b"private-canary" not in called[0][1]["data"]


def test_delayed_lower_event_id_commit_is_not_lost(make_task):
    older, newer = make_task(), make_task(bot="writer")
    with db.connection() as conn:
        conn.execute("DELETE FROM attention_pending")
    with db.connection() as a:
        lower = a.execute(
            "INSERT INTO events(kind,task_id) VALUES('task.failed',%s) RETURNING id", (older["id"],)
        ).fetchone()["id"]
        a.execute("UPDATE tasks SET status='failed' WHERE id=%s", (older["id"],))
        with db.connection() as b:
            higher = b.execute(
                "INSERT INTO events(kind,task_id) VALUES('task.failed',%s) RETURNING id", (newer["id"],)
            ).fetchone()["id"]
            b.execute("UPDATE tasks SET status='failed' WHERE id=%s", (newer["id"],))
        assert lower < higher
        attention.drain()
    attention.drain()
    with db.connection() as conn:
        assert attention.page(conn)["needs_owner"] == 2


def test_projector_poison_subject_does_not_starve_other_work(make_task, monkeypatch):
    bad, good = make_task(), make_task(bot="writer")
    ask(good)
    project = attention.project

    def guarded(conn, kind, identity):
        if kind == "task" and identity == bad["id"]:
            raise ValueError("PRIVATE_CANARY")
        return project(conn, kind, identity)

    monkeypatch.setattr(attention, "project", guarded)
    for _ in range(3):
        try:
            attention.drain()
        except ValueError:
            pass
    with db.connection() as conn:
        assert attention.page(conn)["needs_owner"] == 1
        assert (
            "CANARY"
            not in conn.execute("SELECT worker_error FROM notification_preferences").fetchone()[
                "worker_error"
            ]
        )


def test_stale_lease_cannot_send_or_overwrite_new_claim(make_task, monkeypatch):
    policy(monkeypatch)
    task = make_task()
    ask(task)
    project_task(task)
    ready_deliveries()
    old = notifications.claim()
    with db.connection() as conn:
        conn.execute(
            "UPDATE notification_deliveries SET lease_until=now()-interval '1 second',last_attempt_at=now()-interval '2 minutes'"
        )
    new = notifications.claim()
    sent = []
    monkeypatch.setattr(
        notifications, "send", lambda d: sent.append(d) or type("Accepted", (), {"status_code": 201})()
    )
    notifications.deliver(old)
    assert not sent
    notifications.deliver(new)
    assert len(sent) == 1
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM notification_deliveries").fetchone()["status"] == "accepted"


def test_empty_quiet_digest_does_not_notify_unrelated_unread_question(make_task, monkeypatch):
    policy(monkeypatch)
    first = make_task()
    ask(first)
    project_task(first)
    with db.connection() as conn:
        conn.execute(
            "UPDATE notification_deliveries SET batch_key='quiet:first',due_at=now()-interval '1 second'"
        )
    assert notifications.claim() is None
    other = make_task(bot="writer")
    ask(other)
    project_task(other)
    with db.connection() as conn:
        conn.execute("UPDATE input_requests SET status='closed' WHERE task_id=%s", (first["id"],))
        conn.execute(
            "UPDATE notification_deliveries SET due_at=now()+interval '1 hour' WHERE item_id IS NOT NULL AND status='pending'"
        )
    assert notifications.claim() is None
    with db.connection() as conn:
        assert (
            conn.execute("SELECT status FROM notification_deliveries WHERE purpose='quiet:first'").fetchone()[
                "status"
            ]
            == "suppressed"
        )


def test_quiet_fold_uses_one_period_identity():
    pref = {
        "revision": 1,
        "quiet_enabled": True,
        "timezone": "America/New_York",
        "quiet_start": time(22),
        "quiet_end": time(1, 30),
    }
    one = datetime(2026, 11, 1, 5, 15, tzinfo=timezone.utc)
    two = datetime(2026, 11, 1, 6, 15, tzinfo=timezone.utc)
    assert notifications.quiet_key(pref, one) == notifications.quiet_key(pref, two)


def test_logout_revokes_current_browser_only(monkeypatch):
    from fastapi import Response
    from backend import auth
    from backend.secrets import credential_hash

    first, _ = policy(monkeypatch)
    with db.connection() as conn:
        conn.execute(
            "UPDATE push_subscriptions SET device_hash=%s WHERE id=%s",
            (credential_hash("first-browser"), first),
        )
        conn.execute(
            "INSERT INTO push_subscriptions(id,device_hash,endpoint_hash,generation,origin,key_id,label) VALUES(%s,'other','other',%s,'http://localhost:8780','key','Other')",
            (uuid4(), uuid4()),
        )
    auth.logout(Response(), None, "first-browser")
    with db.connection() as conn:
        assert not conn.execute("SELECT enabled FROM push_subscriptions WHERE id=%s", (first,)).fetchone()[
            "enabled"
        ]
        assert conn.execute("SELECT enabled FROM push_subscriptions WHERE device_hash='other'").fetchone()[
            "enabled"
        ]
        assert notifications.preferences(conn)["push_enabled"]


def test_historical_attention_target_is_read_by_id(make_task):
    task = make_task()
    ask(task)
    item = project_task(task)["items"][0]
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET created_at=now()-interval '3 years' WHERE id=%s", (task["id"],))
    value = client().get("/api/v1/attention/" + item["id"]).json()
    assert value["task"]["id"] == str(task["id"])
    assert value["conversation"]["bot_ids"]
    assert value["work"]["id"] == str(task["work_item_id"])


def test_queue_saturation_uses_one_overflow_obligation(make_task, monkeypatch):
    sub, generation = policy(monkeypatch)
    task = make_task()
    ask(task)
    item = project_task(task)["items"][0]
    with db.connection() as conn:
        conn.execute("DELETE FROM notification_deliveries")
        conn.execute(
            "INSERT INTO notification_deliveries(id,subscription_id,generation,purpose,policy_revision,expires_at) SELECT gen_random_uuid(),%s,%s,'filler:'||n,1,now()+interval '1 hour' FROM generate_series(1,999) n",
            (sub, generation),
        )
        row = conn.execute("SELECT * FROM attention_items WHERE id=%s", (item["id"],)).fetchone()
        for revision in range(10):
            notifications.enqueue(conn, {**row, "version": revision + 1})
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM notification_deliveries WHERE status='pending'"
            ).fetchone()["n"]
            == 1000
        )


def test_malformed_endpoint_port_is_validation_error():
    with pytest.raises(HTTPException) as exc:
        notifications.validate_endpoint("https://example.com:wrong/path")
    assert exc.value.status_code == 422


def test_private_subscription_input_not_echoed_on_validation_error(monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "key")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:owner@example.com")
    response = client().post(
        "/api/v1/notifications/subscriptions",
        json={
            "endpoint": "https://example.com/PRIVATE_CANARY",
            "keys": {"auth": "PRIVATE_CANARY"},
            "label": "Browser",
            "key_id": "x" * 64,
        },
    )
    assert response.status_code == 422 and "PRIVATE_CANARY" not in response.text


def test_failed_send_retry_respects_five_attempt_limit(make_task, monkeypatch):
    policy(monkeypatch)
    task = make_task()
    ask(task)
    project_task(task)
    ready_deliveries()
    with db.connection() as conn:
        conn.execute("UPDATE notification_deliveries SET attempts=4")
    claimed = notifications.claim()

    def timeout(delivery):
        raise TimeoutError("PRIVATE_CANARY")

    monkeypatch.setattr(notifications, "send", timeout)
    notifications.deliver(claimed)
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM notification_deliveries").fetchone()
        assert row["attempts"] == 5 and row["status"] == "failed" and "CANARY" not in str(row)


def test_expiry_is_scheduled_without_waiting_for_history_reconciliation(make_task):
    task = make_task(manager=True)
    ask(task)
    with db.connection() as conn:
        conn.execute(
            "UPDATE grants SET expires_at=now()+interval '10 minutes' WHERE id=%s", (task["grant_id"],)
        )
    attention.drain()
    with db.connection() as conn:
        item = conn.execute(
            "SELECT * FROM attention_items WHERE subject_type='task' AND category='questions'"
        ).fetchone()
        hint = conn.execute(
            "SELECT * FROM attention_pending WHERE subject_type='task' AND subject_id=%s", (task["id"],)
        ).fetchone()
        grant = conn.execute("SELECT expires_at FROM grants WHERE id=%s", (task["grant_id"],)).fetchone()
        assert hint["due_at"] == item["expires_at"] == grant["expires_at"]


def test_push_policy_change_preserves_covered_summary_members(make_task, monkeypatch):
    policy(monkeypatch)
    task = make_task()
    ask(task)
    project_task(task)
    with db.connection() as conn:
        conn.execute(
            "UPDATE notification_deliveries SET batch_key='quiet:old',due_at=now()-interval '1 second'"
        )
    notifications.claim()
    settings = client().get("/api/v1/notifications/settings").json()
    payload = {
        k: settings[k]
        for k in [
            "revision",
            "push_enabled",
            "categories",
            "timezone",
            "quiet_enabled",
            "quiet_start",
            "quiet_end",
        ]
    }
    payload["timezone"] = "Asia/Kolkata"
    assert client().put("/api/v1/notifications/settings", json=payload).status_code == 200
    with db.connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM notification_deliveries WHERE status='pending' AND policy_revision=2 AND item_id IS NOT NULL"
        ).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM notification_deliveries WHERE status='pending' AND policy_revision=1"
        ).fetchone()


def test_read_result_tombstone_is_not_resurrected_by_reconciliation(make_task):
    task = make_task()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task["id"],))
        attention.project(conn, "work", task["work_item_id"])
        item = attention.page(conn, "updates")["items"][0]
        attention.acknowledge(conn, [Observed(id=item["id"], version=1)], "read")
        conn.execute(
            "UPDATE attention_items SET updated_at=now()-interval '91 days' WHERE id=%s", (item["id"],)
        )
    attention.maintenance()
    with db.connection() as conn:
        attention.project(conn, "work", task["work_item_id"])
        retained = conn.execute("SELECT * FROM attention_items WHERE id=%s", (item["id"],)).fetchone()
        assert retained["pruned_at"] and retained["version"] == retained["read_version"] == 1
        assert attention.page(conn, "updates")["unread"] == 0


def test_expired_endpoint_does_not_disable_another_browser(make_task, monkeypatch):
    first, _ = policy(monkeypatch)
    with db.connection() as conn:
        other = uuid4()
        conn.execute(
            "INSERT INTO push_subscriptions(id,device_hash,endpoint_hash,generation,origin,key_id,label) VALUES(%s,'other','other',%s,'http://localhost:8780','key','Other')",
            (other, uuid4()),
        )
    task = make_task()
    ask(task)
    project_task(task)
    ready_deliveries()
    claimed = notifications.claim()
    monkeypatch.setattr(notifications, "send", lambda d: type("Gone", (), {"status_code": 410})())
    notifications.deliver(claimed)
    with db.connection() as conn:
        assert not conn.execute("SELECT enabled FROM push_subscriptions WHERE id=%s", (first,)).fetchone()[
            "enabled"
        ]
        assert conn.execute("SELECT enabled FROM push_subscriptions WHERE id=%s", (other,)).fetchone()[
            "enabled"
        ]


def test_registration_encrypts_credentials_and_rotates_key_generation(monkeypatch):
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    monkeypatch.setenv("VAPID_PUBLIC_KEY", "public")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:owner@example.com")
    monkeypatch.setattr(notifications, "validate_endpoint", lambda value: None)

    def enc(raw):
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    key = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    body = {
        "endpoint": "https://push.example.com/PRIVATE_CANARY",
        "keys": {"auth": enc(b"a" * 16), "p256dh": enc(key)},
        "label": "Test browser",
        "key_id": notifications.configuration()["key_id"],
    }
    browser = client()
    assert browser.post("/api/v1/notifications/subscriptions", json=body).status_code == 200
    with db.connection() as conn:
        old = conn.execute("SELECT * FROM push_subscriptions").fetchone()
        assert "PRIVATE_CANARY" not in str(old)
    safe = browser.get("/api/v1/notifications/settings").json()
    assert "PRIVATE_CANARY" not in str(safe) and safe["devices"][0]["current"]
    body["keys"]["auth"] = enc(b"b" * 16)
    assert browser.post("/api/v1/notifications/subscriptions", json=body).status_code == 200
    with db.connection() as conn:
        new = conn.execute("SELECT * FROM push_subscriptions").fetchone()
        assert old["id"] == new["id"] and old["generation"] != new["generation"]
    browser.delete("/api/v1/session")
    with db.connection() as conn:
        assert not conn.execute("SELECT enabled FROM push_subscriptions").fetchone()["enabled"]


def test_push_burst_defers_fourth_wakeup(make_task, monkeypatch):
    sub, generation = policy(monkeypatch)
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO notification_deliveries(id,subscription_id,generation,purpose,policy_revision,expires_at,status,last_attempt_at) SELECT gen_random_uuid(),%s,%s,'prior:'||n,1,now()+interval '1 hour','accepted',now() FROM generate_series(1,3) n",
            (sub, generation),
        )
    task = make_task()
    ask(task)
    project_task(task)
    ready_deliveries()
    assert notifications.claim() is None
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM notification_deliveries WHERE status='pending'").fetchone()
        assert row["batch_key"].startswith("burst:") and row["due_at"] > service.now()


def test_dependent_setup_warnings_follow_the_real_connection(make_task):
    task = make_task()
    connection, source, trigger = uuid4(), uuid4(), uuid4()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO connections(id,provider,name,status,error) VALUES(%s,'slack','Slack','needs_reconnect','Private provider reason')",
            (connection,),
        )
        conn.execute(
            "INSERT INTO event_sources(id,name,provider,connection_id,transport,status,error) VALUES(%s,'Mentions','slack',%s,'socket','needs_access','Access rejected')",
            (source, connection),
        )
        conn.execute(
            "INSERT INTO event_trigger_definitions(id,source_id,bot_id,conversation_id,state,error) VALUES(%s,%s,'researcher',%s,'paused','Source access rejected')",
            (trigger, source, task["conversation_id"]),
        )
        for kind, identity in [("connection", connection), ("source", source), ("trigger", trigger)]:
            attention.project(conn, kind, identity)
        items = attention.page(conn)["items"]
        assert len(items) == 1 and items[0]["subject_type"] == "connection"
        conn.execute("UPDATE connections SET status='connected',error=NULL WHERE id=%s", (connection,))
        attention.project(conn, "connection", connection)
        attention.project(conn, "source", source)
        assert attention.page(conn)["items"][0]["subject_type"] == "source"


def test_projection_continues_during_slow_push_send(make_task, monkeypatch):
    import asyncio
    import threading
    from backend import notification_worker

    task = make_task()
    ask(task)
    started, release = threading.Event(), threading.Event()

    def slow_send(delivery):
        started.set()
        release.wait(2)

    monkeypatch.setattr(notifications, "claim", lambda: {"test": True})
    monkeypatch.setattr(notifications, "deliver", slow_send)

    async def exercise():
        stop = asyncio.Event()
        monkeypatch.setattr(notification_worker, "STOP", stop)
        sender = asyncio.create_task(notification_worker.pump(notification_worker.delivery_cycle))
        assert await asyncio.to_thread(started.wait, 1)
        projector = asyncio.create_task(notification_worker.pump(notification_worker.project_cycle))
        try:
            for _ in range(20):
                await asyncio.sleep(0.025)
                with db.connection() as conn:
                    if attention.page(conn)["needs_owner"]:
                        break
            else:
                raise AssertionError("Push delivery blocked owner attention")
            assert not release.is_set()
        finally:
            stop.set()
            release.set()
            await asyncio.gather(sender, projector)

    asyncio.run(exercise())


def test_static_occurrence_identity_survives_display_reclassification(make_task):
    from backend import outcomes

    task = make_task()
    with db.connection() as conn:
        outcomes.register(
            conn, task, [{"key": "report", "title": "Report", "kind": "answer", "required": True}]
        )
        conn.execute("UPDATE work_outcomes SET status='fulfilled'")
        conn.execute(
            "UPDATE tasks SET management_work_item_id=work_item_id,work_item_id=NULL,message_intent='discussion',status='waiting_children' WHERE id=%s",
            (task["id"],),
        )
        attention.project(conn, "work", task["work_item_id"])
        assert not attention.page(conn, "updates")["items"]
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task["id"],))
        attention.project(conn, "work", task["work_item_id"])
        assert attention.page(conn, "updates")["items"][0]["refs"]["work_item_id"] == str(
            task["work_item_id"]
        )


def test_static_occurrence_keeps_published_file_links(make_task):
    task = make_task()
    artifact = uuid4()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO artifacts(id,task_id,bot_id,name,content_type,object_key,sha256,size,conversation_id) VALUES(%s,%s,%s,'report.txt','text/plain',%s,'hash',10,%s)",
            (artifact, task["id"], task["bot_id"], str(artifact), task["conversation_id"]),
        )
        conn.execute(
            "UPDATE tasks SET management_work_item_id=work_item_id,work_item_id=NULL,message_intent='discussion',status='completed' WHERE id=%s",
            (task["id"],),
        )
        attention.project(conn, "work", task["work_item_id"])
        value = attention.page(conn, "updates")["items"][0]
    detail = client().get("/api/v1/attention/" + value["id"]).json()
    assert detail["work"]["outputs"][0]["id"] == str(artifact)


def test_policy_change_keeps_overflow_only_backlog(make_task, monkeypatch):
    sub, generation = policy(monkeypatch)
    task = make_task()
    ask(task)
    project_task(task)
    with db.connection() as conn:
        conn.execute("DELETE FROM notification_deliveries")
        conn.execute(
            "INSERT INTO notification_deliveries(id,subscription_id,generation,purpose,policy_revision,expires_at) VALUES(%s,%s,%s,'overflow:old',1,now()+interval '1 hour')",
            (uuid4(), sub, generation),
        )
    settings = client().get("/api/v1/notifications/settings").json()
    payload = {
        k: settings[k]
        for k in [
            "revision",
            "push_enabled",
            "categories",
            "timezone",
            "quiet_enabled",
            "quiet_start",
            "quiet_end",
        ]
    }
    payload["timezone"] = "Asia/Kolkata"
    assert client().put("/api/v1/notifications/settings", json=payload).status_code == 200
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM notification_deliveries WHERE status='pending'").fetchone()
        assert row["purpose"] == "overflow:policy:2" and row["policy_revision"] == 2
