import asyncio

import httpx
import psycopg
import pytest

from backend import db, isolation, provisioning, registry, worker
from tests.conftest import row

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fresh_burst(monkeypatch):
    monkeypatch.setattr(worker, "BURST", isolation.Burst())


def failing_drive(monkeypatch, exc, calls=None):
    async def drive(attempt):
        if calls is not None:
            calls.append(attempt["id"])
        raise exc
    monkeypatch.setattr(worker, "drive", drive)


def attempt_row(attempt_id):
    with db.connection() as conn:
        return conn.execute("SELECT * FROM attempts WHERE id=%s", (attempt_id,)).fetchone()


def fail_repeatedly(attempt, times=isolation.FAILURE_LIMIT):
    for _ in range(times):
        asyncio.run(worker.guarded_drive(attempt_row(attempt["id"])))


def expire_lease(attempt_id, worker_id, in_flight):
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET worker_id=%s,lease_until=now()-interval '1 second',drive_started_at=CASE WHEN %s THEN now() END WHERE id=%s",
                     (worker_id, in_flight, attempt_id))


def test_failure_is_recorded_and_backs_off(make_task, monkeypatch):
    make_task()
    attempt = worker.claim_new()[0]
    failing_drive(monkeypatch, RuntimeError("private detail"))
    asyncio.run(worker.guarded_drive(attempt))
    current = attempt_row(attempt["id"])
    assert current["drive_failures"] == 1 and current["status"] == "dispatching"
    assert current["last_drive_error"].startswith("RuntimeError") and current["drive_not_before"] is not None
    assert worker.claim_existing() == []


def test_repeated_failure_before_start_fails_without_runtime_contact(make_task, monkeypatch):
    task = make_task()
    attempt = worker.claim_new()[0]
    failing_drive(monkeypatch, TypeError("bad data"))
    fail_repeatedly(attempt)
    assert attempt_row(attempt["id"])["status"] == "failed"
    current = row(task["id"])
    assert current["status"] == "failed" and "could not start this execution" in current["error"]
    assert "TypeError" in current["error"] and "bad data" not in current["error"]
    with db.connection() as conn:
        event = conn.execute("SELECT data FROM events WHERE kind='attempt.quarantined' AND task_id=%s", (task["id"],)).fetchone()
    assert event["data"]["started"] is False


@pytest.mark.parametrize("started", ["run_id", "start_marker", "scheduled"])
def test_repeated_failure_after_possible_start_is_never_replayed(make_task, monkeypatch, started):
    task = make_task()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        if started == "run_id":
            conn.execute("UPDATE attempts SET status='running',runtime_run_id='test-run' WHERE id=%s", (attempt["id"],))
        else:
            key = "start_requested_at" if started == "start_marker" else "scheduled"
            conn.execute("UPDATE attempts SET response=response||jsonb_build_object(%s::text,true) WHERE id=%s", (key, attempt["id"]))
    failing_drive(monkeypatch, KeyError("missing"))
    fail_repeatedly(attempt)
    assert attempt_row(attempt["id"])["status"] == "uncertain"
    current = row(task["id"])
    assert current["status"] == "uncertain" and "not replayed automatically" in current["error"]


def test_outages_back_off_without_counting(make_task, monkeypatch):
    make_task()
    attempt = worker.claim_new()[0]
    failing_drive(monkeypatch, httpx.ConnectError("storage down"))
    fail_repeatedly(attempt, times=5)
    current = attempt_row(attempt["id"])
    assert current["drive_failures"] == 0 and current["status"] == "dispatching" and current["drive_not_before"] is not None


def test_failure_shared_by_several_attempts_is_not_charged_to_each(make_task, monkeypatch):
    for bot in ("researcher", "writer", "reviewer"):
        make_task(bot=bot)
    attempts = worker.claim_new()
    assert len(attempts) == 3
    failing_drive(monkeypatch, ValueError("shared cause"))
    for _ in range(isolation.FAILURE_LIMIT + 1):
        for attempt in attempts:
            asyncio.run(worker.guarded_drive(attempt_row(attempt["id"])))
    assert all(attempt_row(attempt["id"])["status"] == "dispatching" for attempt in attempts)


def test_database_loss_and_cancellation_still_stop_the_worker(make_task, monkeypatch):
    make_task()
    attempt = worker.claim_new()[0]
    failing_drive(monkeypatch, psycopg.OperationalError("connection lost"))
    with pytest.raises(psycopg.OperationalError):
        asyncio.run(worker.guarded_drive(attempt))
    failing_drive(monkeypatch, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.guarded_drive(attempt))
    assert attempt_row(attempt["id"])["drive_failures"] == 0


