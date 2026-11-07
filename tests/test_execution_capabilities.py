"""Capability discovery, preparation, schema filtering and explicit handoff boundaries."""

import hashlib
import importlib.util
import json
import sys
import httpx
from types import ModuleType, SimpleNamespace
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
import pytest

from backend import app as application, capabilities, db, native, registry, service, worker
from backend.models import DelegateArgs, PrepareArgs
from backend.settings import settings
from tests.conftest import row
from tests.test_phase4 import connection


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inventory(monkeypatch):
    groups = {
        "file": ["read_file", "write_file", "patch", "search_files"],
        "terminal": ["terminal", "process_manage"],
        "skills": ["skill_view", "skills_list", "skill_manage"],
        "memory": ["memory"],
        "browser": ["browser_navigate"],
        "computer_use": ["computer_use"],
    }
    skills = [
        {
            "name": "hermbot-csv",
            "description": "Analyze a supplied CSV and publish a concise report.",
            "required_tools": ["read_file", "hermbot_publish"],
            "sha256": "csv-hash",
        },
        {"name": "hermbot-code", "description": "Run local code.", "required_tools": ["terminal"]},
        {"name": "hermbot-summary", "description": "Summarize supplied evidence.", "required_tools": []},
    ]
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET permitted_tools=%s", (Jsonb(list(groups)),))
        conn.execute(
            "UPDATE bots SET enabled_tools=%s,enabled_skills=%s,readiness=%s",
            (
                Jsonb(list(groups)),
                Jsonb(["hermbot-csv", "hermbot-code"]),
                Jsonb(
                    {
                        "compatible": True,
                        "inventory": {
                            "version": 1,
                            "toolsets": [
                                {"name": n, "tools": t, "configured": True} for n, t in groups.items()
                            ],
                            "skills": skills,
                        },
                    }
                ),
            ),
        )
    calls = []

    def read(bot, method, path, **kwargs):
        calls.append((bot, method, path))
        content = (
            "---\nname: "
            + path.rsplit("/", 1)[1]
            + "\ndescription: Test procedure\n---\n\nRaw guidance: !`echo MUST_NOT_EXECUTE`\n"
        )
        return {"exists": True, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}

    monkeypatch.setattr(native, "request", read)
    return calls


def claim(task):
    return next(a for a in worker.claim_new() if a["task_id"] == task["id"])


def view(task):
    with db.connection() as conn:
        return capabilities.manifest(conn, task["bot_id"], service.locked_task(conn, task["id"]))


def prepare(task, attempt, key="prepare", **args):
    with db.connection() as conn:
        return service.tool_action(
            conn, task["bot_id"], attempt["session_id"], "prepare", PrepareArgs(**args).model_dump(), key
        )


def test_actual_tools_and_skills_differ_from_declared_expertise(inventory, make_task):
    task = make_task()
    v = view(task)
    assert "terminal" in v["available_tools"] and "terminal" not in v["allowed_tools"]
    assert "hermbot_prepare" in v["allowed_tools"] and "hermbot_publish" not in v["allowed_tools"]
    assert {s["name"] for s in v["skills"]} == {"hermbot-csv", "hermbot-code"}
    with db.connection() as conn:
        conn.execute('UPDATE workspace_settings SET permitted_tools=\'["skills","terminal"]\'')
    assert "read_file" not in view(task)["available_tools"]


def test_prepare_consults_only_selected_raw_skill_and_is_idempotent(inventory, make_task):
    task = make_task()
    attempt = claim(task)
    args = {"skills": ["hermbot-csv"], "tools": ["read_file", "hermbot_publish"]}
    result = prepare(task, attempt, **args)
    assert result["ready"] and "!`echo MUST_NOT_EXECUTE`" in result["skills"][0]["content"]
    assert prepare(task, attempt, **args) == result
    assert inventory == [("researcher", "GET", "/skills/hermbot-csv")]
    assert view(task)["prepared"] and "hermbot_publish" in view(task)["allowed_tools"]
    plan = row(task["id"])["execution_plan"]
    assert plan["skills"][0]["sha256"] == result["skills"][0]["sha256"]
    assert "content" not in json.dumps(plan)


@pytest.mark.parametrize(
    ("name", "code"), [("hermbot-summary", "skill_not_enabled"), ("hermbot-absent", "skill_missing")]
)
def test_disabled_or_missing_skills_do_not_change_permissions(inventory, make_task, name, code):
    task = make_task()
    attempt = claim(task)
    result = prepare(task, attempt, skills=[name])
    assert not result["ready"] and result["blockers"][0]["code"] == code and not inventory
    with db.connection() as conn:
        assert registry.require_bot(conn, "researcher")["enabled_skills"] == ["hermbot-csv", "hermbot-code"]


