"""Resource settings, shared validation and idle-only lifecycle application."""

import asyncio
from contextlib import closing
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
import pytest

from backend import app as application, db, lifecycle, limits, native, provisioning, registry, service, worker
from backend.models import GrantRequest, LifecycleRequest
from backend.settings import settings


pytestmark = pytest.mark.integration
MAC = {"cpus": 4, "memory_bytes": 8_320_299_008, "pid_max": 99_999,
       "threads_max": 63_765, "file_max": 811_881, "nr_open": 1_048_576,
       "logging_driver": "json-file", "pids_limit": True, "memory_limit": True,
       "swap_limit": True, "cgroup_version": "2", "rootless": False}
LIMITS_URL = "/api/v1/settings/limits"


@pytest.fixture(autouse=True)
def recorded_machine(clean_database):
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET machine_facts=%s", (Jsonb(MAC),))


@pytest.fixture
def owner_client(recorded_machine):
    with closing(TestClient(application.app, headers={"Authorization": "Bearer " + settings.owner_token})) as client:
        yield client


def change(client, values, *, url=LIMITS_URL, current=None, key=None, **extra):
    current = current or client.get(url).json()
    return client.put(url, headers={"Idempotency-Key": key or str(uuid4())}, json={
        "values": values, "expected_revision": current["revision"],
        "machine_signature": current["machine_signature"], **extra,
    })


def post(client, url, body):
    return client.post(url, json=body, headers={"Idempotency-Key": str(uuid4())})


def bot_snapshot(bot_id="writer"):
    with db.connection() as conn:
        return conn.execute("SELECT revision,applied_revision,inference_revision,inference_key_hash "
                            "FROM bots WHERE id=%s", (bot_id,)).fetchone()


def pending_operations(bot_id="writer"):
    with db.connection() as conn:
        return conn.execute("SELECT * FROM bot_operations WHERE bot_id=%s ORDER BY created_at", (bot_id,)).fetchall()


def mark_limits_due():
    with db.connection() as conn:
        return conn.execute("UPDATE workspace_settings SET limits_revision=limits_revision+1 "
                            "RETURNING limits_revision").fetchone()["limits_revision"]


def test_resource_endpoints_are_owner_only():
    with closing(TestClient(application.app)) as client:
        for url in (LIMITS_URL, "/api/v1/bots/writer/limits"):
            assert client.get(url).status_code in (401, 403)
            assert client.put(url, json={}, headers={"Idempotency-Key": str(uuid4())}).status_code in (401, 403)


def test_read_shares_machine_metadata_and_labels_disk_inactive(owner_client):
    result = owner_client.get(LIMITS_URL)
    assert result.status_code == 200
    body = result.json()
    assert body["overrides"] == {}
    assert body["limits"]["pids_limit"]["value"] == 1024
    assert body["limits"]["nofile_hard"]["hard"] == [1024, 1048576]
    assert body["resources"]["memory_default_mb"] == 2048
    assert body["capacity"]["memory_today_mb"] == 1536 + 3 * 2048
    assert body["disk_preview"]["active"] is False
    assert body["core_services_pending_restart"] == ["postgres", "storage"]


