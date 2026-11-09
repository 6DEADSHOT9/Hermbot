"""Security and compatibility cases use fake credentials/issuer; never the owner's account."""
import asyncio
import hashlib
import json
import os
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import httpx
import jwt
from psycopg.types.json import Jsonb
import pytest

from backend import app as application, db, model_auth, registry
from backend.model_protocol import chat_to_responses, completed_message, responses_request, sse_events
from backend.secrets import credential_hash, decrypt
from backend.settings import settings
from model_broker.accounts import AccountError, Accounts, ISSUER, RESOURCE, SCOPE
from model_broker.vault import Vault
from model_broker.codex import BASE_URL, CLIENT_ID, CodexAccounts
from model_broker.capacity import Capacity


@pytest.fixture
def vault(tmp_path):
    result = Vault(tmp_path / 'vault', Fernet.generate_key())
    yield result
    result.close()


@pytest.fixture
def owner():
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    yield client
    client.close()


def oauth_provider():
    cid = uuid4()
    with db.connection() as conn:
        conn.execute("INSERT INTO providers(id,name,base_url,models,default_model,auth_kind,transport,connection_status,plan_enabled,verified_models,discovered_models) VALUES(%s,%s,%s,%s,'model','chatgpt_oauth','responses','connected',true,%s,%s)",
                     (cid, 'Test subscription ' + str(cid), RESOURCE, Jsonb(['model']), Jsonb(['model']), Jsonb(['model'])))
        return conn.execute('SELECT * FROM providers WHERE id=%s', (cid,)).fetchone()


def codex_credentials(issuer, **claims):
    return {'access_token': 'codex-access-sentinel', 'refresh_token': 'codex-refresh-sentinel', 'token_type': 'Bearer', 'expires_in': 3600,
            'id_token': issuer[1](aud=CLIENT_ID, **{'https://api.openai.com/auth': {'chatgpt_account_id': 'fixture-account'}}, **claims)}


def codex_network(issuer, callback):
    def handler(request):
        assert request.url.host == 'auth.openai.com'
        if request.url.path == '/.well-known/openid-configuration':
            return httpx.Response(200, json={'issuer': ISSUER, 'jwks_uri': ISSUER + '/jwks'})
        if request.url.path == '/jwks':
            return httpx.Response(200, json={'keys': [issuer[0]]})
        assert request.url.path == '/oauth/token'
        return callback(request)
    return httpx.MockTransport(handler)


def test_codex_browser_owns_pkce_and_rejects_wrong_state(vault, issuer, monkeypatch):
    from model_broker import codex
    async def run():
        cid = str(uuid4())
        listener = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
        port = listener.sockets[0].getsockname()[1]
        listener.close()
        await listener.wait_closed()
        monkeypatch.setattr(codex, 'CALLBACK_PORT', port)
        monkeypatch.setattr(codex, 'REDIRECT_URI', f'http://localhost:{port}/auth/callback')
        nonce = []
        async def visit(url):
            parsed = urlsplit(url)
            params = parse_qs(parsed.query)
            assert parsed.path == '/oauth/authorize' and params['client_id'] == [CLIENT_ID]
            assert 'resource' not in params and 'chatgpt.tokens.use.direct' not in params['scope'][0]
            nonce.append(params['nonce'][0])
            async with httpx.AsyncClient(trust_env=False) as browser:
                callback = f'http://127.0.0.1:{port}/auth/callback'
                rejected = await browser.get(callback + '?' + urlencode({'state': 'wrong', 'code': 'fixture'}), headers={'Host': f'localhost:{port}'})
                assert rejected.status_code == 400
                accepted = await browser.get(callback + '?' + urlencode({'state': params['state'][0], 'code': 'fixture'}), headers={'Host': f'localhost:{port}'})
                assert accepted.status_code == 200
        def launch(url):
            asyncio.create_task(visit(url))
            return True
        accounts = CodexAccounts(vault, open_browser=launch)
        await accounts.http.aclose()
        def exchange(request):
            data = parse_qs(request.content.decode())
            assert data['client_id'] == [CLIENT_ID] and data['code_verifier'] and 'resource' not in data
            return httpx.Response(200, json=codex_credentials(issuer, nonce=nonce[0]))
        accounts.http = httpx.AsyncClient(transport=codex_network(issuer, exchange))
        assert accounts.begin(cid)['status'] == 'connecting'
        await accounts.signins[cid]
        assert accounts.status(cid)['status'] == 'connected'
        assert vault.get(cid)['kind'] == 'codex_oauth'
        assert 'sentinel' not in json.dumps(accounts.status(cid))
        assert accounts.headers(cid, 'fixture')['ChatGPT-Account-Id'] == 'fixture-account'
        await accounts.http.aclose()
    asyncio.run(run())