def test_skill_requiring_blocked_native_tool_fails_before_consultation(inventory, make_task):
    connection()
    with db.connection() as conn:
        conn.execute("UPDATE bots SET enabled_tools='[\"file\",\"skills\"]' WHERE id='researcher'")
    task = make_task()
    attempt = claim(task)
    result = prepare(task, attempt, skills=["hermbot-code"])
    assert not result["ready"] and result["blockers"][0]["name"] == "terminal" and not inventory
    assert not {"terminal", "browser_navigate"} & set(view(task)["available_tools"])
    assert "write_file" in view(task)["available_tools"]
    assert "read_file" in view(task)["available_tools"]


def test_missing_inventory_is_explicit_and_cannot_prepare(inventory, make_task):
    task = make_task()
    attempt = claim(task)
    with db.connection() as conn:
        conn.execute("UPDATE bots SET readiness='{\"compatible\":true}' WHERE id='researcher'")
    result = prepare(task, attempt)
    assert not result["ready"] and result["blockers"][0]["code"] == "runtime_unavailable"


def test_connection_requirements_resources_and_revision_invalidate_preparation(inventory, make_task):
    c = connection(actions=["github.repository"], resources={"github.repository": ["owner/repo"]})
    task = make_task()
    attempt = claim(task)
    requirement = {"connection_id": c["id"], "action": "github.repository", "resource": "other/repo"}
    result = prepare(task, attempt, connectors=[requirement])
    assert not result["ready"] and result["blockers"][0]["code"] == "resource_access_missing"
    requirement["resource"] = "owner/repo"
    assert prepare(task, attempt, key="ready", connectors=[requirement])["ready"]
    with db.connection() as conn:
        conn.execute("UPDATE connections SET revision=revision+1 WHERE id=%s", (c["id"],))
    assert view(task)["preparation_required"]
    assert prepare(task, attempt, key="ready", connectors=[requirement])[
        "ready"
    ]  # Old receipt does not admit current tools.
    assert view(task)["preparation_required"]


def test_discovery_does_not_latch_connected_peer_policy(inventory, make_task):
    connection(bots=["writer"])
    task = make_task()
    attempt = claim(task)
    with db.connection() as conn:
        peers = service.tool_action(conn, "researcher", attempt["session_id"], "teammates", {}, "discover")[
            "teammates"
        ]
        assert not service.locked_task(conn, task["id"])["connector_restricted"]
    peer = next(p for p in peers if p["id"] == "writer")["execution_capabilities"]
    assert "terminal" in peer["native_tools"]
    assert peer["connections"][0]["actions"] and not {"id", "resources", "account"} & set(
        peer["connections"][0]
    )
    assert "content" not in json.dumps(peer)


@pytest.mark.parametrize('status', ['connected', 'revoked'])
def test_connection_state_does_not_remove_configured_tools_or_block_preparation(inventory, make_task, status):
    c = connection()
    task = make_task()
    attempt = claim(task)
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET permitted_tools='[\"file\",\"terminal\",\"skills\",\"memory\",\"browser\",\"computer_use\"]'")
        conn.execute("UPDATE bots SET enabled_tools='[\"file\",\"terminal\",\"skills\",\"memory\",\"browser\",\"computer_use\"]' WHERE id='researcher'")
        conn.execute('UPDATE connections SET status=%s WHERE id=%s', (status, c['id']))
        conn.execute('UPDATE tasks SET connector_restricted=true WHERE id=%s', (task['id'],))
    required = ['terminal', 'write_file', 'patch', 'skill_manage', 'browser_navigate', 'computer_use']
    assert set(required) <= set(view(task)['available_tools'])
    assert prepare(task, attempt, tools=required)['ready']
    assert set(required) <= set(view(task)['allowed_tools'])
    assert not view(task)['connector_restricted']


