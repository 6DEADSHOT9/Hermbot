"""Apply owner revisions and reconcile durable execution commands above the harness."""
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from . import db, outcomes, service

VERSION = 2


def owner_message(conn, task):
    identity = task.get('owner_message_id')
    message = conn.execute("SELECT * FROM messages WHERE id=%s AND role='user' AND author='owner' AND conversation_id=%s",
                           (identity, task['conversation_id'])).fetchone() if identity else None
    if not message:
        message = conn.execute("SELECT * FROM messages WHERE task_id=%s AND role='user' AND author='owner' ORDER BY id DESC LIMIT 1",
                               (task['id'],)).fetchone()
    if not message:
        raise HTTPException(403, 'An authenticated owner message is required for this work change')
    return message


def command(conn, item, task_id, kind, payload):
    entry = conn.execute('INSERT INTO work_commands(id,work_item_id,task_id,revision,kind,payload) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(task_id,revision,kind) DO UPDATE SET payload=work_commands.payload RETURNING *',
                         (uuid4(), item['id'], task_id, item['revision'], kind, Jsonb(payload))).fetchone()
    # Settlement must see the direction in the same transaction as the work revision.
    # A later reconciliation pass cannot safely be the first place it becomes visible.
    conn.execute("INSERT INTO controls(id,task_id,input,source,work_revision,source_message_id) VALUES(%s,%s,%s,'owner',%s,%s) ON CONFLICT(id) DO NOTHING",
                 (entry['id'], task_id, 'Owner direction: ' + payload['instruction'], item['revision'], payload['source_message_id']))
    task = service.locked_task(conn, task_id)
    conn.execute('UPDATE tasks SET orchestration_version=%s,work_revision=%s WHERE id=%s', (VERSION, item['revision'], task_id))
    values = outcomes.rows(conn, item['id']) if not task['parent_id'] else outcomes.assigned(conn, task)
    for outcome in values:
        conn.execute('INSERT INTO task_outcomes VALUES(%s,%s,%s) ON CONFLICT(task_id,outcome_id) DO UPDATE SET outcome_revision=excluded.outcome_revision', (task_id, outcome['id'], outcome['revision']))
        if task.get('coordination_version') == 1:
            prior = conn.execute('SELECT * FROM result_contributions WHERE task_id=%s AND outcome_id=%s ORDER BY outcome_revision DESC LIMIT 1', (task_id, outcome['id'])).fetchone()
            if prior and prior['outcome_revision'] != outcome['revision']:
                conn.execute('INSERT INTO result_contributions(task_id,outcome_id,outcome_revision,required,expected_output) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING', (task_id, outcome['id'], outcome['revision'], prior['required'], prior['expected_output']))


