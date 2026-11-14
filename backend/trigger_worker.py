"""Independent collectors and event admission; a slow provider never holds the agent worker."""
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import signal
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi import HTTPException
from psycopg import errors
from psycopg.types.json import Jsonb
from websockets.asyncio.client import connect

from . import connector_oauth, db, logging_config, service, triggers
from .connector_http import ProviderFailure, request
from .connector_catalog import path
from .secrets import decrypt, encrypt
from .trigger_api import receive
from .trigger_providers import drive_state, fetch_page, normalize_poll, probe, resource_state


LOGGER = logging.getLogger('hermbot.events')
WORKER_ID = uuid4()
LEASE_SECONDS = 360
STOP = asyncio.Event()


def claim(transport, count):
    with db.connection() as conn:
        rows = conn.execute("""SELECT s.* FROM event_sources s WHERE s.enabled
          AND s.status NOT IN ('needs_access','needs_resync','needs_inspection')
          AND ((%s='socket' AND s.transport='socket') OR (%s='poll' AND s.transport<>'socket'))
          AND (s.lease_until IS NULL OR s.lease_until<now()) AND s.next_poll_at<=now()
          AND (s.status='initializing' OR EXISTS(SELECT 1 FROM event_trigger_definitions d WHERE d.source_id=s.id AND d.state='active'))
          ORDER BY s.next_poll_at,s.id FOR UPDATE OF s SKIP LOCKED LIMIT %s""", (transport, transport, count)).fetchall()
        for row in rows:
            conn.execute('UPDATE event_sources SET lease_owner=%s,lease_until=now()+make_interval(secs=>%s) WHERE id=%s', (WORKER_ID, LEASE_SECONDS, row['id']))
        return rows


def leased(conn, src):
    return conn.execute('SELECT * FROM event_sources WHERE id=%s AND lease_owner=%s AND generation=%s AND lease_until>now() FOR UPDATE', (src['id'], WORKER_ID, src['generation'])).fetchone()


def credentials(src):
    with db.connection() as conn:
        current = triggers.source(conn, src['id'])
        if current['generation'] != src['generation']:
            return None
        connection = triggers.source_connection(conn, current)
        if connection:
            # Refresh uses the existing serialized connection credential path.
            connection = conn.execute('SELECT * FROM connections WHERE id=%s FOR UPDATE', (connection['id'],)).fetchone()
            result = connector_oauth.credential(conn, connection)
        else:
            result = {}
        return {**result, **(json.loads(decrypt(current['secret_encrypted'])) if current['secret_encrypted'] else {})}


def verified(src, credential):
    if src['transport'] == 'http' and not credential.get('signing_secret'):
        raise ProviderFailure('Save a private signing secret before receiving HTTP events', auth=True)
    if src['transport'] == 'socket' and not credential.get('app_token'):
        raise ProviderFailure('Save a Slack app-level token with connections:write and enable event subscriptions', auth=True)
    identity = probe(src, credential)
    for key, old in src['remote_identity'].items():
        if key in ('account_id', 'repository_id', 'calendar_id', 'team_id', 'email') and identity.get(key) != old:
            raise ProviderFailure('The authenticated source identity changed; create a new source for the different account or resource', auth=True)
    with db.connection() as conn:
        current = leased(conn, src)
        if not current:
            return None
        triggers.source_connection(conn, current)
        initialized = current['transport'] == 'http'
        conn.execute("UPDATE event_sources SET remote_identity=%s,status=%s,checked_at=now(),error=NULL,initialized_at=CASE WHEN %s THEN COALESCE(initialized_at,now()) ELSE initialized_at END WHERE id=%s",
                     (Jsonb(identity), 'ready' if initialized else 'initializing', initialized, src['id']))
        return triggers.source(conn, src['id'])


