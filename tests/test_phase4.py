"""Connector policy and external-effect durability against isolated PostgreSQL; provider effects are fakes."""

import base64
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
import pytest

from backend import app as application, connections, connector_catalog, connector_http, connector_mcp
from backend import connector_oauth, connector_providers, db, service, worker
from backend.secrets import encrypt, decrypt
from backend.settings import settings

pytestmark = pytest.mark.integration
SECRET = 'PRIVATE_CONNECTOR_CANARY_123456789'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(connector_oauth, 'revoke_remote', lambda *a: False)
    value = TestClient(application.app, base_url='https://testserver', headers={'Authorization': 'Bearer ' + settings.owner_token})
    yield value
    value.close()


def connection(provider='github', bots=None, actions=None, resources=None, capabilities=None):
    identity = uuid4()
    with db.connection() as conn:
        conn.execute("INSERT INTO connections(id,provider,name,status,secret_encrypted,bot_ids,actions,resources,capabilities) VALUES(%s,%s,'Test connection','connected',%s,%s,%s,%s,%s)",
                     (identity, provider, encrypt(json.dumps({'access_token': SECRET})), Jsonb(bots or ['researcher']), Jsonb(actions or [a['name'] for a in connector_catalog.catalog(provider)]), Jsonb(resources or {}), Jsonb(capabilities or [])))
        return connections.require(conn, identity)


def task_row(task):
    with db.connection() as conn:
        return service.locked_task(conn, task['id'])


def proposal(task, connection, action='github.create_issue', args=None):
    with db.connection() as conn:
        result = connections.invoke(conn, service.locked_task(conn, task['id']), {'connection_id': connection['id'], 'action': action, 'arguments': args or {'repo': 'owner/repo', 'title': 'Reviewed title', 'body': 'Reviewed body'}, 'summary': 'Exact external change'})
        if result.get('approval_id'):
            return conn.execute('SELECT * FROM approvals WHERE id=%s', (result['approval_id'],)).fetchone()
        return result


def decide(client, review, decision='once'):
    return client.post('/api/v1/approvals/' + str(review['id']), json={'decision': decision, 'expected_payload_hash': review['payload_hash']})


def operations():
    with db.connection() as conn:
        return conn.execute('SELECT * FROM connector_operations ORDER BY created_at').fetchall()


def effect(monkeypatch, *, error=None):
    calls = []
    def execute(c, secret, action, args, identity):
        assert secret['access_token'] == SECRET
        calls.append((action, args, identity))
        if error:
            raise error
        return {'id': 42, 'html_url': 'https://github.com/owner/repo/issues/42'}
    monkeypatch.setattr(connector_providers, 'execute', execute)
    return calls


def test_connections_owner_only_and_secret_projection(client):
    conn = connection()
    response = client.get('/api/v1/connections')
    assert response.status_code == 200 and SECRET not in response.text and 'secret_encrypted' not in response.text
    assert TestClient(application.app).get('/api/v1/connections').status_code == 401
    assert client.post(f"/api/v1/connections/{conn['id']}/token", json={'token': SECRET + 'new', 'expected_revision': 999}).status_code == 409
    assert client.post(f"/api/v1/connections/{conn['id']}/token", json={'token': SECRET + 'new', 'expected_revision': 1}).status_code == 200
    with db.connection() as sql:
        assert SECRET + 'new' not in connections.require(sql, conn['id'])['secret_encrypted']


def test_validation_errors_do_not_echo_private_form_inputs(client):
    c = connection()
    response = client.post(f"/api/v1/connections/{c['id']}/token", json={'token': SECRET * 500, 'expected_revision': 1})
    assert response.status_code == 422 and SECRET not in response.text
    response = client.put('/api/v1/connector-apps/google', json={'client_id': 'test', 'client_secret': SECRET * 500})
    assert response.status_code == 422 and SECRET not in response.text


def test_manager_does_not_inherit_peer_connection(make_task):
    c = connection(bots=['writer'])
    task = make_task(manager=True)
    with db.connection() as conn:
        assert connections.accessible(conn, 'researcher') == []
        with pytest.raises(HTTPException) as denied:
            connections.invoke(conn, service.locked_task(conn, task['id']), {'connection_id': c['id'], 'action': 'github.repository', 'arguments': {'repo': 'owner/repo'}, 'summary': ''})
        assert denied.value.status_code == 403


def test_connections_do_not_restrict_delegation_or_historical_task_latches(make_task):
    c = connection(bots=['writer'])
    manager = make_task(manager=True)
    with db.connection() as conn:
        parent = service.locked_task(conn, manager['id'])
        assert not connections.native_policy(conn, 'researcher', parent)['restricted']
        child_id = service.delegate(conn, parent, 'writer', 'Use your permitted connector')['task_id']
        child = service.locked_task(conn, child_id)
        assert not connections.native_policy(conn, 'writer', child)['restricted']
        assert not connections.native_policy(conn, 'researcher', parent)['restricted']
        assert not parent['connector_restricted']
        helper_id = service.delegate(conn, parent, 'reviewer', 'Help with the result')['task_id']
        helper = service.locked_task(conn, helper_id)
        assert not connections.native_policy(conn, 'reviewer', helper)['restricted']
        assert connections.accessible(conn, 'reviewer') == []
        conn.execute('UPDATE tasks SET connector_restricted=true WHERE id=%s', (parent['id'],))
        conn.execute("UPDATE connections SET bot_ids='[]' WHERE id=%s", (c['id'],))
        assert not connections.native_policy(conn, 'reviewer', helper)['restricted']
        assert not connections.native_policy(conn, 'reviewer')['restricted']


