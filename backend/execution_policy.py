"""Active execution budgets, independent of harness turns and authorization expiry."""
from datetime import timedelta

from fastapi import HTTPException

from . import db, registry, service

STOPPING = service.STOPPING


def expired(task):
    return task['deadline'] is not None and task['deadline'] <= service.now()


def review_expiry(task):
    expiry = service.now() + timedelta(hours=24)
    return min(expiry, task['deadline']) if task['deadline'] else expiry


def budget(conn, task):
    from .recurring_management import scope
    managed = bool(scope(conn, task))
    identity = task.get('management_work_item_id') or task['work_item_id']
    if identity and (task['message_intent'] in ('assignment', 'update') or managed):
        limit = conn.execute('SELECT active_budget_seconds FROM work_items WHERE id=%s', (identity,)).fetchone()['active_budget_seconds']
        used = conn.execute("""SELECT COALESCE(sum(a.active_seconds + CASE WHEN %s AND a.accounted_at IS NOT NULL
            THEN greatest(0,extract(epoch FROM now()-a.accounted_at)) ELSE 0 END),0) AS used
            FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE
            ((NOT %s AND t.work_item_id=%s AND t.message_intent IN ('assignment','update'))
             OR (%s AND t.management_work_item_id=%s))""", (managed, managed, identity, managed, identity)).fetchone()['used']
        used = float(used)
    else:
        limit = conn.execute('SELECT active_budget_seconds FROM tasks WHERE id=%s', (task['root_id'],)).fetchone()['active_budget_seconds']
        used = conn.execute('SELECT COALESCE(sum(a.active_seconds),0) AS used FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE t.root_id=%s', (task['root_id'],)).fetchone()['used']
    return {'limit_seconds': limit, 'used_seconds': round(used, 3), 'remaining_seconds': max(0, round(limit - used, 3))}


def budget_available(task_id):
    with db.connection() as conn:
        task = conn.execute('SELECT * FROM tasks WHERE id=%s', (task_id,)).fetchone()
        return budget(conn, task)['remaining_seconds']


def account(conn, attempt_id, *, running=True, at=None):
    at = at or service.now()
    attempt = conn.execute('SELECT * FROM attempts WHERE id=%s FOR UPDATE', (attempt_id,)).fetchone()
    if not attempt:
        return
    if running and conn.execute('SELECT 1 FROM computer_controls WHERE bot_id=%s AND paused_at IS NOT NULL', (attempt['bot_id'],)).fetchone():
        running = False  # A bot paused for a takeover does no work, so its clock stays stopped until handback.
    # Charge the interval according to the previous observed state. A waiting execution
    # starts charging again only after it is observed running, including after reconnect.
    elapsed = max(0, (at - attempt['accounted_at']).total_seconds()) if attempt['accounted_at'] else 0
    conn.execute('UPDATE attempts SET active_seconds=active_seconds+%s,accounted_at=%s,started_at=COALESCE(started_at,%s) WHERE id=%s',
                 (elapsed, at if running else None, at, attempt_id))


def pause(conn, task):
    from .recurring_management import tasks as occurrence_tasks
    task = service.locked_task(conn, task['root_id'])
    if task['status'] in service.TERMINAL | STOPPING:
        return
    tree = occurrence_tasks(conn, task)
    for target in tree:
        if target['status'] in service.TERMINAL | STOPPING:
            continue
        active = conn.execute('SELECT status FROM attempts WHERE task_id=%s AND status=ANY(%s)', (target['id'], list(service.ACTIVE_ATTEMPTS) + ['reserved'])).fetchall()
        if any(a['status'] == 'uncertain' for a in active):
            continue
        state = 'pausing_budget' if active else 'paused_budget'
        conn.execute("UPDATE tasks SET status=%s,stop_kind='budget',stop_reason='Active work budget reached',error=NULL,updated_at=now() WHERE id=%s", (state, target['id']))
        conn.execute("UPDATE controls SET status='discarded',updated_at=now() WHERE task_id=%s AND source='monitor' AND status='pending'", (target['id'],))
        conn.execute("UPDATE attempts SET status='stopping' WHERE task_id=%s AND status=ANY(%s)", (target['id'], list(service.ACTIVE_ATTEMPTS) + ['reserved']))
        db.event(conn, 'task.budget_pause_requested', target, data={'status': state, 'budget': budget(conn, target)})