def test_unknown_machine_keeps_floor_settings_and_explicit_unknown_panel_values(owner_client):
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET machine_facts='{}'")
    response = owner_client.get(LIMITS_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["machine"]["cpus"] is None and body["machine"]["memory_bytes"] is None
    assert body["machine"]["rootless"] is None and body["machine"]["observed_at"] is None
    assert body["machine"]["warnings"] == [] and body["machine"]["logging_driver"] == ""
    assert body["limits"]["pids_limit"]["value"] == limits.PIDS_FLOOR
    assert body["limits"]["pids_limit"]["uncertain"] is True
    assert body["resources"]["memory_default_mb"] == limits.MIN_MEMORY_MB
    assert body["resources"]["cpus_default"] == limits.MIN_CPUS
    assert body["resources"]["memory_uncertain"] is True and body["resources"]["cpus_uncertain"] is True
    assert body["capacity"]["capacity"] is None and body["capacity"]["uncertain"] is True


def test_existing_resource_errors_are_reported_without_changing_bots(owner_client):
    with db.connection() as conn:
        conn.execute("UPDATE bots SET memory_mb=512 WHERE id='writer'")
        conn.execute("UPDATE bots SET cpus=8 WHERE id='reviewer'")
    before = {bot: bot_snapshot(bot) for bot in ("writer", "reviewer")}
    response = owner_client.get(LIMITS_URL)
    assert response.status_code == 200
    projected = {bot["id"]: bot for bot in response.json()["bots"]}
    assert any("memory_mb" in error for error in projected["writer"]["resource_errors"])
    assert any("cpus" in error for error in projected["reviewer"]["resource_errors"])
    with db.connection() as conn:
        assert registry.require_bot(conn, "writer")["memory_mb"] == 512
        assert registry.require_bot(conn, "reviewer")["cpus"] == 8
    assert {bot: bot_snapshot(bot) for bot in before} == before
    assert pending_operations() == []


def test_core_restart_pending_clears_only_when_observed_settings_match(owner_client):
    logs = {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "5"}}
    core = {
        "postgres": {"OomScoreAdj": limits.DATA_OOM_SCORE_ADJ, "MemoryReservation": 512 * limits.MIB, "LogConfig": logs},
        "storage": {"OomScoreAdj": limits.DATA_OOM_SCORE_ADJ, "MemoryReservation": 0, "LogConfig": logs},
    }
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET machine_facts=%s", (Jsonb({**MAC, "core_services": core}),))
    assert owner_client.get(LIMITS_URL).json()["core_services_pending_restart"] == []
    core["storage"]["LogConfig"] = {"Type": "json-file", "Config": {}}
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET machine_facts=%s", (Jsonb({**MAC, "core_services": core}),))
    assert owner_client.get(LIMITS_URL).json()["core_services_pending_restart"] == ["storage"]


def test_sparse_save_is_idempotent_and_reset_records_once(owner_client):
    current = owner_client.get(LIMITS_URL).json()
    key = str(uuid4())
    first = change(owner_client, {"pids_limit": 2048}, current=current, key=key)
    assert first.status_code == 200
    body = first.json()
    assert body["overrides"] == {"pids_limit": 2048}
    assert body["revision"] == current["revision"] + 1
    assert body["limits"]["pids_limit"]["source"] == "owner"
    again = change(owner_client, {"pids_limit": 2048}, current=current, key=key)
    assert again.status_code == 200 and again.json() == body
    assert len(body["history"]) == 1
    assert {k: body["history"][0][k] for k in ("setting", "from", "to")} == {
        "setting": "pids_limit", "from": None, "to": 2048}
    reset = change(owner_client, {"pids_limit": None}).json()
    assert reset["overrides"] == {}
    assert reset["limits"]["pids_limit"]["source"] == "computed"
    assert len(reset["history"]) == 2 and reset["history"][0]["to"] is None
    repeat = change(owner_client, {"pids_limit": None}).json()
    assert repeat["revision"] == reset["revision"] and repeat["history"] == reset["history"]


def test_outside_recommendation_requires_confirmation_without_mutation(owner_client):
    before = owner_client.get(LIMITS_URL).json()
    warning = change(owner_client, {"pids_limit": 128}, current=before).json()
    assert warning["requires_confirmation"] is True and warning["warnings"]
    unchanged = owner_client.get(LIMITS_URL).json()
    assert unchanged["overrides"] == {} and unchanged["revision"] == before["revision"]
    assert unchanged["history"] == []
    confirmed = change(owner_client, {"pids_limit": 128}, current=before,
                       accepted_warnings=warning["warnings"]).json()
    assert confirmed["overrides"] == {"pids_limit": 128}
    assert confirmed["history"][0]["accepted_warnings"] == warning["warnings"]
    again = change(owner_client, {"pids_limit": 128}).json()
    assert not again.get("requires_confirmation")
    assert again["revision"] == confirmed["revision"] and len(again["history"]) == 1


@pytest.mark.parametrize("values", [{"pids_limit": 127}, {"nofile_hard": 1048577},
                                    {"pids_limit": True}, {"allow_swap": 1}, {"unknown": 1}])
