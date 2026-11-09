"""The agent's identity cannot use the runtime's control credentials or reach its control ports."""
import ctypes
import importlib.util
import json
import sys
from types import ModuleType
from uuid import uuid4

from fastapi import HTTPException
import pytest

from backend import containers, db, native, registry
from backend.secrets import decrypt
from .test_phase2 import config

pytestmark = pytest.mark.integration


def spec(**changes):
    values = dict(bot_id="bot-test", operation_id=str(uuid4()), revision=1, configuration=config(),
                  model_base_url="https://example.invalid/v1", model_api_key="private-test-key",
                  runtime_key="r" * 32, adapter_key="a" * 32, bot_token="b" * 32)
    return containers.RuntimeSpec(**{**values, **changes})


class Docker:
    """Records Docker API calls and answers the few the supervisor reads."""

    def __init__(self, fence_status=0, started="2026-11-09T09:00:00Z"):
        self.calls, self.fence_status, self.started = [], fence_status, started

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json")))
        if path.startswith("/images/"):
            return {"Id": "sha256:image"}
        if path == "/containers/create":
            return {"Id": "fence-helper" if kwargs["json"].get("Entrypoint", [""])[0] == "/bin/sh" else "runtime"}
        if path.endswith("/wait"):
            return {"StatusCode": self.fence_status}
        if path.endswith("/json"):
            return {"State": {"StartedAt": self.started, "Running": True}}
        return {}

    def created(self, kind):
        return next(body for method, path, body in self.calls if path == "/containers/create" and (body.get("Entrypoint", [""])[0] == "/bin/sh") == (kind == "fence"))


@pytest.fixture
def supervisor(monkeypatch):
    docker = Docker()
    order = []
    monkeypatch.setattr(containers, "docker_request", docker)
    monkeypatch.setattr(containers, "ensure_resources", lambda bot_id: ("hermbot-bot-test", {"data": "d", "workspace": "w", "browser": "b"}))
    monkeypatch.setattr(containers, "find_runtime", lambda bot_id, required=True: None)
    monkeypatch.setattr(containers, "remove_container", lambda container: None)
    monkeypatch.setattr(containers, "runtime_ready", lambda container_id: order.append(("ready", container_id)))
    original_fence = containers.fence
    monkeypatch.setattr(containers, "fence", lambda container_id, bot_id: (order.append(("fence", container_id)), original_fence(container_id, bot_id))[1])
    monkeypatch.setattr(containers, "FENCED", {})
    docker.order = order
    return docker


def test_runtime_gets_a_distinct_adapter_key_and_is_fenced_before_ready(supervisor):
    containers.ensure(spec())
    environment = dict(item.split("=", 1) for item in supervisor.created("runtime")["Env"])
    assert environment["HERMBOT_ADAPTER_KEY"] == "a" * 32 and environment["API_SERVER_KEY"] == "r" * 32
    assert supervisor.order == [("fence", "runtime"), ("ready", "runtime")]
    helper = supervisor.created("fence")
    host = helper["HostConfig"]
    assert host["NetworkMode"] == "container:runtime" and host["CapDrop"] == ["ALL"]
    assert set(host["CapAdd"]) == {"NET_ADMIN", "NET_RAW"} and host["ReadonlyRootfs"] is True
    assert "--uid-owner hermes" in helper["Entrypoint"][2] and "8642,8643" in helper["Entrypoint"][2]
    assert ("DELETE", "/containers/fence-helper", None) in supervisor.calls


def test_runtime_spec_requires_an_adapter_key():
    with pytest.raises(ValueError):
        spec(adapter_key="")


def test_failed_fence_keeps_the_runtime_unready(supervisor):
    supervisor.fence_status = 1
    with pytest.raises(HTTPException) as error:
        containers.ensure(spec())
    assert error.value.status_code == 502
    assert ("ready", "runtime") not in supervisor.order
    assert ("DELETE", "/containers/fence-helper", None) in supervisor.calls


def test_reconcile_refences_only_after_a_restart(supervisor, monkeypatch):
    monkeypatch.setattr(containers, "find_runtime", lambda bot_id, required=True: {"Id": "runtime", "State": "running"})
    containers.reconcile_networks("bot-test")
    containers.reconcile_networks("bot-test")
    assert supervisor.order.count(("fence", "runtime")) == 1
    supervisor.started = "2026-11-09T09:05:00Z"
    containers.reconcile_networks("bot-test")
    assert supervisor.order.count(("fence", "runtime")) == 2


