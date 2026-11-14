import os
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb

from backend import computer, db, service, worker


@pytest.fixture(scope="session", autouse=True)
def database():
    if not os.environ.get("DATABASE_URL", "").endswith("/hermbot_test"):
        pytest.exit("Tests require the isolated hermbot_test database", returncode=2)
    db.initialize()
    yield
    db.pool.close()


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    with db.connection() as conn:
        conn.execute('TRUNCATE timeline_entries,timeline_clocks,timeline_dirty,timeline_previews CASCADE')
        conn.execute("TRUNCATE attention_pending,attention_items,notification_deliveries,push_subscriptions,notification_preferences,event_sources,event_trigger_definitions,routine_schedulers,routine_definitions,work_items,work_item_messages,connections,connector_apps,connector_permissions,connector_operations,connector_oauth_states,computer_controls,knowledge_versions,knowledge_proposals,owner_receipts,owner_sessions,lifecycle_proposals,bot_operations,attachments,message_reactions,thread_sessions,controls,routines,tool_receipts,approvals,artifact_access,artifacts,events,messages,attempts,dependencies,tasks,grants,sessions CASCADE")
        conn.execute("INSERT INTO notification_preferences(owner,timezone) VALUES('owner','UTC')")
        conn.execute("DELETE FROM participants WHERE bot_id NOT IN ('researcher','writer','reviewer')")
        conn.execute("DELETE FROM conversations WHERE kind='group' OR bot_id NOT IN ('researcher','writer','reviewer')")
        conn.execute("UPDATE conversations SET archived_at=NULL WHERE kind='dm' AND bot_id IN ('researcher','writer','reviewer')")
        conn.execute("DELETE FROM bots WHERE id NOT IN ('researcher','writer','reviewer')")
        environment = conn.execute("SELECT id FROM providers WHERE from_environment=true OR name='Environment provider' ORDER BY from_environment DESC LIMIT 1").fetchone()
        conn.execute("UPDATE workspace_settings SET default_provider_id=%s,timezone='UTC',permitted_tools='[\"file\",\"terminal\",\"skills\"]',max_bots=10,max_active=3", (environment["id"],))
        conn.execute("UPDATE workspace_settings SET active_budget_seconds=3600,decision_provider_id=NULL,decision_model='',decision_mode='disabled',decision_actions=false,execution_policy_revision=1,external_action_mode='requested'")
        conn.execute("UPDATE workspace_settings SET limit_overrides='{}',machine_facts='{}',limits_revision=1")
        conn.execute("DELETE FROM providers WHERE id<>%s AND NOT EXISTS(SELECT 1 FROM bots WHERE bots.provider_id=providers.id)", (environment["id"],))
        conn.execute("UPDATE bots SET status='active',desired_state='running',applied_revision=revision,enabled_tools='[\"file\",\"terminal\"]',enabled_skills='[]',readiness=%s", (Jsonb({"compatible": True}),))
        conn.execute("UPDATE bots SET limit_overrides='{}',applied_limits_revision=1,limits_observed='{}',memory_mb=2048,cpus=1.5")
    monkeypatch.setattr(worker, "settings", replace(worker.settings, provider_configured=True, model_name="test"))


@pytest.fixture(autouse=True)
def owner_sessions(monkeypatch):
    """Tests never reach the real supervisor: takeover gets an in-memory private session."""
    class Sessions:
        calls, finish_cookies, failing, pause_fails = [], [], None, False
    sessions = Sessions()
    sessions.calls, sessions.paused = [], set()

    def supervisor(method, path, **kwargs):
        sessions.calls.append(("supervisor", method, path, kwargs.get("json")))
        if path.startswith("/runtimes/"):
            bot_id, action = path.split("/")[2:4]
            if action == "pause":
                if sessions.pause_fails:
                    raise httpx.ConnectError("The runtime could not be paused")
                changed = bot_id not in sessions.paused
                sessions.paused.add(bot_id)
                return {"paused": True, "changed": changed}
            changed = bot_id in sessions.paused
            sessions.paused.discard(bot_id)
            return {"paused": False, "changed": changed}
        return {"url": "http://hermbot-owner-test:8650", "token": "owner-session-token"} if method == "POST" else {"removed": True}

    def session_request(session, method, path, **kwargs):
        sessions.calls.append(("session", method, path, kwargs.get("json")))
        if sessions.failing == path:
            raise httpx.ConnectError("The private browser is unavailable")
        return {"/health": {"ready": True}, "/start": {"started": True}, "/finish": {"cookies": sessions.finish_cookies}}[path]
    monkeypatch.setattr(computer, "supervisor", supervisor)
    monkeypatch.setattr(computer, "session_request", session_request)
    return sessions


@pytest.fixture
def make_task():
    def create(bot="researcher", manager=False, key=None, prompt="Investigate the supplied evidence"):
        with db.connection() as conn:
            conversation = conn.execute("SELECT id FROM conversations WHERE bot_id=%s", (bot,)).fetchone()["id"]
            return service.create_task(conn, bot_id=bot, conversation_id=conversation, prompt=prompt,
                                       request_key=key or str(uuid4()), managed_bot_ids=[b for b in ("researcher", "writer", "reviewer") if b != bot] if manager else None)
    return create


def row(task_id):
    with db.connection() as conn:
        return conn.execute("SELECT * FROM tasks WHERE id=%s", (task_id,)).fetchone()