def test_invalid_overrides_do_not_change_settings(owner_client, values):
    assert change(owner_client, values).status_code == 422
    body = owner_client.get(LIMITS_URL).json()
    assert body["overrides"] == {} and body["history"] == [] and body["revision"] == 1


def test_stale_revision_and_machine_signature_are_refused(owner_client):
    current = owner_client.get(LIMITS_URL).json()
    assert change(owner_client, {"log_files": 6}).status_code == 200
    assert change(owner_client, {"log_files": 7}, current=current).status_code == 409
    current = owner_client.get(LIMITS_URL).json()
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET machine_facts=%s", (Jsonb({**MAC, "cpus": 8}),))
    assert change(owner_client, {"log_files": 7}, current=current).status_code == 409
    assert owner_client.get(LIMITS_URL).json()["overrides"] == {"log_files": 6}


def test_volatile_machine_readings_do_not_invalidate_a_save(owner_client):
    current = owner_client.get(LIMITS_URL).json()
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET machine_facts=%s", (Jsonb({
            **MAC, "observed_at": "2026-11-14T10:00:00Z", "disk_free_bytes": 123,
            "free_inodes": 100, "warnings": ["A fresh observation"]}),))
    assert change(owner_client, {"log_files": 6}, current=current).status_code == 200


def test_bot_process_override_inherits_workspace_and_cannot_set_other_fields(owner_client):
    assert change(owner_client, {"pids_limit": 2048, "log_files": 6}).status_code == 200
    url = "/api/v1/bots/writer/limits"
    inherited = owner_client.get(url).json()
    assert inherited["limits"]["pids_limit"]["source"] == "workspace"
    result = change(owner_client, {"pids_limit": 3072}, url=url)
    assert result.status_code == 200
    assert result.json()["overrides"] == {"pids_limit": 3072}
    assert result.json()["limits"]["log_files"]["value"] == 6
    assert owner_client.get("/api/v1/bots/reviewer/limits").json()["overrides"] == {}
    assert change(owner_client, {"log_files": 7}, url=url).status_code == 422
    operation = pending_operations()[0]
    assert operation["payload"]["limits"]["pids_limit"] == 3072
    assert operation["payload"]["limits"]["log_files"] == 6


def test_legacy_512_memory_is_preserved_on_edit_but_refused_for_creation(owner_client):
    with db.connection() as conn:
        bot = conn.execute("UPDATE bots SET memory_mb=512 WHERE id='writer' RETURNING *").fetchone()
        body = db.jsonable_encoder(registry.configuration(bot))
    body.update(name="Legacy bot renamed", expected_revision=bot["revision"])
    result = owner_client.put("/api/v1/bots/writer", json=body, headers={"Idempotency-Key": str(uuid4())})
    assert result.status_code == 202 and result.json()["bot"]["memory_mb"] == 512
    created = post(owner_client, "/api/v1/bots", {"name": "New small bot", "role": "Test validation", "memory_mb": 512})
    assert created.status_code == 422


@pytest.mark.parametrize("operations,expected", [(["delegate", "inspect"], 201), (["create"], 422), (["configure"], 422)])
def test_coordination_grants_keep_unused_small_limits(owner_client, operations, expected):
    response = post(owner_client, "/api/v1/grants", {
        "manager_id": "researcher", "targets": ["writer"], "operations": operations,
        "max_memory_mb": 512, "max_cpus": .25,
    })
    assert response.status_code == expected


def test_cpu_bounds_follow_large_machine_and_refuse_more_than_it_has(owner_client):
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET machine_facts=%s", (Jsonb({
            **MAC, "cpus": 32, "memory_bytes": 64 * limits.GIB}),))
    base = {"name": "Large machine bot", "role": "Test CPU validation", "memory_mb": 2048}
    allowed = post(owner_client, "/api/v1/bots", {**base, "cpus": 32})
    assert allowed.status_code == 202 and allowed.json()["bot"]["cpus"] == 32
    assert post(owner_client, "/api/v1/bots", {**base, "cpus": 32.25}).status_code == 422


