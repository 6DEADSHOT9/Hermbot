from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend import app as application, db, lifecycle, registry, service, worker
from backend.models import GrantRequest
from backend.secrets import decrypt
from backend.settings import settings

pytestmark = pytest.mark.integration


@pytest.fixture
def owner_client():
    client = TestClient(application.app, headers={"Authorization": "Bearer " + settings.owner_token})
    yield client
    client.close()


def config(name="Phase 2 test bot"):
    return {"name": name, "role": "Verify supplied local data", "capabilities": ["data"], "enabled_tools": ["file", "terminal"]}


def post(client, path, body, key=None):
    return client.post(path, json=body, headers={"Idempotency-Key": key or str(uuid4())})


def create(client, name="Phase 2 test bot"):
    result = post(client, "/api/v1/bots", config(name))
    assert result.status_code == 202
    return result.json()


def settled_bot(bot_id):
    with db.connection() as conn:
        conn.execute("UPDATE bot_operations SET status='completed' WHERE bot_id=%s", (bot_id,))
        conn.execute("UPDATE bots SET status='active',applied_revision=revision,readiness='{\"compatible\":true}' WHERE id=%s", (bot_id,))


def test_dynamic_creation_is_idempotent_and_credentials_are_private(owner_client):
    key = str(uuid4())
    first = post(owner_client, "/api/v1/bots", config(), key)
    second = post(owner_client, "/api/v1/bots", config(), key)
    assert first.status_code == second.status_code == 202
    assert first.json()["bot"]["id"] == second.json()["bot"]["id"]
    assert post(owner_client, "/api/v1/bots", config("Different"), key).status_code == 409
    with db.connection() as conn:
        bot = registry.require_bot(conn, first.json()["bot"]["id"])
        assert bot["bot_token_encrypted"] and decrypt(bot["bot_token_encrypted"])
        assert conn.execute("SELECT count(*) AS n FROM bot_operations").fetchone()["n"] == 1
    assert "encrypted" not in first.text and "bot_token" not in first.text
    assert "secret_encrypted" not in owner_client.get("/api/v1/state").text


def test_clone_selects_state_and_generates_distinct_identities(owner_client):
    original = create(owner_client)["bot"]
    settled_bot(original["id"])
    result = post(owner_client, f"/api/v1/bots/{original['id']}/clone", {"configuration": config("Copy"), "expected_revision": original["revision"], "copy_workspace": True})
    assert result.status_code == 202
    with db.connection() as conn:
        source = registry.require_bot(conn, original["id"])
        copy = registry.require_bot(conn, result.json()["bot"]["id"])
        operation = conn.execute("SELECT payload FROM bot_operations WHERE id=%s", (UUID(result.json()["operation"]["id"]),)).fetchone()
        assert source["bot_token_hash"] != copy["bot_token_hash"]
        assert decrypt(source["runtime_key_encrypted"]) != decrypt(copy["runtime_key_encrypted"])
        assert operation["payload"]["copy_options"] == {"memory": False, "skills": False, "workspace": True, "history": False}
    assert post(owner_client, f"/api/v1/bots/{original['id']}/stop", {"expected_revision": original["revision"]}).status_code == 409


def test_delete_requires_exact_name_and_refuses_unsettled_work(owner_client, make_task):
    bot = create(owner_client)["bot"]
    settled_bot(bot["id"])
    assert post(owner_client, f"/api/v1/bots/{bot['id']}/delete", {"expected_revision": bot["revision"], "confirmation": "wrong"}).status_code == 422
    task = make_task()
    researcher = next(b for b in owner_client.get("/api/v1/state").json()["bots"] if b["id"] == "researcher")
    assert post(owner_client, "/api/v1/bots/researcher/stop", {"expected_revision": researcher["revision"]}).status_code == 409
    result = post(owner_client, "/api/v1/bots/researcher/stop", {"expected_revision": researcher["revision"], "interrupt": True})
    assert result.status_code == 202
    with db.connection() as conn:
        assert service.locked_task(conn, task["id"])["status"] == "cancelled"


