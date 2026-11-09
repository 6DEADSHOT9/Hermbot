import base64
import hashlib
import json
import os
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest

from backend import containers, db, lifecycle, provisioning, registry, service
from backend.models import WorkspaceRequest
from docker.write_attachment import install
from .test_phase2 import config, create, manager_with_grant, owner_client, post, settled_bot

__all__ = ["owner_client"]
pytestmark = pytest.mark.integration


def test_attachment_install_rejects_symlink_directories_and_replaces_leaf_safely(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    body = {"attachment_id": str(uuid4()), "name": "proof.txt", "content_base64": base64.b64encode(b"safe").decode(), "sha256": hashlib.sha256(b"safe").hexdigest()}
    (workspace / "attachments").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        install(workspace, body)
    assert not list(outside.iterdir())
    (workspace / "attachments").unlink()
    install(workspace, body)
    destination = workspace / "attachments" / body["attachment_id"] / body["name"]
    destination.unlink()
    victim = outside / "victim.txt"
    victim.write_text("preserved")
    destination.symlink_to(victim)
    install(workspace, body)
    assert destination.read_bytes() == b"safe" and not destination.is_symlink()
    assert victim.read_text() == "preserved"


@pytest.mark.parametrize('data', [b'safe', b'x' * 120_000])
def test_attachment_staging_works_outside_read_only_runtime_code(monkeypatch, tmp_path, data):
    body = containers.AttachmentSpec(bot_id='researcher', attachment_id=str(uuid4()), name='input.txt',
                                    content_base64=base64.b64encode(data).decode(),
                                    sha256=hashlib.sha256(data).hexdigest())
    commands = []
    staged = bytearray()
    monkeypatch.setattr(containers, 'find_runtime', lambda bot: {'Id': 'runtime'})
    def command(runtime, argv):
        commands.append(argv)
        if argv[1] == '-c' and argv[2] == containers.STAGING_WRITE:
            assert commands[0][-1] == containers.STAGING_INIT
            assert len(argv[4]) < 128_000, 'Payload chunks must fit Linux argument limits.'
            if argv[5] == 'first':
                staged.clear()
            staged.extend(base64.b64decode(argv[4], validate=True))
        elif argv[1] == '/opt/hermbot/write_attachment.py':
            assert argv[2].startswith('/run/hermbot-staging/upload-')
            install(tmp_path, json.loads(staged))
    monkeypatch.setattr(containers, 'runtime_command', command)
    result = containers.transfer_attachment(body)
    assert result['path'].endswith('/' + body.attachment_id + '/input.txt')
    assert (tmp_path / 'attachments' / body.attachment_id / 'input.txt').read_bytes() == data
    assert len(commands) >= 3


@pytest.mark.parametrize('unsafe', ['symlink', 'public'])
def test_staging_directory_rejects_agent_writable_or_symlink_paths(tmp_path, unsafe):
    if os.getuid() != 0:
        pytest.skip('The isolated container checks root-owned runtime staging.')
    code = containers.STAGING_INIT.replace("'/run'", repr(str(tmp_path)))
    exec(code, {})
    stage = tmp_path / 'hermbot-staging'
    assert stage.stat().st_mode & 0o077 == 0
    if unsafe == 'symlink':
        stage.rmdir()
        outside = tmp_path / 'outside'
        outside.mkdir()
        stage.symlink_to(outside, target_is_directory=True)
    else:
        stage.chmod(0o777)
    with pytest.raises((OSError, AssertionError)):
        exec(code, {})


def test_supervisor_refuses_foreign_volume_before_any_mutation(monkeypatch):
    monkeypatch.setattr(containers, "optional_get", lambda path: {"Labels": {"com.docker.compose.project": "meridian"}})
    monkeypatch.setattr(containers, "docker_request", lambda *args, **kwargs: pytest.fail("Unowned resource must not be mutated"))
    with pytest.raises(HTTPException) as exc:
        containers.ensure_resources("bot-owned")
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        containers.resource_names("../meridian")


def test_workspace_changes_reconfigure_timezone_and_enforce_limits(owner_client, make_task):
    settings = owner_client.get("/api/v1/settings").json()["workspace"]
    body = {key: settings[key] for key in WorkspaceRequest.model_fields}
    body["timezone"] = "Asia/Kolkata"
    task = make_task()
    assert owner_client.put("/api/v1/settings", json=body).status_code == 409
    with db.connection() as conn:
        service.cancel(conn, service.locked_task(conn, task["id"]), "Test settlement")
    assert owner_client.put("/api/v1/settings", json=body).status_code == 200
    with db.connection() as conn:
        operations = conn.execute("SELECT payload FROM bot_operations").fetchall()
        assert len(operations) == 3 and all(o["payload"]["timezone"] == "Asia/Kolkata" for o in operations)
    body["max_bots"] = 3
    assert owner_client.put("/api/v1/settings", json=body).status_code == 200
    assert post(owner_client, "/api/v1/bots", config()).status_code == 409


def test_archived_configuration_stays_archived(owner_client, monkeypatch):
    original = create(owner_client)["bot"]
    settled_bot(original["id"])
    with db.connection() as conn:
        conn.execute("UPDATE bots SET status='archived',desired_state='stopped' WHERE id=%s", (original["id"],))
    assert post(owner_client, f"/api/v1/bots/{original['id']}/start", {"expected_revision": original["revision"]}).status_code == 409
    updated = owner_client.put(f"/api/v1/bots/{original['id']}", json={**config("Edited archive"), "expected_revision": original["revision"]}, headers={"Idempotency-Key": str(uuid4())})
    assert updated.status_code == 202
    assert updated.json()["bot"]["readiness"] == {}
    with db.connection() as conn:
        operation = conn.execute("SELECT * FROM bot_operations WHERE id=%s", (UUID(updated.json()["operation"]["id"]),)).fetchone()
    async def receipt(*args, **kwargs):
        return {"state": "stopped", "revision": operation["payload"]["revision"]}
    monkeypatch.setattr(provisioning, "supervisor", receipt)
    import asyncio
    asyncio.run(provisioning.drive(operation))
    with db.connection() as conn:
        assert registry.require_bot(conn, original["id"])["status"] == "archived"


def test_approved_failed_create_can_retry_without_consuming_second_budget(owner_client, make_task):
    task, grant = manager_with_grant(make_task, ["create", "delegate"])
    with db.connection() as conn:
        proposal = lifecycle.propose(conn, task, {"action": "create", "configuration": config(), "summary": "Create"})
        approval = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(proposal["approval_id"]),)).fetchone()
        lifecycle.decide(conn, approval, service.locked_task(conn, task["id"]), "once")
        operation = conn.execute("SELECT * FROM bot_operations").fetchone()
        conn.execute("UPDATE bot_operations SET status='failed' WHERE id=%s", (operation["id"],))
        conn.execute("UPDATE bots SET status='error' WHERE id=%s", (operation["bot_id"],))
    assert owner_client.post(f"/api/v1/bot-operations/{operation['id']}/retry").status_code == 200
    with db.connection() as conn:
        assert conn.execute("SELECT created_bots FROM grants WHERE id=%s", (grant["id"],)).fetchone()["created_bots"] == 1
        conn.execute("UPDATE bot_operations SET status='failed' WHERE id=%s", (operation["id"],))
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task["id"],))
    assert owner_client.post(f"/api/v1/bot-operations/{operation['id']}/retry").status_code == 409


