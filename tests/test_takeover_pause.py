"""A takeover pauses the bot's container, so nothing it runs makes progress while the owner is in control."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from backend import app as application, computer, containers, db, execution_policy, native, provisioning, routines, service, worker
from backend.runtime import HermesClient, RuntimePaused
from backend.settings import settings

pytestmark = pytest.mark.integration
CONTROL = "/api/v1/bots/researcher/computer/control"
STATUS = "/api/v1/bots/researcher/computer"


@pytest.fixture
def owner_client():
    client = TestClient(application.app, headers={"Authorization": "Bearer " + settings.owner_token})
    yield client
    client.close()
    with db.connection() as conn:  # The workspace row outlives each test.
        conn.execute("UPDATE workspace_settings SET takeover_unpausable='ask',takeover_reply_wait_seconds=0")


def identity():
    return computer.viewer("Bearer " + settings.owner_token)


@pytest.fixture
def adapter(monkeypatch):
    """The bot's adapter. It is never reachable while the bot is paused, unless the caller is the takeover itself."""
    calls, lease = [], {"holder": "agent"}

    def request(bot_id, method, path, *, allow_paused=False, **kwargs):
        with db.connection() as conn:
            if native.paused(conn, bot_id) and not allow_paused:
                raise HTTPException(409, native.PAUSED)  # As the real guard does, before any call.
        calls.append((method, path, kwargs.get("json")))
        if path == "/computer/browser-state":
            return {"cookies": [{"name": "sid", "value": "bot", "domain": ".example.com", "path": "/"}], "urls": ["https://example.com/"]}
        if path in ("/computer", "/computer/control"):
            if path == "/computer/control":
                lease["holder"] = "agent" if kwargs["json"]["action"] == "handback" else "human"
            holder = lease["holder"]
            return {"desktop": {"running": True, "geometry": "1280x800"}, "input_ready": True,
                    "lease": {"holder": holder, "epoch": 1, "viewer_hash": hashlib.sha256(identity().encode()).hexdigest()[:12]}}
        return {"ok": True}
    monkeypatch.setattr(native, "request", request)
    return calls


def setting(**values):
    with db.connection() as conn:
        for name, value in values.items():
            conn.execute(f"UPDATE workspace_settings SET {name}=%s", (value,))


def control(client, action, **body):
    return client.post(CONTROL, json={"action": action, **body})


def row():
    with db.connection() as conn:
        return conn.execute("SELECT * FROM computer_controls WHERE bot_id='researcher'").fetchone()


def running_attempt(make_task):
    make_task()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET status='running',runtime_run_id='run-1',started_at=now(),accounted_at=now()-interval '10 seconds' WHERE id=%s",
                     (attempt["id"],))
    return attempt


def end_turn(attempt):
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET status='completed' WHERE id=%s", (attempt["id"],))


def until(check, timeout=8):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "Timed out"
        time.sleep(0.05)


def phase(client):
    return (client.get(STATUS).json().get("takeover") or {}).get("phase")


def started_sessions(owner_sessions):
    return [call for call in owner_sessions.calls if call[1:3] == ("POST", "/owner-sessions/researcher")]


def test_takeover_pauses_the_bot_after_reading_its_browser_and_before_opening_the_private_one(owner_client, adapter, owner_sessions):
    response = control(owner_client, "takeover")
    assert response.status_code == 200
    body = response.json()
    assert body["mine"] and body["private_session"] and body["takeover"] == {"phase": "paused"}
    order = [call[2] for call in owner_sessions.calls if call[0] == "supervisor"]
    assert order == ["/runtimes/researcher/pause", "/owner-sessions/researcher"]
    assert [path for method, path, body in adapter] == ["/computer/control", "/computer", "/computer/browser-state"], \
        "The bot's cookies are read while it can still answer"
    assert owner_sessions.paused == {"researcher"}
    current = row()
    assert current["paused_at"] and current["paused_state"]["desktop"]["geometry"] == "1280x800" and not current["unpaused"]