def test_connected_helper_keeps_tools_without_inheriting_account_access(inventory, make_task):
    connection(bots=['writer'])
    task = make_task(manager=True)
    attempt = claim(task)
    assert prepare(task, attempt, tools=['terminal'])['ready']
    with db.connection() as conn:
        child_id = service.delegate(conn, service.locked_task(conn, task['id']), 'writer', 'Analyze locally')['task_id']
        child = service.locked_task(conn, child_id)
        helper_id = service.delegate(conn, service.locked_task(conn, task['id']), 'reviewer', 'Review locally')['task_id']
        helper = service.locked_task(conn, helper_id)
    assert 'terminal' in view(task)['allowed_tools']
    assert 'terminal' in view(child)['available_tools']
    assert 'terminal' in view(helper)['available_tools']
    assert view(child)['connections'] and not view(helper)['connections']


def test_actual_connected_handoff_preserves_parent_tools_and_preparation(inventory, make_task):
    connection(bots=["writer"])
    task = make_task()
    attempt = claim(task)
    assert prepare(task, attempt, tools=["terminal"])["ready"]
    with db.connection() as conn:
        service.delegate(conn, service.locked_task(conn, task["id"]), "writer", "Summarize supplied evidence")
    assert not view(task)["preparation_required"] and "terminal" in view(task)["allowed_tools"]


def test_new_native_continuation_needs_fresh_preparation(inventory, make_task):
    parent = make_task()
    attempt = claim(parent)
    prepare(parent, attempt)
    with db.connection() as conn:
        child = service.delegate(
            conn, service.locked_task(conn, parent["id"]), "writer", "Write a concise report"
        )
    worker.settle(attempt["id"], {"status": "completed", "output": "Waiting"})
    child_attempt = next(a for a in worker.claim_new() if str(a["task_id"]) == child["task_id"])
    prepare(row(UUID(child["task_id"])), child_attempt)
    worker.settle(child_attempt["id"], {"status": "completed", "output": "Actual report"})
    worker.advance_waiters()
    continued = claim(parent)
    assert view(parent)["preparation_required"]
    with pytest.raises(HTTPException, match='Preparation expired'):
        with db.connection() as conn:
            service.tool_action(conn, parent['bot_id'], continued['session_id'], 'finish', {'result': 'Actual report'}, 'continued-finish')
    assert row(parent['id'])['pending_result'] is None
    assert prepare(parent, continued, key='continued-prepare')['ready']
    with db.connection() as conn:
        result = service.tool_action(conn, parent['bot_id'], continued['session_id'], 'finish', {'result': 'Actual report'}, 'continued-finish')
    assert result['accepted']
    worker.settle(continued['id'], {'status': 'completed', 'output': 'Actual report'})
    assert row(parent['id'])['status'] == 'completed'


def test_blocked_plan_cannot_settle_as_completed_work(inventory, make_task):
    connection()
    with db.connection() as conn:
        conn.execute("UPDATE bots SET enabled_tools='[\"file\",\"skills\",\"terminal\"]' WHERE id='researcher'")
    task = make_task()
    attempt = claim(task)
    prepare(task, attempt, tools=["browser_navigate"])
    worker.settle(
        attempt["id"],
        {"status": "completed", "output": "I need an available local execution tool to do this."},
    )
    final = row(task["id"])
    assert final["status"] == "failed" and final["error"].startswith("Needs access:")
    with db.connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM messages WHERE task_id=%s AND role='assistant'", (task["id"],)
        ).fetchone()


def test_unprepared_work_cannot_claim_success(inventory, make_task):
    task = make_task()
    attempt = claim(task)
    worker.settle(attempt['id'], {'status': 'completed', 'output': 'Done without preparing'})
    assert row(task['id'])['status'] == 'failed'
    assert 'not prepared' in row(task['id'])['error']


def test_unrouted_response_cannot_claim_success(inventory, make_task):
    task = make_task()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET message_intent='auto' WHERE id=%s", (task['id'],))
    attempt = claim(task)
    worker.settle(attempt['id'], {'status': 'completed', 'output': 'Tools unavailable; could not classify'})
    assert row(task['id'])['status'] == 'failed'
    assert 'could not classify' in row(task['id'])['error']


def test_policy_change_before_finish_cannot_claim_success(inventory, make_task):
    task = make_task()
    attempt = claim(task)
    prepare(task, attempt, tools=['terminal'])
    connection()
    worker.settle(attempt['id'], {'status': 'completed', 'output': 'Old preparation'})
    assert row(task['id'])['status'] == 'failed'