def settle_pause(conn, task, attempt, output):
    # Native tools have no application-owned dispatch receipt. If one has run while
    # an assigned native effect remains unconfirmed, continuing could duplicate it.
    if conn.execute("SELECT 1 FROM events WHERE task_id=%s AND kind='tool.started' AND created_at>=%s", (task['id'], attempt['created_at'])).fetchone():
        conn.execute("UPDATE work_outcomes SET status='uncertain',reason='Native execution was interrupted before this external result was confirmed. Verify the destination before continuing.',updated_at=now() WHERE kind='native_effect' AND status NOT IN ('fulfilled','cancelled','uncertain') AND id IN (SELECT outcome_id FROM task_outcomes WHERE task_id=%s)", (task['id'],))
    conn.execute("UPDATE tasks SET status='paused_budget',output=%s,error=NULL,updated_at=now() WHERE id=%s", (output, task['id']))
    db.event(conn, 'task.budget_paused', task, data={'attempt_id': str(attempt['id'])})


def continue_work(conn, task):
    from .recurring_management import scope, tasks as occurrence_tasks
    task = service.locked_task(conn, task['root_id'])
    if task['status'] != 'paused_budget' or task['stop_kind'] != 'budget':
        raise HTTPException(409, 'Only work paused by its budget can continue')
    service.require_known_connector_outcomes(conn, task['root_id'])
    tree = occurrence_tasks(conn, task)
    identities = [t['id'] for t in tree]
    if conn.execute('SELECT 1 FROM attempts WHERE task_id=ANY(%s) AND status=ANY(%s)', (identities, list(service.ACTIVE_ATTEMPTS) + ['reserved'])).fetchone():
        raise HTTPException(409, 'Wait for the old execution to settle before continuing')
    if expired(task):
        raise HTTPException(409, 'The owner deadline has expired')
    service.check_grant(conn, task)
    allowance = registry.workspace(conn)['active_budget_seconds']
    identity = task.get('management_work_item_id') or task['work_item_id']
    if identity:
        required = conn.execute("SELECT status FROM work_outcomes WHERE work_item_id=%s AND required AND status<>'cancelled'", (identity,)).fetchall()
        if required and all(o['status'] == 'fulfilled' for o in required):
            raise HTTPException(409, 'All required results are already ready; assign newly requested work separately')
        # Unknown actions in any revision of this card also fence continuation.
        if conn.execute("SELECT 1 FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE (t.work_item_id=%s OR t.management_work_item_id=%s) AND o.status IN ('dispatching','uncertain','unresolved')", (identity, identity)).fetchone():
            raise HTTPException(409, 'Reconcile the previous application outcome first')
        conn.execute('UPDATE work_items SET active_budget_seconds=active_budget_seconds+%s WHERE id=%s', (allowance, identity))
    else:
        conn.execute('UPDATE tasks SET active_budget_seconds=active_budget_seconds+%s WHERE id=%s', (allowance, task['root_id']))
    paused = [t for t in tree if t['status'] == 'paused_budget'] if scope(conn, task) else conn.execute("SELECT * FROM tasks WHERE root_id=%s AND status='paused_budget' ORDER BY depth DESC", (task['root_id'],)).fetchall()
    for target in paused:
        service.check_grant(conn, target)
        state = 'queued'
        if conn.execute("SELECT 1 FROM input_requests WHERE task_id=%s AND status='pending' AND expires_at>now()", (target['id'],)).fetchone():
            state = 'waiting_input'
        elif conn.execute("SELECT 1 FROM approvals WHERE task_id=%s AND status='pending' UNION ALL SELECT 1 FROM connector_operations WHERE task_id=%s AND status IN ('pending','queued','dispatching','uncertain')", (target['id'], target['id'])).fetchone():
            state = 'waiting_approval'
        elif conn.execute('SELECT 1 FROM dependencies WHERE task_id=%s', (target['id'],)).fetchone():
            state = 'blocked'
        elif target.get('coordination_version') == 1 and conn.execute('SELECT 1 FROM coordination_waits WHERE task_id=%s', (target['id'],)).fetchone():
            state = 'waiting_results'
        elif target.get('coordination_version') != 1 and conn.execute('SELECT 1 FROM tasks WHERE parent_id=%s AND status<>ALL(%s)', (target['id'], list(service.TERMINAL))).fetchone():
            state = 'waiting_children'
        continuation = 'The owner continued this work with additional execution budget. Inspect existing session history, granted artifacts and current outcomes. Continue unfinished results only, prepare again, and never repeat a fulfilled or uncertain external effect. Original request: ' + target['input']
        conn.execute("UPDATE tasks SET status=%s,stop_kind=NULL,stop_reason=NULL,error=NULL,continuation=concat_ws(E'\\n',NULLIF(continuation,''),%s::text),updated_at=now() WHERE id=%s", (state, continuation, target['id']))
    db.event(conn, 'task.budget_continued', task, data={'additional_seconds': allowance})
    return service.locked_task(conn, task['id'])