def test_a_reply_in_progress_finishes_first_and_status_stays_available_meanwhile(owner_client, adapter, owner_sessions, make_task):
    setting(takeover_reply_wait_seconds=15)
    attempt = running_attempt(make_task)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(control, owner_client, "takeover")
        until(lambda: phase(owner_client) == "finishing")  # The per-bot lock is free during the wait.
        status = owner_client.get(STATUS).json()
        assert status["mine"] and 0 < status["takeover"]["seconds_left"] <= 15 and not status["private_session"]
        assert not owner_sessions.paused, "Nothing is paused while the reply finishes"
        end_turn(attempt)
        response = pending.result(timeout=10)
    assert response.status_code == 200 and response.json()["takeover"] == {"phase": "paused"}
    assert owner_sessions.paused == {"researcher"} and row()["finishing_until"] is None


def test_take_over_now_ends_the_wait(owner_client, adapter, owner_sessions, make_task):
    setting(takeover_reply_wait_seconds=60)
    attempt = running_attempt(make_task)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(control, owner_client, "takeover")
        until(lambda: phase(owner_client) == "finishing")
        assert control(owner_client, "takeover_now").status_code == 200
        response = pending.result(timeout=10)
    assert response.status_code == 200 and response.json()["private_session"]
    assert owner_sessions.paused == {"researcher"}
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM attempts WHERE id=%s", (attempt["id"],)).fetchone()["status"] == "running", \
            "The reply was cut short by the owner's choice"


def test_the_wait_ends_at_its_limit(owner_client, adapter, owner_sessions, make_task):
    setting(takeover_reply_wait_seconds=15)
    running_attempt(make_task)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(control, owner_client, "takeover")
        until(lambda: phase(owner_client) == "finishing")
        with db.connection() as conn:
            conn.execute("UPDATE computer_controls SET finishing_until=now() WHERE bot_id='researcher'")
        assert pending.result(timeout=10).status_code == 200
    assert owner_sessions.paused == {"researcher"}


def test_without_a_wait_setting_a_reply_in_progress_is_paused_at_once(owner_client, adapter, owner_sessions, make_task):
    running_attempt(make_task)
    assert control(owner_client, "takeover").json()["takeover"] == {"phase": "paused"}
    assert owner_sessions.paused == {"researcher"}


def test_a_handback_during_the_wait_cancels_the_pause(owner_client, adapter, owner_sessions, make_task):
    setting(takeover_reply_wait_seconds=30)
    running_attempt(make_task)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(control, owner_client, "takeover")
        until(lambda: phase(owner_client) == "finishing")
        assert control(owner_client, "handback").status_code == 200
        response = pending.result(timeout=10)
    assert response.status_code == 409 and "Control changed" in response.json()["detail"]
    assert not owner_sessions.paused and not started_sessions(owner_sessions)
    assert row()["holder"] == "agent" and row()["finishing_until"] is None


def test_ask_mode_keeps_control_and_offers_the_choices(owner_client, adapter, owner_sessions):
    owner_sessions.pause_fails = True
    response = control(owner_client, "takeover")
    assert response.status_code == 200
    body = response.json()
    assert body["mine"] and body["takeover"] == {"phase": "pause_failed"} and not body["private_session"]
    assert not started_sessions(owner_sessions) and row()["holder"] == "human" and row()["paused_at"] is None
    owner_sessions.pause_fails = False
    retried = control(owner_client, "takeover").json()  # Try again
    assert retried["takeover"] == {"phase": "paused"} and retried["private_session"] and not row()["pause_failed"]


def test_ask_mode_can_take_over_without_pausing(owner_client, adapter, owner_sessions):
    assert control(owner_client, "takeover_unpaused").status_code == 409, "Offered only after a pause fails"
    owner_sessions.pause_fails = True
    control(owner_client, "takeover")
    body = control(owner_client, "takeover_unpaused").json()
    assert body["takeover"] == {"phase": "unpaused"} and body["private_session"] and not owner_sessions.paused
    assert row()["unpaused"] and not row()["pause_failed"]


def test_ask_mode_cancel_hands_back(owner_client, adapter, owner_sessions):
    owner_sessions.pause_fails = True
    control(owner_client, "takeover")
    body = control(owner_client, "handback").json()
    assert body["lease"]["holder"] == "agent" and body["takeover"] is None
    assert row()["holder"] == "agent" and not row()["pause_failed"]


