"""Advanced settings: recommended defaults, validated values, a visible change history and reset."""
from fastapi.testclient import TestClient
import pytest

from backend import app as application
from backend.settings import settings

pytestmark = pytest.mark.integration
DEFAULTS = {"takeover_unpausable": "ask", "takeover_reply_wait_seconds": 0}


@pytest.fixture
def owner_client():
    client = TestClient(application.app, headers={"Authorization": "Bearer " + settings.owner_token})
    client.post("/api/v1/settings/advanced/reset")  # The workspace row outlives each test.
    yield client
    client.post("/api/v1/settings/advanced/reset")
    client.close()


def history(client):
    return [{key: row[key] for key in ("setting", "from", "to")} for row in client.get("/api/v1/settings/advanced").json()["history"]]


def test_defaults_are_the_recommended_behaviour(owner_client):
    body = owner_client.get("/api/v1/settings/advanced").json()
    assert body["values"] == DEFAULTS and body["defaults"] == DEFAULTS


def test_each_change_is_recorded_once_with_its_old_and_new_value(owner_client):
    from backend import db
    with db.connection() as conn:
        conn.execute("DELETE FROM events WHERE kind='settings.advanced_changed'")
    response = owner_client.put("/api/v1/settings/advanced", json={"takeover_unpausable": "refuse", "takeover_reply_wait_seconds": 0})
    assert response.status_code == 200 and response.json()["values"] == {**DEFAULTS, "takeover_unpausable": "refuse"}
    assert history(owner_client) == [{"setting": "takeover_unpausable", "from": "ask", "to": "refuse"}], "Unchanged settings are not recorded"
    owner_client.put("/api/v1/settings/advanced", json={"takeover_unpausable": "refuse", "takeover_reply_wait_seconds": 30})
    assert history(owner_client)[0] == {"setting": "takeover_reply_wait_seconds", "from": 0, "to": 30}
    assert owner_client.get("/api/v1/settings/advanced").json()["history"][0]["changed_at"]


@pytest.mark.parametrize("body", [{"takeover_unpausable": "maybe"}, {"takeover_reply_wait_seconds": 45}, {"takeover_reply_wait_seconds": -1}, {"unknown": True}])
def test_only_listed_values_are_accepted(owner_client, body):
    assert owner_client.put("/api/v1/settings/advanced", json=body).status_code == 422
    assert owner_client.get("/api/v1/settings/advanced").json()["values"] == DEFAULTS


def test_reset_restores_the_defaults_and_records_it(owner_client):
    owner_client.put("/api/v1/settings/advanced", json={"takeover_unpausable": "continue", "takeover_reply_wait_seconds": 60})
    body = owner_client.post("/api/v1/settings/advanced/reset").json()
    assert body["values"] == DEFAULTS
    assert history(owner_client)[:2] == [{"setting": "takeover_reply_wait_seconds", "from": 60, "to": 0},
                                         {"setting": "takeover_unpausable", "from": "continue", "to": "ask"}]
    before = len(history(owner_client))
    owner_client.post("/api/v1/settings/advanced/reset")
    assert len(history(owner_client)) == before, "Resetting defaults again changes nothing"


def test_saving_general_settings_leaves_advanced_settings_alone(owner_client):
    owner_client.put("/api/v1/settings/advanced", json={"takeover_unpausable": "refuse", "takeover_reply_wait_seconds": 15})
    workspace = owner_client.get("/api/v1/settings").json()["workspace"]
    general = {key: workspace[key] for key in ("timezone", "default_provider_id", "permitted_tools", "max_bots", "max_active")}
    assert owner_client.put("/api/v1/settings", json=general).status_code == 200
    assert owner_client.get("/api/v1/settings/advanced").json()["values"] == {"takeover_unpausable": "refuse", "takeover_reply_wait_seconds": 15}


def test_advanced_settings_are_owner_only():
    client = TestClient(application.app)
    assert client.get("/api/v1/settings/advanced").status_code in (401, 403)
    assert client.put("/api/v1/settings/advanced", json=DEFAULTS).status_code in (401, 403)
    assert client.post("/api/v1/settings/advanced/reset").status_code in (401, 403)