def test_revoked_approved_operation_is_not_dispatched(owner_client, make_task, monkeypatch):
    task, grant = manager_with_grant(make_task, ["create", "delegate"])
    with db.connection() as conn:
        proposal = lifecycle.propose(conn, task, {"action": "create", "configuration": config(), "summary": "Create"})
        approval = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(proposal["approval_id"]),)).fetchone()
        lifecycle.decide(conn, approval, service.locked_task(conn, task["id"]), "once")
        operation = conn.execute("SELECT * FROM bot_operations").fetchone()
        conn.execute("UPDATE grants SET revoked_at=now() WHERE id=%s", (grant["id"],))
    async def forbidden(*args, **kwargs):
        pytest.fail("Revoked operation must not reach Docker")
    monkeypatch.setattr(provisioning, "supervisor", forbidden)
    import asyncio
    asyncio.run(provisioning.drive(operation))
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM bot_operations WHERE id=%s", (operation["id"],)).fetchone()["status"] == "cancelled"


def test_native_uncertainty_blocks_group_removal(owner_client):
    chat = post(owner_client, "/api/v1/groups", {"title": "Review", "bot_ids": ["researcher", "writer", "reviewer"]}).json()
    with db.connection() as conn:
        task = service.create_task(conn, bot_id="researcher", conversation_id=UUID(chat["id"]), prompt="Unsettled", request_key=str(uuid4()))
        conn.execute("UPDATE tasks SET status='uncertain' WHERE id=%s", (task["id"],))
    assert owner_client.put(f"/api/v1/groups/{chat['id']}", json={"title": "Review", "bot_ids": ["writer", "reviewer"]}).status_code == 409


