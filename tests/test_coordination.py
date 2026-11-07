from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest

from backend import db, service, worker
from tests.conftest import row

pytestmark = pytest.mark.integration


def test_concurrent_owner_submission_has_one_receipt(make_task):
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: make_task(key=key), range(6)))
    assert len({r["id"] for r in results}) == 1
    with pytest.raises(HTTPException) as exc:
        make_task(key=key, prompt="different work")
    assert exc.value.status_code == 409
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM messages").fetchone()["n"] == 1


def test_tool_delivery_deduplication_and_trusted_session(make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    args = {"bot_id": "writer", "prompt": "Synthesize"}
    with db.connection() as conn:
        first = service.tool_action(conn, "researcher", attempt["session_id"], "delegate", args, "call_1")
        again = service.tool_action(conn, "researcher", attempt["session_id"], "delegate", args, "call_1")
        assert first == again
    assert row(task["id"])["handoffs"] == 1
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.tool_action(conn, "reviewer", attempt["session_id"], "finish", {"result": "Forged"}, "call_2")
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.tool_action(conn, "researcher", attempt["session_id"], "delegate", {**args, "prompt": "changed"}, "call_1")


def test_one_slot_and_parent_yields_before_continuation(make_task):
    parent = make_task()
    make_task(key="second-researcher")
    attempt = worker.claim_new()[0]
    assert worker.claim_new() == []
    with db.connection() as conn:
        child = service.delegate(conn, service.locked_task(conn, parent["id"]), "writer", "Write")
    worker.settle(attempt["id"], {"status": "completed", "output": "Waiting"})
    assert row(parent["id"])["status"] == "waiting_children"
    claims = worker.claim_new()
    assert len({a["bot_id"] for a in claims}) == 2
    child_attempt = next(a for a in claims if a["bot_id"] == "writer")
    worker.settle(child_attempt["id"], {"status": "completed", "output": "Written"})
    worker.advance_waiters()
    assert row(parent["id"])["status"] == "queued"
    assert child["task_id"] in row(parent["id"])["continuation"]


def test_cycle_depth_budget_and_unrelated_tasks_are_rejected(make_task):
    parent = make_task()
    unrelated = make_task("reviewer")
    with db.connection() as conn:
        child = service.delegate(conn, service.locked_task(conn, parent["id"]), "writer", "Write")
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.delegate(conn, service.locked_task(conn, UUID(child["task_id"])), "researcher", "Circular")
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.inspect_task(conn, parent, unrelated["id"])
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET handoffs=10 WHERE id=%s", (parent["id"],))
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.delegate(conn, service.locked_task(conn, parent["id"]), "reviewer", "Too many")


def test_failure_wakes_once_and_reassignment_rewires_dependencies(make_task):
    parent = make_task("reviewer", manager=True)
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        a = service.delegate(conn, service.locked_task(conn, parent["id"]), "researcher", "Evidence")
        b = service.delegate(conn, service.locked_task(conn, parent["id"]), "writer", "Synthesis", [UUID(a["task_id"])])
    worker.settle(attempt["id"], {"status": "completed", "output": "Waiting"})
    child = worker.claim_new()[0]
    worker.settle(child["id"], {"status": "failed", "error": "Injected failure"})
    worker.advance_waiters()
    assert row(parent["id"])["status"] == "queued"
    manager_attempt = worker.claim_new()[0]
    worker.settle(manager_attempt["id"], {"status": "completed", "output": "Consider repair"})
    worker.advance_waiters()
    assert row(parent["id"])["status"] == "waiting_children"
    with db.connection() as conn:
        replacement = service.reassign(conn, service.locked_task(conn, parent["id"]), UUID(a["task_id"]), "researcher")
        dependency = conn.execute("SELECT depends_on FROM dependencies WHERE task_id=%s", (UUID(b["task_id"]),)).fetchone()
        assert dependency["depends_on"] == UUID(replacement["task_id"])


def test_grant_revocation_stops_owned_tree_not_other_work(make_task):
    parent = make_task("reviewer", manager=True)
    unrelated = make_task("researcher")
    with db.connection() as conn:
        child = service.delegate(conn, service.locked_task(conn, parent["id"]), "writer", "Write")
        conn.execute("UPDATE grants SET revoked_at=now() WHERE id=%s", (parent["grant_id"],))
    worker.advance_waiters()
    assert row(parent["id"])["status"] == "cancelled"
    assert row(UUID(child["task_id"]))["status"] == "cancelled"
    assert row(unrelated["id"])["status"] == "queued"


def test_uncertain_execution_blocks_reassignment_and_runtime_slot(make_task):
    parent = make_task("reviewer", manager=True)
    with db.connection() as conn:
        child = service.delegate(conn, service.locked_task(conn, parent["id"]), "researcher", "Investigate")
    attempt = next(a for a in worker.claim_new() if a["bot_id"] == "researcher")
    worker.mark_uncertain(attempt, "Missing receipt")
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.reassign(conn, service.locked_task(conn, parent["id"]), UUID(child["task_id"]), "writer")
    make_task("researcher")
    assert all(a["bot_id"] != "researcher" for a in worker.claim_new())


def test_stopped_task_cannot_use_tools_or_resolve_review(make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        approval = service.tool_action(conn, "researcher", attempt["session_id"], "review", {"summary": "Confirm result", "payload": {"file": "report.md"}}, "review1")
        service.cancel(conn, service.locked_task(conn, task["id"]))
        assert conn.execute("SELECT status FROM approvals WHERE id=%s", (UUID(approval["approval_id"]),)).fetchone()["status"] == "cancelled"
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.tool_action(conn, "researcher", attempt["session_id"], "finish", {"result": "late"}, "late")
    worker.settle(attempt["id"], {"status": "completed", "output": "late"})
    assert row(task["id"])["status"] == "cancelled"


def test_partial_management_grant_excludes_other_registered_peer(make_task):
    parent = make_task("reviewer")
    with db.connection() as conn:
        conversation = parent["conversation_id"]
        restricted = service.create_task(conn, bot_id="reviewer", conversation_id=conversation, prompt="Manage Scout only", request_key="restricted-manager", managed_bot_ids=["researcher"])
        service.delegate(conn, service.locked_task(conn, restricted["id"]), "researcher", "Allowed")
    with pytest.raises(HTTPException) as exc:
        with db.connection() as conn:
            service.delegate(conn, service.locked_task(conn, restricted["id"]), "writer", "Outside grant")
    assert exc.value.status_code == 403


def test_depth_limit_is_enforced_before_creating_another_assignment(make_task):
    parent = make_task()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET depth=3 WHERE id=%s", (parent["id"],))
    with pytest.raises(HTTPException) as exc:
        with db.connection() as conn:
            service.delegate(conn, service.locked_task(conn, parent["id"]), "writer", "Too deep")
    assert exc.value.status_code == 409