def test_one_failing_attempt_does_not_stop_the_others(make_task, monkeypatch):
    first, second = make_task(bot="researcher"), make_task(bot="writer")
    driven = []

    async def drive(attempt):
        driven.append(attempt["task_id"])
        if attempt["task_id"] == first["id"]:
            raise RuntimeError("only this attempt is broken")
    monkeypatch.setattr(worker, "drive", drive)
    asyncio.run(worker.iterate(1))
    assert set(driven) == {first["id"], second["id"]}
    assert row(second["id"])["status"] == "running"


def test_failing_loop_step_does_not_stop_dispatch(make_task, monkeypatch):
    task = make_task()
    driven = []

    async def drive(attempt):
        driven.append(attempt["task_id"])

    def broken():
        raise RuntimeError("step failure")
    monkeypatch.setattr(worker, "drive", drive)
    monkeypatch.setattr(worker, "advance_waiters", broken)
    asyncio.run(worker.iterate(1))
    assert driven == [task["id"]]


def test_task_that_cannot_start_fails_alone(make_task, monkeypatch):
    broken, healthy = make_task(bot="researcher"), make_task(bot="writer")
    allowed = worker.routines.start_allowed

    def start_allowed(conn, task):
        if task["id"] == broken["id"]:
            raise TypeError("unexpected shape")
        return allowed(conn, task)
    monkeypatch.setattr(worker.routines, "start_allowed", start_allowed)
    claimed = worker.claim_new()
    assert [attempt["task_id"] for attempt in claimed] == [healthy["id"]]
    current = row(broken["id"])
    assert current["status"] == "failed" and "could not start this task" in current["error"]
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM attempts WHERE task_id=%s", (broken["id"],)).fetchone()["n"] == 0


def test_process_crashes_are_charged_only_to_the_attempt_in_flight(make_task, monkeypatch):
    make_task(bot="researcher")
    make_task(bot="writer")
    fatal, innocent = sorted(worker.claim_new(), key=lambda attempt: attempt["bot_id"])
    calls = []
    failing_drive(monkeypatch, AssertionError("must not drive a quarantined attempt"), calls)
    for crash in range(isolation.FAILURE_LIMIT):
        expire_lease(fatal["id"], f"crashed-{crash}", in_flight=True)
        expire_lease(innocent["id"], f"crashed-{crash}", in_flight=False)
        claimed = {attempt["id"]: attempt for attempt in worker.claim_existing()}
    assert claimed[fatal["id"]]["lease_takeovers"] == isolation.FAILURE_LIMIT
    assert claimed[innocent["id"]]["lease_takeovers"] == 1
    asyncio.run(worker.guarded_drive(claimed[fatal["id"]], suspect=True))
    assert calls == [] and attempt_row(fatal["id"])["status"] == "failed"
    assert attempt_row(innocent["id"])["status"] == "dispatching"


def test_clean_pass_clears_suspicion(make_task, monkeypatch):
    make_task()
    attempt = worker.claim_new()[0]
    expire_lease(attempt["id"], "restarted", in_flight=False)
    suspect = worker.claim_existing()[0]
    assert suspect["lease_takeovers"] == 1

    async def drive(attempt):
        assert attempt_row(attempt["id"])["drive_started_at"] is not None
    monkeypatch.setattr(worker, "drive", drive)
    asyncio.run(worker.guarded_drive(suspect, suspect=True))
    current = attempt_row(attempt["id"])
    assert current["lease_takeovers"] == 0 and current["drive_started_at"] is None


def test_lifecycle_operation_failures_are_contained(monkeypatch):
    with db.connection() as conn:
        bot = conn.execute("SELECT * FROM bots WHERE id='researcher'").fetchone()
        registry.enqueue(conn, bot, "ensure", "isolation-test")

    async def drive(operation):
        raise ValueError("unexpected payload")
    monkeypatch.setattr(provisioning, "drive", drive)
    for _ in range(isolation.FAILURE_LIMIT):
        with db.connection() as conn:
            conn.execute("UPDATE bot_operations SET drive_not_before=NULL WHERE bot_id='researcher'")
        operations = provisioning.claim()
        assert len(operations) == 1
        asyncio.run(provisioning.guarded_drive(operations[0]))
    with db.connection() as conn:
        operation = conn.execute("SELECT * FROM bot_operations WHERE bot_id='researcher'").fetchone()
        bot = conn.execute("SELECT status,runtime_error FROM bots WHERE id='researcher'").fetchone()
    assert operation["status"] == "failed" and "ValueError" in operation["error"]
    assert bot["status"] == "error" and "Check the runtime before retrying" in bot["runtime_error"]