@pytest.mark.parametrize("field,value", [("cpus", True), ("memory_mb", True), ("memory_mb", "1024")])
def test_bot_resource_inputs_do_not_coerce_non_numeric_values(owner_client, field, value):
    response = post(owner_client, "/api/v1/bots", {"name": "Typed resources", "role": "Check numeric inputs", field: value})
    assert response.status_code == 422


def test_management_resource_inputs_reject_boolean_cpu(owner_client):
    response = post(owner_client, "/api/v1/grants", {
        "manager_id": "researcher", "targets": ["writer"], "operations": ["create"],
        "max_memory_mb": 1024, "max_cpus": True,
    })
    assert response.status_code == 422


def test_workspace_capacity_change_requires_accepted_warning(owner_client):
    current = owner_client.get("/api/v1/settings").json()["workspace"]
    body = {key: current[key] for key in ("timezone", "default_provider_id", "permitted_tools", "max_bots", "max_active")}
    body["max_bots"] = 30
    response = owner_client.put("/api/v1/settings", json=body)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "capacity_confirmation_required" and detail["warnings"]
    assert owner_client.get("/api/v1/settings").json()["workspace"]["max_bots"] == current["max_bots"]
    assert owner_client.put("/api/v1/settings", json={**body, "accepted_warnings": detail["warnings"]}).status_code == 200
    assert owner_client.put("/api/v1/settings", json=body).status_code == 200
    assert owner_client.get(LIMITS_URL).json()["limits"]["pids_limit"]["value"] == 1024


def test_workspace_save_does_not_revoke_a_working_bots_model_access(owner_client, make_task):
    make_task(bot="writer")
    attempt = worker.claim_new()[0]
    before = bot_snapshot()
    assert change(owner_client, {"log_files": 6}).status_code == 200
    assert bot_snapshot() == before and pending_operations() == []
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM attempts WHERE id=%s", (attempt["id"],)).fetchone()["status"] == "dispatching"


@pytest.mark.parametrize("status", ["dispatching", "running", "waiting_for_approval", "stopping", "uncertain", "external", "reserved"])
def test_idle_apply_waits_for_every_active_or_reserved_attempt(make_task, status):
    make_task(bot="writer")
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET status=%s WHERE id=%s", (status, attempt["id"]))
    mark_limits_due()
    before = bot_snapshot()
    provisioning.queue_initial("writer")
    assert bot_snapshot() == before and pending_operations() == []


@pytest.mark.parametrize("status", ["uncertain", "waiting_input", "waiting_approval", "waiting_children"])
def test_unsettled_task_without_an_attempt_is_never_idle(make_task, status):
    task = make_task(bot="writer")
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status=%s WHERE id=%s", (status, task["id"]))
    mark_limits_due()
    before = bot_snapshot()
    provisioning.queue_initial("writer")
    assert bot_snapshot() == before and pending_operations() == []


def test_idle_apply_waits_for_human_takeover():
    with db.connection() as conn:
        conn.execute("INSERT INTO computer_controls(bot_id,holder) VALUES('writer','human')")
    mark_limits_due()
    before = bot_snapshot()
    provisioning.queue_initial("writer")
    assert bot_snapshot() == before and pending_operations() == []


@pytest.mark.parametrize("status", ["queued", "applying", "uncertain"])
def test_idle_apply_waits_for_another_lifecycle_operation(status):
    with db.connection() as conn:
        registry.enqueue(conn, registry.require_bot(conn, "writer"), "ensure", str(uuid4()))
        conn.execute("UPDATE bot_operations SET status=%s WHERE bot_id='writer'", (status,))
        conn.execute("UPDATE bots SET status='active',readiness=%s WHERE id='writer'", (Jsonb({"compatible": True}),))
    mark_limits_due()
    before = bot_snapshot()
    provisioning.queue_initial("writer")
    assert bot_snapshot() == before and len(pending_operations()) == 1