def commit_page(src, page):
    with db.connection() as conn:
        current = leased(conn, src)
        if not current:
            return False
        triggers.source_connection(conn, current)
        for item in page.items:
            resource, version, metadata = resource_state(current, item)
            known = conn.execute('SELECT version,metadata FROM trigger_resource_versions WHERE source_id=%s AND resource=%s', (src['id'], resource)).fetchone()
            if not page.baseline:
                event = normalize_poll(current, item, known)
                if event:
                    # Polls have resource-version identities, not immutable provider delivery bodies.
                    triggers.ingest(conn, current, event, 'poll:' + event.key, service.digest({'provider': current['provider'], 'event_key': event.key}))
            conn.execute('INSERT INTO trigger_resource_versions(source_id,resource,version,metadata) VALUES(%s,%s,%s,%s) ON CONFLICT(source_id,resource) DO UPDATE SET version=excluded.version,metadata=excluded.metadata,updated_at=now()',
                         (src['id'], resource, version, Jsonb(metadata)))
        # Both the continuation and the completed cursor cover only committed input.
        conn.execute("UPDATE event_sources SET cursor_encrypted=%s,progress_encrypted=%s,status='ready',error=NULL,failures=0,checked_at=now(),initialized_at=CASE WHEN %s THEN COALESCE(initialized_at,now()) ELSE initialized_at END,next_poll_at=now()+make_interval(secs=>%s),lease_owner=NULL,lease_until=NULL WHERE id=%s",
                     (encrypt(json.dumps(page.cursor)) if page.cursor is not None else None,
                      encrypt(json.dumps(page.progress)) if page.progress is not None else None,
                      page.complete, max(current['poll_interval_seconds'], page.poll_after_seconds) if page.complete else 1, src['id']))
        if page.complete:
            conn.execute("UPDATE event_trigger_definitions SET activated_at=now(),error=NULL WHERE source_id=%s AND state='active' AND activated_at IS NULL", (src['id'],))
        return True


def check_drive_creations(src, credential, page):
    if src['provider'] != 'google_drive' or page.baseline:
        return
    for item in page.items:
        item['_hermbot_creation_checked'] = True
        item.pop('_hermbot_creation_operation_id', None)
        value = item.get('file') or {}
        with db.connection() as conn:
            known = conn.execute('SELECT metadata FROM trigger_resource_versions WHERE source_id=%s AND resource=%s', (src['id'], item['fileId'])).fetchone()
            if known and known['metadata'].get('creation_checked'):
                if known['metadata'].get('file_state') == drive_state(item):
                    item['_hermbot_creation_operation_id'] = known['metadata'].get('creation_operation_id')
                continue
            op = conn.execute("SELECT id,arguments,result FROM connector_operations WHERE connection_id=%s AND id::text=%s AND action='drive.create' AND status='succeeded' AND created_at>=%s AND result->>'id'=%s", (src['connection_id'], (value.get('appProperties') or {}).get('hermbot_operation', ''), src['initialized_at'], item['fileId'])).fetchone()
        if not op or not op['arguments'].get('document') or item.get('removed') or value.get('trashed'):
            continue
        args = op['arguments']
        folder = src['remote_identity'].get('root_id') if args['folder_id'] == 'root' else args['folder_id']
        if value.get('name') != args['name'] or value.get('mimeType') != 'application/vnd.google-apps.document' or set(value.get('parents', [])) != {folder}:
            continue
        # Conversion changes modifiedTime too; verify the initial approved text outside the DB transaction.
        body, _ = request('GET', 'https://www.googleapis.com/drive/v3/files/' + path(item['fileId']) + '/export', token=credential['access_token'], params={'mimeType': 'text/plain'}, raw=True)
        actual = body.decode('utf-8-sig').replace('\r\n', '\n')
        expected = args['content'].replace('\r\n', '\n')
        if actual in (expected, expected + '\n'):
            item['_hermbot_creation_operation_id'] = str(op['id'])


def failure(src, error, *, disconnected=False):
    with db.connection() as conn:
        current = leased(conn, src)
        if not current:
            return
        count = current['failures'] + 1
        message = str(error) if isinstance(error, (ProviderFailure, HTTPException)) else 'Provider data or transport could not be processed (' + type(error).__name__ + ')'
        if isinstance(error, HTTPException):
            message = str(error.detail)
        status = 'reconnecting' if disconnected else 'retrying'
        if isinstance(error, ProviderFailure) and error.auth:
            status = 'needs_access'
        elif isinstance(error, ProviderFailure) and error.status in (404, 410) and src['provider'] in ('gmail', 'google_calendar'):
            status = 'needs_resync'
        elif ((isinstance(error, (ValueError, KeyError, TypeError))) or (isinstance(error, ProviderFailure) and error.status in (400, 404, 422))) and count >= 3:
            status = 'needs_inspection'
        delay = min(3600, 5 * 2 ** min(count, 9))
        if isinstance(error, ProviderFailure) and error.retry_after:
            delay = max(delay, error.retry_after)
        if isinstance(error, HTTPException) and error.status_code == 429:
            delay = 3600
        conn.execute('UPDATE event_sources SET status=%s,error=%s,failures=%s,next_poll_at=now()+make_interval(secs=>%s),checked_at=now(),lease_owner=NULL,lease_until=NULL,gap_since=CASE WHEN %s THEN COALESCE(gap_since,now()) ELSE gap_since END WHERE id=%s',
                     (status, message[:500], count, delay, disconnected or status == 'needs_resync', src['id']))
        if status in ('needs_access', 'needs_resync', 'needs_inspection'):
            triggers.pause_source_triggers(conn, src['id'], message[:500])
        db.event(conn, 'trigger.source.error', data={'source_id': str(src['id']), 'status': status, 'error': message[:500]})