def test_codex_concurrent_refresh_and_uncertain_outcome(vault, issuer):
    async def run():
        cid = str(uuid4())
        accounts = CodexAccounts(vault)
        await accounts.http.aclose()
        exchanges = []
        def exchange(request):
            exchanges.append(request)
            return httpx.Response(200, json=codex_credentials(issuer) | {'refresh_token': 'rotated'})
        accounts.http = httpx.AsyncClient(transport=codex_network(issuer, exchange))
        await accounts.accept(cid, {}, codex_credentials(issuer), 0)
        record = vault.get(cid)
        record['credentials']['expires_at'] = 0
        vault.put(cid, record)
        results = await asyncio.gather(*(accounts.access(cid) for _ in range(12)))
        assert len(exchanges) == 1 and len(results) == 12
        assert vault.get(cid)['credentials']['refresh_token'] == 'rotated'
        record = vault.get(cid)
        record['refresh_in_progress'] = True
        vault.put(cid, record)
        with pytest.raises(AccountError, match='refresh_uncertain'):
            await accounts.access(cid)
        assert len(exchanges) == 1
        await accounts.http.aclose()
    asyncio.run(run())


def test_codex_duplicate_account_and_subject_substitution_rejected(vault, issuer):
    async def run():
        accounts = CodexAccounts(vault)
        await accounts.http.aclose()
        accounts.http = httpx.AsyncClient(transport=codex_network(issuer, lambda r: None))
        cid = str(uuid4())
        await accounts.accept(cid, {}, codex_credentials(issuer), 0)
        with pytest.raises(AccountError, match='duplicate_account'):
            await accounts.accept(str(uuid4()), {}, codex_credentials(issuer), 0)
        with pytest.raises(AccountError, match='invalid_identity'):
            await accounts.accept(cid, vault.get(cid), codex_credentials(issuer, sub='different'), 0)
        await accounts.disconnect(cid)
        assert accounts.status(cid)['status'] == 'disconnected'
        assert 'credentials' not in vault.get(cid)
        await accounts.http.aclose()
    asyncio.run(run())


def test_codex_protocol_does_not_apply_plan_sharing_tool_namespaces():
    body = {'model': 'fixture', 'messages': [{'role': 'system', 'content': 'Use tools'}, {'role': 'user', 'content': 'Read'}],
            'tools': [{'type': 'function', 'function': {'name': 'read', 'parameters': {'type': 'object'}}}], 'reasoning_effort': 'low'}
    result = chat_to_responses(body, 'codex_oauth')
    assert result['tools'][0]['type'] == 'function'
    assert result['reasoning'] == {'effort': 'low'}
    assert result['instructions'] == '' and result['stream'] and not result['store']
    assert chat_to_responses(body)['tools'][0]['type'] == 'namespace'