def test_available_alternative_clears_blocker_and_finishes(inventory, make_task):
    connection()
    with db.connection() as conn:
        conn.execute("UPDATE bots SET enabled_tools='[\"file\",\"skills\",\"terminal\"]' WHERE id='researcher'")
    task = make_task()
    attempt = claim(task)
    assert not prepare(task, attempt, tools=["browser_navigate"])["ready"]
    assert prepare(task, attempt, key="alternative", tools=["read_file", "hermbot_publish"])["ready"]
    worker.settle(attempt["id"], {"status": "completed", "output": "Actual report using supplied data"})
    assert row(task["id"])["status"] == "completed"


def attachment(conn, conversation):
    identity = uuid4()
    conn.execute(
        "INSERT INTO attachments(id,conversation_id,name,content_type,size,sha256,object_key) VALUES(%s,%s,'input.csv','text/csv',4,%s,%s)",
        (identity, conversation, "a" * 64, str(identity)),
    )
    return identity


def test_handoff_shares_only_explicit_inputs_and_expected_output(inventory, make_task):
    parent = make_task()
    attempt = claim(parent)
    with db.connection() as conn:
        given = attachment(conn, parent["conversation_id"])
        private = attachment(conn, parent["conversation_id"])
        conn.execute(
            "UPDATE tasks SET attachment_ids=%s WHERE id=%s",
            (Jsonb([str(given), str(private)]), parent["id"]),
        )
        child = service.delegate(
            conn,
            service.locked_task(conn, parent["id"]),
            "writer",
            "Analyze the supplied rows",
            attachment_ids=[given],
            skill_refs=["hermbot-csv"],
            expected_output="Publish a CSV summary with row count and total.",
        )
    saved = row(UUID(child["task_id"]))
    assert saved["attachment_ids"] == [str(given)] and saved["skill_refs"] == ["hermbot-csv"]
    assert saved["handoff_context"]["expected_output"].startswith("Publish a CSV summary")
    assert str(private) not in json.dumps(saved["handoff_context"])
    worker.settle(attempt["id"], {"status": "completed", "output": "Waiting for report"})
    dispatched = next(a for a in worker.claim_new() if str(a["task_id"]) == child["task_id"])
    assert "/workspace/attachments/" + str(given) in dispatched["input"]
    assert "Publish a CSV summary" in dispatched["input"] and str(private) not in dispatched["input"]


def test_ungranted_same_conversation_attachment_cannot_be_handed_off(inventory, make_task):
    task = make_task()
    with db.connection() as conn:
        ungranted = attachment(conn, task["conversation_id"])
    with pytest.raises(HTTPException) as error:
        with db.connection() as conn:
            service.delegate(conn, task, "writer", "Read", attachment_ids=[ungranted])
    assert error.value.status_code == 403 and row(task["id"])["handoffs"] == 0


def test_missing_recipient_skill_creates_no_child_or_handoff_charge(inventory, make_task):
    task = make_task()
    attempt = claim(task)
    with db.connection() as conn:
        args = DelegateArgs(bot_id="writer", prompt="Summarize", skill_refs=["hermbot-summary"]).model_dump()
        first = service.tool_action(conn, "researcher", attempt["session_id"], "delegate", args, "handoff")
        assert (
            service.tool_action(conn, "researcher", attempt["session_id"], "delegate", args, "handoff")
            == first
        )
        assert not conn.execute("SELECT 1 FROM tasks WHERE parent_id=%s", (task["id"],)).fetchone()
    assert first["delivery"] == "blocked" and row(task["id"])["handoffs"] == 0


def test_schema_projection_preserves_canonical_registry_and_fails_closed(inventory, make_task, monkeypatch):
    filtering = load("schema_filter", "/app/docker/capability_filter.py")
    task = make_task()
    names = ["terminal", "read_file", "hermbot_publish", "hermbot_prepare", "hermbot_review"]
    schemas = [
        {
            "type": "function",
            "function": {
                "name": n,
                "parameters": {
                    "properties": {"kind": {"enum": ["review", "input", "connector"]}},
                    "required": [],
                },
            },
        }
        for n in names
    ]
    original = json.dumps(schemas)
    admission = {"capabilities": view(task)}
    subset = filtering.filter_tools(schemas, admission)
    assert {s["function"]["name"] for s in subset} == {"read_file", "hermbot_prepare", "hermbot_review"}
    assert next(s for s in subset if s["function"]["name"] == "hermbot_review")["function"]["parameters"][
        "properties"
    ]["kind"]["enum"] == ["input"]
    assert json.dumps(schemas) == original
    request = {'tools': schemas, 'messages': [{'role': 'user', 'content': 'Work'}],
               'tool_choice': {'type': 'function', 'function': {'name': 'terminal'}},
               'parallel_tool_calls': False}
    original_request = json.dumps(request)
    filtered = filtering.filter_request(request, admission)
    assert filtered['tools'] == subset and 'tool_choice' not in filtered
    assert filtered['messages'] == request['messages'] and filtered['parallel_tool_calls'] is False
    denied = filtering.filter_request(request, {'error': 'unavailable'})
    assert not {'tools', 'tool_choice', 'parallel_tool_calls'} & denied.keys()
    assert json.dumps(request) == original_request
    assert json.dumps(schemas) == original


