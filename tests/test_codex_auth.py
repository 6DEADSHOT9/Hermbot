"""Direct Codex boundaries use only fake accounts and isolated DB records."""
# ruff: noqa: F811 -- imported pytest fixtures are intentionally used as test parameters.
import asyncio
import ast
from contextlib import asynccontextmanager
import json
from pathlib import Path
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb
import pytest

from backend import db, model_auth, registry
from backend.settings import settings
from model_broker.codex import BASE_URL, CLIENT_ID, CodexAccounts
from backend.model_protocol import PROTOCOL
from tests.test_model_auth import codex_credentials, codex_network, issuer as issuer, owner as owner, vault as vault


def connected(owner):
    cid = owner.post('/api/v1/providers/codex').json()['id']
    with db.connection() as conn:
        conn.execute("UPDATE providers SET connection_status='connected',plan_enabled=true,models=%s,discovered_models=%s,verified_models=%s,default_model='fixture' WHERE id=%s", (Jsonb(['fixture']), Jsonb(['fixture']), Jsonb(['fixture']), cid))
        return conn.execute('SELECT * FROM providers WHERE id=%s', (cid,)).fetchone()


def test_codex_management_dispatch_is_explicit_and_safe(owner, monkeypatch):
    row = connected(owner)
    calls = []
    async def call(cid, action, payload=None, **kwargs):
        calls.append((action, kwargs))
        return {'status': 'connecting', 'device_code': 'TEST-CODE', 'verification_url': 'https://auth.openai.com/codex/device'}
    monkeypatch.setattr(model_auth.relay, 'call', call)
    result = owner.post(f"/api/v1/providers/{row['id']}/chatgpt/device")
    assert result.status_code == 200 and result.json()['status'] == 'connecting'
    assert calls == [('device', {'auth_kind': 'codex_oauth'})]
    assert not model_auth.provider(row['id'])['verified_models']
    assert owner.post(f"/api/v1/providers/{row['id']}/chatgpt/signin", headers={'Origin': 'https://foreign.invalid'}).status_code == 403


def test_codex_internal_completion_routes_tools_without_namespace(owner, monkeypatch):
    row = connected(owner)
    @asynccontextmanager
    async def request(cid, action, payload, **kwargs):
        assert str(cid) == str(row['id']) and kwargs == {'auth_kind': 'codex_oauth', 'caller': 'backend:teaching'}
        assert payload['tools'][0]['type'] == 'function'
        async def events():
            yield {'event': {'type': 'response.completed', 'response': {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'fixture'}]}]}}}
            yield {'result': {}}
        yield events()
    monkeypatch.setattr(model_auth.relay, 'request', request)
    response = owner.post('/internal/model-completion', headers={'Authorization': 'Bearer ' + settings.internal_token}, json={
        'provider_id': str(row['id']), 'provider_revision': row['revision'], 'provider_auth_epoch': row['auth_epoch'], 'caller': 'teaching',
        'payload': {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'read'}], 'tools': [{'type': 'function', 'function': {'name': 'read', 'parameters': {'type': 'object'}}}]}})
    assert response.status_code == 200 and response.json()['choices'][0]['message']['content'] == 'fixture'


def test_codex_provider_rejects_secret_and_destination_changes(owner):
    row = connected(owner)
    body = {'auth_kind': 'codex_oauth', 'name': row['name'], 'base_url': BASE_URL, 'models': ['fixture'], 'default_model': 'fixture', 'expected_revision': row['revision']}
    for changed in ({'api_key': 'forbidden'}, {'base_url': 'https://foreign.invalid'}, {'auth_kind': 'api_key'}):
        response = owner.put('/api/v1/providers/' + str(row['id']), json={**body, **changed}, headers={'Idempotency-Key': str(uuid4())})
        assert response.status_code == 422
    with db.connection() as conn:
        assert conn.execute('SELECT secret_encrypted FROM providers WHERE id=%s', (row['id'],)).fetchone()['secret_encrypted'] is None