def test_codex_provider_migration_replay_and_api_provider_preserved(owner):
    before = owner.get('/api/v1/settings').json()
    result = owner.post('/api/v1/providers/codex')
    assert result.status_code == 201
    cid = result.json()['id']
    assert result.json()['auth_kind'] == 'codex_oauth'
    from pathlib import Path
    with db.connection() as conn:
        for _ in range(2):
            for name in ('020_model_auth.sql', '021_codex_auth.sql'):
                conn.execute((Path('backend/migrations') / name).read_text())
        record = conn.execute('SELECT * FROM providers WHERE id=%s', (cid,)).fetchone()
        assert record['secret_encrypted'] is None and record['base_url'] == BASE_URL
    after = owner.get('/api/v1/settings').json()
    assert after['workspace']['default_provider_id'] == before['workspace']['default_provider_id']
    api_before = [p for p in before['providers'] if p['auth_kind'] == 'api_key']
    assert [p for p in after['providers'] if p['auth_kind'] == 'api_key'] == api_before
    with db.connection() as conn:
        conn.execute("UPDATE providers SET models='[\"fixture\"]' WHERE id=%s", (cid,))
        with pytest.raises(Exception, match='test this model'):
            registry.validate_config(conn, {'name': 'Test', 'role': 'Test', 'provider_id': cid, 'model_name': 'fixture'})


def test_capacity_rotates_callers_and_cancellation_releases_slots():
    async def run():
        capacity = Capacity(1)
        gate = asyncio.Event()
        order = []
        async def job(caller, wait=False):
            async with capacity.slot(caller):
                order.append(caller)
                if wait:
                    await gate.wait()
        first = asyncio.create_task(job('busy', True))
        await asyncio.sleep(0)
        tasks = [asyncio.create_task(job(c)) for c in ['busy', 'busy', 'other', 'other']]
        cancelled = asyncio.create_task(job('cancelled'))
        await asyncio.sleep(0)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        gate.set()
        await asyncio.gather(first, *tasks)
        assert order == ['busy', 'busy', 'other', 'busy', 'other']
        assert capacity.active == 0
    asyncio.run(run())


def test_vault_encrypts_records_and_refuses_symlinks_or_wrong_key(vault, tmp_path):
    vault.put('test', {'refresh_token': 'secret-sentinel'})
    assert vault.get('test')['refresh_token'] == 'secret-sentinel'
    assert b'secret-sentinel' not in (vault.directory / 'accounts.sqlite3').read_bytes()
    assert os.stat(vault.directory / 'accounts.sqlite3').st_mode & 0o077 == 0
    with pytest.raises(InvalidToken):
        Vault(vault.directory, Fernet.generate_key())
    link = tmp_path / 'link'
    link.symlink_to(vault.directory, target_is_directory=True)
    with pytest.raises(RuntimeError):
        Vault(link, Fernet.generate_key())


@pytest.fixture
def issuer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {'kid': 'test-key', 'use': 'sig'}
    def token(**extra):
        return jwt.encode({'iss': ISSUER, 'aud': 'oaiapp_test', 'sub': 'subject', 'iat': time.time(), 'exp': time.time() + 3600, 'email': 'fixture@example.invalid', **extra}, key, algorithm='RS256', headers={'kid': 'test-key'})
    return jwk, token


def network(issuer, token_response=None, on_refresh=None):
    jwk, _ = issuer
    def handler(request):
        assert request.url.host == 'auth.openai.com'
        if request.url.path == '/.well-known/openid-configuration':
            return httpx.Response(200, json={'issuer': ISSUER, 'jwks_uri': ISSUER + '/jwks', 'revocation_endpoint': ISSUER + '/revoke'})
        if request.url.path == '/jwks':
            return httpx.Response(200, json={'keys': [jwk]})
        if request.url.path == '/api/accounts/oauth/token':
            if on_refresh:
                on_refresh(request)
            return httpx.Response(200, json=token_response())
        if request.url.path == '/revoke':
            return httpx.Response(200)
        pytest.fail('Unexpected provider endpoint')
    return httpx.MockTransport(handler)