def test_provider_edits_survive_bootstrap_without_duplicate_import(owner_client):
    with db.connection() as conn:
        original = conn.execute("SELECT * FROM providers WHERE from_environment=true").fetchone()
        conn.execute("UPDATE providers SET name='Owner renamed profile',from_environment=false WHERE id=%s", (original["id"],))
        registry.bootstrap(conn)
        assert conn.execute("SELECT count(*) AS n FROM providers").fetchone()["n"] == 1
        conn.execute("UPDATE providers SET name='Environment provider',from_environment=true WHERE id=%s", (original["id"],))


def test_skills_require_native_skills_tool(owner_client):
    assert post(owner_client, "/api/v1/bots", {**config(), "enabled_skills": ["python"]}).status_code == 422


def test_restarting_runtime_is_stopped_before_removal(monkeypatch):
    calls = []
    monkeypatch.setattr(containers, "docker_request", lambda method, path, **kwargs: calls.append((method, path)))
    containers.remove_container({"Id": "owned-container", "State": "restarting"})
    assert calls == [("POST", "/containers/owned-container/stop"), ("DELETE", "/containers/owned-container")]


def test_supervisor_receipt_replays_without_repeating_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(containers, "Path", lambda path: tmp_path / "receipts")
    calls = []
    def execute(action, spec):
        calls.append(spec.operation_id)
        return {"state": "running", "revision": spec.revision}
    monkeypatch.setattr(containers, "execute_operation", execute)
    spec = containers.RuntimeSpec(bot_id="bot-test", operation_id=str(uuid4()), revision=1, configuration=config(), model_base_url="https://example.invalid/v1", model_api_key="private-test-key", runtime_key="r" * 32, adapter_key="a" * 32, bot_token="b" * 32)
    first = containers.operate("create", spec)
    assert containers.operate("create", spec) == first and calls == [spec.operation_id]
    assert "private-test-key" not in next((tmp_path / "receipts").glob("*.json")).read_text()
    with pytest.raises(HTTPException) as exc:
        containers.operate("create", spec.model_copy(update={"revision": 2}))
    assert exc.value.status_code == 409


def test_transport_ambiguity_reconciles_same_operation(owner_client, monkeypatch):
    import asyncio
    import httpx
    original = create(owner_client)
    with db.connection() as conn:
        operation = conn.execute("SELECT * FROM bot_operations WHERE id=%s", (UUID(original["operation"]["id"]),)).fetchone()
    dispatched = []
    async def ambiguous(method, path, **kwargs):
        dispatched.append(kwargs["json"]["operation_id"])
        raise httpx.ReadTimeout("Receipt missing")
    monkeypatch.setattr(provisioning, "supervisor", ambiguous)
    asyncio.run(provisioning.drive(operation))
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM bot_operations WHERE id=%s", (operation["id"],)).fetchone()["status"] == "applying"
    async def reconciled(method, path, **kwargs):
        dispatched.append(kwargs["json"]["operation_id"])
        return {"state": "running", "revision": operation["payload"]["revision"]}
    monkeypatch.setattr(provisioning, "supervisor", reconciled)
    asyncio.run(provisioning.drive(operation))
    assert dispatched == [str(operation["id"])] * 2
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM bot_operations WHERE id=%s", (operation["id"],)).fetchone()["status"] == "completed"