def test_refuse_mode_gives_control_back(owner_client, adapter, owner_sessions):
    setting(takeover_unpausable="refuse")
    owner_sessions.pause_fails = True
    response = control(owner_client, "takeover")
    with db.connection() as conn:
        name = conn.execute("SELECT name FROM bots WHERE id='researcher'").fetchone()["name"]
    assert response.status_code == 502 and response.json()["detail"] == f"Couldn't pause {name}. Try again."
    assert row()["holder"] == "agent" and not started_sessions(owner_sessions)
    assert ("POST", "/computer/control", {"action": "handback", "force": True, "viewer_id": identity()}) in adapter


def test_continue_mode_takes_over_without_pausing(owner_client, adapter, owner_sessions):
    setting(takeover_unpausable="continue")
    owner_sessions.pause_fails = True
    body = control(owner_client, "takeover").json()
    assert body["takeover"] == {"phase": "unpaused"} and body["private_session"] and not owner_sessions.paused


def test_handback_resumes_the_bot_before_carrying_cookies_and_returning_control(owner_client, adapter, owner_sessions):
    control(owner_client, "takeover")
    owner_sessions.calls.clear()
    adapter.clear()
    body = control(owner_client, "handback").json()
    assert body["lease"]["holder"] == "agent" and not owner_sessions.paused
    assert [call[2] for call in owner_sessions.calls] == ["/runtimes/researcher/resume", "/finish", "/owner-sessions/researcher"]
    assert [path for method, path, body in adapter][:3] == ["/health", "/computer/browser-cookies", "/computer/control"]
    current = row()
    assert current["holder"] == "agent" and current["paused_at"] is None and current["paused_state"] is None and current["session_url"] is None


def test_a_forced_handback_always_resumes(owner_client, adapter, owner_sessions):
    control(owner_client, "takeover")
    with db.connection() as conn:
        conn.execute("UPDATE computer_controls SET viewer_hash='another-session' WHERE bot_id='researcher'")
    refused = control(owner_client, "handback")
    assert refused.status_code == 409 and owner_sessions.paused == {"researcher"}
    assert control(owner_client, "handback", force=True).status_code == 200
    assert not owner_sessions.paused and row()["holder"] == "agent"
    assert ("POST", "/computer/control", {"action": "handback", "force": True, "viewer_id": identity()}) in adapter
    owner_sessions.paused.add("researcher")  # Paused although nothing records it.
    assert control(owner_client, "handback", force=True).status_code == 200 and not owner_sessions.paused


def test_a_handback_that_cannot_resume_keeps_the_owner_in_control(owner_client, adapter, owner_sessions, monkeypatch):
    control(owner_client, "takeover")
    original = computer.supervisor

    def failing(method, path, **kwargs):
        if path.endswith("/resume"):
            raise httpx.ConnectError("The supervisor is unavailable")
        return original(method, path, **kwargs)
    monkeypatch.setattr(computer, "supervisor", failing)
    response = control(owner_client, "handback")
    assert response.status_code == 502 and "still have control" in response.json()["detail"]
    assert row()["holder"] == "human" and row()["paused_at"] and row()["session_url"], "Nothing was lost; hand back again"


def test_another_session_of_the_owner_reclaims_a_paused_takeover_without_calling_the_bot(owner_client, adapter, owner_sessions):
    control(owner_client, "takeover")
    with db.connection() as conn:
        conn.execute("UPDATE computer_controls SET viewer_hash='another-session' WHERE bot_id='researcher'")
    assert not owner_client.get(STATUS).json()["mine"]
    adapter.clear()
    body = control(owner_client, "takeover").json()
    assert body["mine"] and body["private_session"] and adapter == []
    assert control(owner_client, "handback").status_code == 200 and not owner_sessions.paused


def test_an_interrupted_takeover_starts_its_pause_over(owner_client, adapter, owner_sessions):
    control(owner_client, "takeover")
    with db.connection() as conn:  # The API stopped after pausing, before the private browser opened.
        conn.execute("UPDATE computer_controls SET session_url=NULL WHERE bot_id='researcher'")
    owner_sessions.calls.clear()
    body = control(owner_client, "takeover").json()
    assert body["private_session"] and owner_sessions.paused == {"researcher"}
    assert [call[2] for call in owner_sessions.calls if call[0] == "supervisor"][:3] == [
        "/runtimes/researcher/resume", "/runtimes/researcher/pause", "/owner-sessions/researcher"]