def test_denied_action_never_executes(client, make_task, monkeypatch):
    c = connection()
    calls = effect(monkeypatch)
    review = proposal(make_task(), c)
    assert decide(client, review, 'deny').status_code == 200
    assert connections.drive_one() is False and calls == []
    assert operations()[0]['status'] == 'denied'


def test_exact_approval_executes_once_and_resumes(client, make_task, monkeypatch):
    c, task = connection(), make_task()
    calls = effect(monkeypatch)
    review = proposal(task, c)
    assert decide(client, review).status_code == 200
    assert connections.drive_one() is True
    assert connections.drive_one() is False
    assert len(calls) == 1 and calls[0][1]['title'] == 'Reviewed title'
    assert operations()[0]['status'] == 'succeeded'
    assert task_row(task)['status'] == 'queued' and 'Definitive connector receipt' in task_row(task)['continuation']


def test_edit_invalidates_prior_approval(client, make_task, monkeypatch):
    c, task = connection(), make_task()
    calls = effect(monkeypatch)
    original = proposal(task, c)
    action = {**original['payload']['action'], 'arguments': {**original['payload']['action']['arguments'], 'title': 'Owner edited title'}}
    response = client.put('/api/v1/approvals/' + str(original['id']), headers={'Idempotency-Key': str(uuid4())}, json={'expected_payload_hash': original['payload_hash'], 'summary': 'Edited external change', 'payload': action})
    assert response.status_code == 200
    assert decide(client, original).status_code == 409
    with db.connection() as conn:
        replacement = conn.execute('SELECT * FROM approvals WHERE id=%s', (response.json()['approval_id'],)).fetchone()
    assert decide(client, replacement).status_code == 200
    connections.drive_one()
    assert len(calls) == 1 and calls[0][1]['title'] == 'Owner edited title'
    assert operations()[0]['status'] == 'superseded'


@pytest.mark.parametrize('change', ['revision', 'revoke', 'expiry', 'cancel', 'payload'])
def test_dispatch_rechecks_frozen_authority(client, make_task, monkeypatch, change):
    c, task = connection(), make_task()
    calls = effect(monkeypatch)
    review = proposal(task, c)
    assert decide(client, review).status_code == 200
    with db.connection() as conn:
        if change == 'revision':
            conn.execute('UPDATE connections SET revision=revision+1 WHERE id=%s', (c['id'],))
        elif change == 'revoke':
            conn.execute("UPDATE connections SET status='revoked' WHERE id=%s", (c['id'],))
        elif change == 'expiry':
            conn.execute("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=%s", (review['id'],))
        elif change == 'cancel':
            service.cancel(conn, service.locked_task(conn, task['id']), 'Test cancellation')
        else:
            conn.execute("UPDATE connector_operations SET arguments=arguments||'{\"title\":\"Changed\"}'::jsonb")
    connections.drive_one()
    assert calls == [] and operations()[0]['status'] in ('failed', 'cancelled')


def test_uncertain_effect_not_replayed_and_owner_reconciles(client, make_task, monkeypatch):
    c, task = connection(), make_task()
    calls = effect(monkeypatch, error=connector_http.ProviderFailure('Lost delivery response', uncertain=True))
    review = proposal(task, c)
    decide(client, review)
    connections.drive_one()
    assert operations()[0]['status'] == 'uncertain' and task_row(task)['status'] == 'waiting_approval'
    connections.drive_one()
    assert len(calls) == 1
    identity = operations()[0]['id']
    response = client.post(f'/api/v1/connector-operations/{identity}/reconcile', json={'outcome': 'succeeded', 'evidence': 'Provider issue 42 exists with the exact reviewed title'})
    assert response.status_code == 200 and not response.json()['replayed']
    assert task_row(task)['status'] == 'queued'


def test_dispatch_crash_marks_uncertain_without_new_effect(client, make_task, monkeypatch):
    c = connection()
    calls = effect(monkeypatch)
    review = proposal(make_task(), c)
    decide(client, review)
    with db.connection() as conn:
        conn.execute("UPDATE connector_operations SET status='dispatching',lease_until=now()-interval '1 second'")
    connections.drive_one()
    assert operations()[0]['status'] == 'uncertain' and calls == []


def test_permission_resource_expiry_revocation_and_budget(client, make_task, monkeypatch):
    c = connection(resources={'github.create_issue': ['owner/repo']})
    calls = effect(monkeypatch)
    response = client.post('/api/v1/connector-permissions', json={'connection_id': str(c['id']), 'bot_id': 'researcher', 'action': 'github.create_issue', 'resource': 'owner/repo', 'max_uses': 1, 'duration_hours': 1})
    assert response.status_code == 201
    first = proposal(make_task(), c)
    assert first['status'] == 'queued' and 'approval_id' not in first
    connections.drive_one()
    second = proposal(make_task(), c)
    assert second['kind'] == 'connector'  # Exhausted permission falls back to owner review.
    assert len(calls) == 1
    assert client.post('/api/v1/connector-permissions/' + response.json()['id'] + '/revoke').status_code == 200
    assert proposal(make_task(), c)['kind'] == 'connector'
    with pytest.raises(HTTPException):
        proposal(make_task(), c, args={'repo': 'other/repo', 'title': 'Outside policy', 'body': ''})