def test_browser_session_is_opaque_expiring_and_revocable():
    browser = TestClient(application.app)
    try:
        assert browser.get("/api/v1/state").status_code == 401
        result = browser.post("/api/v1/session", headers={"Authorization": "Bearer " + settings.owner_token})
        assert result.status_code == 200
        token = browser.cookies.get("hermbot_session")
        assert token and token != settings.owner_token
        assert "HttpOnly" in result.headers["set-cookie"] and "SameSite=strict" in result.headers["set-cookie"]
        assert browser.get("/api/v1/state").status_code == 200
        browser.delete("/api/v1/session")
        assert browser.get("/api/v1/state").status_code == 401
        browser.cookies.set("hermbot_session", token)
        assert browser.get("/api/v1/state").status_code == 401
    finally:
        browser.close()


def test_provider_secret_is_encrypted_and_not_returned(owner_client):
    body = {"name": "Test provider", "base_url": "http://example.invalid/v1", "models": ["test-model"], "default_model": "test-model", "api_key": "provider-secret-for-test"}
    response = post(owner_client, "/api/v1/providers", body)
    assert response.status_code == 201
    assert "provider-secret-for-test" not in response.text
    with db.connection() as conn:
        provider = conn.execute("SELECT * FROM providers WHERE id=%s", (UUID(response.json()["id"]),)).fetchone()
        assert provider["secret_encrypted"] != body["api_key"] and decrypt(provider["secret_encrypted"]) == body["api_key"]
    assert "secret_encrypted" not in owner_client.get("/api/v1/settings").text
    body["base_url"] = "https://example.invalid/v1?key=unsafe"
    assert post(owner_client, "/api/v1/providers", body).status_code == 422


def manager_with_grant(make_task, operations, targets=None, creation=1):
    with db.connection() as conn:
        grant = registry.create_grant(conn, GrantRequest(manager_id="reviewer", targets=targets or ["researcher"], operations=operations, max_created_bots=creation))
        conversation = conn.execute("SELECT id FROM conversations WHERE bot_id='reviewer'").fetchone()["id"]
        task = service.create_task(conn, bot_id="reviewer", conversation_id=conversation, prompt="Manage the assigned outcome", request_key=str(uuid4()), grant_id=grant["id"])
    return task, grant


def test_manager_cannot_escalate_create_delete_or_control_outside_grant(make_task):
    task, _ = manager_with_grant(make_task, ["configure", "inspect"])
    with pytest.raises(HTTPException) as exc:
        with db.connection() as conn:
            lifecycle.propose(conn, task, {"action": "create", "configuration": config(), "summary": "Escalate"})
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        with db.connection() as conn:
            lifecycle.propose(conn, task, {"action": "configure", "bot_id": "writer", "configuration": config(), "summary": "Outside scope"})
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        with db.connection() as conn:
            service.delegate(conn, task, "researcher", "Not permitted")
    assert exc.value.status_code == 403
    from backend.models import ProposalArgs
    with pytest.raises(ValidationError):
        ProposalArgs(action="delete", bot_id="researcher", summary="Delete")


def test_denied_lifecycle_proposal_never_enqueues_a_runtime_operation(owner_client, make_task):
    task, _ = manager_with_grant(make_task, ["create", "delegate"])
    with db.connection() as conn:
        result = lifecycle.propose(conn, task, {"action": "create", "configuration": config(), "summary": "Create specialist"})
        approval = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(result["approval_id"]),)).fetchone()
    response = owner_client.post("/api/v1/approvals/" + result["approval_id"], json={"decision": "deny", "expected_payload_hash": approval["payload_hash"]})
    assert response.status_code == 200
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM bot_operations").fetchone()["n"] == 0
        assert service.locked_task(conn, task["id"])["status"] == "queued"


