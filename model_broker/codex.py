"""Hermbot-owned Codex OAuth session; never imports another client's token chain."""
import asyncio
import base64
import hashlib
import hmac
import json
import math
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from backend.model_protocol import TOKEN_REFRESH_ERRORS

from .accounts import AccountError, Accounts, ISSUER

CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann'
BASE_URL = 'https://chatgpt.com/backend-api/codex'
CALLBACK_PORT = 1455
REDIRECT_URI = 'http://localhost:1455/auth/callback'
SCOPE = 'openid profile email offline_access'
ERRORS = {'authorization_denied', 'browser_unavailable', 'callback_port_busy', 'authorization_timeout',
          'invalid_identity', 'verification_unavailable', 'duplicate_account', 'account_changed',
          'device_authorization_unavailable', 'provider_unavailable', 'invalid_token_response'}


class CodexAccounts(Accounts):
    def status(self, cid):
        record = self.vault.get(cid) or {}
        status = 'connecting' if cid in self.signins else 'verification_pending' if record.get('pending') else 'reauth_required' if record.get('refresh_in_progress') else record.get('status', 'disconnected')
        if status == 'connecting' and cid not in self.signins:
            status = 'reauth_required'
            record = {**record, 'status': status, 'error': 'sign_in_interrupted'}
            self.vault.put(cid, record)
        plan = ''
        if record.get('credentials'):
            try:
                # This ID token was verified before promotion into encrypted credentials.
                claims = jwt.decode(record['credentials']['id_token'], options={'verify_signature': False})
                value = claims.get('https://api.openai.com/auth', {}).get('chatgpt_plan_type')
                if value in ('free', 'go', 'plus', 'pro', 'team', 'business', 'enterprise', 'edu'):
                    plan = value.upper() if value == 'edu' else value.capitalize()
            except (KeyError, TypeError, AttributeError, jwt.PyJWTError):
                pass
        return {'status': status, 'label': record.get('label', ''), 'plan_enabled': status == 'connected',
                'plan': plan,
                'error': record.get('error', ''), **({'device_code': record['device_code'], 'verification_url': ISSUER + '/codex/device'} if status == 'connecting' and record.get('device_code') else {})}

    def begin(self, cid, *, device=False):
        if cid in self.signins:
            raise AccountError('connection_busy')
        record = self.vault.get(cid) or {}
        if record.get('kind', 'codex_oauth') != 'codex_oauth':
            raise AccountError('connection_kind_mismatch')
        self.epochs[cid] = self.epoch(cid) + 1
        epoch = self.epoch(cid)
        self.vault.put(cid, {k: v for k, v in record.items() if k in ('subject', 'account_id', 'label')} | {'kind': 'codex_oauth', 'status': 'connecting'})

        async def run():
            try:
                await (self.device_signin(cid, record, epoch) if device else self.browser_signin(cid, record, epoch))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if epoch == self.epoch(cid):
                    current = self.vault.get(cid) or {}
                    code = 'authorization_timeout' if isinstance(error, TimeoutError) else error.code if isinstance(error, AccountError) and error.code in ERRORS else 'authorization_failed'
                    self.vault.put(cid, {**current, 'status': 'reauth_required', 'error': code})
            finally:
                self.signins.pop(cid, None)
        self.signins[cid] = asyncio.create_task(run())
        return self.status(cid)

    async def browser_signin(self, cid, original, epoch):
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
                hosts = [line.split(':', 1)[1].strip() for line in lines[1:] if line.lower().startswith('host:')]
                parsed = urlsplit(path)
                params = parse_qs(parsed.query, keep_blank_values=True)
                if method != 'GET' or protocol != 'HTTP/1.1' or hosts != [f'localhost:{CALLBACK_PORT}'] or parsed.path != '/auth/callback' or parsed.scheme or parsed.netloc:
                    raise ValueError()
                if any(len(v) != 1 for v in params.values()) or not hmac.compare_digest(params.get('state', [''])[0], state) or callback.done():
                    raise ValueError()
                callback.set_result({k: v[0] for k, v in params.items()})
                ok = True
            except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                pass
            finally:
                writer.write(b'HTTP/1.1 ' + (b'200 OK' if ok else b'400 Bad Request') + b"\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src 'none'\r\nConnection: close\r\n\r\nReturn to Hermbot for connection status.")
                await writer.drain()
                writer.close()

        try:
            server = await asyncio.start_server(receive, '127.0.0.1', CALLBACK_PORT, limit=8192)
        except OSError:
            raise AccountError('callback_port_busy') from None
        try:
            params = {'client_id': CLIENT_ID, 'response_type': 'code', 'redirect_uri': REDIRECT_URI, 'scope': SCOPE,
                      'state': state, 'nonce': nonce, 'code_challenge_method': 'S256',
                      'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
                      'id_token_add_organizations': 'true', 'codex_cli_simplified_flow': 'true', 'originator': 'hermbot'}
            if not self.open_browser(ISSUER + '/oauth/authorize?' + urlencode(params)):
                raise AccountError('browser_unavailable')
            try:
                value = await asyncio.wait_for(callback, 600)
            except TimeoutError:
                raise AccountError('authorization_timeout') from None
            if value.get('error') or not value.get('code'):
                raise AccountError('authorization_denied')
            result = await self.json_request('POST', ISSUER + '/oauth/token', data={
                'grant_type': 'authorization_code', 'client_id': CLIENT_ID, 'code': value['code'],
                'redirect_uri': REDIRECT_URI, 'code_verifier': verifier})
            await self.accept(cid, original, result, epoch, nonce=nonce)
        finally:
            server.close()
            await server.wait_closed()

    async def device_signin(self, cid, original, epoch):
        data = await self.json_request('POST', ISSUER + '/api/accounts/deviceauth/usercode', json={'client_id': CLIENT_ID})
        code, device_id = data.get('user_code'), data.get('device_auth_id')
        if not isinstance(code, str) or not 1 <= len(code) <= 32 or not isinstance(device_id, str) or not device_id:
            raise AccountError('device_authorization_unavailable')
        interval = max(5, min(30, int(data.get('interval', 5))))
        self.vault.put(cid, {**(self.vault.get(cid) or {}), 'device_code': code})
        async with asyncio.timeout(600):
            while epoch == self.epoch(cid):
                await asyncio.sleep(interval)
                async with self.http.stream('POST', ISSUER + '/api/accounts/deviceauth/token', json={'device_auth_id': device_id, 'user_code': code}) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 100_000:
                            raise AccountError('invalid_token_response')
                    if response.status_code in (403, 404):
                        continue
                    if response.status_code != 200:
                        raise AccountError('authorization_denied')
                    value = json.loads(raw)
                if not all(isinstance(value.get(k), str) and value[k] for k in ('authorization_code', 'code_verifier')):
                    raise AccountError('invalid_token_response')
                result = await self.json_request('POST', ISSUER + '/oauth/token', data={
                    'grant_type': 'authorization_code', 'client_id': CLIENT_ID, 'code': value['authorization_code'],
                    'redirect_uri': ISSUER + '/deviceauth/callback', 'code_verifier': value['code_verifier']})
                await self.accept(cid, original, result, epoch)
                return

    def credentials(self, result, **extra):
        if not all(isinstance(result.get(k), str) and result[k] for k in ('access_token', 'refresh_token', 'id_token')) or result.get('token_type', 'bearer').lower() != 'bearer':
            raise AccountError('invalid_token_response')
        expiry = result.get('expires_in')
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or not 0 < expiry <= 30 * 86400:
            raise AccountError('invalid_token_response')
        return {**result, 'scope': result.get('scope') or SCOPE, 'expires_at': time.time() + expiry, 'received_at': time.time(), **extra}

    async def accept(self, cid, original, result, epoch, **extra):
        if epoch != self.epoch(cid):
            raise AccountError('connection_changed')
        record = {k: v for k, v in original.items() if k in ('subject', 'account_id', 'label')}
        record.update(kind='codex_oauth', client_id=CLIENT_ID, pending=self.credentials(result, **extra))
        self.vault.put(cid, record)
        await self.verify_pending(cid, record, epoch)

    async def verify_pending(self, cid, record, epoch):
        pending = record['pending']
        identity = await self.identity(pending['id_token'], CLIENT_ID, nonce=pending.get('nonce'), subject=record.get('subject'), received_at=pending['received_at'])
        claims = jwt.decode(pending['id_token'], options={'verify_signature': False})
        account_id = claims.get('https://api.openai.com/auth', {}).get('chatgpt_account_id')
        if not isinstance(account_id, str) or not account_id or len(account_id) > 200:
            raise AccountError('invalid_identity')
        if record.get('account_id', account_id) != account_id:
            raise AccountError('account_changed')
        if epoch != self.epoch(cid):
            raise AccountError('connection_changed')
        with self.vault.lock():
            for (other_id,) in self.vault.conn.execute('SELECT id FROM state WHERE id<>?', (cid,)):
                other = self.vault.get(other_id) or {}
                if other.get('kind') == 'codex_oauth' and other.get('account_id') == account_id and (other.get('credentials') or other.get('pending')):
                    raise AccountError('duplicate_account')
            record = {**record, **identity, 'account_id': account_id, 'credentials': pending, 'status': 'connected'}
            for key in ('pending', 'refresh_in_progress', 'error', 'device_code'):
                record.pop(key, None)
            self.vault.put(cid, record)
        return record

    async def access(self, cid):
        async with self.lock(cid):
            if cid in self.signins:
                raise AccountError('connection_busy')
            record = self.vault.get(cid) or {}
            if record.get('kind') != 'codex_oauth':
                raise AccountError('connection_kind_mismatch')
            epoch = self.epoch(cid)
            if record.get('pending'):
                record = await self.verify_pending(cid, record, epoch)
            if record.get('refresh_in_progress'):
                raise AccountError('refresh_uncertain')
            if record.get('status') != 'connected':
                raise AccountError('sign_in_required')
            creds = record['credentials']
            if creds['expires_at'] <= time.time() + 60:
                record['refresh_in_progress'] = True
                self.vault.put(cid, record)
                try:
                    result = await self.json_request('POST', ISSUER + '/oauth/token', data={'grant_type': 'refresh_token', 'client_id': CLIENT_ID, 'refresh_token': creds['refresh_token']})
                    # Some rotations omit an ID token; retain its already-verified identity and timestamp.
                    if not result.get('id_token'):
                        result = {**result, 'id_token': creds['id_token']}
                        extra = {'received_at': creds['received_at']}
                    else:
                        extra = {}
                    if epoch != self.epoch(cid):
                        raise AccountError('connection_changed')
                    record['pending'] = self.credentials({**result, 'refresh_token': result.get('refresh_token') or creds['refresh_token']}, **extra)
                    record.pop('refresh_in_progress', None)
                    self.vault.put(cid, record)
                    record = await self.verify_pending(cid, record, epoch)
                except AccountError as error:
                    if error.code in TOKEN_REFRESH_ERRORS:
                        self.vault.put(cid, {k: v for k, v in record.items() if k in ('kind', 'subject', 'account_id', 'label')} | {'status': 'reauth_required'})
                    raise
            return record['credentials']['access_token'], epoch

    def headers(self, cid, token):
        return {'Authorization': 'Bearer ' + token, 'ChatGPT-Account-Id': self.vault.get(cid)['account_id'],
                'Accept': 'text/event-stream', 'originator': 'hermbot'}

    async def catalog(self, cid, token):
        # Match pinned Hermes: request the newest catalog before its legacy compatibility fallback.
        failure = None
        for version in ('99.0.0', '0.0.0'):
            try:
                data = await self.json_request('GET', BASE_URL + '/models?client_version=' + version,
                                               headers={**self.headers(cid, token), 'Accept': 'application/json'})
                if isinstance(data.get('models'), list) and data['models']:
                    return data
            except (AccountError, httpx.HTTPError) as error:
                failure = error
        if failure:
            raise failure
        return {'models': []}

    async def disconnect(self, cid):
        self.epochs[cid] = self.epoch(cid) + 1
        task = self.signins.get(cid)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self.lock(cid):
            record = self.vault.get(cid) or {}
            self.vault.put(cid, {k: v for k, v in record.items() if k in ('subject', 'account_id', 'label')} | {'kind': 'codex_oauth', 'status': 'disconnected'})
        # No unverified revocation endpoint is called with the Codex refresh token.
        return {**self.status(cid), 'revocation_confirmed': False}