def test_a_failed_private_browser_resumes_the_bot(owner_client, adapter, owner_sessions):
    owner_sessions.failing = "/start"
    assert control(owner_client, "takeover").status_code == 502
    assert not owner_sessions.paused and row()["holder"] == "agent" and row()["paused_at"] is None


def test_status_thumbnail_and_watchers_of_a_paused_bot_never_reach_it(owner_client, adapter, owner_sessions):
    control(owner_client, "takeover")
    adapter.clear()
    status = owner_client.get(STATUS).json()
    assert status["mine"] and status["private_session"] and status["admission_paused"] and status["input_ready"]
    assert status["desktop"]["geometry"] == "1280x800" and status["lease"]["holder"] == "human"
    assert owner_client.get(STATUS + "/thumbnail").json() == {"image": None, "paused": True}
    assert adapter == [], "Status and thumbnail are answered without calling the paused bot"
    assert not computer.stored_status(row(), "someone-else")["mine"]
    with pytest.raises(HTTPException) as watcher:
        computer.upstream_view("researcher", "someone-else")
    assert watcher.value.status_code == 409 and watcher.value.detail == native.PAUSED
    assert owner_client.post(STATUS + "/stop").status_code == 409, "Desktop actions wait for handback"


def pause_researcher():
    with db.connection() as conn:
        computer.fence(conn, "researcher", "human", "owner")
        conn.execute("UPDATE computer_controls SET paused_at=now(),paused_state='{}' WHERE bot_id='researcher'")


def test_the_guard_answers_without_any_network_call(monkeypatch):
    pause_researcher()

    def offline(*args, **kwargs):
        pytest.fail("A paused bot was called")
    monkeypatch.setattr(httpx, "Client", offline)
    monkeypatch.setattr(httpx, "AsyncClient", offline)
    with pytest.raises(HTTPException) as refused:
        native.request("researcher", "GET", "/skills")
    assert refused.value.status_code == 409 and refused.value.detail == native.PAUSED
    client = HermesClient("researcher")
    with pytest.raises(RuntimePaused):
        asyncio.run(client.status("run-1"))

    async def stream():
        async for _ in client.events("run-1"):
            pass
    with pytest.raises(RuntimePaused):
        asyncio.run(stream())


def test_the_worker_leaves_a_paused_bot_alone(make_task, monkeypatch):
    attempt = running_attempt(make_task)
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET lease_until=now()-interval '1 second' WHERE id=%s", (attempt["id"],))
        routines.ensure_binding(conn, "researcher")
    pause_researcher()
    assert worker.claim_existing() == []
    probed = []

    class Client:
        def __init__(self, bot_id):
            probed.append(bot_id)

        async def capabilities(self):
            return {"compatible": True}

    async def supervisor(method, path, **kwargs):
        return {"runtimes": []} if path == "/runtimes" else {}
    monkeypatch.setattr(worker, "HermesClient", Client)
    monkeypatch.setattr(provisioning, "supervisor", supervisor)
    asyncio.run(worker.probe())
    assert "researcher" not in probed and {"writer", "reviewer"} <= set(probed)
    monkeypatch.setattr(native, "request", lambda *a, **k: pytest.fail("The routine scheduler called a paused bot"))
    asyncio.run(routines.reconcile_schedulers())
    with db.connection() as conn:
        conn.execute("UPDATE computer_controls SET paused_at=NULL WHERE bot_id='researcher'")
    assert [row["id"] for row in worker.claim_existing()] == [attempt["id"]], "Handback lets the worker continue"


def test_a_paused_attempt_is_not_counted_as_a_drive_failure(make_task, monkeypatch):
    attempt = running_attempt(make_task)

    async def paused(attempt):
        raise RuntimePaused(native.PAUSED)
    monkeypatch.setattr(worker, "drive", paused)
    asyncio.run(worker.guarded_drive(attempt))
    with db.connection() as conn:
        assert conn.execute("SELECT drive_failures FROM attempts WHERE id=%s", (attempt["id"],)).fetchone()["drive_failures"] == 0


