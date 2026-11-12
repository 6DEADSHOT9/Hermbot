"""Owner-selected standing authority, frozen on an occurrence above unmodified Hermes."""
from uuid import UUID

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from . import db, registry, service


def validate(conn, definition):
    selected = definition.management
    if not selected:
        return None
    grant = conn.execute('SELECT * FROM grants WHERE id=%s', (selected.grant_id,)).fetchone()
    if not grant or grant['kind'] != 'standing' or grant['manager_id'] != definition.bot_id:
        raise HTTPException(403, 'Choose a standing permission belonging to this manager')
    if grant['revoked_at'] or grant['expires_at'] <= service.now():
        raise HTTPException(403, 'Management permission expired or was revoked; select valid authority and resume explicitly')
    if set(selected.targets) - set(grant['targets']) or set(selected.operations) - set(grant['operations']):
        raise HTTPException(403, 'Selected teammates or operations exceed this management permission')
    for target in selected.targets:
        bot = registry.require_bot(conn, target)
        if target == definition.bot_id or bot['status'] in ('archived', 'deleting', 'deleted'):
            raise HTTPException(409, 'Choose available registered collaborators other than the manager')
    return grant


def scope(conn, task):
    identity = task.get('management_work_item_id') or task.get('work_item_id')
    if not identity or (not task.get('management_work_item_id') and task.get('message_intent') not in ('assignment', 'update') and task.get('execution_kind') != 'peer_message'):
        return {}
    row = conn.execute('SELECT management_scope FROM work_items WHERE id=%s', (identity,)).fetchone()
    return row['management_scope'] if row else {}


def bind(conn, definition, task, source_message_id, context):
    grant = validate(conn, definition)
    if not grant:
        return
    frozen = {**definition.management.model_dump(mode='json'), 'manager_id': definition.bot_id,
              'initiating_user': 'owner', 'source_message_id': source_message_id,
              'observed_expires_at': grant['expires_at'].isoformat(), 'origin': context}
    conn.execute('UPDATE work_items SET management_scope=%s WHERE id=%s', (Jsonb(frozen), task['work_item_id']))
    inherit(conn, task)


def inherit(conn, task):
    frozen = scope(conn, task)
    if not frozen:
        return task
    if task.get('grant_id') and str(task['grant_id']) != frozen['grant_id']:
        raise HTTPException(403, 'A work revision cannot replace the occurrence management permission')
    origin = frozen['origin']
    routine = origin.get('routine', {}).get('run_id')
    event = origin.get('event_trigger', {}).get('run_id')
    conn.execute('UPDATE tasks SET grant_id=%s,routine_run_id=%s,trigger_run_id=%s,management_work_item_id=%s,coordination_version=1 WHERE id=%s',
                 (UUID(frozen['grant_id']), UUID(routine) if routine else None, UUID(event) if event else None, task.get('management_work_item_id') or task['work_item_id'], task['id']))
    task = conn.execute('SELECT * FROM tasks WHERE id=%s', (task['id'],)).fetchone()
    service.check_grant(conn, task)
    return task


def tasks(conn, task):
    if scope(conn, task):
        return conn.execute('SELECT * FROM tasks WHERE management_work_item_id=%s ORDER BY id', (task.get('management_work_item_id') or task['work_item_id'],)).fetchall()
    return conn.execute('SELECT * FROM tasks WHERE root_id=%s ORDER BY id', (task['root_id'],)).fetchall()


def pending_messages(conn, item_id):
    return bool(conn.execute("""SELECT 1 FROM teammate_messages m JOIN work_items w ON w.id=m.work_item_id
        WHERE w.id=%s AND w.management_scope<>'{}'::jsonb AND """ + service.OPEN_TEAMMATE_MESSAGE + " LIMIT 1", (item_id,)).fetchone())


def definite_failure(conn, task):
    if not scope(conn, task):
        return task['status'] == 'failed'
    executions = tasks(conn, task)
    roots = [t for t in executions if not t['parent_id'] and t['execution_kind'] != 'peer_message']
    current = max(roots, key=lambda t: (t['created_at'], str(t['id'])))
    if current['status'] != 'failed' or any(t['status'] not in service.TERMINAL - {'uncertain'} for t in executions):
        return False
    identities = [t['id'] for t in executions]
    if conn.execute('SELECT 1 FROM attempts WHERE task_id=ANY(%s) AND status=ANY(%s)', (identities, list(service.ACTIVE_ATTEMPTS) + ['reserved'])).fetchone():
        return False
    identity = task.get('management_work_item_id') or task['work_item_id']
    results = conn.execute("SELECT status FROM work_outcomes WHERE work_item_id=%s AND required AND status<>'cancelled'", (identity,)).fetchall()
    return bool(results) and any(r['status'] == 'failed' for r in results) and not any(r['status'] in ('blocked', 'uncertain') for r in results) and not pending_messages(conn, identity)


def pause_invalid(conn):
    # Lock definitions before work, matching saved-work admission. Never block another claimant.
    from .routine_models import SavedWorkDefinition
    for table, revisions, key, kind in (
        ('routine_definitions', 'routine_revisions', 'routine_id', 'routine'),
        ('event_trigger_definitions', 'event_trigger_revisions', 'trigger_id', 'trigger'),
    ):
        rows = conn.execute(f"""SELECT d.id,d.conversation_id,v.definition FROM {table} d
            JOIN {revisions} v ON v.{key}=d.id AND v.revision=d.revision
            WHERE d.state='active' AND v.definition->'management' IS NOT NULL
              AND v.definition->'management'<>'null'::jsonb FOR UPDATE OF d SKIP LOCKED""").fetchall()
        for row in rows:
            body = {k: v for k, v in row['definition'].items() if k in SavedWorkDefinition.model_fields}
            try:
                validate(conn, SavedWorkDefinition.model_validate(body))
            except HTTPException as exc:
                extra = ',next_run_at=NULL' if kind == 'routine' else ''
                conn.execute(f"UPDATE {table} SET state='paused',error=%s,updated_at=now(){extra} WHERE id=%s", (str(exc.detail), row['id']))
                db.event(conn, kind + '.paused', conversation_id=row['conversation_id'], data={key: str(row['id']), 'reason': str(exc.detail)})
