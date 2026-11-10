"""Authenticated local broker relay. OpenAI tokens never enter this process."""
import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import time
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb

from . import auth, db, model_relay, registry, service
from .model_protocol import BROKERED_AUTH, CODEX_URL, MAX_BODY, MAX_EVENT, PROTOCOL, REAUTH_ERRORS, chat_to_responses, completed_message, responses_request
from .secrets import credential_hash
from .settings import settings


class BrokerFailure(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Relay:
    def __init__(self):
        self.socket = None
        self.pending = {}
        self.send_lock = asyncio.Lock()

    async def send(self, value):
        async with self.send_lock:
            if self.socket is None:
                raise BrokerFailure('broker_offline')
            await self.socket.send_json({**value, 'version': PROTOCOL})

    @asynccontextmanager
    async def request(self, cid, action, payload=None, *, auth_kind='chatgpt_oauth', caller='backend'):
        if len(self.pending) >= (20 if action in ('disconnect', 'status') else 16):
            raise BrokerFailure('broker_busy')
        rid = str(uuid4())
        queue = asyncio.Queue(maxsize=32)
        self.pending[rid] = (str(cid), queue)
        try:
            await self.send({'id': rid, 'connection_id': str(cid), 'action': action, 'payload': payload or {}, 'auth_kind': auth_kind, 'caller': caller})
            async def events():
                async with asyncio.timeout(650):
                    while True:
                        item = await queue.get()
                        if 'error' in item:
                            raise BrokerFailure(item['error'])
                        yield item
                        if 'result' in item:
                            return
            yield events()
        finally:
            self.pending.pop(rid, None)
            async def cancel_upstream():
                try:
                    async with asyncio.timeout(2):
                        await self.send({'id': rid, 'action': 'cancel'})
                except Exception:
                    pass
            # ASGI disconnect cancellation must not cancel its own upstream cancellation message.
            await asyncio.shield(asyncio.create_task(cancel_upstream()))

    async def call(self, cid, action, payload=None, **kwargs):
        async with self.request(cid, action, payload, **kwargs) as events:
            async for item in events:
                if 'result' in item:
                    return item['result']

    def fail(self, cid=None):
        for connection, queue in self.pending.values():
            if cid is None or str(cid) == connection:
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({'error': 'connection_ended'})


relay = Relay()


def provider(cid):
    with db.connection() as conn:
        row = conn.execute('SELECT * FROM providers WHERE id=%s', (cid,)).fetchone()
    if not row or row['auth_kind'] not in BROKERED_AUTH:
        raise HTTPException(404, 'ChatGPT connection not found')
    return row


def ready(row, model, *, require_test=False):
    if row['connection_status'] != 'connected' or not row['plan_enabled']:
        raise HTTPException(409, 'Connect this ChatGPT account and enable plan usage in Providers')
    if model not in row['models']:
        raise HTTPException(403, 'This model is not permitted for the selected provider')
    if require_test and model not in row['verified_models']:
        raise HTTPException(409, 'model_test_required')


def record_failure(cid, error, epoch=None):
    if error.code in ('subscription_sharing_usage_limit_exceeded', 'codex_usage_limit'):
        with db.connection() as conn:
            conn.execute("UPDATE providers SET connection_status='quota_blocked' WHERE id=%s AND auth_kind IN ('chatgpt_oauth','codex_oauth') AND connection_status='connected' AND (%s::integer IS NULL OR auth_epoch=%s)", (cid, epoch, epoch))
    elif error.code in REAUTH_ERRORS:
        with db.connection() as conn:
            conn.execute("UPDATE providers SET connection_status='reauth_required',plan_enabled=false WHERE id=%s AND auth_kind IN ('chatgpt_oauth','codex_oauth') AND (%s::integer IS NULL OR auth_epoch=%s)", (cid, epoch, epoch))


def bearer(authorization):
    return authorization[7:] if authorization.startswith('Bearer ') else ''


def admit_inference(token):
    """A runtime's relay token is valid only for its bot's current revision, while the bot runs."""
    with db.connection() as conn:
        bot = conn.execute("SELECT * FROM bots WHERE inference_key_hash=%s AND inference_revision=revision AND status='active' AND desired_state='running'", (credential_hash(token),)).fetchone() if token else None
        if not bot:
            raise HTTPException(401, 'Inference credential expired; apply the current runtime configuration')
        stopping = conn.execute("SELECT 1 FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE a.bot_id=%s AND a.status=ANY(%s) AND t.status=ANY(%s)",
                                (bot['id'], list(service.ACTIVE_ATTEMPTS), list(service.TERMINAL | service.STOPPING))).fetchone()
        if stopping:
            raise HTTPException(409, 'This execution is stopping; no further inference is admitted')
        row = conn.execute('SELECT * FROM providers WHERE id=%s', (bot['provider_id'],)).fetchone()
        if not row:
            raise HTTPException(409, 'This teammate has no model provider')
        return bot, row


async def body(request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BODY:
            raise HTTPException(413, 'Model request exceeds the size limit')
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except ValueError:
        raise HTTPException(422, 'Invalid model request') from None


def routes(owner):
    router = APIRouter()

    @router.websocket('/internal/model-broker')
    async def broker(socket: WebSocket):
        path = os.environ.get('MODEL_BROKER_TOKEN_FILE')
        try:
            secret = Path(path).read_text().strip() if path else ''
        except OSError:
            secret = ''
        bearer = socket.headers.get('authorization', '')
        supplied = bearer[7:] if bearer.startswith('Bearer ') else ''
        if len(secret) != 64 or socket.headers.get('origin') or socket.headers.get('x-hermbot-model-protocol') != str(PROTOCOL) or not hmac.compare_digest(credential_hash(supplied), secret) or relay.socket is not None:
            await socket.close(code=1008)
            return
        await socket.accept()
        relay.socket = socket
        try:
            while True:
                raw = await socket.receive_text()
                if len(raw.encode()) > MAX_EVENT + 8192:
                    break
                item = json.loads(raw)
                if not isinstance(item, dict) or item.get('version') != PROTOCOL:
                    break
                entry = relay.pending.get(item.get('id'))
                if entry:
                    try:
                        await asyncio.wait_for(entry[1].put(item), 5)
                    except TimeoutError:
                        while not entry[1].empty():
                            entry[1].get_nowait()
                        entry[1].put_nowait({'error': 'slow_consumer'})
                        await relay.send({'id': item['id'], 'action': 'cancel'})
        except (WebSocketDisconnect, ValueError, TypeError):
            pass
        finally:
            relay.socket = None
            relay.fail()

    @router.post('/api/v1/providers/chatgpt', dependencies=[Depends(owner)], status_code=201)
    def create():
        with db.connection() as conn:
            cid = uuid4()
            # Pending connections cannot be selected until a catalog is obtained and a model tested.
            conn.execute("INSERT INTO providers(id,name,base_url,models,default_model,auth_kind,transport,name_is_custom) VALUES(%s,%s,'https://api.openai.com/v1','[]','','chatgpt_oauth','responses',false)",
                         (cid, registry.provider_name(conn, 'chatgpt_oauth')))
            return conn.execute(f'SELECT {registry.PROVIDER_COLUMNS} FROM providers WHERE id=%s', (cid,)).fetchone()

    @router.post('/api/v1/providers/codex', dependencies=[Depends(owner)], status_code=201)
    def create_codex():
        with db.connection() as conn:
            cid = uuid4()
            conn.execute("INSERT INTO providers(id,name,base_url,models,default_model,auth_kind,transport,name_is_custom) VALUES(%s,%s,%s,'[]','','codex_oauth','responses',false)", (cid, registry.provider_name(conn, 'codex_oauth'), CODEX_URL))
            return conn.execute(f'SELECT {registry.PROVIDER_COLUMNS} FROM providers WHERE id=%s', (cid,)).fetchone()

    @router.post('/api/v1/providers/{cid}/chatgpt/{action}', dependencies=[Depends(owner)])
    async def manage(cid: UUID, action: str, request: Request):
        row = provider(cid)
        if action not in ('signin', 'device', 'status', 'disconnect', 'cancel', 'models', 'test', 'retry') or (action == 'device' and row['auth_kind'] != 'codex_oauth'):
            raise HTTPException(404, 'Unknown connection operation')
        # Pairing and browser authorization are local-only for this implementation.
        if request.headers.get('origin') and request.headers['origin'] not in ('http://localhost:8780', 'http://127.0.0.1:8780'):
            raise HTTPException(403, 'Use the local workspace to manage ChatGPT authentication')
        try:
            if action in ('signin', 'device'):
                with db.connection() as conn:
                    current = conn.execute('SELECT * FROM providers WHERE id=%s FOR UPDATE', (cid,)).fetchone()
                    if not current or current['auth_epoch'] != row['auth_epoch']:
                        raise HTTPException(409, 'Connection changed; reload Providers')
                    for bot in conn.execute("SELECT * FROM bots WHERE provider_id=%s AND status<>'deleted' ORDER BY id FOR UPDATE", (cid,)).fetchall():
                        registry.reserve_lifecycle(conn, bot)
                    row = conn.execute("UPDATE providers SET connection_status='connecting',plan_enabled=false,auth_epoch=auth_epoch+1 WHERE id=%s RETURNING *", (cid,)).fetchone()
                relay.fail(cid)
            if action in ('disconnect', 'cancel'):
                with db.connection() as conn:
                    row = conn.execute("UPDATE providers SET connection_status='disconnecting',plan_enabled=false,auth_epoch=auth_epoch+1 WHERE id=%s RETURNING *", (cid,)).fetchone()
                    if not row:
                        raise HTTPException(404, 'Connection was removed')
                relay.fail(cid)
                result = await relay.call(cid, 'disconnect', auth_kind=row['auth_kind'])
            elif action == 'test':
                value = await body(request)
                model = value.get('model')
                ready(row, model)
                completed = False
                async with relay.request(cid, 'responses', {'model': model, 'input': [{'role': 'user', 'content': 'Reply exactly: Connected'}], 'store': False, 'stream': True}, auth_kind=row['auth_kind'], caller='owner:test') as events:
                    async for item in events:
                        if item.get('event', {}).get('type') == 'response.completed':
                            completed = True
                if not completed:
                    raise BrokerFailure('model_response_interrupted')
                with db.connection() as conn:
                    updated = conn.execute("UPDATE providers SET verified_models=verified_models || %s WHERE id=%s AND revision=%s AND auth_epoch=%s RETURNING id", (Jsonb([model]), cid, row['revision'], row['auth_epoch'])).fetchone()
                    if not updated:
                        raise HTTPException(409, 'Connection changed during the model test')
                return {'verified': True, 'model': model}
            else:
                result = await relay.call(cid, action, auth_kind=row['auth_kind'])
                # The browser session that authorized the operation must still exist.
                try:
                    auth.verify(request.headers.get('authorization'), request.cookies.get('hermbot_session'))
                except HTTPException:
                    if action in ('signin', 'device'):
                        await relay.call(cid, 'disconnect', auth_kind=row['auth_kind'])
                    raise
            if action in ('signin', 'device', 'models', 'status', 'retry', 'disconnect', 'cancel'):
                status = result.get('status', 'disconnected')
                if status not in ('connecting', 'connected', 'disconnected', 'reauth_required', 'verification_pending', 'quota_blocked'):
                    status = 'disconnected'
                with db.connection() as conn:
                    conn.execute('SELECT pg_advisory_xact_lock(887832103)')
                    updated = conn.execute("UPDATE providers SET connection_status=%s,account_label=%s,plan_enabled=%s,verified_models=CASE WHEN %s THEN '[]'::jsonb ELSE verified_models END WHERE id=%s AND auth_epoch=%s RETURNING id",
                                           (status, str(result.get('label', ''))[:200], result.get('plan_enabled') is True, action in ('signin', 'device'), cid, row['auth_epoch'])).fetchone()
                    if not updated:
                        raise HTTPException(409, 'Connection changed during this operation')
                    conn.execute('UPDATE providers SET account_plan=%s WHERE id=%s', (str(result.get('plan', ''))[:40], cid))
                    current_name = conn.execute('SELECT name_is_custom,auth_kind,account_label FROM providers WHERE id=%s', (cid,)).fetchone()
                    if not current_name['name_is_custom']:
                        conn.execute('UPDATE providers SET name=%s WHERE id=%s', (registry.provider_name(conn, current_name['auth_kind'], current_name['account_label'], cid), cid))
                    if action == 'models':
                        catalog = [m['slug'] for m in result.get('models', []) if isinstance(m.get('slug'), str) and 0 < len(m['slug']) <= 200][:100]
                        current = conn.execute('SELECT * FROM providers WHERE id=%s', (cid,)).fetchone()
                        conn.execute('UPDATE providers SET discovered_models=%s WHERE id=%s', (Jsonb(catalog), cid))
                        # Preserve explicit subsets and concurrent owner edits; an unrestricted catalog follows refresh.
                        if catalog and current['revision'] == row['revision'] and (not current['models'] or set(current['discovered_models']).issubset(current['models'])):
                            used = conn.execute("SELECT model_name FROM bots WHERE provider_id=%s AND status<>'deleted'", (cid,)).fetchall()
                            default = current['default_model'] or catalog[0]
                            permitted = list(dict.fromkeys(catalog + [b['model_name'] for b in used] + ([default] if current['default_model'] else [])))
                            conn.execute('UPDATE providers SET models=%s,default_model=%s WHERE id=%s', (Jsonb(permitted), default, cid))
                return {k: result[k] for k in ('status', 'label', 'plan_enabled', 'models', 'revocation_confirmed', 'error', 'device_code', 'verification_url') if k in result}
        except BrokerFailure as error:
            record_failure(cid, error, row['auth_epoch'])
            if action in ('signin', 'device'):
                with db.connection() as conn:
                    conn.execute("UPDATE providers SET connection_status='reauth_required',plan_enabled=false WHERE id=%s AND auth_epoch=%s AND connection_status='connecting'", (cid, row['auth_epoch']))
            raise HTTPException(503, {'message': 'ChatGPT connection could not complete: ' + error.code, 'recovery': 'Check the local broker and Providers. No other account or API billing was used.'}) from None

    @router.post('/internal/model-completion')
    async def completion(request: Request, authorization: str = Header(default='')):
        if not hmac.compare_digest(authorization, 'Bearer ' + settings.internal_token):
            raise HTTPException(401, 'Internal model authentication required')
        value = await body(request)
        try:
            row = provider(UUID(value['provider_id']))
            payload = chat_to_responses(value['payload'], row['auth_kind'])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, 'Invalid subscription completion request') from None
        if row['revision'] != value.get('provider_revision') or row['auth_epoch'] != value.get('provider_auth_epoch'):
            raise HTTPException(409, 'Provider configuration changed before inference')
        ready(row, payload['model'], require_test=True)
        result = None
        try:
            async with relay.request(row['id'], 'responses', payload, auth_kind=row['auth_kind'], caller='backend:' + str(value.get('caller', 'model'))[:40]) as events:
                async for item in events:
                    event = item.get('event', {})
                    if event.get('type') == 'response.completed':
                        result = event['response']
            if result is None:
                raise BrokerFailure('model_response_interrupted')
            current = provider(row['id'])
            ready(current, payload['model'], require_test=True)
            if current['revision'] != row['revision'] or current['auth_epoch'] != row['auth_epoch']:
                raise BrokerFailure('connection_changed')
            return {'choices': [{'message': completed_message(result)}], 'usage': result.get('usage', {})}
        except BrokerFailure as error:
            record_failure(row['id'], error, row['auth_epoch'])
            raise HTTPException(503, error.code) from None

    @router.post('/internal/inference/v1/responses')
    async def inference(request: Request, authorization: str = Header(default='')):
        token = bearer(authorization)
        bot, row = await asyncio.to_thread(admit_inference, token)
        if row['auth_kind'] not in BROKERED_AUTH:
            return await model_relay.forward(request, 'responses', token, bot, row, admit_inference)
        try:
            payload = responses_request(await body(request), row['auth_kind'])
        except ValueError:
            raise HTTPException(422, 'Unsupported subscription model request') from None
        ready(row, payload['model'], require_test=True)
        if payload['model'] != bot['model_name']:
            raise HTTPException(403, 'Use this teammate’s assigned model')
        task = await asyncio.to_thread(model_relay.active_task, bot['id'])
        async def stream():
            request_id, checked = None, time.monotonic()
            try:
                async with relay.request(row['id'], 'responses', payload, auth_kind=row['auth_kind'], caller='bot:' + bot['id']) as events:
                    async for item in events:
                        # Re-check admission every few seconds off the event loop, not once per streamed event.
                        if time.monotonic() - checked >= model_relay.ADMISSION_SECONDS:
                            checked = time.monotonic()
                            latest_bot, latest = await asyncio.to_thread(admit_inference, token)
                            ready(latest, payload['model'], require_test=True)
                            if latest_bot['revision'] != bot['revision'] or latest['revision'] != row['revision'] or latest['auth_epoch'] != row['auth_epoch']:
                                raise BrokerFailure('connection_changed')
                        if item.get('state') in ('waiting', 'active') and task:
                            request_id = item['id']
                            await asyncio.to_thread(model_relay.record, task, item['state'], request_id)
                        if 'event' in item:
                            yield 'event: ' + item['event']['type'] + '\ndata: ' + json.dumps(item['event']) + '\n\n'
            except (BrokerFailure, HTTPException, TimeoutError) as error:
                if isinstance(error, BrokerFailure):
                    record_failure(row['id'], error, row['auth_epoch'])
                # A late failure must remain a failure to native stream consumers.
                yield 'event: error\ndata: ' + json.dumps({'type': 'error', 'code': 'hermbot_inference_unavailable', 'message': 'Inference ended before successful completion. Inspect provider status; do not replay completed tools.'}) + '\n\n'
            finally:
                if request_id and task:
                    with db.connection() as conn:
                        db.event(conn, 'model.request.finished', task, data={'request_id': request_id})
        return StreamingResponse(stream(), media_type='text/event-stream')

    async def api_key_only(request, path, authorization):
        token = bearer(authorization)
        bot, row = await asyncio.to_thread(admit_inference, token)
        if row['auth_kind'] in BROKERED_AUTH:
            raise HTTPException(404, 'Subscription providers use the Responses route only')
        return await model_relay.forward(request, path, token, bot, row, admit_inference)

    @router.post('/internal/inference/v1/chat/completions')
    async def chat_completions(request: Request, authorization: str = Header(default='')):
        return await api_key_only(request, 'chat/completions', authorization)

    @router.post('/internal/inference/v1/messages')
    async def messages(request: Request, authorization: str = Header(default='')):
        return await api_key_only(request, 'messages', authorization)

    @router.get('/internal/inference/v1/models')
    async def models(request: Request, authorization: str = Header(default='')):
        return await api_key_only(request, 'models', authorization)

    return router
