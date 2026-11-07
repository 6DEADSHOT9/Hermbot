"""Original implementation of OpenAI's documented local public-client protocol."""
import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4
import webbrowser

import httpx
import jwt

from backend.model_protocol import TOKEN_REFRESH_ERRORS

ISSUER = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
SCOPE = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'


class AccountError(Exception):
    def __init__(self, code, *, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


class Accounts:
    def __init__(self, vault, *, open_browser=webbrowser.open):
        self.vault, self.open_browser = vault, open_browser
        self.locks, self.epochs, self.signins = {}, {}, {}
        self.http = httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)
        self.discovery = None
        with self.vault.lock():
            if not self.vault.get('host'):
                self.vault.put('host', {'id': 'urn:uuid:' + str(uuid4())})

    def lock(self, cid):
        return self.locks.setdefault(cid, asyncio.Lock())

    def epoch(self, cid):
        return self.epochs.get(cid, 0)

    async def json_request(self, method, url, **kwargs):
        async with self.http.stream(method, url, **kwargs) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 1_000_000:
                    raise AccountError('invalid_provider_response')
            try:
                result = json.loads(data)
            except ValueError:
                raise AccountError('invalid_provider_response', retryable=response.status_code >= 500) from None
            if response.status_code != 200:
                code = result.get('error') if isinstance(result, dict) else None
                if isinstance(code, dict):
                    code = code.get('code')
                known = TOKEN_REFRESH_ERRORS | {'invalid_client'}
                raise AccountError(code if code in known else 'provider_unavailable', retryable=response.status_code >= 500 or response.status_code == 429)
            if not isinstance(result, dict):
                raise AccountError('invalid_provider_response')
            return result

    async def metadata(self):
        if self.discovery is None:
            value = await self.json_request('GET', ISSUER + '/.well-known/openid-configuration')
            if value.get('issuer') != ISSUER:
                raise AccountError('invalid_discovery')
            for name in ('jwks_uri', 'revocation_endpoint'):
                endpoint = value.get(name)
                if endpoint is None and name == 'revocation_endpoint':
                    continue
                parsed = urlsplit(endpoint or '')
                if parsed.scheme != 'https' or parsed.netloc != 'auth.openai.com' or parsed.username or parsed.fragment:
                    raise AccountError('invalid_discovery')
            self.discovery = value
        return self.discovery

    async def identity(self, token, client_id, *, nonce=None, subject=None, received_at=None):
        try:
            discovery = await self.metadata()
            jwks = await self.json_request('GET', discovery['jwks_uri'])
        except (httpx.HTTPError, AccountError):
            raise AccountError('verification_unavailable', retryable=True) from None
        try:
            header = jwt.get_unverified_header(token)
            if header.get('alg') != 'RS256' or header.get('jku') or header.get('x5u'):
                raise ValueError()
            keys = [k for k in jwks.get('keys', []) if k.get('kid') == header.get('kid') and k.get('kty') == 'RSA']
            if len(keys) != 1:
                raise AccountError('verification_unavailable', retryable=True)
            claims = jwt.decode(token, jwt.PyJWK.from_dict(keys[0], algorithm='RS256').key, algorithms=['RS256'],
                                audience=client_id, issuer=ISSUER, leeway=5,
                                options={'require': ['iss', 'aud', 'exp', 'iat', 'sub'], 'verify_exp': received_at is None, 'verify_iat': received_at is None, 'verify_nbf': received_at is None})
            at = time.time() if received_at is None else received_at
            if not isinstance(claims['sub'], str) or not claims['sub'] or claims['exp'] < at - 5 or claims['iat'] > at + 5 or claims.get('nbf', 0) > at + 5:
                raise ValueError()
            if nonce is not None and claims.get('nonce') != nonce:
                raise ValueError()
            if subject is not None and claims['sub'] != subject:
                raise ValueError()
            if claims.get('azp', client_id) != client_id or (isinstance(claims['aud'], list) and len(claims['aud']) > 1 and claims.get('azp') != client_id):
                raise ValueError()
            return {'subject': claims['sub'], 'label': str(claims.get('email', 'ChatGPT account'))[:200]}
        except AccountError:
            raise
        except (ValueError, TypeError, KeyError, jwt.PyJWTError):
            raise AccountError('invalid_identity') from None

    def status(self, cid):
        record = self.vault.get(cid) or {}
        status = 'verification_pending' if record.get('pending') else 'reauth_required' if record.get('refresh_in_progress') else record.get('status', 'disconnected')
        return {'status': status, 'label': record.get('label', ''),
                'plan_enabled': status == 'connected' and 'chatgpt.tokens.use.direct' in record.get('scope', '').split()}

    async def signin(self, cid):
        if cid in self.signins:
            raise AccountError('connection_busy')
        self.signins[cid] = asyncio.current_task()
        server = None
        original = self.vault.get(cid) or {}
        epoch = self.epoch(cid)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        callback = asyncio.get_running_loop().create_future()

        async def receive(reader, writer):
            ok = False
            try:
                raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
                if len(raw) > 8192:
                    raise ValueError()
                lines = raw.decode('ascii').split('\r\n')
                method, path, protocol = lines[0].split(' ')
                headers = [line.split(':', 1) for line in lines[1:] if line]
                hosts = [v.strip() for k, v in headers if k.lower() == 'host']
                parsed = urlsplit(path)
                params = parse_qs(parsed.query, keep_blank_values=True)
                if method != 'GET' or protocol != 'HTTP/1.1' or hosts != [f'127.0.0.1:{port}'] or parsed.path != '/auth/callback' or parsed.scheme or parsed.netloc:
                    raise ValueError()
                if any(len(v) != 1 for v in params.values()) or not hmac.compare_digest(params.get('state', [''])[0], state) or callback.done():
                    raise ValueError()
                callback.set_result({k: v[0] for k, v in params.items()})
                ok = True
            except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                pass
            finally:
                message = b'Authorization received. Return to Hermbot to check connection status.' if ok else b'Authorization was not accepted. Return to Hermbot.'
                writer.write(b'HTTP/1.1 ' + (b'200 OK' if ok else b'400 Bad Request') + b"\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src 'none'\r\nConnection: close\r\n\r\n" + message)
                await writer.drain()
                writer.close()

        try:
            server = await asyncio.start_server(receive, '127.0.0.1', 0, limit=8192)
            port = server.sockets[0].getsockname()[1]
            redirect = f'http://127.0.0.1:{port}/auth/callback'
            params = {'client_id': original.get('client_id', 'dynamic_agent_client'), 'ext_agent_host_id': self.vault.get('host')['id'],
                      'response_type': 'code', 'redirect_uri': redirect, 'scope': SCOPE, 'resource': RESOURCE,
                      'state': state, 'nonce': nonce, 'code_challenge_method': 'S256',
                      'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
            if not original.get('client_id'):
                params['agent_name_hint'] = 'Hermbot'
            if not self.open_browser(ISSUER + '/api/accounts/authorize?' + urlencode(params)):
                raise AccountError('browser_unavailable')
            value = await asyncio.wait_for(callback, 600)
            if value.get('error'):
                raise AccountError('authorization_denied')
            client_id = value.get('client_id', original.get('client_id', ''))
            if not client_id.startswith('oaiapp_') or (original.get('client_id') and original['client_id'] != client_id) or not value.get('code'):
                raise AccountError('invalid_registration')
            record = {**original, 'client_id': client_id}
            with self.vault.lock():
                self.vault.put(cid, record)
            result = await self.json_request('POST', ISSUER + '/api/accounts/oauth/token', data={
                'grant_type': 'authorization_code', 'client_id': client_id, 'code': value['code'], 'code_verifier': verifier,
                'redirect_uri': redirect, 'resource': RESOURCE})
            record['pending'] = self.credentials(result, nonce=nonce)
            with self.vault.lock():
                self.vault.put(cid, record)
            await self.verify_pending(cid, record, epoch)
            return self.status(cid)
        finally:
            if server:
                server.close()
                await server.wait_closed()
            self.signins.pop(cid, None)

    def credentials(self, result, **extra):
        if not all(isinstance(result.get(k), str) and result[k] for k in ('access_token', 'refresh_token', 'id_token', 'scope')) or result.get('token_type', '').lower() != 'bearer':
            raise AccountError('invalid_token_response')
        expiry = result.get('expires_in')
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not 0 < expiry <= 86400:
            raise AccountError('invalid_token_response')
        return {**result, 'expires_at': time.time() + expiry, 'received_at': time.time(), **extra}

    async def verify_pending(self, cid, record, epoch):
        pending = record['pending']
        identity = await self.identity(pending['id_token'], record['client_id'], nonce=pending.get('nonce'),
                                       subject=record.get('subject'), received_at=pending['received_at'])
        if epoch != self.epoch(cid):
            raise AccountError('connection_changed')
        record = {**record, **identity, 'credentials': pending, 'scope': pending['scope'], 'status': 'connected'}
        record.pop('pending', None)
        record.pop('refresh_in_progress', None)
        with self.vault.lock():
            self.vault.put(cid, record)
        return record

    async def access(self, cid):
        async with self.lock(cid):
            if cid in self.signins:
                raise AccountError('connection_busy')
            record = self.vault.get(cid) or {}
            epoch = self.epoch(cid)
            if record.get('pending'):
                record = await self.verify_pending(cid, record, epoch)
            if record.get('refresh_in_progress'):
                raise AccountError('refresh_uncertain')
            if record.get('status') != 'connected':
                raise AccountError('sign_in_required')
            if 'chatgpt.tokens.use.direct' not in record.get('scope', '').split():
                raise AccountError('plan_permission_required')
            creds = record['credentials']
            if creds['expires_at'] <= time.time() + 60:
                earliest = creds.get('earliest_refresh_at', 0)
                if isinstance(earliest, str):
                    from datetime import datetime
                    try:
                        earliest = datetime.fromisoformat(earliest.replace('Z', '+00:00')).timestamp()
                    except ValueError:
                        raise AccountError('invalid_token_response') from None
                if not isinstance(earliest, (float, int)) or earliest > time.time():
                    raise AccountError('refresh_not_ready', retryable=True)
                record['refresh_in_progress'] = True
                with self.vault.lock():
                    self.vault.put(cid, record)
                try:
                    result = await self.json_request('POST', ISSUER + '/api/accounts/oauth/token', data={
                        'grant_type': 'refresh_token', 'client_id': record['client_id'], 'refresh_token': creds['refresh_token'], 'resource': RESOURCE})
                    record['pending'] = self.credentials(result)
                    record.pop('refresh_in_progress', None)
                    with self.vault.lock():
                        self.vault.put(cid, record)
                    record = await self.verify_pending(cid, record, epoch)
                except AccountError as error:
                    if error.code in TOKEN_REFRESH_ERRORS:
                        with self.vault.lock():
                            self.vault.put(cid, {k: v for k, v in record.items() if k in ('client_id', 'subject', 'label')} | {'status': 'reauth_required'})
                    raise
            if 'chatgpt.tokens.use.direct' not in record.get('scope', '').split():
                raise AccountError('plan_permission_required')
            if record['credentials']['expires_at'] <= time.time():
                raise AccountError('refresh_not_ready', retryable=True)
            return record['credentials']['access_token'], epoch

    async def disconnect(self, cid):
        self.epochs[cid] = self.epoch(cid) + 1
        if cid in self.signins:
            self.signins[cid].cancel()
        async with self.lock(cid):
            record = self.vault.get(cid) or {}
            confirmed = False
            try:
                creds = record.get('pending', record.get('credentials', {}))
                if creds.get('refresh_token'):
                    endpoint = (await self.metadata()).get('revocation_endpoint')
                    if endpoint:
                        result = await self.http.post(endpoint, data={'token': creds['refresh_token'], 'token_type_hint': 'refresh_token', 'client_id': record['client_id']})
                        confirmed = result.status_code == 200
            except (httpx.HTTPError, AccountError):
                pass
            finally:
                with self.vault.lock():
                    self.vault.put(cid, {k: v for k, v in record.items() if k in ('client_id', 'subject', 'label')} | {'status': 'disconnected'})
            return {**self.status(cid), 'revocation_confirmed': confirmed}