def test_edit_invalidates_old_proposal_and_approval_waits_for_provisioning(owner_client, make_task):
    task, grant = manager_with_grant(make_task, ["create", "delegate"])
    with db.connection() as conn:
        initial = lifecycle.propose(conn, task, {"action": "create", "configuration": config(), "summary": "Initial"})
        old = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(initial["approval_id"]),)).fetchone()
    edited = owner_client.put("/api/v1/proposals/" + initial["proposal_id"], json={"action": "create", "configuration": config("Edited specialist"), "summary": "Edited"}, headers={"Idempotency-Key": str(uuid4())})
    assert edited.status_code == 200
    assert owner_client.post("/api/v1/approvals/" + initial["approval_id"], json={"decision": "once", "expected_payload_hash": old["payload_hash"]}).status_code == 409
    with db.connection() as conn:
        approval = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(edited.json()["approval_id"]),)).fetchone()
    assert owner_client.post("/api/v1/approvals/" + edited.json()["approval_id"], json={"decision": "once", "expected_payload_hash": approval["payload_hash"]}).status_code == 200
    with db.connection() as conn:
        proposal = conn.execute("SELECT * FROM lifecycle_proposals WHERE id=%s", (UUID(edited.json()["proposal_id"]),)).fetchone()
        assert registry.require_bot(conn, proposal["bot_id"])["name"] == "Edited specialist"
        assert service.locked_task(conn, task["id"])["status"] == "waiting_approval"
        updated = conn.execute("SELECT * FROM grants WHERE id=%s", (grant["id"],)).fetchone()
        assert updated["created_bots"] == 1 and proposal["bot_id"] in updated["targets"]


def test_stale_target_configuration_rejects_approved_payload(owner_client, make_task):
    task, _ = manager_with_grant(make_task, ["configure"])
    with db.connection() as conn:
        result = lifecycle.propose(conn, task, {"action": "configure", "bot_id": "researcher", "configuration": config("Scout edited"), "summary": "Edit Scout"})
        approval = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(result["approval_id"]),)).fetchone()
        conn.execute("UPDATE bots SET revision=revision+1 WHERE id='researcher'")
    assert owner_client.post("/api/v1/approvals/" + result["approval_id"], json={"decision": "once", "expected_payload_hash": approval["payload_hash"]}).status_code == 409
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM bot_operations").fetchone()["n"] == 0


def test_standing_grant_expiry_and_revocation_stop_only_owned_operations(owner_client, make_task):
    task, grant = manager_with_grant(make_task, ["delegate", "inspect"])
    unrelated = make_task("writer")
    with db.connection() as conn:
        conn.execute("UPDATE grants SET expires_at=%s WHERE id=%s", (service.now() - timedelta(seconds=1), grant["id"]))
    worker.advance_waiters()
    with db.connection() as conn:
        assert service.locked_task(conn, task["id"])["status"] == "cancelled"
        assert service.locked_task(conn, unrelated["id"])["status"] == "queued"


def test_threads_have_separate_runtime_sessions_and_reactions(owner_client, make_task):
    parent = make_task()
    with db.connection() as conn:
        message_id = conn.execute("SELECT id FROM messages WHERE task_id=%s", (parent["id"],)).fetchone()["id"]
        thread = service.create_task(conn, bot_id="researcher", conversation_id=parent["conversation_id"], prompt="Reply in this thread", request_key=str(uuid4()), reply_to_message_id=message_id)
    first = worker.claim_new()[0]
    worker.settle(first["id"], {"status": "completed", "output": "Parent result"})
    second = worker.claim_new()[0]
    assert second["task_id"] == thread["id"] and first["session_id"] != second["session_id"]
    path = f"/api/v1/conversations/{parent['conversation_id']}"
    main = owner_client.get(path).json()
    replies = owner_client.get(path, params={"thread": message_id}).json()
    assert all(message["reply_to_message_id"] is None for message in main["messages"])
    assert any(message["reply_to_message_id"] == message_id for message in replies["messages"])
    assert owner_client.post(f"/api/v1/messages/{message_id}/reactions", json={"emoji": "👍"}).json()["reacted"]
    assert not owner_client.post(f"/api/v1/messages/{message_id}/reactions", json={"emoji": "👍"}).json()["reacted"]


def test_queued_edit_and_steer_receipts_are_stale_safe(owner_client, make_task):
    task = make_task()
    payload = {"prompt": "Revised instructions", "expected_input_hash": service.digest(task["input"])}
    assert owner_client.put(f"/api/v1/tasks/{task['id']}", json=payload).status_code == 200
    assert owner_client.put(f"/api/v1/tasks/{task['id']}", json=payload).status_code == 409
    key = str(uuid4())
    first = post(owner_client, f"/api/v1/tasks/{task['id']}/steer", {"prompt": "Queued direction"}, key)
    second = post(owner_client, f"/api/v1/tasks/{task['id']}/steer", {"prompt": "Queued direction"}, key)
    assert first.status_code == second.status_code == 200
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM controls").fetchone()["n"] == 1