def credentials(issuer):
    return {'access_token': 'access-sentinel', 'refresh_token': 'refresh-sentinel', 'id_token': issuer[1](),
            'token_type': 'Bearer', 'scope': SCOPE, 'expires_in': 3600, 'expires_at': time.time() + 3600}


def test_oidc_rejects_nonce_audience_subject_and_algorithm(vault, issuer):
    async def run():
        accounts = Accounts(vault)
        await accounts.http.aclose()
        accounts.http = httpx.AsyncClient(transport=network(issuer))
        try:
            assert (await accounts.identity(issuer[1](nonce='right'), 'oaiapp_test', nonce='right'))['subject'] == 'subject'
            for token, kwargs in [(issuer[1](nonce='wrong'), {'nonce': 'right'}), (issuer[1](aud='foreign'), {}),
                                  (issuer[1](sub='foreign'), {'subject': 'subject'}), (jwt.encode({'sub': 'subject'}, 'test-signing-secret-for-unit-tests', algorithm='HS256'), {})]:
                with pytest.raises(AccountError, match='invalid_identity'):
                    await accounts.identity(token, 'oaiapp_test', **kwargs)
        finally:
            await accounts.http.aclose()
    asyncio.run(run())


def test_refresh_is_single_owner_for_concurrent_callers(vault, issuer):
    count = []
    cid = str(uuid4())
    vault.put(cid, {'client_id': 'oaiapp_test', 'subject': 'subject', 'status': 'connected', 'scope': SCOPE,
                    'credentials': credentials(issuer) | {'expires_at': 0}})
    async def run():
        accounts = Accounts(vault)
        await accounts.http.aclose()
        accounts.http = httpx.AsyncClient(transport=network(issuer, lambda: credentials(issuer), lambda request: count.append(request)))
        try:
            result = await asyncio.gather(*(accounts.access(cid) for _ in range(12)))
            assert all(token == 'access-sentinel' for token, _ in result)
            assert len(count) == 1
            assert parse_qs(count[0].content.decode())['client_id'] == ['oaiapp_test']
            assert 'scope' not in parse_qs(count[0].content.decode())
            assert not vault.get(cid).get('refresh_in_progress')
        finally:
            await accounts.http.aclose()
    asyncio.run(run())


def test_received_rotation_survives_verification_outage_without_refresh_replay(vault, issuer):
    cid = str(uuid4())
    pending = credentials(issuer) | {'received_at': time.time()}
    vault.put(cid, {'client_id': 'oaiapp_test', 'subject': 'subject', 'pending': pending})
    async def run():
        accounts = Accounts(vault)
        await accounts.http.aclose()
        accounts.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503, json={})))
        assert accounts.status(cid)['status'] == 'verification_pending'
        with pytest.raises(AccountError, match='verification_unavailable'):
            await accounts.access(cid)
        assert vault.get(cid)['pending']['refresh_token'] == 'refresh-sentinel'
        await accounts.http.aclose()
        accounts.http = httpx.AsyncClient(transport=network(issuer))
        assert (await accounts.access(cid))[0] == 'access-sentinel'
        assert not vault.get(cid).get('pending')
        await accounts.http.aclose()
    asyncio.run(run())


def test_uncertain_refresh_requires_reconnect_and_disconnect_clears_secrets(vault, issuer):
    cid = str(uuid4())
    vault.put(cid, {'client_id': 'oaiapp_test', 'subject': 'subject', 'status': 'connected', 'scope': SCOPE,
                    'credentials': credentials(issuer), 'refresh_in_progress': True})
    async def run():
        accounts = Accounts(vault)
        await accounts.http.aclose()
        accounts.http = httpx.AsyncClient(transport=network(issuer))
        with pytest.raises(AccountError, match='refresh_uncertain'):
            await accounts.access(cid)
        result = await accounts.disconnect(cid)
        assert result['revocation_confirmed']
        assert set(vault.get(cid)) == {'client_id', 'subject', 'status'}
        assert accounts.epoch(cid) == 1
        await accounts.http.aclose()
    asyncio.run(run())