def apply(conn, task, item_id, *, objective='', requirements=(), supersede_keys=(), effects=(), kind='update'):
    """Only message intake/owner endpoints call this; agent tool prose is not authority."""
    item = conn.execute('SELECT * FROM work_items WHERE id=%s AND conversation_id=%s FOR UPDATE',
                        (item_id, task['conversation_id'])).fetchone()
    if not item:
        raise HTTPException(404, 'Work does not belong to this conversation')
    source = owner_message(conn, task)
    prior = conn.execute('SELECT * FROM work_changes WHERE work_item_id=%s AND source_message_id=%s AND kind=%s',
                         (item_id, source['id'], kind)).fetchone()
    if prior:
        return item
    conn.execute('UPDATE tasks SET work_item_id=%s,owner_message_id=%s,orchestration_version=%s WHERE id=%s',
                 (item_id, source['id'], VERSION, task['id']))
    task = service.locked_task(conn, task['id'])
    from .recurring_management import inherit
    task = inherit(conn, task)
    legacy_active = conn.execute("SELECT 1 FROM tasks WHERE work_item_id=%s AND id<>%s AND coordination_version=0 AND parent_id IS NULL AND status<>ALL(%s)", (item_id, task['id'], list(service.TERMINAL))).fetchone()
    if legacy_active:
        conn.execute('UPDATE tasks SET coordination_version=0 WHERE id=%s', (task['id'],))
        task = service.locked_task(conn, task['id'])
    current = {o['key']: o for o in outcomes.rows(conn, item_id)}
    if set(supersede_keys) - set(current):
        raise HTTPException(422, 'The revision references an unknown requirement')
    if set(supersede_keys) & {s['key'] for s in requirements}:
        raise HTTPException(422, 'Replace a requirement with a new key or revise its existing key')
    for key in supersede_keys:
        old = current[key]
        invalidate_operations(conn, old['id'])
        conn.execute("UPDATE work_outcomes SET status='cancelled',reason='Superseded by owner instruction',revision=revision+1,source_message_id=%s,updated_at=now() WHERE id=%s", (source['id'], old['id']))
        conn.execute('UPDATE work_authorizations SET revoked_at=now() WHERE outcome_id=%s AND revoked_at IS NULL', (old['id'],))
        conn.execute("UPDATE input_requests SET status='cancelled' WHERE outcome_id=%s AND status='pending'", (old['id'],))
    # Intake provides full changed requirements. Unchanged requirements/evidence remain intact.
    outcomes.register(conn, task, list(requirements), owner_revision=True)
    if task.get('coordination_version') == 1 and task['message_intent'] in ('assignment', 'update'):
        conn.execute("UPDATE work_outcomes o SET owner_task_id=%s WHERE o.work_item_id=%s AND o.status NOT IN ('fulfilled','cancelled') AND EXISTS(SELECT 1 FROM tasks t WHERE t.id=o.owner_task_id AND t.status=ANY(%s))", (task['id'], item_id, list(service.TERMINAL - {'uncertain'})))
    revision = item['revision'] + (0 if kind == 'created' and not current else 1)
    objective = objective.strip() or item['objective']
    conn.execute('UPDATE work_items SET objective=%s,revision=%s,orchestration_version=%s,outcomes_version=1,hidden_at=NULL,updated_at=now() WHERE id=%s',
                 (objective, revision, VERSION, item_id))
    item = conn.execute('SELECT * FROM work_items WHERE id=%s', (item_id,)).fetchone()
    conn.execute('UPDATE tasks SET work_revision=%s WHERE id=%s', (revision, task['id']))
    register_authorizations(conn, task, source, effects)
    conn.execute('INSERT INTO work_changes(id,work_item_id,revision,source_message_id,source_task_id,kind,change) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                 (uuid4(), item_id, revision, source['id'], task['id'], kind,
                  Jsonb({'objective': objective, 'requirements': list(requirements), 'supersede_keys': list(supersede_keys)})))
    # Changes to a shared request do not grant the receiving bot management credentials.
    for target in conn.execute("SELECT * FROM tasks WHERE work_item_id=%s AND id<>%s AND status<>ALL(%s)",
                               (item_id, task['id'], list(service.TERMINAL | service.STOPPING))).fetchall():
        command(conn, item, target['id'], 'revise', {'source_message_id': source['id'], 'instruction': source['content']})
    db.event(conn, 'work.revised', task, data={'work_item_id': str(item_id), 'revision': revision})
    return item


def invalidate_operations(conn, outcome_id):
    """Retire only drafts that have not been dispatched; in-flight receipts remain auditable."""
    operations = conn.execute("UPDATE connector_operations SET status='cancelled',error='Requirement revised by owner',updated_at=now() WHERE outcome_id=%s AND status IN ('pending','queued') RETURNING approval_id", (outcome_id,)).fetchall()
    for operation in operations:
        if operation['approval_id']:
            conn.execute("UPDATE approvals SET status='stale' WHERE id=%s AND status IN ('pending','decided')", (operation['approval_id'],))