def test_schema_plugin_admits_with_framework_identity_without_hook_context(inventory, make_task, monkeypatch):
    filtering = load('capability_filter', '/app/docker/capability_filter.py')
    monkeypatch.setitem(sys.modules, 'capability_filter', filtering)
    plugin = load('capability_schema_plugin', '/app/plugins/hermbot/__init__.py')
    task = make_task()
    seen = []

    def post(path, body):
        seen.append((path, body))
        return {'capabilities': view(task)} if path == '/internal/context' else {'recorded': True}

    monkeypatch.setattr(plugin, 'post', post)
    schemas = [{'function': {'name': name}} for name in ['terminal', 'hermbot_prepare', 'read_file']]
    result = plugin.llm_request({'tools': schemas}, task_id='stable', session_id='rotated')
    assert {s['function']['name'] for s in result['request']['tools']} == {'read_file', 'hermbot_prepare'}
    assert all(body['session_id'] == 'stable' for _, body in seen)
    assert seen[1][0] == '/internal/tool-visibility'
    assert 'tools' not in plugin.llm_request({'tools': schemas})['request']
    monkeypatch.setattr(plugin, 'post', lambda *a: {'error': 'admission unavailable'})
    assert 'tools' not in plugin.llm_request({'tools': schemas}, task_id='stable')['request']


def test_request_projection_supports_flat_tools_and_preserves_cache_metadata(monkeypatch):
    filtering = load('flat_schema_filter', '/app/docker/capability_filter.py')
    request = {'tools': [{'type': 'function', 'name': 'terminal', 'parameters': {}},
                         {'type': 'function', 'name': 'read_file', 'parameters': {},
                          'cache_control': {'type': 'ephemeral'}}],
               'input': [{'role': 'user', 'content': 'Inspect'}],
               'tool_choice': {'type': 'function', 'name': 'read_file'}}
    original = json.dumps(request)
    result = filtering.filter_request(request, {'capabilities': {'allowed_tools': ['read_file'],
                                                               'preparation_required': False}})
    assert result['tools'] == request['tools'][1:] and result['tool_choice'] == request['tool_choice']
    assert result['input'] == request['input'] and json.dumps(request) == original


def test_native_skill_consultation_uses_public_middleware_without_preprocessing(inventory, make_task, monkeypatch, tmp_path):
    plugin = load('raw_skill_plugin', '/app/plugins/hermbot/__init__.py')
    task = make_task()
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    desktop = ModuleType('tools.bot_desktop')
    desktop.lease = SimpleNamespace(human_holds=lambda: False)
    skills = ModuleType('tools.skills_tool')
    calls = []
    def raw(name, **kwargs):
        calls.append((name, kwargs))
        return '{"content":"!`echo DO_NOT_EXECUTE`"}'
    skills.skill_view = raw
    monkeypatch.setitem(sys.modules, 'tools', ModuleType('tools'))
    monkeypatch.setitem(sys.modules, 'tools.bot_desktop', desktop)
    monkeypatch.setitem(sys.modules, 'tools.skills_tool', skills)
    monkeypatch.setattr(plugin, 'post', lambda *a: {'capabilities': view(task)})
    result = plugin.execution_boundary('skill_view', {'name': 'hermbot-csv', 'file_path': 'references/data.md'},
                                       lambda args: pytest.fail('Default preprocessing wrapper must not run'),
                                       session_id='rotated', task_id='stable')
    assert 'DO_NOT_EXECUTE' in result
    assert calls == [('hermbot-csv', {'file_path': 'references/data.md', 'task_id': 'stable', 'preprocess': False})]