def test_loopback_signin_rejects_wrong_state_and_persists_issued_registration(vault, issuer):
    cid = str(uuid4())
    nonces = []
    async def run():
        async def visit(url):
            params = parse_qs(urlsplit(url).query)
            assert params['client_id'] == ['dynamic_agent_client']
            assert params['agent_name_hint'] == ['Hermbot']
            assert params['code_challenge_method'] == ['S256']
            nonces.append(params['nonce'][0])
            async with httpx.AsyncClient(trust_env=False) as browser:
                callback = params['redirect_uri'][0]
                rejected = await browser.get(callback + '?' + urlencode({'state': 'wrong', 'code': 'code', 'client_id': 'oaiapp_test'}))
                assert rejected.status_code == 400
                accepted = await browser.get(callback + '?' + urlencode({'state': params['state'][0], 'code': 'code', 'client_id': 'oaiapp_test'}))
                assert accepted.status_code == 200
                assert accepted.headers['cache-control'] == 'no-store'
        def open_browser(url):
            asyncio.create_task(visit(url))
            return True
        accounts = Accounts(vault, open_browser=open_browser)
        await accounts.http.aclose()
        def result():
            assert vault.get(cid)['client_id'] == 'oaiapp_test'
            return credentials(issuer) | {'id_token': issuer[1](nonce=nonces[0])}
        accounts.http = httpx.AsyncClient(transport=network(issuer, result))
        value = await accounts.signin(cid)
        assert value['status'] == 'connected' and value['plan_enabled']
        assert 'access-sentinel' not in json.dumps(value)
        assert not accounts.signins
        await accounts.http.aclose()
    asyncio.run(run())


