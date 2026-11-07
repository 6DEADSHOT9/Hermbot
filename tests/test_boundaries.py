import asyncio
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from backend import app as api_module, db, service, worker
from backend.artifacts import ArtifactStore
from backend.settings import settings
from tests.conftest import row

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    # The session fixture owns the test DB lifecycle; don't run the production lifespan.
    return TestClient(api_module.app, headers={"Authorization": "Bearer " + settings.owner_token})


def test_owner_bot_origin_and_argument_boundaries(client, make_task):
    assert TestClient(api_module.app).get("/api/v1/state").status_code == 401
    bot_headers = {"Authorization": "Bearer " + settings.bot_token("researcher")}
    assert TestClient(api_module.app, headers=bot_headers).get("/api/v1/state").status_code == 401
    task = make_task()
    attempt = worker.claim_new()[0]
    reply = client.post("/api/v1/tasks/" + str(task["id"]) + "/stop", headers={"Origin": "https://unrelated.example"})
    assert reply.status_code == 403 and row(task["id"])["status"] == "running"
    reply = TestClient(api_module.app, headers=bot_headers).post("/internal/tools", json={"session_id": attempt["session_id"], "receipt_id": "callX", "operation": "finish", "arguments": {"result": "Done", "sender_id": "owner"}})
    assert reply.status_code == 422


@pytest.mark.parametrize('auth_kind', ['api_key', 'chatgpt_oauth', 'codex_oauth'])
def test_worker_leaves_provider_selection_to_applied_runtime(make_task, auth_kind):
    task = make_task()
    with db.connection() as conn:
        original = conn.execute('SELECT provider_id,model_name FROM bots WHERE id=%s', (task['bot_id'],)).fetchone()
        if auth_kind == 'api_key':
            provider_id = conn.execute("SELECT id FROM providers WHERE auth_kind='api_key' LIMIT 1").fetchone()['id']
        else:
            provider_id = uuid4()
            endpoint = 'https://api.openai.com/v1' if auth_kind == 'chatgpt_oauth' else 'https://chatgpt.com/backend-api/codex'
            conn.execute("INSERT INTO providers(id,name,base_url,models,default_model,auth_kind,transport) VALUES(%s,%s,%s,'[\"test\"]','test',%s,'responses')", (provider_id, str(provider_id), endpoint, auth_kind))
        conn.execute("UPDATE bots SET provider_id=%s,model_name='test' WHERE id=%s", (provider_id, task['bot_id']))
    try:
        request = worker.claim_new()[0]['response']['request']
        assert request['model'] == 'test'
        assert 'provider' not in request and 'api_key' not in request and 'base_url' not in request
    finally:
        with db.connection() as conn:
            conn.execute('UPDATE bots SET provider_id=%s,model_name=%s WHERE id=%s', (original['provider_id'], original['model_name'], task['bot_id']))


def test_review_payload_binding_duplicate_decision_and_expiry(client, make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        review = service.request_review(conn, service.locked_task(conn, task["id"]), "Approve report", {"report": "exact v1"})
        item = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(review["approval_id"]),)).fetchone()
    path = "/api/v1/approvals/" + review["approval_id"]
    assert client.post(path, json={"decision": "once", "expected_payload_hash": "0" * 64}).status_code == 409
    payload = {"decision": "deny", "expected_payload_hash": item["payload_hash"]}
    assert client.post(path, json=payload).status_code == 200
    assert client.post(path, json=payload).status_code == 200
    assert client.post(path, json={**payload, "decision": "once"}).status_code == 409
    worker.settle(attempt["id"], {"status": "completed", "output": "Waiting"})
    assert row(task["id"])["status"] == "queued"
    assert "deny" in row(task["id"])["continuation"]


def test_private_artifact_scope_explicit_sharing_and_checksum(make_task):
    parent = make_task()
    unrelated = make_task("reviewer")
    store = ArtifactStore()
    store.bucket = "hermbot-test"
    store.initialize()
    with db.connection() as conn:
        asset = store.publish(conn, parent, "evidence.csv", "x,y\n2,3\n", "text/csv", "artifact-test")
        assert service.artifact_visible(conn, UUID(asset["artifact_id"]), unrelated) is None
        first = service.delegate(conn, service.locked_task(conn, parent["id"]), "writer", "Use evidence", artifact_ids=[UUID(asset["artifact_id"])])
        second = service.delegate(conn, service.locked_task(conn, parent["id"]), "reviewer", "Review without private evidence")
        allowed = service.artifact_visible(conn, UUID(asset["artifact_id"]), service.locked_task(conn, UUID(first["task_id"])))
        assert store.read(allowed) == "x,y\n2,3\n"
        assert service.artifact_visible(conn, UUID(asset["artifact_id"]), service.locked_task(conn, UUID(second["task_id"]))) is None
        with pytest.raises(HTTPException):
            store.publish(conn, parent, "../private.md", "secret", "text/plain", "badpath")
        corrupted = {**allowed, "sha256": "0" * 64}
        with pytest.raises(HTTPException):
            store.read(corrupted)


def test_worker_lease_recovery_preserves_attempt_and_dispatch_body(make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET worker_id='dead-worker',lease_until=now()-interval '1 second' WHERE id=%s", (attempt["id"],))
    recovered = worker.claim_existing()
    assert len(recovered) == 1 and recovered[0]["id"] == attempt["id"]
    assert recovered[0]["idempotency_key"] == attempt["idempotency_key"]
    assert recovered[0]["response"]["request"] == attempt["response"]["request"]
    assert row(task["id"])["attempts"] == 1


def test_ambiguous_steer_is_not_automatically_repeated(make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        service.steer(conn, service.locked_task(conn, task["id"]), "Use the new limit")
    class LostResponse:
        calls = 0
        async def steer(self, run_id, text):
            self.calls += 1
            raise httpx.ReadTimeout("Delivery response was lost")
    transport = LostResponse()
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(worker.deliver_decisions(transport, {**attempt, "runtime_run_id": "test-run"}))
    asyncio.run(worker.deliver_decisions(transport, {**attempt, "runtime_run_id": "test-run"}))
    assert transport.calls == 1
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM controls WHERE task_id=%s", (task["id"],)).fetchone()["status"] == "uncertain"


def test_scheduled_reservation_stop_preserves_slot_until_native_receipt(make_task):
    task = make_task()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='scheduled' WHERE id=%s", (task["id"],))
        conn.execute("INSERT INTO attempts(id,task_id,bot_id,number,session_id,status,idempotency_key,input) VALUES (%s,%s,'researcher',1,'scheduled:test','reserved',%s,'test')", (uuid4(), task["id"], str(uuid4())))
        service.cancel(conn, service.locked_task(conn, task["id"]))
        assert conn.execute("SELECT status FROM attempts WHERE task_id=%s", (task["id"],)).fetchone()["status"] == "stopping"
    assert row(task["id"])["status"] == "cancelling"
    make_task("researcher")
    assert worker.claim_new() == []
