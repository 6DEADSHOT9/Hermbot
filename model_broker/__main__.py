"""Host broker: outbound loopback relay, explicit account operations, bounded inference."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from backend.model_protocol import MAX_BODY, PROTOCOL, responses_request, sse_events
from .accounts import AccountError, Accounts, RESOURCE
from .codex import BASE_URL, CodexAccounts
from .capacity import Capacity
from .vault import Vault, private_directory, private_file

DEFAULT_DIRECTORY = Path.home() / 'Library/Application Support/Hermbot/Model Auth'


async def serve(vault, secret, target):
    accounts = Accounts(vault)
    codex_accounts = CodexAccounts(vault)
    tasks, connections, quotas = {}, {}, set()
    send_lock = asyncio.Lock()
    slots = asyncio.Semaphore(3)
    per_account = {}
    try:
        while True:
            try:
                async with connect(target, additional_headers={'Authorization': 'Bearer ' + secret, 'X-Hermbot-Model-Protocol': str(PROTOCOL)},
                                   max_size=MAX_BODY + 4096, max_queue=8, proxy=None, open_timeout=5) as socket:
                    async def send(value):
                        async with send_lock:
                            await socket.send(json.dumps({**value, 'version': PROTOCOL}))

                    async def perform(message):
                        rid = message['id']
                        cid = message['connection_id']
                        kind = None
                        try:
                            UUID(cid)
                            kind = message.get('auth_kind', 'chatgpt_oauth')
                            if kind not in ('chatgpt_oauth', 'codex_oauth'):
                                raise AccountError('connection_kind_mismatch')
                            account = codex_accounts if kind == 'codex_oauth' else accounts
                            saved = vault.get(cid)
                            if saved and saved.get('kind', 'chatgpt_oauth') != kind:
                                raise AccountError('connection_kind_mismatch')
                            action = message['action']
                            if action == 'status':
                                if saved and saved.get('pending') and cid not in account.signins:
                                    async with account.lock(cid):
                                        await account.verify_pending(cid, saved, account.epoch(cid))
                                result = account.status(cid)
                                if cid in quotas:
                                    result['status'] = 'quota_blocked'
                            elif action in ('signin', 'device'):
                                result = codex_accounts.begin(cid, device=action == 'device') if kind == 'codex_oauth' else await accounts.signin(cid)
                                quotas.discard(cid)
                            elif action == 'disconnect':
                                for task_id, task_cid in list(connections.items()):
                                    if task_cid == cid and task_id != rid:
                                        tasks[task_id].cancel()
                                result = await account.disconnect(cid)
                                quotas.discard(cid)
                            elif action in ('models', 'responses'):
                                if cid in quotas:
                                    raise AccountError('codex_usage_limit' if kind == 'codex_oauth' else 'subscription_sharing_usage_limit_exceeded')
                                if action == 'responses':
                                    await send({'id': rid, 'state': 'waiting'})
                                # No unbounded waiting queue or hidden account/billing fallback.
                                async with asyncio.timeout(600), per_account.setdefault(cid, Capacity()).slot(message.get('caller', 'backend')), slots:
                                    if action == 'responses':
                                        await send({'id': rid, 'state': 'active'})
                                    token, epoch = await asyncio.shield(account.access(cid))
                                    headers = codex_accounts.headers(cid, token) if kind == 'codex_oauth' else {'Authorization': 'Bearer ' + token, 'Accept': 'text/event-stream'}
                                    base = BASE_URL if kind == 'codex_oauth' else RESOURCE
                                    if action == 'models':
                                        data = await codex_accounts.catalog(cid, token) if kind == 'codex_oauth' else await account.json_request('GET', base + '/models', headers={**headers, 'Accept': 'application/json'})
                                        result = {'models': [{'slug': m['slug'], 'display_name': m.get('display_name', m['slug'])} for m in data.get('models', []) if isinstance(m, dict) and m.get('visibility', 'list') == 'list' and isinstance(m.get('slug'), str)][:100], **account.status(cid)}
                                    else:
                                        body = responses_request(message['payload'], kind)
                                        terminal = False
                                        async with account.http.stream('POST', base + '/responses', headers=headers, json=body, timeout=httpx.Timeout(300, connect=10)) as response:
                                            if response.status_code != 200:
                                                code = 'model_request_rejected'
                                                candidate = None
                                                raw = bytearray()
                                                async for chunk in response.aiter_bytes():
                                                    raw.extend(chunk)
                                                    if len(raw) > 100_000:
                                                        break
                                                try:
                                                    candidate = json.loads(raw).get('error', {}).get('code')
                                                    if candidate in SAFE_CODES:
                                                        code = candidate
                                                except (ValueError, AttributeError):
                                                    pass
                                                if kind == 'codex_oauth':
                                                    code = 'sign_in_required' if response.status_code == 401 else 'codex_usage_limit' if candidate == 'usage_limit_reached' else 'codex_rate_limited' if response.status_code == 429 else code
                                                raise AccountError(code, retryable=response.status_code >= 500)
                                            total = 0
                                            async for event in sse_events(response):
                                                if epoch != account.epoch(cid):
                                                    raise AccountError('connection_changed')
                                                total += len(json.dumps(event).encode())
                                                if total > 32 * 1024 * 1024:
                                                    raise AccountError('response_too_large')
                                                if event['type'] in ('response.failed', 'response.incomplete', 'error'):
                                                    candidate = (event.get('response', {}).get('error') or event.get('error') or {}).get('code')
                                                    raise AccountError('codex_usage_limit' if kind == 'codex_oauth' and candidate == 'usage_limit_reached' else candidate if candidate in SAFE_CODES else 'model_response_incomplete')
                                                if event['type'] == 'response.completed' and event.get('response', {}).get('status') != 'completed':
                                                    raise AccountError('model_response_incomplete')
                                                await send({'id': rid, 'event': event})
                                                if event['type'] == 'response.completed':
                                                    terminal = True
                                                    break
                                        if not terminal:
                                            raise AccountError('model_response_interrupted')
                                        result = {}
                            elif action == 'retry':
                                quotas.discard(cid)
                                result = account.status(cid)
                            else:
                                raise AccountError('unsupported_operation')
                            await send({'id': rid, 'result': result})
                        except asyncio.CancelledError:
                            await send({'id': rid, 'error': 'cancelled'})
                        except Exception as error:
                            code = error.code if isinstance(error, AccountError) else 'broker_operation_failed'
                            if code == 'sign_in_required' and kind == 'codex_oauth':
                                current = vault.get(cid) or {}
                                vault.put(cid, {**current, 'status': 'reauth_required'})
                            if code in ('subscription_sharing_usage_limit_exceeded', 'codex_usage_limit'):
                                quotas.add(cid)
                            await send({'id': rid, 'error': code})
                        finally:
                            connections.pop(rid, None)
                            tasks.pop(rid, None)

                    async for raw in socket:
                        message = json.loads(raw)
                        if not isinstance(message, dict) or message.get('version') != PROTOCOL:
                            raise ValueError('Incompatible broker protocol')
                        rid = str(UUID(message['id']))
                        if message.get('action') == 'cancel':
                            if rid in tasks:
                                tasks[rid].cancel()
                            continue
                        if rid in tasks or len(tasks) >= (20 if message.get('action') in ('disconnect', 'status') else 16):
                            await send({'id': rid, 'error': 'broker_busy'})
                            continue
                        connections[rid] = message['connection_id']
                        tasks[rid] = asyncio.create_task(perform(message))
            except (OSError, ValueError, KeyError, TypeError, WebSocketException):
                pass
            finally:
                active = list(tasks.values())
                for task in active:
                    task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
            await asyncio.sleep(3)
    finally:
        for task in list(codex_accounts.signins.values()):
            task.cancel()
        await asyncio.gather(*list(codex_accounts.signins.values()), return_exceptions=True)
        await accounts.http.aclose()
        await codex_accounts.http.aclose()


SAFE_CODES = {'subscription_sharing_usage_limit_exceeded', 'subscription_sharing_usage_unavailable',
              'subscription_sharing_unsupported_capability', 'subscription_sharing_route_not_supported',
              'subscription_sharing_invalid_user', 'subscription_sharing_user_unavailable',
              'chatpass_v2_scope_not_authorized', 'chatpass_v2_invalid_authorization_context'}


def main():
    parser = argparse.ArgumentParser(description='Hermbot local ChatGPT credential broker')
    parser.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument('--api', default='ws://127.0.0.1:8780/internal/model-broker')
    parser.add_argument('--prepare', action='store_true', help='Create the private pairing file without signing in')
    parser.add_argument('--rotate-vault-key', action='store_true', help='Rotate the OS-held vault key while the broker service is stopped')
    args = parser.parse_args()
    parsed = urlsplit(args.api)
    if parsed.scheme != 'ws' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != '/internal/model-broker':
        parser.error('The broker requires the exact local 127.0.0.1 API endpoint')
    directory = private_directory(args.directory)
    fd = private_file(directory / 'broker.lock')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        pairing = directory / 'pairing.key'
        key_fd = private_file(pairing)
        with os.fdopen(key_fd, 'r+') as file:
            secret = file.read(200).strip()
            if not secret:
                secret = secrets.token_urlsafe(48)
                file.write(secret)
                file.flush()
                os.fsync(file.fileno())
        if len(secret) < 48:
            raise RuntimeError('Invalid broker pairing file')
        digest_path = directory / 'pairing.sha256'
        digest_fd = os.open(digest_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(digest_fd, 'w') as file:
            file.write(hashlib.sha256(secret.encode()).hexdigest())
            file.truncate()
            file.flush()
            os.fchmod(file.fileno(), 0o644)  # Only the non-secret verifier is readable by the API UID.
            os.fsync(file.fileno())
        if args.prepare:
            print('Pairing prepared. Mount pairing.sha256 into the API; pairing.key stays private on this host.')
            return
        vault = Vault(directory)
        try:
            if args.rotate_vault_key:
                vault.rotate_key()
                print('Vault encryption key rotated and verified. Account registrations were preserved.')
                return
            asyncio.run(serve(vault, secret, args.api))
        finally:
            vault.close()
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