def test_responses_conversion_preserves_tool_ids_images_and_reasoning():
    history = [{'role': 'system', 'content': 'Interpret'}, {'role': 'user', 'content': [{'type': 'text', 'text': 'Read image'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,fixture'}}]},
               {'role': 'assistant', '_response_items': [{'type': 'reasoning', 'encrypted_content': 'opaque'}, {'type': 'function_call', 'call_id': 'call_one', 'name': 'read_context', 'arguments': '{"query":"earlier"}'}]},
               {'role': 'tool', 'tool_call_id': 'call_one', 'content': '{"found":true}'}]
    result = chat_to_responses({'model': 'model', 'messages': history, 'tools': [{'type': 'function', 'function': {'name': 'read_context', 'parameters': {'type': 'object'}}}]})
    assert result['input'][0]['role'] == 'developer'
    assert result['input'][1]['content'][1]['type'] == 'input_image'
    assert result['input'][2]['encrypted_content'] == 'opaque'
    assert result['input'][-1] == {'type': 'function_call_output', 'call_id': 'call_one', 'output': '{"found":true}'}
    assert result['tools'][0]['type'] == 'namespace'
    assert result['stream'] and result['store'] is False
    message = completed_message({'status': 'completed', 'output': [{'type': 'function_call', 'id': 'server_item', 'call_id': 'call_one', 'name': 'read_context', 'arguments': '{}'}]})
    assert message['tool_calls'][0]['id'] == 'call_one'
    assert 'id' not in message['_response_items'][0]


@pytest.mark.parametrize('field,value', [('previous_response_id', 'other'), ('background', True), ('store', True), ('tools', [{'type': 'computer_use_preview'}]), ('authorization', 'foreign')])
def test_unsupported_semantic_requests_are_rejected(field, value):
    with pytest.raises(ValueError):
        responses_request({'model': 'model', 'input': [], field: value})


def test_sse_handles_fragmentation_and_rejects_truncated_event():
    async def run():
        response = httpx.Response(200, content=b'data: {"type":"response.completed",\ndata: "response":{"status":"completed"}}\n\n')
        assert [e async for e in sse_events(response)][0]['type'] == 'response.completed'
        with pytest.raises(ValueError, match='incomplete event'):
            _ = [e async for e in sse_events(httpx.Response(200, content=b'data: {"type":"response.completed"}'))]
    asyncio.run(run())


def test_provider_projection_and_oauth_outbox_contain_no_account_tokens(owner):
    row = oauth_provider()
    settings_data = owner.get('/api/v1/settings').json()
    public = next(p for p in settings_data['providers'] if p['id'] == str(row['id']))
    assert not public['key_configured'] and public['auth_kind'] == 'chatgpt_oauth'
    from backend.models import BotConfiguration
    with db.connection() as conn:
        config = registry.validate_config(conn, BotConfiguration(name='Subscription fixture', role='Read local fixtures', provider_id=row['id']).model_dump())
        bot = registry.register(conn, config)
        operation = registry.enqueue(conn, bot, 'create', str(uuid4()))
        payload = conn.execute('SELECT payload FROM bot_operations WHERE id=%s', (operation['id'],)).fetchone()['payload']
        stored = registry.require_bot(conn, bot['id'])
    assert 'secret_encrypted' not in payload['provider']
    local = decrypt(payload['provider']['inference_key_encrypted'])
    assert credential_hash(local) == stored['inference_key_hash']
    assert stored['inference_revision'] == stored['revision']
    assert 'refresh_token' not in json.dumps(settings_data)


def test_provider_rejects_credential_type_switch_and_untested_model(owner):
    row = oauth_provider()
    update = {'name': row['name'], 'base_url': RESOURCE, 'models': ['model'], 'default_model': 'model', 'expected_revision': row['revision'], 'api_key': 'forbidden'}
    result = owner.put('/api/v1/providers/' + str(row['id']), json=update, headers={'Idempotency-Key': str(uuid4())})
    assert result.status_code == 422
    with db.connection() as conn:
        conn.execute("UPDATE providers SET verified_models='[]' WHERE id=%s", (row['id'],))
        with pytest.raises(Exception, match='test this model'):
            registry.validate_config(conn, {'name': 'Test', 'role': 'Test', 'provider_id': row['id']})


def test_runtime_inference_rejects_wrong_or_stale_credentials(owner):
    assert owner.post('/internal/inference/v1/responses', json={'model': 'model', 'input': []}).status_code == 401
    assert owner.post('/internal/model-completion', json={}).status_code == 401
    assert TestClient(application.app).post('/api/v1/providers/chatgpt').status_code == 401


def test_bridge_rejects_browser_origin_and_unauthorized_pairing(owner, monkeypatch, tmp_path):
    secret = 'fixture-pairing-secret-' * 4
    verifier = tmp_path / 'verifier'
    verifier.write_text(hashlib.sha256(secret.encode()).hexdigest())
    monkeypatch.setenv('MODEL_BROKER_TOKEN_FILE', str(verifier))
    with pytest.raises(Exception):
        with owner.websocket_connect('/internal/model-broker', headers={'Authorization': 'Bearer ' + secret, 'Origin': 'http://evil.invalid', 'X-Hermbot-Model-Protocol': '1'}):
            pytest.fail('Browser origin was admitted')
    with pytest.raises(Exception):
        with owner.websocket_connect('/internal/model-broker', headers={'Authorization': 'Bearer wrong'}):
            pytest.fail('Invalid pairing was admitted')


def test_relay_cancellation_and_disconnect_never_replay():
    async def run():
        relay = model_auth.Relay()
        sent = []
        class Socket:
            async def send_json(self, message):
                sent.append(message)
                if message['action'] != 'cancel':
                    relay.pending[message['id']][1].put_nowait({'result': {'status': 'connected'}})
        relay.socket = Socket()
        assert (await relay.call(str(uuid4()), 'status'))['status'] == 'connected'
        assert [r['action'] for r in sent] == ['status', 'cancel']
        assert not relay.pending
        relay.socket = None
        with pytest.raises(model_auth.BrokerFailure, match='broker_offline'):
            await relay.call(str(uuid4()), 'responses', {})
        assert len(sent) == 2 and not relay.pending
    asyncio.run(run())


def test_verified_refresh_with_removed_scope_does_not_authorize_inference(vault, issuer):
    cid = str(uuid4())
    vault.put(cid, {'client_id': 'oaiapp_test', 'subject': 'subject', 'status': 'connected', 'scope': SCOPE,
                    'credentials': credentials(issuer) | {'expires_at': 0}})
    async def run():
        accounts = Accounts(vault)
        await accounts.http.aclose()
        accounts.http = httpx.AsyncClient(transport=network(issuer, lambda: credentials(issuer) | {'scope': 'openid profile email'}))
        with pytest.raises(AccountError, match='plan_permission_required'):
            await accounts.access(cid)
        assert not accounts.status(cid)['plan_enabled']
        await accounts.http.aclose()
    asyncio.run(run())


def test_stale_quota_error_cannot_block_a_reconnected_account():
    row = oauth_provider()
    with db.connection() as conn:
        conn.execute('UPDATE providers SET auth_epoch=auth_epoch+1 WHERE id=%s', (row['id'],))
    model_auth.record_failure(row['id'], model_auth.BrokerFailure('subscription_sharing_usage_limit_exceeded'), row['auth_epoch'])
    assert model_auth.provider(row['id'])['connection_status'] == 'connected'


def test_broker_setup_preserves_private_environment_and_writes_only_verifier_path(tmp_path):
    from scripts.model_broker_setup import save_pairing_path
    path = tmp_path / '.env'
    path.write_text('MODEL_API_KEY=fixture-secret\nOWNER_TOKEN=fixture-owner\nMODEL_BROKER_PAIRING_FILE=old\n')
    save_pairing_path(tmp_path, tmp_path / 'Private Model Auth')
    result = path.read_text()
    assert result.count('MODEL_BROKER_PAIRING_FILE=') == 1
    assert 'MODEL_API_KEY=fixture-secret\nOWNER_TOKEN=fixture-owner\n' in result
    assert 'pairing.sha256' in result and 'pairing.key' not in result
    assert path.stat().st_mode & 0o077 == 0
    assert not list(tmp_path.glob('.env.broker-*'))


def test_vault_key_rotation_preserves_records_and_old_key_cannot_decrypt(vault):
    old_key = vault.keys[0]
    vault.put('account', {'refresh_token': 'private-rotation-fixture'})
    checkpoints = []
    vault.rotate_key(lambda keys: checkpoints.append(list(keys)))
    assert len(checkpoints) == 2 and old_key in checkpoints[0] and old_key not in checkpoints[1]
    assert vault.get('account') == {'refresh_token': 'private-rotation-fixture'}
    encrypted = vault.conn.execute("SELECT encrypted FROM state WHERE id='account'").fetchone()[0]
    with pytest.raises(InvalidToken):
        Fernet(old_key).decrypt(encrypted)


def test_key_rotation_cleanup_failure_retains_recoverable_keychain_keys(vault):
    vault.put('account', {'refresh_token': 'private-rotation-fixture'})
    saved = []
    def failing_cleanup(keys):
        if saved:
            raise RuntimeError('Simulated Keychain unavailable during cleanup')
        saved.append(list(keys))
    with pytest.raises(RuntimeError, match='Keychain unavailable'):
        vault.rotate_key(failing_cleanup)
    from cryptography.fernet import MultiFernet
    encrypted = vault.conn.execute("SELECT encrypted FROM state WHERE id='account'").fetchone()[0]
    assert json.loads(MultiFernet([Fernet(key) for key in saved[0]]).decrypt(encrypted))['refresh_token'] == 'private-rotation-fixture'


def test_stale_status_cannot_reactivate_disconnected_account(owner, monkeypatch):
    row = oauth_provider()
    async def old_status(cid, action, payload=None, **kwargs):
        with db.connection() as conn:
            conn.execute("UPDATE providers SET auth_epoch=auth_epoch+1,connection_status='disconnected',plan_enabled=false WHERE id=%s", (cid,))
        return {'status': 'connected', 'plan_enabled': True}
    monkeypatch.setattr(model_auth.relay, 'call', old_status)
    result = owner.post(f"/api/v1/providers/{row['id']}/chatgpt/status")
    assert result.status_code == 409
    assert model_auth.provider(row['id'])['connection_status'] == 'disconnected'


def test_disconnect_offline_blocks_admission_without_claiming_revocation(owner, monkeypatch):
    row = oauth_provider()
    async def offline(*args, **kwargs):
        raise model_auth.BrokerFailure('broker_offline')
    monkeypatch.setattr(model_auth.relay, 'call', offline)
    result = owner.post(f"/api/v1/providers/{row['id']}/chatgpt/disconnect")
    assert result.status_code == 503
    current = model_auth.provider(row['id'])
    assert current['connection_status'] == 'disconnecting' and not current['plan_enabled']
    assert current['auth_epoch'] == row['auth_epoch'] + 1
    assert owner.delete(f"/api/v1/providers/{row['id']}").status_code == 409


def test_quota_blocking_preserves_valid_account_and_requires_explicit_retry(owner, monkeypatch):
    row = oauth_provider()
    model_auth.record_failure(row['id'], model_auth.BrokerFailure('subscription_sharing_usage_limit_exceeded'))
    current = model_auth.provider(row['id'])
    assert current['connection_status'] == 'quota_blocked' and current['plan_enabled']
    with pytest.raises(Exception, match='Connect this ChatGPT'):
        model_auth.ready(current, 'model')
    async def retry(cid, action, payload=None, **kwargs):
        assert action == 'retry'
        return {'status': 'connected', 'plan_enabled': True}
    monkeypatch.setattr(model_auth.relay, 'call', retry)
    assert owner.post(f"/api/v1/providers/{row['id']}/chatgpt/retry").status_code == 200
    assert model_auth.provider(row['id'])['connection_status'] == 'connected'


def test_completed_model_test_is_fenced_against_account_changes(owner, monkeypatch):
    from contextlib import asynccontextmanager
    row = oauth_provider()
    @asynccontextmanager
    async def stale_test(cid, action, payload=None, **kwargs):
        async def events():
            with db.connection() as conn:
                conn.execute("UPDATE providers SET auth_epoch=auth_epoch+1,verified_models='[]' WHERE id=%s", (cid,))
            yield {'event': {'type': 'response.completed'}}
            yield {'result': {}}
        yield events()
    monkeypatch.setattr(model_auth.relay, 'request', stale_test)
    result = owner.post(f"/api/v1/providers/{row['id']}/chatgpt/test", json={'model': 'model'})
    assert result.status_code == 409
    assert not model_auth.provider(row['id'])['verified_models']


def test_subscription_internal_calls_require_current_authority(owner):
    row = oauth_provider()
    result = owner.post('/internal/model-completion', headers={'Authorization': 'Bearer ' + settings.internal_token}, json={
        'provider_id': str(row['id']), 'provider_revision': row['revision'], 'provider_auth_epoch': row['auth_epoch'] - 1,
        'payload': {'model': 'model', 'messages': [{'role': 'user', 'content': 'fixture'}]}})
    assert result.status_code == 409
    assert 'secret_encrypted' not in result.text


def test_model_stream_size_limit_applies_before_unterminated_line_can_grow():
    from backend.model_protocol import MAX_EVENT
    async def run():
        response = httpx.Response(200, content=b'data: ' + b'x' * (MAX_EVENT + 1))
        with pytest.raises(ValueError, match='size limit'):
            _ = [e async for e in sse_events(response)]
    asyncio.run(run())