def record_owner_action(conn, task, kind, summary):
    """An authenticated UI operation has explicit provenance without fabricated chat text."""
    item = conn.execute('UPDATE work_items SET revision=revision+1,updated_at=now() WHERE id=%s RETURNING *', (task['work_item_id'],)).fetchone()
    conn.execute('INSERT INTO work_changes(id,work_item_id,revision,source_task_id,kind,change) VALUES(%s,%s,%s,%s,%s,%s)',
                 (uuid4(), item['id'], item['revision'], task['id'], kind, Jsonb({'objective': item['objective'], 'summary': summary, 'source': 'authenticated_owner_action'})))
    for target in conn.execute('SELECT * FROM tasks WHERE work_item_id=%s AND status<>ALL(%s)', (item['id'], list(service.TERMINAL | service.STOPPING))).fetchall():
        command(conn, item, target['id'], 'revise', {'source_message_id': None, 'instruction': summary})
    db.event(conn, 'work.owner_action', task, data={'revision': item['revision'], 'kind': kind})


def register_authorizations(conn, task, source, effects):
    known = {o['key']: o for o in outcomes.rows(conn, task['work_item_id'])}
    for effect in effects:
        outcome = known.get(effect['outcome_key'])
        quote = effect['instruction_quote'].strip()
        if not outcome or outcome['kind'] != 'connector_effect' or outcome['status'] == 'cancelled':
            raise HTTPException(422, 'Authorization must identify a current external requirement')
        if not quote or quote not in source['content']:
            raise HTTPException(422, 'Authorization evidence must quote the actual owner instruction')
        if outcome['target'].get('action') not in (None, '', effect['action']):
            raise HTTPException(422, 'Authorization action differs from its requirement')
        conn.execute('INSERT INTO work_authorizations(id,work_item_id,outcome_id,outcome_revision,source_message_id,action,resource,resource_label,instruction_quote) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                     (uuid4(), task['work_item_id'], outcome['id'], outcome['revision'], source['id'], effect['action'],
                      effect.get('resource', ''), effect.get('resource_label', ''), quote))