def poll(src):
    try:
        credential = credentials(src)
        if credential is None:
            return
        if src['status'] == 'initializing' or not src['remote_identity']:
            src = verified(src, credential)
            if not src:
                return
        if src['transport'] == 'http':
            with db.connection() as conn:
                if leased(conn, src):
                    conn.execute("UPDATE event_sources SET lease_owner=NULL,lease_until=NULL,next_poll_at=now()+interval '60 seconds',checked_at=now() WHERE id=%s", (src['id'],))
            return
        cursor = json.loads(decrypt(src['cursor_encrypted'])) if src['cursor_encrypted'] else None
        progress = json.loads(decrypt(src['progress_encrypted'])) if src['progress_encrypted'] else None
        page = fetch_page(src, credential, cursor, progress)
        check_drive_creations(src, credential, page)
        commit_page(src, page)
    except Exception as error:
        logging_config.log_exception(LOGGER, 'Event source processing failed', error)
        failure(src, error)


def socket_address(url):
    from .library import public_address
    parsed = urlsplit(url)
    if parsed.scheme != 'wss' or not parsed.hostname or not parsed.hostname.endswith('.slack.com') or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ProviderFailure('Slack returned an unsupported Socket Mode address')
    _, address, port = public_address(urlunsplit(('https', parsed.netloc, parsed.path, parsed.query, '')))
    sock = socket.create_connection((address, port), timeout=4)
    sock.setblocking(False)
    return sock, parsed.hostname


def socket_heartbeat(src, app_id=None):
    with db.connection() as conn:
        current = leased(conn, src)
        if not current:
            return False
        triggers.source_connection(conn, current)
        if not conn.execute("SELECT 1 FROM event_trigger_definitions WHERE source_id=%s AND state='active' LIMIT 1", (src['id'],)).fetchone():
            conn.execute("UPDATE event_sources SET lease_owner=NULL,lease_until=NULL,status='ready',checked_at=now() WHERE id=%s", (src['id'],))
            return False
        remote = current['remote_identity']
        if app_id:
            if remote.get('app_id') and remote['app_id'] != app_id:
                raise ProviderFailure('Slack app identity changed; create a new source for the new app', auth=True)
            remote = {**remote, 'app_id': app_id}
        conn.execute("UPDATE event_sources SET remote_identity=%s,status='listening',error=NULL,failures=0,checked_at=now(),connected_at=COALESCE(connected_at,now()),initialized_at=COALESCE(initialized_at,now()),lease_until=now()+make_interval(secs=>%s) WHERE id=%s", (Jsonb(remote), LEASE_SECONDS, src['id']))
        return True


def socket_event(src, frame):
    payload = frame.get('payload')
    if not isinstance(payload, dict) or not isinstance(frame.get('envelope_id'), str):
        raise ValueError('Invalid Socket Mode envelope')
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    with db.connection() as conn:
        current = leased(conn, src)
        if not current:
            return False
        triggers.source_connection(conn, current)
        outside_workspace = frame.get('type') == 'events_api' and payload.get('team_id') != current['remote_identity'].get('team_id')
        if frame.get('type') == 'events_api' and not outside_workspace:
            receive(conn, current, {}, payload, raw)
        else:
            key = 'socket:' + frame['envelope_id'][:150]
            conn.execute("INSERT INTO trigger_deliveries(source_id,delivery_key,body_hash,disposition,source_revision) VALUES(%s,%s,%s,'unsupported',%s) ON CONFLICT(source_id,delivery_key) DO UPDATE SET duplicate_count=trigger_deliveries.duplicate_count+1,last_received_at=now()",
                         (src['id'], key, hashlib.sha256(raw).hexdigest(), src['revision']))
    return True


