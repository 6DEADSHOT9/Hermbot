"""Knowledge CAS, review authority and desktop fencing against real isolated PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import importlib.util
from pathlib import Path
import threading
import time
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend import app as application, computer, db, knowledge, native, registry, service, worker
from backend.settings import settings

pytestmark = pytest.mark.integration
EMPTY = hashlib.sha256(b"").hexdigest()


@pytest.fixture
def owner_client():
    client = TestClient(application.app, headers={"Authorization": "Bearer " + settings.owner_token})
    yield client
    client.close()


@pytest.fixture
def files(monkeypatch):
    values = {}

    def rpc(bot, method, path, **kwargs):
        if path == "/memory":
            return {
                "targets": [
                    {
                        "name": n,
                        "content": values.get((bot, n), ""),
                        "sha256": hashlib.sha256(values.get((bot, n), "").encode()).hexdigest(),
                        "limit": 2200,
                    }
                    for n in ("memory", "user")
                ]
            }
        name = path.rsplit("/", 1)[1]
        current = values.get((bot, name), "")
        if method == "GET":
            return {
                "name": name,
                "exists": bool(current),
                "content": current,
                "sha256": hashlib.sha256(current.encode()).hexdigest(),
            }
        body = kwargs["json"]
        if (
            body["content"] != current
            and body["expected_hash"] != hashlib.sha256(current.encode()).hexdigest()
        ):
            raise HTTPException(409, "Native file changed")
        values[bot, name] = body["content"]
        return {"sha256": hashlib.sha256(body["content"].encode()).hexdigest()}

    monkeypatch.setattr(native, "request", rpc)
    return values


def put(client, path, body, key=None):
    return client.put(path, json=body, headers={"Idempotency-Key": key or str(uuid4())})


def decision(client, review, choice="once"):
    return client.post(
        "/api/v1/approvals/" + str(review["id"]),
        json={"decision": choice, "expected_payload_hash": review["payload_hash"]},
    )


def review(task, kind="review", payload=None):
    with db.connection() as conn:
        result = service.request_review(
            conn,
            service.locked_task(conn, task["id"]),
            "Review exact proposed action",
            payload or {"draft": "initial"},
            kind,
        )
        return conn.execute("SELECT * FROM approvals WHERE id=%s", (result["approval_id"],)).fetchone()


def settle_refresh():
    with db.connection() as conn:
        conn.execute("UPDATE bot_operations SET status='completed'")
        conn.execute(
            "UPDATE bots SET status='active',applied_revision=revision,readiness='{\"compatible\":true}'"
        )


def test_native_file_cas_concurrency_symlinks_and_skill_removal(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("hermbot_test_adapter", Path("/app/docker/runtime_api.py"))
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    monkeypatch.setattr(adapter, "HOME", tmp_path)

    def save(value):
        try:
            return adapter.write_file(["memories"], "MEMORY.md", value, EMPTY)
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ["one", "two"]))
    assert sum(isinstance(r, dict) for r in results) == 1 and 409 in results
    actual = adapter.read_file(["memories"], "MEMORY.md")
    assert adapter.write_file(["memories"], "MEMORY.md", actual, EMPTY)["replayed"]
    (tmp_path / "memories" / "USER.md").symlink_to("/etc/passwd")
    with pytest.raises(HTTPException):
        adapter.write_file(["memories"], "USER.md", "x", EMPTY)
    adapter.write_file(["skills", "hermbot", "hermbot-demo"], "SKILL.md", "draft", EMPTY)
    adapter.write_file(["skills", "hermbot", "hermbot-demo"], "SKILL.md", "", adapter.sha("draft"))
    assert not (tmp_path / "skills" / "hermbot" / "hermbot-demo" / "SKILL.md").exists()


def test_memory_versions_replay_stale_and_busy(owner_client, files, make_task):
    path = "/api/v1/bots/researcher/knowledge/memory/memory"
    key = str(uuid4())
    body = {"content": "Use supplied data only.", "expected_hash": EMPTY}
    saved = put(owner_client, path, body, key)
    assert saved.status_code == 202
    assert put(owner_client, path, body, key).json() == saved.json()
    assert "encrypted" not in saved.text
    settle_refresh()
    assert put(owner_client, path, {"content": "stale", "expected_hash": EMPTY}).status_code == 409
    assert len(files) == 1
    task = make_task()
    assert (
        put(
            owner_client, path, {"content": "busy", "expected_hash": saved.json()["version"]["sha256"]}
        ).status_code
        == 409
    )
    with db.connection() as conn:
        service.cancel(conn, task)
    updated = put(owner_client, path, {"content": "", "expected_hash": saved.json()["version"]["sha256"]})
    assert updated.status_code == 202 and updated.json()["version"]["version"] == 2
    assert (
        owner_client.get(
            "/api/v1/bots/researcher/knowledge/versions/" + saved.json()["version"]["id"]
        ).json()["content"]
        == body["content"]
    )


def test_review_edit_invalidates_old_approval_and_is_idempotent(owner_client, make_task):
    task = make_task()
    old = review(task)
    body = {
        "expected_payload_hash": old["payload_hash"],
        "summary": "Edited draft",
        "payload": {"draft": "edited"},
    }
    key = str(uuid4())
    edited = put(owner_client, "/api/v1/approvals/" + str(old["id"]), body, key)
    assert edited.status_code == 200
    assert put(owner_client, "/api/v1/approvals/" + str(old["id"]), body, key).json() == edited.json()
    assert decision(owner_client, old).status_code == 409
    with db.connection() as conn:
        fresh = conn.execute(
            "SELECT * FROM approvals WHERE id=%s", (edited.json()["approval_id"],)
        ).fetchone()
    assert fresh["supersedes"] == old["id"] and fresh["payload"]["action"] == {"draft": "edited"}
    assert decision(owner_client, fresh).status_code == 200
    assert decision(owner_client, fresh).status_code == 200


def test_review_expiry_cancel_and_owner_auth(owner_client, make_task):
    task = make_task()
    item = review(task)
    assert (
        TestClient(application.app)
        .post(
            "/api/v1/approvals/" + str(item["id"]),
            json={"decision": "once", "expected_payload_hash": item["payload_hash"]},
        )
        .status_code
        == 401
    )
    with db.connection() as conn:
        conn.execute(
            "UPDATE tasks SET deadline=%s WHERE id=%s", (service.now() - timedelta(seconds=1), task["id"])
        )
        conn.execute(
            "UPDATE approvals SET expires_at=%s WHERE id=%s",
            (service.now() - timedelta(seconds=1), item["id"]),
        )
    assert decision(owner_client, item).status_code == 409
    worker.advance_waiters()
    with db.connection() as conn:
        assert (
            conn.execute("SELECT status FROM approvals WHERE id=%s", (item["id"],)).fetchone()["status"]
            == "expired"
        )
    fresh = review(make_task())
    assert decision(owner_client, fresh, "cancel").status_code == 200
    assert decision(owner_client, fresh).status_code == 409


def test_reviewed_learning_waits_for_native_refresh(owner_client, files, make_task):
    task = make_task()
    draft = {
        "name": "hermbot-sum",
        "content": "---\nname: hermbot-sum\ndescription: Sum supplied values\n---\nRead then sum.",
        "enable": True,
    }
    item = review(task, "skill", draft)
    assert files == {}
    assert decision(owner_client, item).status_code == 200
    assert worker.claim_new() == []
    knowledge.drive_proposals()
    assert files["researcher", "hermbot-sum"] == draft["content"]
    with db.connection() as conn:
        assert (
            conn.execute("SELECT status FROM tasks WHERE id=%s", (task["id"],)).fetchone()["status"]
            == "waiting_approval"
        )
        assert registry.require_bot(conn, "researcher")["enabled_skills"] == ["hermbot-sum"]
    knowledge.drive_proposals()
    settle_refresh()
    knowledge.drive_proposals()
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM knowledge_proposals").fetchone()["status"] == "completed"
        assert (
            conn.execute("SELECT status FROM tasks WHERE id=%s", (task["id"],)).fetchone()["status"]
            == "queued"
        )
        assert conn.execute("SELECT count(*) AS n FROM knowledge_versions").fetchone()["n"] == 1


def test_reviewed_learning_checks_native_changes_and_bot_revision(owner_client, files, make_task):
    task = make_task()
    item = review(task, "memory", {"name": "memory", "content": "Proposed learning"})
    decision(owner_client, item)
    files["researcher", "memory"] = "Concurrent native edit"
    knowledge.drive_proposals()
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM knowledge_proposals").fetchone()["status"] == "failed"
        assert conn.execute("SELECT count(*) AS n FROM knowledge_versions").fetchone()["n"] == 0
    assert files["researcher", "memory"] == "Concurrent native edit"


def test_learning_cross_bot_authority_and_cancel_before_effect(owner_client, files, make_task):
    task = make_task()
    with db.connection() as conn:
        with pytest.raises(HTTPException) as exc:
            service.request_review(
                conn, task, "Change peer", {"bot_id": "writer", "name": "memory", "content": "x"}, "memory"
            )
        assert exc.value.status_code == 403
    item = review(task, "memory", {"name": "memory", "content": "Approved but stopped"})
    decision(owner_client, item)
    with db.connection() as conn:
        service.cancel(conn, task)
    knowledge.drive_proposals()
    assert files == {}


def test_computer_fences_admission_and_does_not_release_on_status(owner_client, monkeypatch, make_task):
    task = make_task()
    identity = computer.viewer("Bearer " + settings.owner_token)
    state = {
        "desktop": {"running": True},
        "lease": {
            "holder": "human",
            "viewer_hash": hashlib.sha256(identity.encode()).hexdigest()[:12],
            "epoch": 1,
        },
        "input_ready": True,
    }
    monkeypatch.setattr(native, "request", lambda *a, **k: state.copy())
    assert owner_client.post("/api/v1/bots/researcher/computer/control", json={"action": "takeover"}).json()[
        "mine"
    ]
    assert worker.claim_new() == []
    assert owner_client.get("/api/v1/bots/researcher/computer").json()["admission_paused"]
    state["lease"] = {"holder": "agent", "epoch": 2}
    assert owner_client.get("/api/v1/bots/researcher/computer").json()["admission_paused"]
    assert (
        owner_client.post("/api/v1/bots/researcher/computer/control", json={"action": "handback"}).status_code
        == 200
    )
    assert worker.claim_new()[0]["task_id"] == task["id"]


def test_desktop_stop_settles_before_takeover_can_admit_input(owner_client, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def rpc(bot, method, path, **kwargs):
        calls.append(path)
        if path == "/computer/stop":
            entered.set()
            assert release.wait(6)
        return {"desktop": {"running": False}, "lease": {"holder": "agent", "epoch": 1}}

    monkeypatch.setattr(native, "request", rpc)
    with ThreadPoolExecutor(2) as executor:
        stopped = executor.submit(owner_client.post, "/api/v1/bots/researcher/computer/stop")
        assert entered.wait(3)
        takeover = executor.submit(
            owner_client.post,
            "/api/v1/bots/researcher/computer/control",
            json={"action": "takeover"},
        )
        try:
            deadline = time.monotonic() + 3
            while True:
                with db.connection() as conn:
                    waiting = conn.execute(
                        "SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted"
                    ).fetchone()
                if waiting:
                    break
                assert time.monotonic() < deadline, "Takeover did not wait for the desktop mutation"
                time.sleep(0.01)
            assert calls == ["/computer/stop"]
            with db.connection() as conn:
                assert not conn.execute(
                    "SELECT 1 FROM computer_controls WHERE bot_id='researcher'"
                ).fetchone()
        finally:
            release.set()
        assert stopped.result().status_code == takeover.result().status_code == 200
    assert calls[:2] == ["/computer/stop", "/computer/control"]


def test_secure_input_review_records_metadata_only_and_requires_handback(
    owner_client, monkeypatch, make_task
):
    task = make_task()
    item = review(task, "input", {"input_type": "secure"})
    monkeypatch.setattr(native, "request", lambda *a, **k: {"lease": {"holder": "human"}})
    assert decision(owner_client, item).status_code == 409
    monkeypatch.setattr(native, "request", lambda *a, **k: {"lease": {"holder": "agent"}})
    assert decision(owner_client, item).status_code == 200
    with db.connection() as conn:
        continuation = conn.execute("SELECT continuation FROM tasks WHERE id=%s", (task["id"],)).fetchone()[
            "continuation"
        ]
    assert "secure input is COMPLETED" in continuation
    with db.connection() as conn:
        fresh = make_task(bot="writer")
        with pytest.raises(HTTPException):
            service.request_review(
                conn, fresh, "Secret", {"input_type": "secure", "secret": "unsafe"}, "input"
            )


def test_websocket_denies_cross_origin_and_unauthenticated():
    client = TestClient(application.app)
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/bots/researcher/computer/ws", headers={"Origin": "https://evil.example"}
        ):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/bots/researcher/computer/ws", headers={"Origin": "http://testserver"}
        ):
            pass


def test_paused_native_turn_resumes_only_after_explicit_handback(make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        computer.fence(conn, "researcher", "human")
        conn.execute(
            "UPDATE attempts SET response=response||'{\"computer_paused\":true}'::jsonb WHERE id=%s",
            (attempt["id"],),
        )
    worker.settle(attempt["id"], {"status": "completed", "output": "Waiting for owner handback"})
    worker.advance_waiters()
    with db.connection() as conn:
        assert (
            conn.execute("SELECT status FROM tasks WHERE id=%s", (task["id"],)).fetchone()["status"]
            == "waiting_computer"
        )
        computer.fence(conn, "researcher", "agent")
    worker.advance_waiters()
    assert worker.claim_new()[0]["task_id"] == task["id"]


def test_invalid_knowledge_names_and_revoked_manager_proposal(owner_client, files, make_task):
    task = make_task(manager=True)
    with db.connection() as conn:
        with pytest.raises(HTTPException) as exc:
            service.request_review(conn, task, "Malformed name", {"name": {}, "content": "x"}, "skill")
        assert exc.value.status_code == 422
    item = review(task, "skill", {"name": "hermbot-procedure", "content": "draft"})
    decision(owner_client, item)
    with db.connection() as conn:
        conn.execute("UPDATE grants SET revoked_at=now() WHERE id=%s", (task["grant_id"],))
    knowledge.drive_proposals()
    assert files == {}
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM knowledge_proposals").fetchone()["status"] == "failed"


def test_database_computer_fence_blocks_native_admission_even_if_adapter_is_unreachable(make_task):
    from backend.models import ContextRequest

    task = make_task()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        computer.fence(conn, "researcher", "human")
    result = application.context(ContextRequest(session_id=attempt["session_id"]), bot_id="researcher")
    assert result["error"] == "human_has_control"
    with db.connection() as conn:
        assert conn.execute("SELECT response FROM attempts WHERE id=%s", (attempt["id"],)).fetchone()[
            "response"
        ]["computer_paused"]
    worker.settle(attempt["id"], {"status": "completed", "output": "Waiting for handback"})
    with db.connection() as conn:
        assert (
            conn.execute("SELECT status FROM tasks WHERE id=%s", (task["id"],)).fetchone()["status"]
            == "waiting_computer"
        )