@pytest.mark.parametrize('mode', ['revoked', 'expired', 'exhausted'])
def test_queued_standing_action_rechecks_permission(client, make_task, monkeypatch, mode):
    c = connection()
    calls = effect(monkeypatch)
    permission = client.post('/api/v1/connector-permissions', json={'connection_id': str(c['id']), 'bot_id': 'researcher', 'action': 'github.create_issue', 'resource': 'owner/repo'}).json()
    proposal(make_task(), c)
    with db.connection() as conn:
        statement = {'revoked': 'revoked_at=now()', 'expired': "expires_at=now()-interval '1 second'", 'exhausted': 'used=max_uses'}[mode]
        conn.execute('UPDATE connector_permissions SET ' + statement + ' WHERE id=%s', (permission['id'],))
    connections.drive_one()
    assert calls == [] and operations()[0]['status'] == 'failed'


def test_provider_auth_failure_marks_reconnect(make_task, monkeypatch):
    c, task = connection(), make_task()
    effect(monkeypatch, error=connector_http.ProviderFailure('Expired token', auth=True))
    with db.connection() as conn:
        result = connections.invoke(conn, service.locked_task(conn, task['id']), {'connection_id': c['id'], 'action': 'github.repository', 'arguments': {'repo': 'owner/repo'}, 'summary': ''})
        assert result['needs_reconnect']
    with db.connection() as conn:
        assert connections.require(conn, c['id'])['status'] == 'needs_reconnect'


def test_slack_read_permission_failure_preserves_other_actions(make_task, monkeypatch):
    c, task = connection(provider='slack'), make_task()
    calls = effect(monkeypatch, error=connector_http.ProviderFailure(connector_http.SLACK_RECOVERY['missing_scope']))
    with db.connection() as conn:
        result = connections.invoke(conn, service.locked_task(conn, task['id']),
                                    {'connection_id': c['id'], 'action': 'slack.history',
                                     'arguments': {'channel': 'D123'}})
        assert 'missing_scope' in result['error'] and not result['needs_reconnect']
        assert connections.require(conn, c['id'])['status'] == 'connected'
        assert any(account['id'] == str(c['id']) for account in connections.accessible(conn, task['bot_id']))
    review = proposal(task, c, action='slack.post', args={'channel': 'U123', 'text': 'Exact draft'})
    assert review['status'] == 'pending' and len(calls) == 1
    assert calls[0][0] == 'slack.history' and operations()[0]['status'] == 'pending'


@pytest.mark.parametrize('provider', ['google', 'slack', 'github'])
def test_oauth_binding_one_use_pkce_and_secret_storage(client, monkeypatch, provider):
    if provider == 'slack':
        monkeypatch.setenv('PUBLIC_URL', 'https://hermbot.example')
    c = connection(provider)
    assert client.put('/api/v1/connector-apps/' + provider, json={'client_id': 'test-client', 'client_secret': SECRET}).status_code == 200
    url = client.post(f"/api/v1/connections/{c['id']}/authorize").json()['authorization_url']
    params = parse_qs(urlsplit(url).query)
    assert params['redirect_uri'][0].endswith('/' + provider + '/callback')
    if provider != 'slack':
        assert params['code_challenge_method'] == ['S256']
    def remote(method, url, **kwargs):
        if 'profile' in url:
            return {'emailAddress': 'test@example.com'}
        if url.endswith('/user'):
            return {'login': 'test-owner'}
        return {'access_token': SECRET, 'refresh_token': SECRET + '-refresh', 'expires_in': 3600, 'team': {'name': 'Test team'}}
    monkeypatch.setattr(connector_oauth, 'request', remote)
    callback = '/api/v1/connections/oauth/' + provider + '/callback?state=' + params['state'][0] + '&code=test-code'
    assert TestClient(application.app).get(callback).status_code == 400
    good = client.get(callback)
    assert good.status_code == 200 and SECRET not in good.text
    assert client.get(callback).status_code == 400
    with db.connection() as conn:
        saved = connections.require(conn, c['id'])
        assert saved['status'] == 'connected' and saved['account']
        assert SECRET not in saved['secret_encrypted']
        assert json.loads(decrypt(saved['secret_encrypted']))['refresh_token'] == SECRET + '-refresh'


def test_oauth_refresh_rotates_privately_without_changing_policy(monkeypatch):
    c = connection('google')
    with db.connection() as conn:
        conn.execute('INSERT INTO connector_apps VALUES(%s,%s,%s,1)', ('google', 'test-client', encrypt(SECRET)))
        token = {'access_token': 'expired', 'refresh_token': 'refresh-old', 'expires_at': (service.now()-timedelta(seconds=1)).isoformat()}
        conn.execute('UPDATE connections SET secret_encrypted=%s WHERE id=%s', (encrypt(json.dumps(token)), c['id']))
    monkeypatch.setattr(connector_oauth, 'request', lambda *a, **k: {'access_token': SECRET, 'refresh_token': 'refresh-new', 'expires_in': 3600})
    with db.connection() as conn:
        refreshed = connector_oauth.credential(conn, connections.require(conn, c['id']))
        assert refreshed['access_token'] == SECRET and refreshed['refresh_token'] == 'refresh-new'
        assert connections.require(conn, c['id'])['revision'] == 1