async def socket_source(src):
    sock = None
    try:
        credential = await asyncio.to_thread(credentials, src)
        if credential is None:
            return
        current = await asyncio.to_thread(verified, src, credential)
        if not current:
            return
        src = current
        opened = await asyncio.to_thread(request, 'POST', 'https://slack.com/api/apps.connections.open', token=credential['app_token'])
        sock, hostname = await asyncio.to_thread(socket_address, opened['url'])
        # Use the installed maintained WebSocket client, with bounded frames/queue and no proxy inheritance.
        async with connect(opened['url'], sock=sock, ssl=ssl.create_default_context(), server_hostname=hostname,
                           proxy=None, open_timeout=10, close_timeout=5, ping_interval=20, ping_timeout=20,
                           max_size=1_048_576, max_queue=4) as client:
            hello = json.loads(await asyncio.wait_for(client.recv(), timeout=10))
            app_id = (hello.get('connection_info') or {}).get('app_id')
            if hello.get('type') != 'hello' or not isinstance(app_id, str) or not app_id:
                raise ProviderFailure('Slack Socket Mode did not confirm its app identity')
            if not await asyncio.to_thread(socket_heartbeat, src, app_id):
                return
            while not STOP.is_set():
                if not await asyncio.to_thread(socket_heartbeat, src):
                    return
                try:
                    frame = json.loads(await asyncio.wait_for(client.recv(), timeout=10))
                except TimeoutError:
                    continue
                if frame.get('type') == 'disconnect':
                    raise ProviderFailure('Slack requested a connection refresh; reconnecting')
                if frame.get('type') == 'hello':
                    continue
                if await asyncio.to_thread(socket_event, src, frame):
                    await client.send(json.dumps({'envelope_id': frame['envelope_id']}))
                else:
                    return
    except asyncio.CancelledError:
        raise
    except errors.UniqueViolation:
        await asyncio.to_thread(failure, src, ProviderFailure('This Slack app already has a source; reuse it for triggers in its configured workspace', auth=True))
    except Exception as error:
        logging_config.log_exception(LOGGER, 'Socket source processing failed', error)
        await asyncio.to_thread(failure, src, error, disconnected=True)
    finally:
        if sock:
            sock.close()
        with db.connection() as conn:
            conn.execute("UPDATE event_sources SET lease_owner=NULL,lease_until=NULL,next_poll_at=GREATEST(next_poll_at,now()+interval '5 seconds'),status=CASE WHEN status='listening' THEN 'reconnecting' ELSE status END,gap_since=CASE WHEN status='listening' THEN COALESCE(gap_since,now()) ELSE gap_since END WHERE id=%s AND lease_owner=%s AND generation=%s", (src['id'], WORKER_ID, src['generation']))


def advance():
    with db.connection() as conn:
        triggers.admit_pending(conn)
        triggers.maintenance(conn)


async def main():
    logging_config.configure_logging()
    db.initialize()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, STOP.set)
    jobs = {}
    try:
        while not STOP.is_set():
            for identity, (job, kind) in list(jobs.items()):
                if job.done():
                    try:
                        job.result()
                    except Exception as error:
                        logging_config.log_exception(LOGGER, 'Collector stopped', error)
                    del jobs[identity]
            try:
                for kind, limit in (('poll', 4), ('socket', 10)):
                    room = limit - sum(k == kind for _, k in jobs.values())
                    if room > 0:
                        for src in await asyncio.to_thread(claim, kind, room):
                            if src['id'] not in jobs:
                                job = asyncio.create_task(socket_source(src) if kind == 'socket' else asyncio.to_thread(poll, src))
                                jobs[src['id']] = (job, kind)
                await asyncio.to_thread(advance)
                Path('/tmp/hermbot-events-heartbeat').touch()
            except Exception as error:
                logging_config.log_exception(LOGGER, 'Event processing pass failed', error)
            try:
                await asyncio.wait_for(STOP.wait(), timeout=1)
            except TimeoutError:
                pass
    finally:
        for job, _ in jobs.values():
            job.cancel()
        await asyncio.gather(*(job for job, _ in jobs.values()), return_exceptions=True)
        db.pool.close()


if __name__ == '__main__':
    asyncio.run(main())