@pytest.mark.parametrize("status", ["active", "archived"], ids=["stopped", "archived"])
def test_stopped_or_archived_bot_keeps_pending_marker_until_started(status):
    with db.connection() as conn:
        conn.execute("UPDATE bots SET status=%s,desired_state='stopped' WHERE id='writer'", (status,))
    mark_limits_due()
    before = bot_snapshot()
    provisioning.queue_initial("writer")
    assert bot_snapshot() == before and pending_operations() == []


def test_queued_work_waits_for_limits_before_dispatch(make_task):
    task = make_task(bot="writer")
    wanted = mark_limits_due()
    before = bot_snapshot()
    assert worker.claim_new() == []
    provisioning.queue_initial("writer")
    queued = pending_operations()
    assert len(queued) == 1 and queued[0]["payload"]["limits_revision"] == wanted
    assert queued[0]["payload"]["limits"]["pids_limit"] == 1024
    after = bot_snapshot()
    assert after["revision"] == before["revision"] + 1
    assert after["inference_revision"] == after["revision"]
    assert after["inference_key_hash"] != before["inference_key_hash"]
    with db.connection() as conn:
        conn.execute("UPDATE bot_operations SET status='completed' WHERE id=%s", (queued[0]["id"],))
        conn.execute("UPDATE bots SET status='active',applied_revision=revision,readiness=%s WHERE id='writer'",
                     (Jsonb({"compatible": True}),))
    assert worker.claim_new() == []
    with db.connection() as conn:
        conn.execute("UPDATE bots SET applied_limits_revision=%s WHERE id='writer'", (wanted,))
    claimed = worker.claim_new()
    assert len(claimed) == 1 and claimed[0]["task_id"] == task["id"]


def test_old_operation_keeps_limits_absent_and_does_not_ack_new_limits(monkeypatch):
    wanted = mark_limits_due()
    with db.connection() as conn:
        operation = registry.enqueue(conn, registry.require_bot(conn, "writer"), "ensure", str(uuid4()))
        legacy = conn.execute("UPDATE bot_operations SET payload=payload-'limits'-'limits_revision' "
                              "WHERE id=%s RETURNING *", (operation["id"],)).fetchone()
        old_hash = service.digest({"action": "ensure", "payload": legacy["payload"]})
        conn.execute("UPDATE bot_operations SET request_hash=%s WHERE id=%s", (old_hash, operation["id"]))
    calls = []

    async def supervisor(method, path, **kwargs):
        calls.append(kwargs["json"])
        return {"container_id": "legacy-container", "state": "running", "revision": legacy["payload"]["revision"]}

    monkeypatch.setattr(provisioning, "supervisor", supervisor)
    asyncio.run(provisioning.drive(legacy))
    assert len(calls) == 1 and "limits" not in calls[0]
    with db.connection() as conn:
        bot = registry.require_bot(conn, "writer")
        stored = conn.execute("SELECT payload,request_hash FROM bot_operations WHERE id=%s", (operation["id"],)).fetchone()
    assert "limits" not in stored["payload"] and "limits_revision" not in stored["payload"]
    assert stored["request_hash"] == old_hash
    assert bot["applied_limits_revision"] < wanted and bot["limits_observed"] == {}
    provisioning.queue_initial("writer")
    assert len(pending_operations()) == 2


