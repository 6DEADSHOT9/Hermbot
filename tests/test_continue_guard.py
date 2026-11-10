"""Messages queued for a task's next run are only ever added, and work that has run before is resumed, not restarted."""
from pathlib import Path
import re

import pytest

from backend import db, execution_policy, knowledge, service, worker
from tests.test_phase3_native import decision, owner_client, review  # noqa: F401  (owner_client is a fixture)

pytestmark = pytest.mark.integration
GUARD = "never repeat a fulfilled or uncertain external effect"
EARLIER = "Owner answer: the client email already went out at 9:00; do not send it again."


def tree(make_task, dependency_status="completed"):
    """A root, a dependency and a dependent child, all paused by their budget; the child carries an earlier answer."""
    root = make_task(prompt="Prepare and send the weekly client update")
    dependency = make_task(prompt="Collect the weekly numbers")
    child = make_task(prompt="Email the weekly update to the client")
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET root_id=%s,parent_id=%s,depth=1 WHERE id IN (%s,%s)", (root["id"], root["id"], dependency["id"], child["id"]))
        conn.execute("UPDATE tasks SET status=%s,output='Numbers collected' WHERE id=%s", (dependency_status, dependency["id"]))
        conn.execute("INSERT INTO dependencies VALUES (%s,%s)", (child["id"], dependency["id"]))
        conn.execute("UPDATE tasks SET status='paused_budget',stop_kind='budget',attempts=1 WHERE id IN (%s,%s)", (root["id"], child["id"]))
        conn.execute("UPDATE tasks SET continuation=%s WHERE id=%s", (EARLIER, child["id"]))
        execution_policy.continue_work(conn, service.locked_task(conn, root["id"]))
    return root, dependency, child


def state(task_id):
    with db.connection() as conn:
        return conn.execute("SELECT status,continuation FROM tasks WHERE id=%s", (task_id,)).fetchone()


def dispatched_input(task_id):
    attempts = [attempt for attempt in worker.claim_new() if attempt["task_id"] == task_id]
    assert attempts, "The task was not dispatched"
    return attempts[0]["input"]


def test_continue_keeps_the_guard_and_earlier_answers_through_to_the_bot(make_task):
    _, _, child = tree(make_task)
    assert state(child["id"])["status"] == "queued", "A long-finished dependency no longer blocks"
    worker.advance_waiters()
    sent = dispatched_input(child["id"])
    assert GUARD in sent and EARLIER in sent
    assert sent.startswith(service.RESUME_RULE), "Work that has run before is resumed, not restarted"


def test_a_dependency_release_after_continue_adds_its_results_instead_of_replacing(make_task):
    _, dependency, child = tree(make_task, dependency_status="running")
    assert state(child["id"])["status"] == "blocked"
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (dependency["id"],))
    worker.advance_waiters()
    released = state(child["id"])
    assert released["status"] == "queued"
    assert GUARD in released["continuation"] and EARLIER in released["continuation"] and "Dependency results:" in released["continuation"]
    sent = dispatched_input(child["id"])
    assert GUARD in sent and EARLIER in sent and "Numbers collected" in sent


def test_a_task_released_by_dependencies_for_the_first_time_still_gets_its_request(make_task):
    dependency = make_task(prompt="Collect the weekly numbers")
    child = make_task(prompt="Email the weekly update to the client")
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed',output='Numbers collected' WHERE id=%s", (dependency["id"],))
        conn.execute("INSERT INTO dependencies VALUES (%s,%s)", (child["id"], dependency["id"]))
        conn.execute("UPDATE tasks SET status='blocked' WHERE id=%s", (child["id"],))
    worker.advance_waiters()
    sent = dispatched_input(child["id"])
    assert sent.startswith("Email the weekly update to the client") and "Numbers collected" in sent
    assert service.RESUME_RULE not in sent, "A first run is not told it has run before"


def pending(make_task, status):
    task = make_task(prompt="Send the invoice")
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status=%s,continuation=%s,attempts=1 WHERE id=%s", (status, EARLIER, task["id"]))
    return task


def test_an_answered_question_adds_to_what_is_queued(make_task):
    task = pending(make_task, "waiting_input")
    worker.advance_waiters()
    current = state(task["id"])
    assert current["status"] == "queued" and EARLIER in current["continuation"] and "resolved or removed the pending question" in current["continuation"]


def test_a_handback_adds_to_what_is_queued(make_task):
    task = pending(make_task, "waiting_computer")
    worker.advance_waiters()
    current = state(task["id"])
    assert current["status"] == "queued" and EARLIER in current["continuation"] and "handed back the computer" in current["continuation"]


def test_a_knowledge_decision_adds_to_what_is_queued(make_task):
    task = pending(make_task, "waiting_approval")
    with db.connection() as conn:
        knowledge.resume(conn, service.locked_task(conn, task["id"]), "Owner declined this knowledge change.")
    current = state(task["id"])
    assert EARLIER in current["continuation"] and "Owner declined this knowledge change." in current["continuation"]


def test_an_approval_decision_adds_to_what_is_queued(owner_client, make_task):  # noqa: F811
    task = make_task(prompt="Send the invoice")
    item = review(task, "review", {"draft": "invoice"})
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET continuation=%s WHERE id=%s", (EARLIER, task["id"]))
    assert decision(owner_client, item).status_code == 200
    current = state(task["id"])
    assert EARLIER in current["continuation"] and "Owner review decision: once" in current["continuation"]


def test_editing_work_that_has_not_started_replaces_its_request(owner_client, make_task):  # noqa: F811
    task = make_task(prompt="Send the invoice")
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET continuation=%s WHERE id=%s", (EARLIER, task["id"]))
    response = owner_client.put(f"/api/v1/tasks/{task['id']}", json={"prompt": "Send the corrected invoice", "expected_input_hash": service.digest("Send the invoice")})
    assert response.status_code == 200, response.text
    with db.connection() as conn:
        current = conn.execute("SELECT input,continuation FROM tasks WHERE id=%s", (task["id"],)).fetchone()
    assert current["input"] == "Send the corrected invoice" and current["continuation"] is None, "The owner rewrote unstarted work"


def test_no_writer_replaces_a_queued_continuation():
    """Only two places may set it outright: editing work that hasn't started, and dispatch clearing it."""
    allowed = {("backend/product.py", "continuation=NULL"), ("backend/worker.py", "continuation=NULL")}
    found = set()
    for path in Path("backend").glob("*.py"):
        for match in re.finditer(r"continuation=(?!concat_ws|COALESCE\(NULLIF\(continuation)(%s|NULL|input|COALESCE\(continuation)", path.read_text()):
            found.add((str(path), match.group(0)))
    assert found <= allowed, found - allowed
