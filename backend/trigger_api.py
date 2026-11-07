"""Private trigger setup and provider-authenticated ingress; credentials never enter receipts."""
import asyncio
import base64
from datetime import datetime, timedelta
import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import ValidationError

from . import connections, db, registry, service, triggers
from .routine_models import RoutineControl
from .secrets import credential_hash, decrypt, encrypt
from .trigger_models import EVENTS, SourceDefinition, SourceSecrets, TriggerDefinition, TriggerPreview
from .trigger_providers import safe_normalize, verify_signature


MAX_BODY = 1_048_576
SAFE_HEADERS = ('x-github-event', 'x-github-delivery', 'webhook-id')


def cursor_time(value, tie=None):
    if bool(value) != bool(tie):
        raise HTTPException(422, 'Supply both the history timestamp and its matching identifier')
    try:
        at = datetime.fromisoformat(value) if value else None
        if at and at.tzinfo is None:
            raise ValueError()
        return at
    except ValueError:
        raise HTTPException(422, 'History cursor requires an absolute timestamp') from None


def save_secrets(conn, identity, body):
    src = triggers.source(conn, identity, lock=True)
    if src['status'] == 'archived':
        raise HTTPException(409, 'This source is archived; create a new source to monitor it again')
    if src['revision'] != body.expected_revision:
        raise HTTPException(409, 'Source changed; reload before saving its private credentials')
    if not body.signing_secret and not body.app_token:
        raise HTTPException(422, 'Provide a signing secret or Slack app-level token')
    if body.app_token and (src['provider'] != 'slack' or src['transport'] != 'socket' or not body.app_token.startswith('xapp-')):
        raise HTTPException(422, 'Socket Mode needs a Slack app-level token beginning with xapp-')
    if body.signing_secret and src['transport'] != 'http':
        raise HTTPException(422, 'Signing secrets are used for HTTP event sources')
    if body.signing_secret and src['provider'] == 'webhook':
        try:
            if not body.signing_secret.startswith('whsec_') or not 24 <= len(base64.b64decode(body.signing_secret[6:], validate=True)) <= 64:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'Use a Standard Webhooks secret: whsec_ followed by 24–64 base64-encoded random bytes') from None
    current = json.loads(decrypt(src['secret_encrypted'])) if src['secret_encrypted'] else {}
    previous, until = None, None
    if body.signing_secret:
        if current.get('signing_secret') and body.previous_secret_minutes:
            previous = encrypt(json.dumps({'signing_secret': current['signing_secret']}))
            until = service.now() + timedelta(minutes=body.previous_secret_minutes)
        current['signing_secret'] = body.signing_secret
    if body.app_token:
        current['app_token'] = body.app_token
    # Rotation changes transport credentials, not the saved account/resource authority.
    conn.execute("UPDATE event_sources SET secret_encrypted=%s,previous_secret_encrypted=%s,previous_secret_until=%s,revision=revision+1,generation=generation+1,lease_owner=NULL,lease_until=NULL,status='initializing',next_poll_at=now(),error=NULL,updated_at=now() WHERE id=%s",
                 (encrypt(json.dumps(current)), previous, until, identity))
    triggers.pause_source_triggers(conn, identity, 'Source credentials changed; save the trigger again after checking this source')
    return triggers.project_source(triggers.source(conn, identity))


def source_control(conn, identity, action, revision):
    src = triggers.source(conn, identity, lock=True)
    if src['revision'] != revision or src['status'] == 'archived':
        raise HTTPException(409, 'Source changed; reload before acting')
    if action not in ('pause', 'check', 'resync', 'archive'):
        raise HTTPException(422, 'Choose pause, check, resync or archive')
    if action in ('pause', 'archive'):
        triggers.pause_source_triggers(conn, identity, 'Source paused; explicitly check and resume')
        conn.execute("UPDATE event_sources SET enabled=false,status=%s,revision=revision+1,generation=generation+1,lease_owner=NULL,lease_until=NULL,secret_encrypted=CASE WHEN %s THEN NULL ELSE secret_encrypted END,previous_secret_encrypted=NULL,previous_secret_until=NULL,updated_at=now() WHERE id=%s", ('archived' if action == 'archive' else 'paused', action == 'archive', identity))
    else:
        connection = connections.require(conn, src['connection_id'], lock=False) if src['connection_id'] else None
        if connection and connection['status'] != 'connected':
            raise HTTPException(409, 'Reconnect the account before checking this source')
        reset = action == 'resync' or src['status'] in ('needs_access', 'needs_resync', 'paused') or (connection and connection['revision'] != src['connection_revision'])
        triggers.pause_source_triggers(conn, identity, 'Source check requested; save or explicitly resume after it is ready')
        conn.execute("UPDATE event_sources SET enabled=true,status='initializing',connection_revision=%s,revision=revision+1,generation=generation+1,lease_owner=NULL,lease_until=NULL,next_poll_at=now(),failures=0,error=NULL,cursor_encrypted=CASE WHEN %s THEN NULL ELSE cursor_encrypted END,progress_encrypted=CASE WHEN %s THEN NULL ELSE progress_encrypted END,initialized_at=CASE WHEN %s THEN NULL ELSE initialized_at END,updated_at=now() WHERE id=%s",
                     (connection['revision'] if connection else None, reset, reset, reset, identity))
        if reset:
            conn.execute('DELETE FROM trigger_resource_versions WHERE source_id=%s', (identity,))
    return triggers.project_source(triggers.source(conn, identity))