def test_revoked_owner_session_cannot_finish_oauth(client):
    c = connection('github')
    client.put('/api/v1/connector-apps/github', json={'client_id': 'test-client', 'client_secret': SECRET})
    assert client.post('/api/v1/session').status_code == 200
    url = client.post(f"/api/v1/connections/{c['id']}/authorize").json()['authorization_url']
    state = parse_qs(urlsplit(url).query)['state'][0]
    client.delete('/api/v1/session')
    assert client.get('/api/v1/connections/oauth/github/callback?state='+state+'&code=test').status_code == 401


def test_connected_and_revoked_policies_leave_native_execution_available(client, monkeypatch, tmp_path):
    c = connection()
    with db.connection() as conn:
        assert not connections.native_policy(conn, 'researcher')['restricted']
        assert not connections.native_policy(conn, 'writer')['restricted']
    client.post(f"/api/v1/connections/{c['id']}/revoke")
    with db.connection() as conn:
        assert not connections.native_policy(conn, 'researcher')['restricted']
        assert connections.accessible(conn, 'researcher') == []
    spec = importlib.util.spec_from_file_location('phase4_plugin', Path('/app/plugins/hermbot/__init__.py'))
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    desktop = ModuleType('tools.bot_desktop')
    desktop.lease = SimpleNamespace(human_holds=lambda: False)
    monkeypatch.setitem(sys.modules, 'tools', ModuleType('tools'))
    monkeypatch.setitem(sys.modules, 'tools.bot_desktop', desktop)
    # Even stale client-side restriction data cannot reintroduce the removed ban.
    monkeypatch.setattr(plugin, 'post', lambda *a: {'connector_policy': {'restricted': True}})
    calls = []
    for name in ('terminal', 'browser_navigate', 'write_file', 'patch', 'execute_code', 'cronjob', 'delegate_task'):
        assert plugin.execution_boundary(name, {}, lambda args: calls.append(name) or 'executed', session_id='admitted-task') == 'executed'
    assert calls == ['terminal', 'browser_navigate', 'write_file', 'patch', 'execute_code', 'cronjob', 'delegate_task']
    assert plugin.execution_boundary('read_file', {}, lambda args: 'safe read', session_id='admitted-task') == 'safe read'


def test_native_review_schema_exposes_every_backend_review_kind():
    from typing import get_args
    from backend.models import ReviewArgs
    spec = importlib.util.spec_from_file_location('phase4_review_plugin', Path('/app/plugins/hermbot/__init__.py'))
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    assert set(plugin.TOOLS['review'][1]['kind']['enum']) == set(get_args(ReviewArgs.model_fields['kind'].annotation))


def test_connector_payload_cannot_be_disguised_as_generic_review(make_task):
    task = make_task()
    c = connection('google')
    with db.connection() as conn:
        with pytest.raises(HTTPException) as error:
            service.request_review(conn, task, 'Post to selected channel', {'connection_id': str(c['id']), 'action': 'gmail.send', 'arguments': {'to': 'test@example.com', 'subject': 'Test', 'body': 'Draft'}})
        assert error.value.status_code == 422 and 'kind connector' in error.value.detail
        assert not conn.execute('SELECT 1 FROM approvals WHERE task_id=%s', (task['id'],)).fetchone()
        assert not conn.execute('SELECT 1 FROM connector_operations WHERE task_id=%s', (task['id'],)).fetchone()


def test_mcp_endpoint_change_discards_credentials_and_discovery(client, monkeypatch):
    c = connection('mcp', capabilities=[{'name': 'find', 'inputSchema': {'type': 'object'}}])
    monkeypatch.setattr(connector_mcp, 'validate_endpoint', lambda url: url)
    body = {'provider': 'mcp', 'name': 'Moved MCP', 'bot_ids': [], 'actions': [], 'resources': {}, 'endpoint': 'https://new.example/mcp', 'expected_revision': 1}
    response = client.put(f"/api/v1/connections/{c['id']}", headers={'Idempotency-Key': str(uuid4())}, json=body)
    assert response.status_code == 200 and response.json()['status'] == 'disconnected'
    with db.connection() as conn:
        row = connections.require(conn, c['id'])
        assert row['secret_encrypted'] is None and row['capabilities'] == []


def test_detaching_busy_bot_requires_work_to_finish(client, make_task):
    c = connection()
    make_task()
    body = {'provider': 'github', 'name': 'Detached', 'bot_ids': [], 'actions': [], 'resources': {}, 'expected_revision': 1}
    assert client.put(f"/api/v1/connections/{c['id']}", headers={'Idempotency-Key': str(uuid4())}, json=body).status_code == 409


@pytest.mark.parametrize('provider', ['google', 'slack', 'github', 'mcp'])
def test_provider_revocation_is_best_effort_and_no_secrets_returned(client, monkeypatch, provider):
    c = connection(provider)
    with db.connection() as conn:
        conn.execute('INSERT INTO connector_apps VALUES(%s,%s,%s,1)', ('github', 'test-client', encrypt(SECRET)))
        conn.execute('UPDATE connections SET secret_encrypted=%s WHERE id=%s', (encrypt(json.dumps({'access_token': SECRET, 'oauth': True, 'search_token': SECRET + 'search'})), c['id']))
    monkeypatch.undo()
    calls = []
    monkeypatch.setattr(connector_oauth, 'request', lambda method, url, **kwargs: calls.append((method, url, kwargs)) or {'revoked': True})
    response = client.post(f"/api/v1/connections/{c['id']}/revoke")
    assert response.status_code == 200 and SECRET not in response.text
    assert response.json()['provider_revoked'] == (provider != 'mcp')
    assert len(calls) == {'google': 1, 'slack': 2, 'github': 1, 'mcp': 0}[provider]
    with db.connection() as conn:
        assert connections.require(conn, c['id'])['secret_encrypted'] is None