def test_codex_provisioning_contains_only_restricted_credential(owner):
    row = connected(owner)
    with db.connection() as conn:
        config = registry.validate_config(conn, {'name': 'Codex test', 'role': 'Fixtures', 'provider_id': row['id']})
        bot = registry.register(conn, config)
        operation = registry.enqueue(conn, bot, 'create', str(uuid4()))
        payload = conn.execute('SELECT payload FROM bot_operations WHERE id=%s', (operation['id'],)).fetchone()['payload']
    assert payload['provider']['auth_kind'] == 'codex_oauth'
    assert 'inference_key_encrypted' in payload['provider']
    assert not any(field in json.dumps(payload) for field in ('refresh_token', 'access_token', 'secret_encrypted'))


def test_codex_cancel_closes_pending_login_and_listener(vault, monkeypatch):
    from model_broker import codex
    async def run():
        server = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        monkeypatch.setattr(codex, 'CALLBACK_PORT', port)
        accounts = CodexAccounts(vault, open_browser=lambda url: True)
        cid = str(uuid4())
        accounts.begin(cid)
        await asyncio.sleep(0.03)
        result = await accounts.disconnect(cid)
        assert result['status'] == 'disconnected' and not result['revocation_confirmed']
        assert not accounts.signins and 'pending' not in vault.get(cid)
        rebound = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', port)
        rebound.close()
        await rebound.wait_closed()
        await accounts.http.aclose()
    asyncio.run(run())


def test_codex_refresh_without_id_token_keeps_verified_principal(vault, issuer):
    async def run():
        accounts = CodexAccounts(vault)
        await accounts.http.aclose()
        accounts.http = httpx.AsyncClient(transport=codex_network(issuer, lambda r: httpx.Response(200, json={'access_token': 'replacement', 'refresh_token': 'rotated', 'expires_in': 864000})))
        cid = str(uuid4())
        await accounts.accept(cid, {}, codex_credentials(issuer), 0)
        original = vault.get(cid)
        original['credentials']['expires_at'] = 0
        vault.put(cid, original)
        token, _ = await accounts.access(cid)
        assert token == 'replacement' and vault.get(cid)['subject'] == original['subject']
        assert vault.get(cid)['account_id'] == original['account_id']
        await accounts.http.aclose()
    asyncio.run(run())


def test_api_switch_removes_hermbot_auxiliary_routes():
    tree = ast.parse(Path('docker/hermes_bootstrap.py').read_text())
    branch = next(node for node in tree.body if isinstance(node, ast.If) and 'MODEL_AUTH_KIND' in ast.unparse(node.test))
    config = {'providers': {'hermbot-chatgpt': {}, 'other': {}}, 'auxiliary': {'vision': {'provider': 'custom:hermbot-chatgpt'}, 'other': {'provider': 'other'}}}
    module = ast.fix_missing_locations(ast.Module(body=branch.orelse, type_ignores=[]))
    exec(compile(module, '<bootstrap-api-switch>', 'exec'), {'config': config})
    assert config == {'providers': {'other': {}}, 'auxiliary': {'other': {'provider': 'other'}}}


def test_codex_jwt_audience_is_not_dynamic_application(vault, issuer):
    async def run():
        accounts = CodexAccounts(vault)
        await accounts.http.aclose()
        accounts.http = httpx.AsyncClient(transport=codex_network(issuer, lambda r: None))
        result = codex_credentials(issuer)
        result['id_token'] = issuer[1]()
        with pytest.raises(Exception, match='invalid_identity'):
            await accounts.accept(str(uuid4()), {}, result, 0)
        assert CLIENT_ID.startswith('app_')
        await accounts.http.aclose()
    asyncio.run(run())


def test_interrupted_codex_login_does_not_poll_forever(vault):
    async def run():
        cid = str(uuid4())
        vault.put(cid, {'kind': 'codex_oauth', 'status': 'connecting'})
        accounts = CodexAccounts(vault)
        assert accounts.status(cid)['status'] == 'reauth_required'
        assert accounts.status(cid)['error'] == 'sign_in_interrupted'
        await accounts.http.aclose()
    asyncio.run(run())