def delivery_key(src, headers, payload):
    value = headers.get('x-github-delivery') if src['provider'] == 'github' else headers.get('webhook-id') if src['provider'] == 'webhook' else payload.get('event_id')
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise HTTPException(422, 'Provider delivery requires a bounded, stable delivery identifier')
    return value


def receive(conn, src, headers, payload, raw, key=None):
    key = key or delivery_key(src, headers, payload)
    digest = hashlib.sha256(raw).hexdigest()
    previous = conn.execute('SELECT * FROM trigger_deliveries WHERE source_id=%s AND delivery_key=%s', (src['id'], key)).fetchone()
    if previous:
        if previous['body_hash'] != digest:
            raise HTTPException(409, 'Delivery identifier already has different content')
        conn.execute('UPDATE trigger_deliveries SET duplicate_count=duplicate_count+1,last_received_at=now() WHERE source_id=%s AND delivery_key=%s', (src['id'], key))
        return {'disposition': 'duplicate', 'event_id': previous['event_id']}
    if conn.execute("SELECT count(*) AS n FROM trigger_deliveries WHERE source_id=%s AND received_at>now()-interval '1 day'", (src['id'],)).fetchone()['n'] >= triggers.DAILY_DELIVERIES:
        raise HTTPException(429, 'Source delivery limit reached; inspect source history')
    error, event = None, None
    try:
        event = safe_normalize(src, headers, payload)
    except HTTPException as rejected:
        if rejected.status_code == 403:
            raise
        error = 'Authenticated payload could not be normalized; inspect the supported source schema'
    if event:
        result = triggers.ingest(conn, src, event, key, digest)
        conn.execute('UPDATE trigger_deliveries SET source_revision=%s,payload_encrypted=%s,headers=%s WHERE source_id=%s AND delivery_key=%s',
                     (src['revision'], encrypt(raw.decode('utf-8')) if result['disposition'] == 'recorded' else None, db.Jsonb({k: headers[k] for k in SAFE_HEADERS if k in headers}), src['id'], key))
    else:
        disposition = 'recording_failed' if error else 'unsupported'
        conn.execute('INSERT INTO trigger_deliveries(source_id,delivery_key,body_hash,disposition,source_revision,payload_encrypted,headers,error) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                     (src['id'], key, digest, disposition, src['revision'], encrypt(raw.decode('utf-8')) if error else None,
                      db.Jsonb({k: headers[k] for k in SAFE_HEADERS if k in headers}), error))
        result = {'disposition': disposition, 'event_id': None}
    return result