def test_new_empty_handoff_fields_replay_historical_receipt(inventory, make_task):
    task = make_task()
    attempt = claim(task)
    current = DelegateArgs(bot_id='writer', prompt='Summarize supplied facts').model_dump()
    historical = {k: v for k, v in current.items() if k not in ('attachment_ids', 'skill_refs', 'expected_output')}
    with db.connection() as conn:
        original = service.tool_action(conn, 'researcher', attempt['session_id'], 'delegate', historical, 'historical-handoff')
        assert service.tool_action(conn, 'researcher', attempt['session_id'], 'delegate', current, 'historical-handoff') == original
        assert len(conn.execute('SELECT id FROM tasks WHERE parent_id=%s', (task['id'],)).fetchall()) == 1


def test_native_boundary_checks_preparation_and_disabled_skill(inventory, make_task, monkeypatch, tmp_path):
    plugin = load("capability_plugin", "/app/plugins/hermbot/__init__.py")
    task = make_task()
    attempt = claim(task)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    desktop = ModuleType("tools.bot_desktop")
    desktop.lease = SimpleNamespace(human_holds=lambda: False)
    monkeypatch.setitem(sys.modules, "tools", ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.bot_desktop", desktop)
    monkeypatch.setattr(plugin, "post", lambda *args: {"capabilities": view(task)})
    called = []
    result = json.loads(
        plugin.execution_boundary(
            "terminal", {}, lambda args: called.append(args), session_id=attempt["session_id"]
        )
    )
    assert result["error"] == "execution_capability_unavailable" and not called
    result = json.loads(
        plugin.execution_boundary(
            "skill_view",
            {"name": "hermbot-summary"},
            lambda args: called.append(args),
            session_id=attempt["session_id"],
        )
    )
    assert result["error"] == "skill_not_enabled" and not called
    prepare(task, attempt)
    assert (
        plugin.execution_boundary(
            "terminal", {}, lambda args: "actual execution", session_id=attempt["session_id"]
        )
        == "actual execution"
    )


def test_owner_projection_requires_owner_auth_and_correct_task(inventory, make_task):
    task = make_task()
    client = TestClient(application.app)
    try:
        assert client.get("/api/v1/bots/researcher/capabilities").status_code == 401
        response = client.get(
            "/api/v1/bots/researcher/capabilities",
            headers={"Authorization": "Bearer " + settings.owner_token},
        )
        assert response.status_code == 200 and response.json()["inventory_known"]
        assert "secret_encrypted" not in response.text and "runtime_key" not in response.text
        assert (
            client.get(
                "/api/v1/bots/writer/capabilities?task_id=" + str(task["id"]),
                headers={"Authorization": "Bearer " + settings.owner_token},
            ).status_code
            == 404
        )

    finally:
        client.close()


def test_owner_skill_list_uses_adapter_without_patching_native_route(monkeypatch):
    calls = []
    def catalog(bot, method, path):
        calls.append((bot, method, path))
        return {'data': [{'name': 'installed-skill', 'description': 'Installed native procedure'}]}
    monkeypatch.setattr(native, 'request', catalog)
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    try:
        response = client.get('/api/v1/bots/researcher/skills')
        assert response.status_code == 200 and response.json()['data'][0]['name'] == 'installed-skill'
    finally:
        client.close()
    assert calls == [('researcher', 'GET', '/skills')]


@pytest.mark.parametrize('failure', ['offline', 'auth', 'missing'])
def test_native_history_reports_runtime_availability_and_preserves_recovery(make_task, monkeypatch, failure):
    task = make_task()
    attempt = claim(task)
    client = TestClient(application.app)
    path = f"/api/v1/conversations/{task['conversation_id']}/runtime-history/researcher"
    headers = {'Authorization': 'Bearer ' + settings.owner_token}

    async def unavailable(self, method, path):
        request = httpx.Request('GET', 'http://private-runtime/history')
        if failure == 'offline':
            raise httpx.ConnectError('Runtime restarting', request=request)
        response = httpx.Response(404 if failure == 'missing' else 401, request=request)
        raise httpx.HTTPStatusError('Private upstream details', request=request, response=response)

    async def recovered(self, method, path):
        assert attempt['session_id'] in path
        return {'data': [{'role': 'assistant', 'content': 'Saved native history'}]}

    try:
        monkeypatch.setattr(application.HermesClient, 'request', unavailable)
        response = client.get(path, headers=headers)
        assert response.status_code == (404 if failure == 'missing' else 503)
        assert 'Private upstream details' not in response.text
        if failure != 'missing':
            assert response.headers['Retry-After'] == '5'
        monkeypatch.setattr(application.HermesClient, 'request', recovered)
        response = client.get(path, headers=headers)
        assert response.status_code == 200 and response.json()['data'][0]['content'] == 'Saved native history'
    finally:
        client.close()