@pytest.mark.parametrize("action", ["configure", "start", "stop"])
def test_approved_manager_runtime_changes_execute_exact_snapshot(action, owner_client, make_task, monkeypatch):
    import asyncio
    task, _ = manager_with_grant(make_task, [action])
    args = {"action": action, "bot_id": "researcher", "summary": "Exact authorized change"}
    if action == "configure":
        args["configuration"] = config("Owner reviewed Scout")
    with db.connection() as conn:
        proposal = lifecycle.propose(conn, task, args)
        approval = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(proposal["approval_id"]),)).fetchone()
        lifecycle.decide(conn, approval, service.locked_task(conn, task["id"]), "once")
        operation = conn.execute("SELECT * FROM bot_operations").fetchone()
    async def execute(method, path, **kwargs):
        assert path == f"/operations/{action}"
        spec = kwargs["json"]
        assert spec["configuration"] == operation["payload"]["configuration"]
        assert spec["desired_state"] == ("stopped" if action == "stop" else "running")
        return {"state": spec["desired_state"], "revision": spec["revision"]}
    monkeypatch.setattr(provisioning, "supervisor", execute)
    asyncio.run(provisioning.drive(operation))
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM bot_operations WHERE id=%s", (operation["id"],)).fetchone()["status"] == "completed"
        assert service.locked_task(conn, task["id"])["status"] == "queued"


def test_revoking_standing_grant_cancels_all_its_roots_only(owner_client, make_task):
    task, grant = manager_with_grant(make_task, ["delegate", "inspect"])
    with db.connection() as conn:
        second = service.create_task(conn, bot_id="reviewer", conversation_id=task["conversation_id"], prompt="Second outcome", request_key=str(uuid4()), grant_id=grant["id"])
    unrelated = make_task("writer")
    assert owner_client.post(f"/api/v1/grants/{grant['id']}/revoke").status_code == 200
    with db.connection() as conn:
        assert all(service.locked_task(conn, item["id"])["status"] == "cancelled" for item in (task, second))
        assert service.locked_task(conn, unrelated["id"])["status"] == "queued"


def test_global_execution_limit_applies_to_dynamic_bots(owner_client, make_task):
    from backend import worker
    bot = create(owner_client)["bot"]
    settled_bot(bot["id"])
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET max_active=2")
        conversation = conn.execute("SELECT id FROM conversations WHERE bot_id=%s", (bot["id"],)).fetchone()["id"]
        service.create_task(conn, bot_id=bot["id"], conversation_id=conversation, prompt="Fourth runtime", request_key=str(uuid4()))
    for identity in ("researcher", "writer", "reviewer"):
        make_task(identity)
    assert len(worker.claim_new()) == 2
    assert worker.claim_new() == []


def test_resource_budget_and_provider_revision_are_rechecked(owner_client, make_task):
    task, _ = manager_with_grant(make_task, ["create"])
    with pytest.raises(HTTPException) as exc:
        with db.connection() as conn:
            lifecycle.propose(conn, task, {"action": "create", "configuration": {**config(), "memory_mb": 4096}, "summary": "Too much memory"})
    assert exc.value.status_code == 403
    with db.connection() as conn:
        proposal = lifecycle.propose(conn, task, {"action": "create", "configuration": config(), "summary": "Reviewed provider"})
        approval = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(proposal["approval_id"]),)).fetchone()
        conn.execute("UPDATE providers SET revision=revision+1")
    assert owner_client.post(f"/api/v1/approvals/{approval['id']}", json={"decision": "once", "expected_payload_hash": approval["payload_hash"]}).status_code == 409
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM bot_operations").fetchone()["n"] == 0


def test_runtime_admission_cannot_race_lifecycle_configuration(owner_client):
    with db.connection() as blocker:
        registry.require_bot(blocker, "researcher", lock=True)
        conversation = blocker.execute("SELECT id FROM conversations WHERE bot_id='researcher'").fetchone()["id"]
        result = post(owner_client, "/api/v1/tasks", {"bot_id": "researcher", "conversation_id": str(conversation), "prompt": "Concurrent admission"})
        assert result.status_code == 409
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM tasks").fetchone()["n"] == 0