def routes(owner):
    router = APIRouter(dependencies=[Depends(owner)])

    @router.get('/api/v1/event-sources')
    def sources():
        with db.connection() as conn:
            # shortcut: list at most 100 archived sources; add archive pagination if this workspace outgrows it.
            rows = conn.execute("SELECT s.*,EXISTS(SELECT 1 FROM event_trigger_definitions d WHERE d.source_id=s.id AND d.state='active') AS monitoring FROM event_sources s ORDER BY (s.status='archived'),s.created_at DESC,s.id LIMIT 200").fetchall()
            return {'data': [triggers.project_source(row) for row in rows], 'event_types': EVENTS,
                    'local_delivery': 'Slack uses an outbound connection. Google and GitHub polling need no public URL. External HTTP webhooks need a reachable endpoint.'}

    @router.post('/api/v1/event-sources', status_code=201)
    def create_source(body: SourceDefinition, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            return registry.mutation(conn, key, {'action': 'event-source.create', 'body': body.model_dump(mode='json')}, lambda: triggers.save_source(conn, body))

    @router.put('/api/v1/event-sources/{identity}')
    def edit_source(identity: UUID, body: SourceDefinition, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            return registry.mutation(conn, key, {'action': 'event-source.save', 'id': str(identity), 'body': body.model_dump(mode='json')}, lambda: triggers.save_source(conn, body, identity))

    @router.post('/api/v1/event-sources/{identity}/secrets')
    def secrets(identity: UUID, body: SourceSecrets, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            return registry.mutation(conn, key, {'action': 'event-source.secrets', 'id': str(identity), 'body_hash': credential_hash(body.model_dump_json())}, lambda: save_secrets(conn, identity, body))

    @router.post('/api/v1/event-sources/{identity}/{action}')
    def source_action(identity: UUID, action: str, body: RoutineControl, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            return registry.mutation(conn, key, {'action': 'event-source.' + action, 'id': str(identity), 'revision': body.expected_revision}, lambda: source_control(conn, identity, action, body.expected_revision))

    @router.get('/api/v1/event-sources/{identity}/deliveries')
    def deliveries(identity: UUID, before: str | None = None, before_key: str = '', limit: int = Query(default=30, ge=1, le=100)):
        at = cursor_time(before, before_key)
        with db.connection() as conn:
            triggers.source(conn, identity)
            rows = conn.execute('SELECT delivery_key,event_id,disposition,error,duplicate_count,received_at,last_received_at,(payload_encrypted IS NOT NULL) AS replay_available FROM trigger_deliveries WHERE source_id=%s AND (%s::timestamptz IS NULL OR (received_at,delivery_key)<(%s,%s)) ORDER BY received_at DESC,delivery_key DESC LIMIT %s', (identity, at, at, before_key, limit)).fetchall()
            return {'data': rows}

    @router.post('/api/v1/event-sources/{identity}/deliveries/{delivery}/retry')
    def retry_delivery(identity: UUID, delivery: str, body: RoutineControl, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            def retry():
                src = triggers.source(conn, identity, lock=True)
                record = conn.execute('SELECT * FROM trigger_deliveries WHERE source_id=%s AND delivery_key=%s', (identity, delivery)).fetchone()
                if not record or record['disposition'] != 'recording_failed' or not record['payload_encrypted']:
                    raise HTTPException(409, 'Only a retained recording failure can be retried here; existing work uses its ordinary retry action')
                if src['revision'] != body.expected_revision or record['source_revision'] != src['revision']:
                    raise HTTPException(409, 'Source authority changed; this delivery cannot be replayed against different access')
                triggers.source_connection(conn, src)
                raw = decrypt(record['payload_encrypted']).encode()
                event = safe_normalize(src, record['headers'], json.loads(raw))
                if not event:
                    raise HTTPException(409, 'This event type is not supported')
                result = triggers.ingest(conn, src, event, 'reprocess:' + credential_hash(delivery), record['body_hash'])
                conn.execute("UPDATE trigger_deliveries SET disposition='reprocessed',event_id=%s,last_received_at=now() WHERE source_id=%s AND delivery_key=%s", (result['event_id'], identity, delivery))
                return result
            return registry.mutation(conn, key, {'action': 'event-delivery.retry', 'source': str(identity), 'delivery': delivery, 'revision': body.expected_revision}, retry)

    @router.get('/api/v1/event-triggers')
    def listing(before: str | None = None, before_id: UUID | None = None, limit: int = Query(default=100, ge=1, le=100)):
        at = cursor_time(before, before_id)
        with db.connection() as conn:
            rows = conn.execute(f'SELECT {triggers.TRIGGER_COLUMNS} FROM {triggers.TRIGGER_JOIN} WHERE (%s::timestamptz IS NULL OR (d.created_at,d.id)<(%s,%s::uuid)) ORDER BY d.created_at DESC,d.id DESC LIMIT %s', (at, at, before_id or UUID(int=0), limit)).fetchall()
            for row in rows:
                row['latest_run'] = next(iter(triggers.history(conn, row['id'], limit=1)), None)
                pending = conn.execute("SELECT count(*) AS count,min(created_at) AS oldest FROM trigger_runs WHERE trigger_id=%s AND status='pending'", (row['id'],)).fetchone()
                row['pending'] = pending
            return {'data': rows}

    @router.get('/api/v1/event-triggers/{identity}')
    def by_identity(identity: UUID):
        with db.connection() as conn:
            return triggers.get(conn,identity)

    @router.post('/api/v1/event-triggers', status_code=201)
    def create(body: TriggerDefinition, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            return registry.mutation(conn, key, {'action': 'event-trigger.create', 'body': body.model_dump(mode='json')}, lambda: triggers.save(conn, body))

    @router.put('/api/v1/event-triggers/{identity}')
    def update(identity: UUID, body: TriggerDefinition, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            return registry.mutation(conn, key, {'action': 'event-trigger.save', 'id': str(identity), 'body': body.model_dump(mode='json')}, lambda: triggers.save(conn, body, identity))

    @router.post('/api/v1/event-triggers/preview')
    def preview(body: TriggerPreview):
        with db.connection() as conn:
            src = triggers.source(conn, body.source_id)
            triggers.validate_filters(src, body)
            accepted, reason = triggers.match(body, body.event)
            triggers.event_access(conn, src, body, body.event)
            return {'matched': accepted, 'reason': reason, 'creates_work': False, 'executes_actions': False}

    @router.post('/api/v1/event-triggers/{identity}/{action}')
    def action(identity: UUID, action: str, body: RoutineControl, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            return registry.mutation(conn, key, {'action': 'event-trigger.' + action, 'id': str(identity), 'revision': body.expected_revision}, lambda: triggers.control(conn, identity, action, body.expected_revision))

    @router.get('/api/v1/event-triggers/{identity}/runs')
    def runs(identity: UUID, before: str | None = None, before_id: UUID | None = None, limit: int = Query(default=30, ge=1, le=100)):
        at = cursor_time(before, before_id)
        with db.connection() as conn:
            triggers.get(conn, identity)
            return {'data': triggers.history(conn, identity, at, before_id, limit)}

    @router.get('/api/v1/event-triggers/{identity}/events')
    def events(identity: UUID, before: str | None = None, before_id: UUID | None = None, limit: int = Query(default=30, ge=1, le=100)):
        at = cursor_time(before, before_id)
        with db.connection() as conn:
            triggers.get(conn, identity)
            rows = conn.execute('SELECT e.id,e.event_type,e.resource,e.summary,e.occurred_at,e.received_at,e.payload_encrypted,m.revision,m.matched,m.reason,m.run_id FROM trigger_matches m JOIN trigger_events e ON e.id=m.event_id WHERE m.trigger_id=%s AND (%s::timestamptz IS NULL OR (e.received_at,e.id)<(%s,%s::uuid)) ORDER BY e.received_at DESC,e.id DESC LIMIT %s', (identity, at, at, before_id or UUID(int=0), limit)).fetchall()
            for row in rows:
                encrypted = row.pop('payload_encrypted')
                row['event'] = json.loads(decrypt(encrypted)) if encrypted else None
            return {'data': rows}

    return router


def ingress_routes():
    router = APIRouter()

    @router.post('/hooks/{provider}/{identity}')
    async def webhook(provider: str, identity: UUID, request: Request):
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY:
                        raise HTTPException(413, 'Event exceeds the 1 MiB delivery limit')
                    body.extend(chunk)
        except TimeoutError:
            raise HTTPException(408, 'Event body exceeded the delivery time limit') from None
        raw = bytes(body)
        headers = {key.lower(): value for key, value in request.headers.items()}
        with db.connection() as conn:
            src = triggers.source(conn, identity, lock=True)
            if src['provider'] != provider or src['transport'] != 'http':
                raise HTTPException(404, 'HTTP event source not found')
            credentials = json.loads(decrypt(src['secret_encrypted'])) if src['secret_encrypted'] else {}
            secrets = [credentials.get('signing_secret')]
            if src['previous_secret_encrypted'] and src['previous_secret_until'] and src['previous_secret_until'] > service.now():
                secrets.append(json.loads(decrypt(src['previous_secret_encrypted'])).get('signing_secret'))
            if not any(verify_signature(src, secret, headers, raw) for secret in secrets):
                raise HTTPException(401, 'Provider signature verification failed')
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError()
            except (ValueError, RecursionError, UnicodeDecodeError):
                raise HTTPException(400, 'Expected a bounded UTF-8 JSON event object') from None
            if provider == 'slack' and payload.get('type') == 'url_verification':
                challenge = payload.get('challenge')
                if not isinstance(challenge, str) or len(challenge) > 500:
                    raise HTTPException(400, 'Invalid Slack verification challenge')
                return {'challenge': challenge}
            try:
                result = receive(conn, src, headers, payload, raw)
            except ValidationError:
                raise HTTPException(422, 'Event exceeds the supported schema limits') from None
        # The transaction has committed before FastAPI sends the acknowledgement.
        return result

    return router