def test_failed_provider_revocation_still_commits_local_revocation(client, monkeypatch):
    c = connection()
    monkeypatch.setattr(connector_oauth, 'revoke_remote', lambda *a: False)
    assert client.post(f"/api/v1/connections/{c['id']}/revoke").json()['revoked']
    with db.connection() as conn:
        assert connections.require(conn, c['id'])['status'] == 'revoked'


def test_refresh_invalid_grant_requires_reconnect(make_task, monkeypatch):
    c, task = connection('google'), make_task()
    with db.connection() as conn:
        conn.execute('INSERT INTO connector_apps VALUES(%s,%s,%s,1)', ('google', 'test-client', encrypt(SECRET)))
        conn.execute('UPDATE connections SET secret_encrypted=%s WHERE id=%s', (encrypt(json.dumps({'access_token': 'expired', 'refresh_token': SECRET, 'expires_at': (service.now()-timedelta(seconds=1)).isoformat()})), c['id']))
    def reject(*a, **k):
        raise connector_http.ProviderFailure('HTTP 400', status=400)
    monkeypatch.setattr(connector_oauth, 'request', reject)
    with db.connection() as conn:
        result = connections.invoke(conn, service.locked_task(conn, task['id']), {'connection_id': c['id'], 'action': 'gmail.search', 'arguments': {'query': 'test'}, 'summary': ''})
        assert result['needs_reconnect']
        assert connections.require(conn, c['id'])['status'] == 'needs_reconnect'


@pytest.mark.parametrize('schema', [{'$ref': '#'}, {'type': 'string', 'pattern': '(a+)+$'}, {'anyOf': [{'anyOf': [{}] * 20}] * 20}])
def test_mcp_rejects_unbounded_validation(schema):
    with pytest.raises(HTTPException):
        connector_mcp.check_schema(schema)


def test_provider_effect_followed_by_transaction_loss_is_not_replayed(client, make_task, monkeypatch):
    c = connection()
    calls = effect(monkeypatch)
    decide(client, proposal(make_task(), c))
    original = connections.finish_operation
    def lose_receipt(*a, **k):
        raise RuntimeError('Simulated lost database transaction')
    monkeypatch.setattr(connections, 'finish_operation', lose_receipt)
    with pytest.raises(RuntimeError):
        connections.drive_one()
    assert len(calls) == 1 and operations()[0]['status'] == 'dispatching'
    monkeypatch.setattr(connections, 'finish_operation', original)
    with db.connection() as conn:
        conn.execute("UPDATE connector_operations SET lease_until=now()-interval '1 second'")
    connections.drive_one()
    assert operations()[0]['status'] == 'uncertain' and len(calls) == 1


def test_lost_permission_usage_commit_cannot_overspend(client, make_task, monkeypatch):
    c = connection()
    calls = effect(monkeypatch)
    client.post('/api/v1/connector-permissions', json={'connection_id': str(c['id']), 'bot_id': 'researcher', 'action': 'github.create_issue', 'resource': 'owner/repo', 'max_uses': 1}).raise_for_status()
    proposal(make_task(), c)
    original = connections.finish_operation
    def lose_commit(*a, **k):
        raise RuntimeError('Simulated lost usage counter commit')
    monkeypatch.setattr(connections, 'finish_operation', lose_commit)
    with pytest.raises(RuntimeError):
        connections.drive_one()
    monkeypatch.setattr(connections, 'finish_operation', original)
    with db.connection() as conn:
        assert conn.execute('SELECT used FROM connector_permissions').fetchone()['used'] == 0
    proposal(make_task(), c)
    connections.drive_one()
    assert len(calls) == 1 and operations()[-1]['status'] == 'failed'


def test_different_app_access_follows_explicit_handoff(make_task, monkeypatch):
    google = connection('google', bots=['researcher'])
    slack = connection('slack', bots=['writer'])
    manager = make_task(manager=True)
    calls = []
    monkeypatch.setattr(connector_providers, 'execute', lambda c, token, action, args, identity: calls.append((c['provider'], action)) or {'content': 'Application evidence'})
    with db.connection() as conn:
        parent = service.locked_task(conn, manager['id'])
        child = service.delegate(conn, parent, 'writer', 'Read your own Slack connection and return evidence')['task_id']
        connections.invoke(conn, parent, {'connection_id': google['id'], 'action': 'gmail.search', 'arguments': {'query': 'test'}, 'summary': ''})
        result = connections.invoke(conn, service.locked_task(conn, child), {'connection_id': slack['id'], 'action': 'slack.search', 'arguments': {'query': 'test'}, 'summary': ''})
        assert result['result']['content'] == 'Application evidence'
        assert [c['provider'] for c in connections.accessible(conn, 'researcher')] == ['google']
        assert [c['provider'] for c in connections.accessible(conn, 'writer')] == ['slack']
    with db.connection() as conn:
        with pytest.raises(HTTPException):
            connections.invoke(conn, service.locked_task(conn, manager['id']), {'connection_id': slack['id'], 'action': 'slack.search', 'arguments': {'query': 'test'}, 'summary': ''})
    assert calls == [('google', 'gmail.search'), ('slack', 'slack.search')]


