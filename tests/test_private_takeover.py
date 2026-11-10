"""Computer takeover happens in a private owner session the bot cannot reach."""
import hashlib
import importlib
import sys

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend import app as application, computer, containers, db, native
from backend.secrets import decrypt
from backend.settings import settings

pytestmark = pytest.mark.integration

BOT_COOKIES = [
    {"name": "sid", "value": "bot", "domain": ".example.com", "path": "/", "secure": True, "httpOnly": True,
     "sameSite": "Lax", "expires": 1900000000, "priority": "Medium"},
    {"name": "gone", "value": "x", "domain": "example.com", "path": "/", "session": True, "expires": -1},
    {"name": "bad", "value": 1, "domain": "example.com"},
]


@pytest.fixture
def owner_client():
    client = TestClient(application.app, headers={"Authorization": "Bearer " + settings.owner_token})
    yield client
    client.close()


def identity():
    return computer.viewer("Bearer " + settings.owner_token)


@pytest.fixture
def adapter(monkeypatch):
    calls, lease = [], {"holder": "agent"}

    def request(bot_id, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        if path == "/computer/browser-state":
            return {"cookies": BOT_COOKIES, "urls": ["https://example.com/login", "file:///etc/passwd", "javascript:alert(1)"]}
        if path in ("/computer", "/computer/control"):
            if path == "/computer/control":
                lease["holder"] = "agent" if kwargs["json"]["action"] == "handback" else "human"
            holder = lease["holder"]
            return {"desktop": {"running": True, "geometry": "1280x800"}, "input_ready": True,
                    "lease": {"holder": holder, "epoch": 1, "viewer_hash": hashlib.sha256(identity().encode()).hexdigest()[:12]}}
        return {"applied": 1}
    monkeypatch.setattr(native, "request", request)
    return calls


def takeover(owner_client):
    return owner_client.post("/api/v1/bots/researcher/computer/control", json={"action": "takeover"})


def test_takeover_opens_a_private_session_seeded_only_with_bot_cookies_and_pages(owner_client, adapter, owner_sessions):
    response = takeover(owner_client)
    assert response.status_code == 200 and response.json()["private_session"] is True
    assert ("supervisor", "POST", "/owner-sessions/researcher", {"geometry": "1280x800"}) in owner_sessions.calls
    seed = next(body for kind, method, path, body in owner_sessions.calls if path == "/start")
    assert seed["urls"] == ["https://example.com/login"]
    assert seed["cookies"] == [
        {"name": "sid", "value": "bot", "domain": ".example.com", "path": "/", "httpOnly": True, "secure": True,
         "sameSite": "Lax", "expires": 1900000000},
        {"name": "gone", "value": "x", "domain": "example.com", "path": "/"},
    ]
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM computer_controls WHERE bot_id='researcher'").fetchone()
    assert row["session_token_encrypted"] != "owner-session-token" and decrypt(row["session_token_encrypted"]) == "owner-session-token"
    assert "bot" not in decrypt(row["session_seed_encrypted"]), "Cookie values are not kept after the session starts"


def test_owner_in_control_only_ever_sees_the_private_session(owner_client, adapter):
    with db.connection() as conn:
        computer.fence(conn, "researcher", "human", identity())
    with pytest.raises(HTTPException) as starting:
        computer.upstream_view("researcher", identity())
    assert starting.value.status_code == 409, "Never route the owner in control to the bot's own screen"
    takeover(owner_client)
    url, headers = computer.upstream_view("researcher", identity())
    assert url == "ws://hermbot-owner-test:8650/view" and headers == {"Authorization": "Bearer owner-session-token"}
    assert owner_client.get("/api/v1/bots/researcher/computer").status_code == 200  # The UI polls status throughout.
    assert computer.upstream_view("researcher", identity())[0] == "ws://hermbot-owner-test:8650/view", "Status polls keep the owner's route"
    with pytest.raises(HTTPException) as paused:
        computer.upstream_view("researcher", "someone-else")
    assert paused.value.detail == native.PAUSED, "Nobody watches a paused bot"
    with db.connection() as conn:
        conn.execute("UPDATE computer_controls SET paused_at=NULL WHERE bot_id='researcher'")
    url, headers = computer.upstream_view("researcher", "someone-else")
    assert url.endswith(":8643/computer/ws") and len(headers["X-Hermbot-Viewer"]) == 64 and headers["X-Hermbot-Viewer"] != "someone-else"


def test_the_bots_own_screen_never_accepts_owner_input(owner_client, adapter):
    # Even the owner in control watches the bot's screen through a viewer the adapter's lease never matches.
    takeover(owner_client)
    with db.connection() as conn:
        conn.execute("UPDATE computer_controls SET session_url=NULL,paused_at=NULL WHERE bot_id='researcher'")
        conn.execute("UPDATE computer_controls SET holder='agent' WHERE bot_id='researcher'")
    url, headers = computer.upstream_view("researcher", identity())
    assert url.endswith(":8643/computer/ws") and headers["X-Hermbot-Viewer"] != identity()


def test_handback_carries_cookies_back_and_removes_ones_the_owner_deleted(owner_client, adapter, owner_sessions):
    takeover(owner_client)
    owner_sessions.finish_cookies = [
        {"name": "sid", "value": "owner-login", "domain": ".example.com", "path": "/", "secure": True},
        {"name": "new", "value": "y", "domain": "example.org", "path": "/"},
    ]
    owner_sessions.calls.clear()
    adapter.clear()
    assert owner_client.post("/api/v1/bots/researcher/computer/control", json={"action": "handback"}).status_code == 200
    assert [call[:3] for call in owner_sessions.calls] == [("supervisor", "POST", "/runtimes/researcher/resume"), ("session", "POST", "/finish"),
                                                           ("supervisor", "DELETE", "/owner-sessions/researcher")]
    cookies = next(body for method, path, body in adapter if path == "/computer/browser-cookies")
    assert cookies["set"] == owner_sessions.finish_cookies
    assert cookies["delete"] == [{"name": "gone", "domain": "example.com", "path": "/"}]
    assert [path for method, path, body in adapter][:3] == ["/health", "/computer/browser-cookies", "/computer/control"], \
        "The bot is awake to receive the logins, and gets control back only after them"
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM computer_controls WHERE bot_id='researcher'").fetchone()
        event = conn.execute("SELECT data FROM events WHERE kind='computer.control' ORDER BY id DESC LIMIT 1").fetchone()
    assert row["holder"] == "agent" and row["session_url"] is None
    assert event["data"]["cookies_carried"] == 2


def test_failed_private_session_returns_control_to_the_bot(owner_client, adapter, owner_sessions):
    owner_sessions.failing = "/start"
    response = takeover(owner_client)
    assert response.status_code == 502
    assert ("supervisor", "DELETE", "/owner-sessions/researcher", None) in owner_sessions.calls
    assert ("supervisor", "POST", "/runtimes/researcher/resume", None) in owner_sessions.calls and not owner_sessions.paused
    assert ("POST", "/computer/control", {"action": "handback", "force": True, "viewer_id": identity()}) in adapter
    with db.connection() as conn:
        row = conn.execute("SELECT holder,session_url FROM computer_controls WHERE bot_id='researcher'").fetchone()
    assert row["holder"] == "agent" and row["session_url"] is None


def test_the_bots_own_browser_is_not_offered_to_the_owner(owner_client):
    assert owner_client.post("/api/v1/bots/researcher/computer/browser").status_code == 404


def test_owner_session_container_is_isolated_from_the_bot(monkeypatch):
    calls = []

    def docker(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json"), kwargs.get("params")))
        return {"Id": "owner-session"} if path == "/containers/create" else {}
    monkeypatch.setattr(containers, "docker_request", docker)
    monkeypatch.setattr(containers, "optional_get", lambda path: None)
    requested = []
    monkeypatch.setattr(containers, "filter_containers", lambda labels, all=True: requested.append(labels) or [{"Id": "api-1"}])
    result = containers.start_owner_session("researcher", containers.OwnerSessionSpec(geometry="1280x800"))
    assert requested == [["com.docker.compose.project=hermbot", "com.docker.compose.service=api"]], "Only the API joins"
    network = next(body for method, path, body, params in calls if path == "/networks/create")
    assert network["Name"] == "hermbot-owner-researcher" and network["Labels"]["com.hermbot.kind"] == "owner-session"
    assert ("POST", "/networks/hermbot-owner-researcher/connect", {"Container": "api-1"}, None) in calls
    created = next(body for method, path, body, params in calls if path == "/containers/create")
    host = created["HostConfig"]
    assert created["User"] == "10001:10001" and created["Entrypoint"][:2] == containers.RUNTIME_PYTHON
    assert host["NetworkMode"] == "hermbot-owner-researcher" and "Binds" not in host and not host.get("Privileged")
    assert host["ReadonlyRootfs"] and host["CapDrop"] == ["ALL"] and host["SecurityOpt"] == ["no-new-privileges:true"]
    assert f"OWNER_SESSION_TOKEN={result['token']}" in created["Env"] and len(result["token"]) >= 32
    assert result["url"] == "http://hermbot-owner-researcher:8650"


def test_owner_session_removal_refuses_resources_it_does_not_own(monkeypatch):
    monkeypatch.setattr(containers, "optional_get", lambda path: {"Id": "x", "Config": {"Labels": {"com.hermbot.kind": "runtime"}}})
    monkeypatch.setattr(containers, "docker_request", lambda *a, **k: pytest.fail("Unowned resources must not be removed"))
    with pytest.raises(HTTPException) as error:
        containers.remove_owner_session("researcher")
    assert error.value.status_code == 409


def test_owner_session_removal_reads_the_network_after_the_container_is_gone(monkeypatch):
    labels = containers.owner_session_labels("researcher")
    resources = {"/containers/hermbot-owner-researcher/json": {"Id": "session", "Config": {"Labels": labels}},
                 "/networks/hermbot-owner-researcher": {"Labels": labels, "Containers": {"session": {}, "api-1": {}}}}
    calls = []

    def docker(method, path, **kwargs):
        calls.append((method, path, (kwargs.get("json") or {}).get("Container")))
        if path == "/containers/session":  # Deleting the container also removes it from the network.
            del resources["/networks/hermbot-owner-researcher"]["Containers"]["session"]
        return {}
    monkeypatch.setattr(containers, "optional_get", lambda path: resources.get(path))
    monkeypatch.setattr(containers, "docker_request", docker)
    containers.remove_owner_session("researcher")
    assert calls == [("DELETE", "/containers/session", None),
                     ("POST", "/networks/hermbot-owner-researcher/disconnect", "api-1"),
                     ("DELETE", "/networks/hermbot-owner-researcher", None)]


def test_adapter_carries_cookies_and_expires_deleted_ones_with_valid_parameters(monkeypatch):
    from .test_control_isolation import load_adapter
    adapter = load_adapter(monkeypatch, "k" * 32)
    sent = []

    class Browser:
        def call(self, method, **params):
            sent.append((method, params))
            return {}

        def close(self):
            pass
    monkeypatch.setattr(adapter, "bot_browser", lambda: Browser())
    body = adapter.BrowserCookies(set=[{"name": "sid", "value": "v", "domain": ".example.com", "path": "/", "session": True}],
                                  delete=[{"name": "old", "domain": ".example.com", "path": "/"}])
    assert adapter.browser_cookies(body) == {"applied": 2}
    method, params = sent[0]
    # Chromium requires a value on every cookie it sets, including the expired ones that delete.
    assert method == "Storage.setCookies" and all(isinstance(cookie.get("value"), str) for cookie in params["cookies"])
    assert params["cookies"][1] == {"name": "old", "domain": ".example.com", "path": "/", "value": "", "expires": 1}
    assert "expires" not in params["cookies"][0]


def load_session(monkeypatch):
    monkeypatch.setenv("OWNER_SESSION_TOKEN", "session-token")
    monkeypatch.syspath_prepend("/app/docker")
    sys.modules.pop("owner_session", None)
    return importlib.import_module("owner_session")


def test_private_browser_requires_its_token_and_opens_only_web_pages(monkeypatch):
    session = load_session(monkeypatch)
    calls = []

    class Pipe:
        def call(self, method, **params):
            calls.append((method, params))
            return {"targetInfos": [{"type": "page", "targetId": "blank"}]} if method == "Target.getTargets" else {}
    monkeypatch.setattr(session, "launch_browser", lambda: Pipe())
    client = TestClient(session.app)
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    seed = {"cookies": BOT_COOKIES, "urls": ["https://example.com/", "file:///etc/passwd"]}
    assert client.post("/start", json=seed, headers={"Authorization": "Bearer session-token"}).status_code == 200
    assert [method for method, params in calls] == ["Storage.setCookies", "Target.getTargets", "Target.createTarget", "Target.closeTarget"]
    assert calls[2][1] == {"url": "https://example.com/"}
    assert all(set(cookie) <= set(session.browser_control.COOKIE_FIELDS) for cookie in calls[0][1]["cookies"])
    assert client.post("/start", json=seed, headers={"Authorization": "Bearer session-token"}).status_code == 409