def test_codex_device_login_uses_its_own_exchange(vault, issuer, monkeypatch):
    from model_broker import codex
    async def run():
        original_sleep = asyncio.sleep
        async def short_sleep(seconds):
            await original_sleep(0)
        monkeypatch.setattr(codex.asyncio, 'sleep', short_sleep)
        accounts = CodexAccounts(vault)
        await accounts.http.aclose()
        def handler(request):
            if request.url.path == '/api/accounts/deviceauth/usercode':
                assert json.loads(request.content) == {'client_id': CLIENT_ID}
                return httpx.Response(200, json={'user_code': 'FAKE-CODE', 'device_auth_id': 'fake-device', 'interval': 5})
            if request.url.path == '/api/accounts/deviceauth/token':
                return httpx.Response(200, json={'authorization_code': 'fixture', 'code_verifier': 'fixture-verifier'})
            return codex_network(issuer, lambda r: httpx.Response(200, json=codex_credentials(issuer))).handler(request)
        accounts.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        cid = str(uuid4())
        assert accounts.begin(cid, device=True)['status'] == 'connecting'
        await accounts.signins[cid]
        assert accounts.status(cid)['status'] == 'connected'
        assert 'device_code' not in accounts.status(cid) and 'device_code' not in vault.get(cid)
        await accounts.http.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('event,expected', [
    ({'type': 'response.completed', 'response': {'status': 'completed', 'output': []}}, None),
    ({'type': 'response.completed', 'response': {'status': 'incomplete'}}, 'model_response_incomplete'),
    ({'type': 'response.failed', 'response': {'error': {'code': 'usage_limit_reached'}}}, 'codex_usage_limit'),
    ({'type': 'response.created'}, 'model_response_interrupted'),
])
def test_broker_codex_stream_requires_terminal_success(vault, monkeypatch, event, expected):
    from model_broker import __main__ as broker
    from websockets.asyncio.server import serve
    async def run():
        cid, rid = str(uuid4()), str(uuid4())
        vault.put(cid, {'kind': 'codex_oauth'})
        calls, observed = [], []
        done = asyncio.get_running_loop().create_future()
        def upstream(request):
            calls.append(request)
            assert str(request.url) == BASE_URL + '/responses'
            assert request.headers['authorization'] == 'Bearer upstream-sentinel'
            assert request.headers['chatgpt-account-id'] == 'fixture-account'
            assert json.loads(request.content)['tools'][0]['type'] == 'function'
            return httpx.Response(200, content=('data: ' + json.dumps(event) + '\n\n').encode())
        class FakeAccounts:
            def __init__(self, unused):
                self.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
                self.signins = {}
            async def access(self, unused):
                return 'upstream-sentinel', 0
            def epoch(self, unused):
                return 0
            def headers(self, unused, token):
                return {'Authorization': 'Bearer ' + token, 'ChatGPT-Account-Id': 'fixture-account'}
        monkeypatch.setattr(broker, 'Accounts', FakeAccounts)
        monkeypatch.setattr(broker, 'CodexAccounts', FakeAccounts)
        async def connection(socket):
            assert socket.request.headers['authorization'] == 'Bearer pairing-fixture'
            await socket.send(json.dumps({'version': PROTOCOL, 'id': rid, 'connection_id': cid, 'auth_kind': 'codex_oauth', 'action': 'responses', 'caller': 'bot:fixture',
                                          'payload': {'model': 'fixture', 'input': [], 'tools': [{'type': 'function', 'name': 'read', 'parameters': {'type': 'object'}}]}}))
            async for raw in socket:
                value = json.loads(raw)
                observed.append(value)
                if 'result' in value or 'error' in value:
                    done.set_result(value)
                    return
        async with serve(connection, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            task = asyncio.create_task(broker.serve(vault, 'pairing-fixture', f'ws://127.0.0.1:{port}'))
            try:
                result = await asyncio.wait_for(done, 5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert result.get('error') == expected
        assert len(calls) == 1
        assert [item['state'] for item in observed if 'state' in item] == ['waiting', 'active']
        if expected:
            assert not any(item.get('event', {}).get('type') == 'response.completed' for item in observed)
    asyncio.run(run())


def test_asgi_disconnect_still_sends_upstream_cancellation():
    import anyio
    async def run():
        relay = model_auth.Relay()
        cancelled = asyncio.Event()
        class Socket:
            async def send_json(self, value):
                await asyncio.sleep(0)
                if value['action'] == 'cancel':
                    cancelled.set()
        relay.socket = Socket()
        with anyio.CancelScope() as scope:
            async with relay.request(str(uuid4()), 'responses', {}):
                scope.cancel()
        await asyncio.wait_for(cancelled.wait(), 1)
        assert not relay.pending
    asyncio.run(run())


@pytest.mark.parametrize('fallback', [False, True])
def test_codex_catalog_tries_newest_before_legacy(vault, monkeypatch, fallback):
    async def run():
        accounts = CodexAccounts(vault)
        cid = str(uuid4())
        vault.put(cid, {'account_id': 'fixture'})
        calls = []
        async def request(method, url, **kwargs):
            calls.append(url)
            if fallback and len(calls) == 1:
                return {'models': []}
            return {'models': [{'slug': 'new-model', 'visibility': 'list'}]}
        monkeypatch.setattr(accounts, 'json_request', request)
        result = await accounts.catalog(cid, 'fixture')
        assert result['models'][0]['slug'] == 'new-model'
        assert calls == [BASE_URL + '/models?client_version=99.0.0'] + ([BASE_URL + '/models?client_version=0.0.0'] if fallback else [])
        await accounts.http.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('restricted,concurrent', [(False, False), (True, False), (False, True)])
def test_catalog_refresh_updates_unrestricted_picker_preserving_owner_choice(owner, monkeypatch, restricted, concurrent):
    row = connected(owner)
    with db.connection() as conn:
        conn.execute('UPDATE providers SET discovered_models=%s WHERE id=%s', (Jsonb(['fixture', 'restricted-model'] if restricted else ['fixture']), row['id']))
    async def call(cid, action, **kwargs):
        assert action == 'models'
        if concurrent:
            with db.connection() as conn:
                conn.execute('UPDATE providers SET models=%s,revision=revision+1 WHERE id=%s', (Jsonb(['owner-choice']), cid))
        return {'status': 'connected', 'plan_enabled': True, 'models': [{'slug': m} for m in ['new-model', 'fixture', 'restricted-model']]}
    monkeypatch.setattr(model_auth.relay, 'call', call)
    response = owner.post(f"/api/v1/providers/{row['id']}/chatgpt/models")
    assert response.status_code == 200
    current = model_auth.provider(row['id'])
    assert current['discovered_models'] == ['new-model', 'fixture', 'restricted-model']
    assert current['models'] == (['owner-choice'] if concurrent else ['fixture'] if restricted else ['new-model', 'fixture', 'restricted-model'])
    assert current['default_model'] == 'fixture' and current['verified_models'] == ['fixture']


def test_reconnected_model_reports_test_requirement_without_inference(owner, monkeypatch):
    from backend import model_client
    row = connected(owner)
    with db.connection() as conn:
        conn.execute("UPDATE providers SET verified_models='[]' WHERE id=%s", (row['id'],))
    async def unexpected(*args, **kwargs):
        raise AssertionError('Untested model must not reach the broker')
    monkeypatch.setattr(model_auth.relay, 'request', unexpected)
    response = owner.post('/internal/model-completion', headers={'Authorization': 'Bearer ' + settings.internal_token}, json={
        'provider_id': str(row['id']), 'provider_revision': row['revision'], 'provider_auth_epoch': row['auth_epoch'],
        'caller': 'intake', 'payload': {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'Fixture'}]}})
    assert response.status_code == 409 and response.json()['detail'] == 'model_test_required'
    try:
        model_client.result(response)
    except model_client.ModelConnectionError as error:
        assert 'click Test model' in str(error) and 'saved work is preserved' in str(error)
    else:
        raise AssertionError('The prerequisite must remain enforced')