def test_each_bot_gets_its_own_adapter_key_once():
    with db.connection() as conn:
        for bot in conn.execute("SELECT * FROM bots WHERE status<>'deleted'").fetchall():
            assert decrypt(bot["adapter_key_encrypted"]) != decrypt(bot["runtime_key_encrypted"])
        before = conn.execute("UPDATE bots SET adapter_key_encrypted=NULL WHERE id='researcher' RETURNING revision").fetchone()["revision"]
        registry.bootstrap(conn)
        registry.bootstrap(conn)
        bot = conn.execute("SELECT * FROM bots WHERE id='researcher'").fetchone()
    assert bot["revision"] == before + 1 and bot["adapter_key_revision"] == before + 1
    assert len(decrypt(bot["adapter_key_encrypted"])) >= 32


def test_adapter_requests_switch_keys_only_after_reprovisioning():
    with db.connection() as conn:
        bot = conn.execute("UPDATE bots SET adapter_key_revision=applied_revision+1 WHERE id='researcher' RETURNING *").fetchone()
    assert native.target("researcher")[1]["Authorization"] == "Bearer " + decrypt(bot["runtime_key_encrypted"])
    with db.connection() as conn:
        conn.execute("UPDATE bots SET adapter_key_revision=applied_revision WHERE id='researcher'")
    assert native.target("researcher")[1]["Authorization"] == "Bearer " + decrypt(bot["adapter_key_encrypted"])


def load_adapter(monkeypatch, key):
    monkeypatch.setenv("HERMBOT_ADAPTER_KEY", key)
    loaded = importlib.util.spec_from_file_location("isolation_adapter", "/app/docker/runtime_api.py")
    adapter = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(adapter)
    return adapter


def test_adapter_drops_its_key_from_inherited_environment_and_is_not_dumpable(monkeypatch):
    import os
    adapter = load_adapter(monkeypatch, "k" * 32)
    assert "HERMBOT_ADAPTER_KEY" not in os.environ
    assert ctypes.CDLL(None).prctl(3, 0, 0, 0, 0) == 0  # PR_GET_DUMPABLE
    adapter.authorize("Bearer " + "k" * 32)
    for supplied in ("Bearer " + "r" * 32, "", "Bearer "):
        with pytest.raises(HTTPException) as error:
            adapter.authorize(supplied)
        assert error.value.status_code == 401


def test_adapter_without_a_key_refuses_every_request(monkeypatch):
    adapter = load_adapter(monkeypatch, "")
    with pytest.raises(HTTPException):
        adapter.authorize("Bearer ")


def test_wake_job_is_toggled_through_the_local_job_store(monkeypatch, tmp_path):
    from pathlib import Path
    adapter = load_adapter(monkeypatch, "k" * 32)
    monkeypatch.setattr(adapter, "HOME", tmp_path)
    job = {"id": "managed-job", "name": "Hermbot wake 00000000-0000-0000-0000-000000000001", "no_agent": True,
           "script": "hermbot_routine_wakeup.py", "schedule": {"kind": "interval", "minutes": 1}, "enabled": False}
    resumed = []
    store = ModuleType("cron.jobs")
    store.list_jobs = lambda include_disabled: [job]
    store.resume_job = resumed.append
    store.pause_job = lambda job_id: pytest.fail("An enabled request must not pause the job")
    monkeypatch.setitem(sys.modules, "cron", ModuleType("cron"))
    monkeypatch.setitem(sys.modules, "cron.jobs", store)
    original_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: "# fixed script" if str(path) == "/opt/hermbot/routine_wakeup.py" else original_read(path, *a, **kw))
    monkeypatch.setenv("BOT_ID", "researcher")
    body = adapter.RoutineScheduler(generation="00000000-0000-0000-0000-000000000001", token="scoped-wake-token-" * 3, enabled=True)
    assert adapter.routine_scheduler(body) == {"job_id": "managed-job", "enabled": True}
    assert resumed == ["managed-job"]
    assert json.loads((tmp_path / "hermbot-routine-wakeup.json").read_text())["token"] == body.token