def reconcile(conn, item_id):
    item = conn.execute('SELECT * FROM work_items WHERE id=%s FOR UPDATE SKIP LOCKED', (item_id,)).fetchone()
    if not item:
        return
    for entry in conn.execute("SELECT * FROM work_commands WHERE work_item_id=%s AND status='pending' ORDER BY revision,created_at", (item_id,)).fetchall():
        task = service.locked_task(conn, entry['task_id'])
        if task['status'] in service.TERMINAL | service.STOPPING:
            conn.execute("UPDATE work_commands SET status='superseded',updated_at=now() WHERE id=%s", (entry['id'],))
            conn.execute("UPDATE controls SET status='superseded',updated_at=now() WHERE id=%s AND status='pending'", (entry['id'],))
            continue
        payload = entry['payload']
        conn.execute("INSERT INTO controls(id,task_id,input,source,work_revision,source_message_id) VALUES(%s,%s,%s,'owner',%s,%s) ON CONFLICT(id) DO NOTHING",
                     (entry['id'], task['id'], 'Owner direction: ' + payload['instruction'], entry['revision'], payload['source_message_id']))
        conn.execute('UPDATE tasks SET orchestration_version=%s,work_revision=%s WHERE id=%s', (VERSION, entry['revision'], task['id']))
        # Refresh bindings only after accepting the owner's changed requirements.
        for outcome in outcomes.assigned(conn, task):
            conn.execute('UPDATE task_outcomes SET outcome_revision=%s WHERE task_id=%s AND outcome_id=%s', (outcome['revision'], task['id'], outcome['id']))
        if not task['parent_id']:
            for outcome in outcomes.rows(conn, item_id):
                conn.execute('INSERT INTO task_outcomes VALUES(%s,%s,%s) ON CONFLICT(task_id,outcome_id) DO UPDATE SET outcome_revision=excluded.outcome_revision', (task['id'], outcome['id'], outcome['revision']))
        conn.execute("UPDATE work_commands SET status='recorded',updated_at=now() WHERE id=%s", (entry['id'],))
    admit_requested_drafts(conn, item_id)
    for row in conn.execute("SELECT * FROM tasks WHERE work_item_id=%s AND status IN ('blocked','waiting_children','waiting_results','waiting_approval','waiting_input')", (item_id,)).fetchall():
        active = conn.execute('SELECT 1 FROM attempts WHERE task_id=%s AND status=ANY(%s)', (row['id'], list(service.ACTIVE_ATTEMPTS))).fetchone()
        direction = conn.execute("SELECT 1 FROM controls WHERE task_id=%s AND source='owner' AND status='pending'", (row['id'],)).fetchone()
        if active or not direction:
            continue
        if row['status'] == 'blocked' and row['parent_id']:
            dependencies = conn.execute('SELECT t.status FROM dependencies d JOIN tasks t ON t.id=d.depends_on WHERE d.task_id=%s', (row['id'],)).fetchall()
            if any(dependency['status'] != 'completed' for dependency in dependencies):
                continue  # The manager replans; a correction does not manufacture missing inputs.
        # An owner correction can change a plan while a prior action is unresolved;
        # the affected action itself remains fenced by its receipt state.
        conn.execute("UPDATE tasks SET status='queued',continuation=%s,updated_at=now() WHERE id=%s",
                     ('Apply the pending owner directions to the current work. Preserve completed results and inspect outstanding external receipts before acting.', row['id']))
        db.event(conn, 'work.direction_ready', row)
    cursor = conn.execute('SELECT COALESCE(max(e.id),0) AS id FROM events e JOIN tasks t ON t.id=e.task_id WHERE t.work_item_id=%s', (item_id,)).fetchone()['id']
    conn.execute('UPDATE work_items SET last_processed_event=%s WHERE id=%s', (cursor, item_id))


def admit_requested_drafts(conn, item_id):
    from . import connector_catalog, tool_references
    for operation in conn.execute("SELECT o.* FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.work_item_id=%s AND o.status='pending' AND o.outcome_id IS NOT NULL", (item_id,)).fetchall():
        task = service.locked_task(conn, operation['task_id'])
        if task['status'] in service.TERMINAL | service.STOPPING:
            continue
        connection = conn.execute('SELECT * FROM connections WHERE id=%s', (operation['connection_id'],)).fetchone()
        if not connection or connection['revision'] != operation['connection_revision']:
            continue
        try:
            _, _, resource = connector_catalog.validate(connection, operation['action'], operation['arguments'])
        except HTTPException:
            continue
        authorization = tool_references.requested_authorization(conn, task, operation['outcome_id'], connection, operation['action'], resource)
        if not authorization:
            continue
        # The queued payload remains frozen and the dispatcher checks current permissions,
        # task state, revisions and authorization again before any external action.
        conn.execute("UPDATE connector_operations SET status='queued',authorization_id=%s,approval_id=NULL,updated_at=now() WHERE id=%s", (authorization['id'], operation['id']))
        if operation['approval_id']:
            conn.execute("UPDATE approvals SET status='stale' WHERE id=%s AND status='pending'", (operation['approval_id'],))
        db.event(conn, 'connector.request_authorized', task, data={'operation_id': str(operation['id'])})


def advance():
    with db.connection() as conn:
        identities = conn.execute("SELECT DISTINCT w.id FROM work_items w LEFT JOIN tasks t ON t.work_item_id=w.id WHERE w.orchestration_version=%s AND (t.status<>ALL(%s) OR EXISTS(SELECT 1 FROM work_commands c WHERE c.work_item_id=w.id AND c.status='pending'))",
                                  (VERSION, list(service.TERMINAL))).fetchall()
    for identity in identities:
        with db.connection() as conn:
            reconcile(conn, identity['id'])