def test_slack_local_tokens_keep_bot_and_search_credentials_private(client, monkeypatch):
    c = connection('slack')
    response = client.post(f"/api/v1/connections/{c['id']}/token", json={'token': SECRET, 'search_token': SECRET + '-user', 'expected_revision': 1})
    assert response.status_code == 200 and SECRET not in response.text
    with db.connection() as conn:
        row = connections.require(conn, c['id'])
        assert connector_oauth.credential(conn, row)['access_token'] == SECRET
        assert connector_oauth.credential(conn, row, search=True)['access_token'] == SECRET + '-user'
    monkeypatch.setenv('PUBLIC_URL', 'http://localhost:8780')
    assert client.post(f"/api/v1/connections/{c['id']}/authorize").status_code == 409


def test_public_mcp_can_connect_without_token(client):
    c = connection('mcp')
    assert client.post(f"/api/v1/connections/{c['id']}/token", json={'token': '', 'expected_revision': 1}).status_code == 200


def test_mcp_pattern_property_is_data_and_sse_receipts_are_correlated(monkeypatch):
    schema = {'type': 'object', 'properties': {'pattern': {'type': 'string'}}}
    connector_mcp.validate_arguments(schema, {'pattern': 'search words'})
    with pytest.raises(HTTPException):
        connector_mcp.validate_arguments({'type': 'object', 'properties': {'pattern': {'type': 'string', 'pattern': '(a+)+$'}}}, {})
    session = object.__new__(connector_mcp.Session)
    session.endpoint, session.credential, session.headers = 'https://public.example/mcp', {'access_token': SECRET}, {}
    def response(*a, **kw):
        identity = kw['json']['id']
        payload = {'jsonrpc': '2.0', 'id': identity, 'result': {'content': [{'text': 'actual receipt'}]}}
        body = 'data: ' + json.dumps({'jsonrpc': '2.0', 'method': 'notifications/progress'}) + '\n\ndata: ' + json.dumps(payload) + '\n\n'
        return body.encode(), {'content-type': 'text/event-stream', 'mcp-session-id': 'session-1'}
    monkeypatch.setattr(connector_mcp, 'request', response)
    assert session.rpc('tools/call', {}, mutation=True)['content'][0]['text'] == 'actual receipt'
    assert session.headers['MCP-Session-Id'] == 'session-1'
    monkeypatch.setattr(connector_mcp, 'request', lambda *a, **kw: (b'[]', {}))
    with pytest.raises(connector_http.ProviderFailure) as error:
        session.rpc('tools/call', {}, mutation=True)
    assert error.value.uncertain


def test_stale_recovery_waiting_for_dispatch_does_not_overwrite_success(client, make_task, monkeypatch):
    c = connection()
    decide(client, proposal(make_task(), c))
    recovery_started = threading.Event()
    original_lock = service.locked_task
    def lock(conn, identity):
        if threading.current_thread().name.startswith('recovery'):
            recovery_started.set()
        return original_lock(conn, identity)
    monkeypatch.setattr(service, 'locked_task', lock)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='recovery') as executor:
        futures = []
        def execute(c, credential, action, args, identity):
            with db.connection() as conn:
                conn.execute("UPDATE connector_operations SET lease_until=now()-interval '1 second' WHERE id=%s", (identity,))
            futures.append(executor.submit(connections.drive_one))
            assert recovery_started.wait(timeout=3)
            return {'id': 42}
        monkeypatch.setattr(connector_providers, 'execute', execute)
        assert connections.drive_one()
        futures[0].result(timeout=5)
    assert operations()[0]['status'] == 'succeeded'


@pytest.mark.parametrize('status', ['pending', 'queued', 'dispatching', 'uncertain', 'unresolved', 'succeeded'])
def test_task_connector_state_exposes_action_waits_and_recovery(client, make_task, status):
    task = make_task()
    review = proposal(task, connection())
    with db.connection() as conn:
        conn.execute('UPDATE connector_operations SET status=%s WHERE approval_id=%s', (status, review['id']))
    expected = None if status == 'succeeded' else status
    item = client.get('/api/v1/tasks/' + str(task['id'])).json()
    assert item['connector_state'] == expected
    projected = next(t for t in client.get('/api/v1/state').json()['tasks'] if t['id'] == str(task['id']))
    assert projected['connector_state'] == expected


def test_mcp_discovery_ignores_readonly_hint_and_validates_schema(monkeypatch):
    monkeypatch.setattr(connector_mcp, 'validate_endpoint', lambda url: url)
    methods = []
    def rpc(method, url, **kwargs):
        if method == 'DELETE':
            return {}
        payload = kwargs.get('json', {})
        methods.append(payload.get('method'))
        if payload['method'] == 'initialize':
            result = {'protocolVersion': connector_mcp.VERSION}
        elif payload['method'] == 'tools/list':
            result = {'tools': [{'name': 'find', 'annotations': {'readOnlyHint': True}, 'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query'], 'additionalProperties': False}}]}
        elif payload['method'] == 'tools/call':
            result = {'content': [{'type': 'text', 'text': 'Found ' + SECRET}]}
        else:
            return b'', {}
        return json.dumps({'jsonrpc': '2.0', 'id': payload['id'], 'result': result}).encode(), {'mcp-session-id': 'test-session'}
    monkeypatch.setattr(connector_mcp, 'request', rpc)
    c = {'endpoint': 'https://public.example/mcp'}
    tools = connector_mcp.discover(c, {'access_token': SECRET})
    assert tools[0]['mutation'] is True and methods[:3] == ['initialize', 'notifications/initialized', 'tools/list']
    with pytest.raises(HTTPException):
        connector_mcp.validate_arguments(tools[0]['inputSchema'], {'query': 42})
    result = connector_mcp.execute(c, {'access_token': SECRET}, 'mcp.find', {'query': 'test'})
    assert SECRET not in json.dumps(result)
    with pytest.raises(HTTPException):
        connector_mcp.check_schema({'$ref': 'http://localhost/private'})