def test_no_active_time_is_charged_while_paused(owner_client, adapter, owner_sessions, make_task):
    attempt = running_attempt(make_task)
    control(owner_client, "takeover")

    def clock():
        with db.connection() as conn:
            return conn.execute("SELECT active_seconds,accounted_at FROM attempts WHERE id=%s", (attempt["id"],)).fetchone()
    stopped = clock()
    assert stopped["accounted_at"] is None and 10 <= stopped["active_seconds"] < 20, "Time before the pause is charged"
    with db.connection() as conn:  # The worker observes the run while the bot is paused.
        execution_policy.account(conn, attempt["id"])
        execution_policy.account(conn, attempt["id"], at=service.now() + timedelta(hours=1))
    assert clock() == stopped, "The clock stays stopped while paused"
    control(owner_client, "handback")
    with db.connection() as conn:
        execution_policy.account(conn, attempt["id"])
    resumed = clock()
    assert resumed["accounted_at"] and resumed["active_seconds"] == stopped["active_seconds"], "The pause itself is never charged"


def test_recovery_resumes_a_runtime_paused_without_a_takeover(monkeypatch):
    calls = []

    async def supervisor(method, path, **kwargs):
        calls.append((method, path))
        if path == "/runtimes":
            return {"runtimes": [{"bot_id": "researcher", "state": "paused"}, {"bot_id": "writer", "state": "paused"},
                                 {"bot_id": "reviewer", "state": "running"}]}
        return {}
    monkeypatch.setattr(provisioning, "supervisor", supervisor)
    with db.connection() as conn:
        computer.fence(conn, "writer", "human", "owner")
        conn.execute("UPDATE computer_controls SET paused_at=now() WHERE bot_id='writer'")
    asyncio.run(worker.resume_orphaned_pauses())
    assert calls == [("GET", "/runtimes"), ("POST", "/runtimes/researcher/resume")], "Only the orphan is resumed"


def test_the_supervisor_pauses_only_the_bots_own_runtime_and_verifies_it(monkeypatch):
    state, calls, filters = {"Paused": False, "Running": True}, [], []

    def docker(method, path, **kwargs):
        calls.append((method, path))
        if path == "/containers/runtime-1/json":
            return {"State": dict(state)}
        if path.endswith("/pause"):
            state["Paused"] = True
        if path.endswith("/unpause"):
            state["Paused"] = False
        return {}
    monkeypatch.setattr(containers, "docker_request", docker)
    monkeypatch.setattr(containers, "filter_containers", lambda labels, all=True: filters.append(labels) or [{"Id": "runtime-1", "State": "running"}])
    assert containers.pause_runtime("researcher") == {"paused": True, "changed": True}
    assert {"com.hermbot.project=hermbot", "com.hermbot.bot-id=researcher", "com.hermbot.kind=runtime"} <= set(filters[0])
    assert containers.pause_runtime("researcher") == {"paused": True, "changed": False}
    assert calls.count(("POST", "/containers/runtime-1/pause")) == 1, "Pausing is idempotent"
    assert containers.resume_runtime("researcher") == {"paused": False, "changed": True}
    assert containers.resume_runtime("researcher") == {"paused": False, "changed": False}
    monkeypatch.setattr(containers, "docker_request", lambda method, path, **kwargs: {"State": {"Paused": False, "Running": True}})
    with pytest.raises(HTTPException) as unverified:
        containers.pause_runtime("researcher")
    assert unverified.value.status_code == 502, "A pause is reported only once Docker shows it"
    monkeypatch.setattr(containers, "filter_containers", lambda labels, all=True: [])
    with pytest.raises(HTTPException) as missing:
        containers.pause_runtime("researcher")
    assert missing.value.status_code == 404
    assert containers.resume_runtime("researcher") == {"paused": False, "changed": False}
    with pytest.raises(HTTPException) as invalid:
        containers.pause_runtime("../writer")
    assert invalid.value.status_code == 422


def test_a_paused_runtime_is_thawed_before_it_is_stopped_or_started(monkeypatch):
    calls = []
    monkeypatch.setattr(containers, "docker_request", lambda method, path, **kwargs: calls.append((method, path)) or {})
    containers.container_stop({"Id": "runtime-1", "State": "paused"})
    assert calls == [("POST", "/containers/runtime-1/unpause"), ("POST", "/containers/runtime-1/stop")]