def test_duplicate_task_receipt_survives_later_runtime_stop(owner_client, make_task):
    task = make_task()
    with db.connection() as conn:
        conn.execute("UPDATE bots SET desired_state='stopped' WHERE id='researcher'")
    result = post(owner_client, "/api/v1/tasks", {"bot_id": "researcher", "conversation_id": str(task["conversation_id"]), "prompt": task["input"]}, task["request_key"])
    assert result.status_code == 202 and result.json()["id"] == str(task["id"])


def test_specialist_approval_respects_current_group_member_limit(owner_client):
    from backend.models import GrantRequest
    added = [create(owner_client, f"Member {i}")["bot"] for i in range(3)]
    for bot in added:
        settled_bot(bot["id"])
    members = ["researcher", "writer", "reviewer", *[b["id"] for b in added]]
    group = post(owner_client, "/api/v1/groups", {"title": "Full group", "bot_ids": members}).json()
    with db.connection() as conn:
        grant = registry.create_grant(conn, GrantRequest(manager_id="reviewer", targets=["researcher"], operations=["create", "delegate"]))
        task = service.create_task(conn, bot_id="reviewer", conversation_id=UUID(group["id"]), prompt="Propose specialist", request_key=str(uuid4()), grant_id=grant["id"])
        proposal = lifecycle.propose(conn, task, {"action": "create", "configuration": config(), "summary": "One more member"})
        approval = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(proposal["approval_id"]),)).fetchone()
    assert owner_client.post(f"/api/v1/approvals/{approval['id']}", json={"decision": "once", "expected_payload_hash": approval["payload_hash"]}).status_code == 409
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM bots").fetchone()["n"] == 6
        assert conn.execute("SELECT created_bots FROM grants WHERE id=%s", (grant["id"],)).fetchone()["created_bots"] == 0


def test_deleted_teammates_keep_owner_conversation_history_without_credentials(owner_client):
    bot = create(owner_client)["bot"]
    with db.connection() as conn:
        conn.execute("UPDATE bots SET status='deleted',bot_token_hash=NULL,bot_token_encrypted=NULL,runtime_key_encrypted=NULL WHERE id=%s", (bot["id"],))
        conversation = conn.execute("SELECT id FROM conversations WHERE bot_id=%s", (bot["id"],)).fetchone()["id"]
    state = owner_client.get("/api/v1/state").json()
    assert not any(b["id"] == bot["id"] for b in state["bots"])
    archived = next(b for b in state["retired_bots"] if b["id"] == bot["id"])
    assert archived["name"] == bot["name"] and not any("token" in key or "encrypted" in key for key in archived)
    assert any(c["id"] == str(conversation) for c in state["conversations"])
    assert owner_client.get(f"/api/v1/conversations/{conversation}").status_code == 200


def test_old_unsettled_work_and_conversation_outputs_survive_recent_state_window(owner_client, make_task):
    old = make_task()
    other = make_task("writer")
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET created_at=now()-interval '1 day' WHERE id=%s", (old["id"],))
        for i in range(301):
            identity = uuid4()
            conn.execute("INSERT INTO tasks(id,root_id,bot_id,conversation_id,input,status,deadline,request_key,request_hash) VALUES (%s,%s,'writer',%s,'Recent completed work','completed',now()+interval '1 hour',%s,%s)", (identity, identity, other["conversation_id"], str(identity), "a" * 64))
        old_artifact = uuid4()
        conn.execute("INSERT INTO artifacts(id,task_id,bot_id,name,content_type,object_key,sha256,size,created_at) VALUES (%s,%s,'researcher','old-output.txt','text/plain',%s,%s,4,now()-interval '1 day')", (old_artifact, old["id"], str(old_artifact), "a" * 64))
        for i in range(100):
            identity = uuid4()
            conn.execute("INSERT INTO artifacts(id,task_id,bot_id,name,content_type,object_key,sha256,size) VALUES (%s,%s,'writer','recent-output.txt','text/plain',%s,%s,4)", (identity, other["id"], str(identity), "a" * 64))
    state = owner_client.get("/api/v1/state").json()
    assert any(t["id"] == str(old["id"]) for t in state["tasks"])
    assert not any(a["id"] == str(old_artifact) for a in state["artifacts"])
    conversation = owner_client.get(f"/api/v1/conversations/{old['conversation_id']}").json()
    assert any(a["id"] == str(old_artifact) for a in conversation["artifacts"])