@pytest.mark.parametrize('action,args', [
    ('gmail.search', {'query': 'test'}), ('gmail.read', {'message_id': 'a/b'}),
    ('gmail.send', {'to': 'test@example.com', 'subject': 'Draft', 'body': 'Reviewed content'}),
    ('calendar.list', {}), ('calendar.availability', {'calendar_id': 'primary', 'start': '2026-10-10T09:00:00Z', 'end': '2026-10-10T10:00:00Z'}),
    ('calendar.events', {'calendar_id': 'primary', 'start': '2026-10-10T09:00:00Z', 'end': '2026-10-10T10:00:00Z'}),
    ('calendar.create', {'calendar_id': 'primary', 'summary': 'Reviewed event', 'start': '2026-10-10T09:00:00Z', 'end': '2026-10-10T10:00:00Z'}),
    ('calendar.update', {'calendar_id': 'primary', 'event_id': 'id', 'summary': 'Reviewed edit', 'start': '2026-10-10T09:00:00Z', 'end': '2026-10-10T10:00:00Z'}),
    ('calendar.delete', {'calendar_id': 'primary', 'event_id': 'id'}),
    ('drive.search', {'query': "name contains 'report'"}), ('drive.read', {'file_id': 'id'}), ('drive.import', {'file_id': 'id'}),
    ('drive.create', {'name': 'Reviewed doc', 'content': 'Text', 'folder_id': 'root', 'document': True}),
    ('slack.search', {'query': 'test'}), ('slack.channels', {}), ('slack.history', {'channel': 'C123'}), ('slack.post', {'channel': 'C123', 'text': 'Reviewed message'}),
    ('github.repositories', {}), ('github.repository', {'repo': 'owner/repo'}), ('github.issues', {'repo': 'owner/repo'}),
    ('github.issue', {'repo': 'owner/repo', 'number': 1}), ('github.pulls', {'repo': 'owner/repo'}), ('github.pull', {'repo': 'owner/repo', 'number': 1}),
    ('github.file', {'repo': 'owner/repo', 'path': 'file.txt'}), ('github.create_issue', {'repo': 'owner/repo', 'title': 'Title', 'body': 'Body'}),
    ('github.comment', {'repo': 'owner/repo', 'number': 1, 'body': 'Body'}), ('github.update_issue', {'repo': 'owner/repo', 'number': 1, 'title': 'Title', 'body': 'Body'}),
    ('github.create_pull', {'repo': 'owner/repo', 'title': 'Title', 'body': 'Body', 'head': 'branch', 'base': 'main'}),
    ('github.update_file', {'repo': 'owner/repo', 'path': 'file.txt', 'content': 'Text', 'message': 'Reviewed change', 'branch': 'branch'}),
])
def test_adapter_routes_are_fixed_and_writes_marked_uncertain(monkeypatch, action, args):
    provider = connector_catalog.CATALOG[action][0]
    c = {'provider': provider}
    args, mutation, _ = connector_catalog.validate(c, action, args)
    calls = []
    def remote(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/profile'):
            return {'emailAddress': 'sender@example.com'}
        if url.endswith('/files/id') and not kwargs.get('raw'):
            return {'mimeType': 'text/plain', 'name': 'test.txt'}
        if kwargs.get('raw'):
            return b'Imported file contents', {}
        return {'ok': True, 'id': 42, 'canary': SECRET}
    monkeypatch.setattr(connector_providers, 'request', remote)
    result = connector_providers.execute(c, {'access_token': SECRET}, action, args, uuid4())
    assert calls and SECRET not in json.dumps(result)
    assert all(url.startswith(('https://gmail.googleapis.com/', 'https://www.googleapis.com/', 'https://slack.com/api/', 'https://api.github.com/')) for _, url, _ in calls)
    assert calls[-1][2]['mutation'] == mutation
    if action == 'gmail.send':
        raw = base64.urlsafe_b64decode(calls[-1][2]['json']['raw']).decode()
        assert 'To: test@example.com' in raw and 'Message-ID: <hermbot-' in raw and 'From: sender@example.com' in raw


def test_malformed_arguments_reject_url_header_and_path_injection():
    for action, args in [('gmail.send', {'to': 'test@example.com\nBcc: other@example.com', 'subject': 'Title', 'body': 'Text'}), ('github.file', {'repo': 'owner/repo', 'path': '../token'}), ('github.repository', {'repo': 'https://evil.test'}), ('calendar.create', {'calendar_id': 'primary', 'summary': 'Bad date', 'start': '2026-10-10', 'end': '2026-10-11'})]:
        with pytest.raises(HTTPException):
            connector_catalog.validate({'provider': connector_catalog.CATALOG[action][0]}, action, args)


def test_worker_waits_for_uncertain_connector_receipt(make_task):
    task = make_task()
    c = connection()
    review = proposal(task, c)
    with db.connection() as conn:
        attempt = worker.claim_new()  # Task is waiting for review, so no work starts.
        assert attempt == []
        conn.execute("UPDATE connector_operations SET status='uncertain' WHERE approval_id=%s", (review['id'],))
    assert task_row(task)['status'] == 'waiting_approval'


def test_worker_terminal_does_not_resume_before_connector_dispatch(make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    review = proposal(task, connection())
    with db.connection() as conn:
        conn.execute("UPDATE approvals SET status='decided',decision='once' WHERE id=%s", (review['id'],))
        conn.execute("UPDATE connector_operations SET status='queued' WHERE approval_id=%s", (review['id'],))
    worker.settle(attempt['id'], {'status': 'completed', 'output': 'Waiting for actual connector effect'})
    assert task_row(task)['status'] == 'waiting_approval'


def test_mutation_and_reviews_wait_for_existing_external_receipt(client, make_task):
    c, task = connection(), make_task()
    review = proposal(task, c)
    decide(client, review)
    with pytest.raises(HTTPException) as error:
        proposal(task, c)
    assert error.value.status_code == 409 and len(operations()) == 1
    with db.connection() as conn:
        with pytest.raises(HTTPException):
            service.request_review(conn, service.locked_task(conn, task['id']), 'Overlapping review', {})
        conn.execute("UPDATE tasks SET status='queued' WHERE id=%s", (task['id'],))
    assert worker.claim_new() == []
    assert task_row(task)['status'] == 'waiting_approval'


def test_stop_unknown_effect_cannot_retry_or_close_while_active(client, make_task, monkeypatch):
    c, task = connection(), make_task()
    calls = effect(monkeypatch, error=connector_http.ProviderFailure('Lost response', uncertain=True))
    decide(client, proposal(task, c))
    connections.drive_one()
    identity = operations()[0]['id']
    route = f'/api/v1/connector-operations/{identity}/reconcile'
    evidence = {'outcome': 'unresolved', 'evidence': 'Receipt was lost; task stopped without replay; outcome remains unknown'}
    assert client.post(route, json=evidence).status_code == 409
    with db.connection() as conn:
        service.cancel(conn, service.locked_task(conn, task['id']))
    retry = f"/api/v1/tasks/{task['id']}/retry"
    assert client.post(retry, headers={'Idempotency-Key': str(uuid4())}, json={}).status_code == 409
    assert client.post(route, json=evidence).json() == {'status': 'unresolved', 'replayed': False}
    assert task_row(task)['status'] == 'cancelled'
    assert operations()[0]['error'] == 'Lost response'
    assert operations()[0]['result']['reconciled'] is False
    assert client.post(retry, headers={'Idempotency-Key': str(uuid4())}, json={}).status_code == 409
    assert not connections.drive_one() and len(calls) == 1
    assert client.post(route, json={'outcome': 'failed', 'evidence': 'Provider audit verifies that the issue was never created'}).status_code == 200
    assert task_row(task)['status'] == 'cancelled'
    assert client.post(retry, headers={'Idempotency-Key': str(uuid4())}, json={}).status_code == 202


def test_stopped_child_unknown_effect_cannot_be_reassigned(client, make_task, monkeypatch):
    parent = make_task(manager=True)
    with db.connection() as conn:
        child_id = service.delegate(conn, service.locked_task(conn, parent['id']), 'writer', 'Create exact issue')['task_id']
        child = service.locked_task(conn, child_id)
    c = connection(bots=['writer'])
    calls = effect(monkeypatch, error=connector_http.ProviderFailure('Lost child response', uncertain=True))
    decide(client, proposal(child, c))
    connections.drive_one()
    with db.connection() as conn:
        service.cancel(conn, service.locked_task(conn, child_id))
    for outcome in ('uncertain', 'unresolved'):
        if outcome == 'unresolved':
            response = client.post(f"/api/v1/connector-operations/{operations()[0]['id']}/reconcile", json={'outcome': outcome, 'evidence': 'Task stopped without knowing whether provider changed state'})
            assert response.status_code == 200
        with db.connection() as conn:
            with pytest.raises(HTTPException) as denied:
                service.reassign(conn, service.locked_task(conn, parent['id']), child_id, 'reviewer')
            assert denied.value.status_code == 409
    assert len(calls) == 1


def test_unknown_closed_effect_keeps_standing_permission_consumed(client, make_task, monkeypatch):
    c, task = connection(), make_task()
    calls = effect(monkeypatch, error=connector_http.ProviderFailure('Lost receipt', uncertain=True))
    permission = client.post('/api/v1/connector-permissions', json={'connection_id': str(c['id']), 'bot_id': 'researcher', 'action': 'github.create_issue', 'resource': 'owner/repo', 'max_uses': 1}).json()
    proposal(task, c)
    connections.drive_one()
    with db.connection() as conn:
        service.cancel(conn, service.locked_task(conn, task['id']))
        conn.execute('UPDATE connector_permissions SET used=0 WHERE id=%s', (permission['id'],))
    identity = operations()[0]['id']
    assert client.post(f'/api/v1/connector-operations/{identity}/reconcile', json={'outcome': 'unresolved', 'evidence': 'Provider response unavailable; task stopped without replay'}).status_code == 200
    proposal(make_task(), c)
    connections.drive_one()
    assert len(calls) == 1 and operations()[1]['status'] == 'failed'