@pytest.mark.parametrize("before_mb,proposed_mb,expected", [(512, 512, "completed"), (2048, 4096, "failed")])
def test_dispatch_grandfathers_the_preapproval_limit_only(owner_client, monkeypatch, before_mb, proposed_mb, expected):
    with db.connection() as conn:
        bot = conn.execute("UPDATE bots SET memory_mb=%s WHERE id='writer' RETURNING *", (before_mb,)).fetchone()
        grant = registry.create_grant(conn, GrantRequest(
            manager_id="reviewer", targets=["writer"], operations=["configure"], max_memory_mb=4096))
        conversation = conn.execute("SELECT id FROM conversations WHERE bot_id='reviewer'").fetchone()["id"]
        task = service.create_task(conn, bot_id="reviewer", conversation_id=conversation,
                                   prompt="Review resource configuration", request_key=str(uuid4()), grant_id=grant["id"])
        proposed = {**registry.configuration(bot), "memory_mb": proposed_mb}
        proposal = lifecycle.propose(conn, task, {"action": "configure", "bot_id": "writer",
                                                 "configuration": proposed, "summary": "Adjust resource settings"})
        approval = conn.execute("SELECT * FROM approvals WHERE id=%s", (UUID(proposal["approval_id"]),)).fetchone()
    response = owner_client.post("/api/v1/approvals/" + proposal["approval_id"], json={
        "decision": "once", "expected_payload_hash": approval["payload_hash"]})
    assert response.status_code == 200
    with db.connection() as conn:
        assert registry.require_bot(conn, "writer")["memory_mb"] == proposed_mb
        operation = conn.execute("SELECT * FROM bot_operations WHERE proposal_id=%s",
                                 (UUID(proposal["proposal_id"]),)).fetchone()
        conn.execute("UPDATE workspace_settings SET machine_facts=%s", (Jsonb({**MAC, "memory_bytes": 4 * limits.GIB}),))
    calls = []

    async def supervisor(method, path, **kwargs):
        calls.append(kwargs["json"])
        return {"container_id": "configured-runtime", "state": "running", "revision": operation["payload"]["revision"]}

    monkeypatch.setattr(provisioning, "supervisor", supervisor)
    asyncio.run(provisioning.drive(operation))
    with db.connection() as conn:
        result = conn.execute("SELECT status FROM bot_operations WHERE id=%s", (operation["id"],)).fetchone()
    assert result["status"] == expected
    assert len(calls) == (1 if expected == "completed" else 0)


@pytest.mark.parametrize("action", ["stop", "archive"])
def test_stop_and_archive_preserve_the_observed_limits_until_recreation(monkeypatch, action):
    observed = {"PidsLimit": 512, "MemorySwap": 2048 * limits.MIB}
    with db.connection() as conn:
        bot = conn.execute("UPDATE bots SET limits_observed=%s WHERE id='writer' RETURNING *", (Jsonb(observed),)).fetchone()
    wanted = mark_limits_due()
    with db.connection() as conn:
        queued = registry.owner_operation(conn, "writer", action, str(uuid4()), LifecycleRequest(expected_revision=bot["revision"]))
        operation = conn.execute("SELECT * FROM bot_operations WHERE id=%s", (queued["id"],)).fetchone()
    assert operation["payload"]["limits_revision"] == wanted

    async def supervisor(method, path, **kwargs):
        assert path == f"/operations/{action}"
        return {"state": "stopped", "revision": operation["payload"]["revision"]}

    monkeypatch.setattr(provisioning, "supervisor", supervisor)
    asyncio.run(provisioning.drive(operation))
    with db.connection() as conn:
        stopped = registry.require_bot(conn, "writer")
    assert stopped["applied_limits_revision"] == bot["applied_limits_revision"] < wanted
    assert stopped["limits_observed"] == observed and stopped["desired_state"] == "stopped"
    provisioning.queue_initial("writer")
    assert len(pending_operations()) == 1


@pytest.mark.parametrize("operation", ["memory", "skill_enabled"])
def test_legacy_resources_do_not_block_native_knowledge_edits(owner_client, monkeypatch, operation):
    with db.connection() as conn:
        bot = conn.execute("UPDATE bots SET memory_mb=512 WHERE id='writer' RETURNING *").fetchone()
    calls = []

    def request(bot_id, method, path, **kwargs):
        calls.append((bot_id, method, path))
        return {"exists": True, "sha256": "0" * 64}

    monkeypatch.setattr(native, "request", request)
    if operation == "memory":
        response = owner_client.put("/api/v1/bots/writer/knowledge/memory/memory", json={
            "content": "Keep this reviewed fact", "expected_hash": "0" * 64,
        }, headers={"Idempotency-Key": str(uuid4())})
    else:
        response = post(owner_client, "/api/v1/bots/writer/knowledge/skills/hermbot-test/enabled", {
            "enabled": True, "expected_revision": bot["revision"],
        })
    assert response.status_code == 202 and calls
    assert pending_operations()[0]["payload"]["configuration"]["memory_mb"] == 512
