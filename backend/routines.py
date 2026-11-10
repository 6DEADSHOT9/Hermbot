"""Saved owner instructions and bounded occurrence admission above unmodified Hermes."""
import asyncio
from datetime import timedelta
import secrets
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from psycopg.types.json import Jsonb

from . import db, native, registry, service, work_controller
from .routine_models import RoutineControl, RoutineDefinition, Schedule
from .routine_schedule import next_at, preview
from .secrets import credential_hash, decrypt, encrypt


COLUMNS = 'd.*,v.definition,v.source_message_id'
JOIN = 'routine_definitions d JOIN routine_revisions v ON v.routine_id=d.id AND v.revision=d.revision'


def clock(conn):
    return conn.execute('SELECT now() AS at').fetchone()['at']


def get(conn, identity, *, lock=False):
    row = conn.execute(f'SELECT {COLUMNS} FROM {JOIN} WHERE d.id=%s' + (' FOR UPDATE OF d' if lock else ''), (identity,)).fetchone()
    if not row:
        raise HTTPException(404, 'Routine not found')
    return row


def validate_access(conn, definition):
    from .recurring_management import validate
    validate(conn, definition)
    bot = registry.require_bot(conn, definition.bot_id)
    chat = conn.execute('SELECT archived_at FROM conversations WHERE id=%s', (definition.conversation_id,)).fetchone()
    if not chat or chat['archived_at'] or not conn.execute('SELECT 1 FROM participants WHERE conversation_id=%s AND bot_id=%s', (definition.conversation_id, definition.bot_id)).fetchone():
        raise HTTPException(409, 'Choose an available conversation containing this teammate')
    if bot['status'] != 'active' or bot['desired_state'] != 'running' or bot['revision'] != bot['applied_revision'] or not bot['readiness'].get('compatible') or not bot['provider_id']:
        raise HTTPException(409, 'Start the teammate and wait for its configuration and runtime to be ready')
    if registry.pending_operation(conn, bot['id']):
        raise HTTPException(409, 'A runtime lifecycle change is pending')
    if set(definition.skill_refs) - set(bot['enabled_skills']):
        raise HTTPException(409, 'A referenced skill is no longer enabled')
    from .library import visible_in_conversation
    if any(not visible_in_conversation(conn, a, definition.conversation_id) for a in definition.artifact_ids):
        raise HTTPException(409, 'A selected file version is no longer shared with the conversation')
    for identity in definition.attachment_ids:
        if not conn.execute('SELECT 1 FROM attachments WHERE id=%s AND conversation_id=%s', (identity, definition.conversation_id)).fetchone():
            raise HTTPException(409, 'A selected attachment is unavailable')


def ensure_binding(conn, bot_id):
    token = secrets.token_urlsafe(32)
    conn.execute('INSERT INTO routine_schedulers(bot_id,token_hash,token_encrypted,generation) VALUES(%s,%s,%s,%s) ON CONFLICT(bot_id) DO UPDATE SET enabled=true',
                 (bot_id, credential_hash(token), encrypt(token), uuid4()))


def cancel_unstarted(conn, identity, reason):
    rows = conn.execute("""SELECT t.*,r.id AS run_id FROM routine_runs r JOIN tasks t ON t.id=r.task_id
        WHERE r.routine_id=%s AND t.status='queued'
          AND NOT EXISTS(SELECT 1 FROM attempts a JOIN tasks e ON e.id=a.task_id WHERE e.root_id=t.root_id)
        FOR UPDATE OF t SKIP LOCKED""", (identity,)).fetchall()
    for task in rows:
        service.cancel(conn, task, reason)
        conn.execute('UPDATE routine_runs SET reason=%s WHERE id=%s', (reason, task['run_id']))


def save(conn, body, identity=None, *, draft=False, draft_source=None):
    at = clock(conn)
    if identity:
        old = get(conn, identity, lock=True)
        if old['state'] == 'archived':
            raise HTTPException(409, 'This routine is archived. Create a new routine to schedule it again')
        if body.expected_revision != old['revision']:
            raise HTTPException(409, 'Routine changed. Reload before saving')
        revision = old['revision'] + 1
    else:
        identity, revision = uuid4(), 1
    if draft:
        registry.require_bot(conn, body.bot_id)
        if not conn.execute('SELECT 1 FROM participants WHERE conversation_id=%s AND bot_id=%s', (body.conversation_id, body.bot_id)).fetchone():
            raise HTTPException(409, 'Choose a teammate in the destination conversation')
    else:
        validate_access(conn, body)
    upcoming = next_at(body.schedule, at, inclusive=True)
    if body.enabled and not draft and upcoming is None:
        raise HTTPException(422, 'Choose a future schedule')
    state = 'draft' if draft else 'active' if body.enabled else 'paused'
    definition = body.model_dump(mode='json', exclude={'expected_revision'})
    if revision == 1:
        conn.execute('INSERT INTO routine_definitions(id,bot_id,conversation_id,state) VALUES(%s,%s,%s,%s)', (identity, body.bot_id, body.conversation_id, state))
    # Inactive model proposals retain the real owner request; only an owner save records authority.
    source = {'id': draft_source} if draft else conn.execute("INSERT INTO messages(conversation_id,author,role,content,source_id) VALUES(%s,'owner','user',%s,%s) RETURNING id",
                          (body.conversation_id, body.instructions, f'routine-revision:{identity}:{revision}')).fetchone()
    conn.execute('INSERT INTO routine_revisions(routine_id,revision,definition,source_message_id) VALUES(%s,%s,%s,%s)', (identity, revision, Jsonb(definition), source['id']))
    conn.execute('UPDATE routine_definitions SET bot_id=%s,conversation_id=%s,revision=%s,state=%s,next_run_at=%s,error=NULL,updated_at=now() WHERE id=%s',
                 (body.bot_id, body.conversation_id, revision, state, upcoming if state == 'active' else None, identity))
    if revision > 1 and not draft:
        cancel_unstarted(conn, identity, 'Routine revision changed before execution')
    if not draft and body.enabled:
        ensure_binding(conn, body.bot_id)
    db.event(conn, 'routine.drafted' if draft else 'routine.saved', conversation_id=body.conversation_id, bot_id=body.bot_id, data={'routine_id': str(identity), 'revision': revision})
    return get(conn, identity)


def unfinished(conn, identity):
    # Include owner revisions/retries in the same work and unresolved effects after a stop.
    return conn.execute("""SELECT r.id,r.task_id FROM routine_runs r JOIN tasks original ON original.id=r.task_id
        WHERE r.routine_id=%s AND (
          EXISTS(SELECT 1 FROM tasks t WHERE (t.work_item_id=COALESCE(original.management_work_item_id,original.work_item_id) OR t.management_work_item_id=original.management_work_item_id) AND
            (t.status<>ALL(%s) OR EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=t.id AND a.status=ANY(%s))))
          OR EXISTS(SELECT 1 FROM teammate_messages m JOIN work_items w ON w.id=m.work_item_id WHERE w.id=COALESCE(original.management_work_item_id,original.work_item_id) AND w.management_scope<>'{}'::jsonb AND m.status='pending' AND m.reply_to IS NULL AND m.response_task_id IS NULL AND NOT EXISTS(SELECT 1 FROM teammate_messages reply WHERE reply.reply_to=m.id))
          OR EXISTS(SELECT 1 FROM work_outcomes o WHERE o.work_item_id=COALESCE(original.management_work_item_id,original.work_item_id) AND o.status='uncertain')
          OR EXISTS(SELECT 1 FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE (t.work_item_id=COALESCE(original.management_work_item_id,original.work_item_id) OR t.management_work_item_id=original.management_work_item_id) AND o.status IN ('pending','queued','dispatching','uncertain','unresolved')))
        ORDER BY r.created_at LIMIT 1""", (identity, list(service.TERMINAL), list(service.ACTIVE_ATTEMPTS) + ['reserved'])).fetchone()


def create_saved_task(conn, definition, source_message_id, request_key, thread, context, *, origin='routine'):
    """Both timers and events use the same owner authority, outcomes and connector checks."""
    task = service.create_task(conn, bot_id=definition.bot_id, conversation_id=definition.conversation_id,
            prompt=definition.instructions, request_key=request_key, context=origin,
            owner_message_id=source_message_id, reply_to_message_id=thread,
            grant_id=definition.management.grant_id if definition.management else None,
            artifact_ids=definition.artifact_ids, attachment_ids=definition.attachment_ids, skill_refs=definition.skill_refs,
            orchestration_version=2, coordination_version=1)
    conn.execute("UPDATE tasks SET intake_status='applied',active_budget_seconds=%s WHERE id=%s", (definition.active_budget_seconds, task['id']))
    conn.execute('UPDATE work_items SET title=%s,active_budget_seconds=%s WHERE id=%s', (definition.name, definition.active_budget_seconds, task['work_item_id']))
    from .recurring_management import bind
    bind(conn, definition, task, source_message_id, context)
    task = service.locked_task(conn, task['id'])
    work_controller.apply(conn, task, task['work_item_id'], objective=definition.instructions,
            requirements=[s.model_dump(mode='json') for s in definition.requirements],
            effects=[e.model_dump(mode='json') for e in definition.requested_effects], kind='created')
    conn.execute('UPDATE tasks SET handoff_context=%s WHERE id=%s', (Jsonb(context), task['id']))
    conn.execute('UPDATE messages SET task_id=%s WHERE id=%s', (task['id'], thread))
    return task


def admit(conn, row, scheduled_at, origin, key, at):
    existing = conn.execute('SELECT * FROM routine_runs WHERE routine_id=%s AND occurrence_key=%s', (row['id'], key)).fetchone()
    if existing:
        return existing
    if origin == 'scheduled':
        prior = conn.execute("SELECT * FROM routine_runs WHERE routine_id=%s AND scheduled_at=%s AND origin='scheduled' AND status='admitted' ORDER BY created_at LIMIT 1", (row['id'], scheduled_at)).fetchone()
        if prior:
            return prior
    definition = RoutineDefinition.model_validate(row['definition'])
    before = scheduled_at + timedelta(minutes=definition.start_window_minutes)
    reason = ''
    active = unfinished(conn, row['id'])
    if active:
        reason = f"Earlier run {active['id']} remains unfinished or needs reconciliation"
    elif origin == 'scheduled' and at > before:
        reason = 'Missed permitted start window'
    elif conn.execute("SELECT count(*) AS n FROM routine_runs WHERE routine_id=%s AND status='admitted' AND created_at>=date_trunc('day',%s::timestamptz AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'", (row['id'], at)).fetchone()['n'] >= definition.max_runs_per_day:
        reason = 'Daily run limit reached (UTC day)'
    else:
        try:
            validate_access(conn, definition)
        except HTTPException as exc:
            reason = str(exc.detail)
    identity = uuid4()
    run = conn.execute('INSERT INTO routine_runs(id,routine_id,revision,occurrence_key,scheduled_at,origin,status,reason,start_before) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                       (identity, row['id'], row['revision'], key, scheduled_at, origin, 'skipped' if reason else 'admitted', reason, before)).fetchone()
    if reason:
        db.event(conn, 'routine.skipped', conversation_id=row['conversation_id'], data={'routine_id': str(row['id']), 'run_id': str(identity), 'reason': reason})
        return run
    context = {'routine_id': str(row['id']), 'run_id': str(identity), 'saved_revision': row['revision'],
               'scheduled_at': scheduled_at.isoformat(), 'admitted_at': at.isoformat(),
               'timezone': definition.schedule.timezone, 'window_mode': definition.window_mode}
    if definition.window_mode == 'since_success':
        previous = conn.execute("""SELECT r.id,r.scheduled_at FROM routine_runs r JOIN routine_revisions v ON v.routine_id=r.routine_id AND v.revision=r.revision
          JOIN tasks t ON t.id=r.task_id WHERE r.routine_id=%s AND r.scheduled_at<%s AND (t.status='completed' OR (t.management_work_item_id IS NOT NULL AND EXISTS(SELECT 1 FROM tasks repaired WHERE repaired.management_work_item_id=t.management_work_item_id AND repaired.parent_id IS NULL AND repaired.execution_kind<>'peer_message' AND repaired.status='completed')))
          AND v.definition->>'instructions'=%s AND v.definition->'requirements'=%s
          AND v.definition->>'bot_id'=%s
          AND COALESCE(v.definition->'artifact_ids','[]'::jsonb)=%s
          AND COALESCE(v.definition->'attachment_ids','[]'::jsonb)=%s
          AND COALESCE(v.definition->'skill_refs','[]'::jsonb)=%s
          AND (COALESCE(NULLIF(v.definition->'management','null'::jsonb),'{}'::jsonb)-'grant_id')=%s
          AND EXISTS(SELECT 1 FROM work_outcomes o WHERE o.work_item_id=COALESCE(t.management_work_item_id,t.work_item_id) AND o.required AND o.status='fulfilled')
          AND NOT EXISTS(SELECT 1 FROM work_outcomes o WHERE o.work_item_id=COALESCE(t.management_work_item_id,t.work_item_id) AND o.required AND o.status NOT IN ('fulfilled','cancelled'))
          AND NOT EXISTS(SELECT 1 FROM connector_operations o JOIN tasks e ON e.id=o.task_id WHERE (e.work_item_id=COALESCE(t.management_work_item_id,t.work_item_id) OR e.management_work_item_id=t.management_work_item_id) AND o.status IN ('pending','queued','dispatching','uncertain','unresolved'))
          AND NOT EXISTS(SELECT 1 FROM tasks e WHERE (e.work_item_id=COALESCE(t.management_work_item_id,t.work_item_id) OR e.management_work_item_id=t.management_work_item_id) AND (e.message_intent IN ('assignment','update') OR e.execution_kind='peer_message') AND e.status<>ALL(%s))
          AND NOT EXISTS(SELECT 1 FROM attempts a JOIN tasks e ON e.id=a.task_id WHERE (e.work_item_id=COALESCE(t.management_work_item_id,t.work_item_id) OR e.management_work_item_id=t.management_work_item_id) AND a.status=ANY(%s))
          AND NOT EXISTS(SELECT 1 FROM teammate_messages m WHERE m.work_item_id=COALESCE(t.management_work_item_id,t.work_item_id) AND m.status='pending' AND m.reply_to IS NULL AND m.response_task_id IS NULL AND NOT EXISTS(SELECT 1 FROM teammate_messages reply WHERE reply.reply_to=m.id))
          ORDER BY r.scheduled_at DESC LIMIT 1""", (row['id'], scheduled_at, definition.instructions, Jsonb(row['definition']['requirements']), definition.bot_id,
          Jsonb(row['definition'].get('artifact_ids', [])), Jsonb(row['definition'].get('attachment_ids', [])), Jsonb(row['definition'].get('skill_refs', [])),
          Jsonb({k: v for k, v in (row['definition'].get('management') or {}).items() if k != 'grant_id'}), list(service.TERMINAL), list(service.ACTIVE_ATTEMPTS) + ['reserved'])).fetchone()
        context['window_start'] = (previous['scheduled_at'] if previous else scheduled_at - timedelta(minutes=definition.initial_lookback_minutes)).isoformat()
        context['window_end'] = scheduled_at.isoformat()
        context['previous_successful_run_id'] = str(previous['id']) if previous else None
        context['window_instruction'] = 'Use the half-open source-data time window [window_start, window_end). Saved instructions define the work. These timestamps do not authorize actions or guarantee source-item deduplication.'
    conn.execute('UPDATE routine_runs SET context=%s WHERE id=%s', (Jsonb(context), identity))
    # A system thread identifies the occurrence without impersonating a new owner message.
    thread = conn.execute("INSERT INTO messages(conversation_id,author,role,content,source_id) VALUES(%s,'system','system',%s,%s) RETURNING id",
                          (row['conversation_id'], f"{definition.name} · {scheduled_at.isoformat()} · saved revision {row['revision']}", f'routine-run:{identity}')).fetchone()['id']
    try:
        with conn.transaction():
            task = create_saved_task(conn, definition, row['source_message_id'], f'routine:{identity}', thread, {'routine': context})
            conn.execute('UPDATE routine_runs SET task_id=%s WHERE id=%s', (task['id'], identity))
            conn.execute('UPDATE tasks SET routine_run_id=%s WHERE id=%s', (identity, task['id']))
            db.event(conn, 'routine.admitted', task, data={'routine_id': str(row['id']), 'run_id': str(identity), 'revision': row['revision']})
    except HTTPException as exc:
        conn.execute("UPDATE routine_runs SET status='skipped',reason=%s WHERE id=%s", (str(exc.detail), identity))
        conn.execute('DELETE FROM messages WHERE id=%s AND task_id IS NULL', (thread,))
    return conn.execute('SELECT * FROM routine_runs WHERE id=%s', (identity,)).fetchone()


def tick(conn, bot_id):
    at = clock(conn)
    rows = conn.execute(f"SELECT {COLUMNS} FROM {JOIN} WHERE d.bot_id=%s AND d.state='active' AND d.next_run_at<=%s ORDER BY d.next_run_at,d.id FOR UPDATE OF d SKIP LOCKED LIMIT 50", (bot_id, at)).fetchall()
    for row in rows:
        definition = RoutineDefinition.model_validate(row['definition'])
        recent = conn.execute("""SELECT t.* FROM routine_runs r JOIN tasks t ON t.id=r.task_id
            WHERE r.routine_id=%s AND r.status='admitted' ORDER BY r.created_at DESC,r.id DESC LIMIT 3""", (row['id'],)).fetchall()
        from .recurring_management import definite_failure
        if len(recent) == 3 and all(definite_failure(conn, run) for run in recent):
            conn.execute("UPDATE routine_definitions SET state='paused',next_run_at=NULL,error='Three consecutive runs failed; inspect history and resume explicitly',checked_at=%s WHERE id=%s", (at, row['id']))
            db.event(conn, 'routine.paused', conversation_id=row['conversation_id'], data={'routine_id': str(row['id']), 'reason': 'Three consecutive runs failed'})
            continue
        chat = conn.execute('SELECT archived_at FROM conversations WHERE id=%s', (row['conversation_id'],)).fetchone()
        if not chat or chat['archived_at']:
            conn.execute("UPDATE routine_definitions SET state='paused',next_run_at=NULL,error='Destination conversation archived; explicitly resume after restoring it',checked_at=%s WHERE id=%s", (at, row['id']))
            db.event(conn, 'routine.paused', conversation_id=row['conversation_id'], data={'routine_id': str(row['id']), 'reason': 'Destination conversation archived'})
            continue
        due = row['next_run_at']
        # Bounded history for long outages: one summary plus the latest candidate, not millions of rows.
        recent = next_at(definition.schedule, at - timedelta(minutes=definition.start_window_minutes), inclusive=True)
        if recent and recent <= at and recent > due:
            conn.execute("INSERT INTO routine_runs(id,routine_id,revision,occurrence_key,scheduled_at,origin,status,reason,start_before) VALUES(%s,%s,%s,%s,%s,'scheduled','skipped',%s,%s) ON CONFLICT DO NOTHING", (uuid4(), row['id'], row['revision'], f"{row['revision']}:{due.isoformat()}:gap", due, f'Missed occurrences before {recent.isoformat()}; no backlog replay', recent))
            if definition.catch_up == 'latest':
                while (following := next_at(definition.schedule, recent)) and following <= at:
                    recent = following
                due = recent
        key = f"{row['revision']}:{due.isoformat()}"
        admit(conn, row, due, 'scheduled', key, at)
        upcoming = next_at(definition.schedule, at)
        conn.execute("UPDATE routine_definitions SET next_run_at=%s,checked_at=%s,state=CASE WHEN %s::timestamptz IS NULL THEN 'finished' ELSE state END WHERE id=%s", (upcoming, at, upcoming, row['id']))
    return len(rows)


def start_allowed(conn, task):
    root = conn.execute('SELECT routine_run_id FROM tasks WHERE id=%s', (task['root_id'],)).fetchone()
    if not root or not root['routine_run_id']:
        return True
    run = conn.execute('SELECT r.*,d.state,d.revision AS current_revision FROM routine_runs r JOIN routine_definitions d ON d.id=r.routine_id WHERE r.id=%s', (root['routine_run_id'],)).fetchone()
    from .recurring_management import scope
    started = conn.execute('SELECT 1 FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE t.root_id=%s OR (%s AND t.work_item_id=%s) LIMIT 1', (task['root_id'], bool(scope(conn, task)), task['work_item_id'])).fetchone()
    if started:
        return True  # Pause/edit applies to future runs; stop current uses ordinary cancellation.
    reason = None
    if run['state'] not in ('active', 'finished') and run['origin'] != 'manual':
        reason = 'Routine paused or archived before execution'
    if run['state'] == 'archived' or run['revision'] != run['current_revision']:
        reason = 'Routine revision superseded before execution'
    chat = conn.execute('SELECT archived_at FROM conversations WHERE id=%s', (task['conversation_id'],)).fetchone()
    if not chat or chat['archived_at']:
        reason = 'Destination conversation archived before execution'
    if clock(conn) > run['start_before']:
        reason = 'Missed permitted start window while queued'
    if not reason:
        saved = conn.execute('SELECT definition FROM routine_revisions WHERE routine_id=%s AND revision=%s', (run['routine_id'], run['revision'])).fetchone()
        try:
            validate_access(conn, RoutineDefinition.model_validate(saved['definition']))
        except HTTPException as exc:
            reason = str(exc.detail)
    if reason:
        service.cancel(conn, task, reason)
        conn.execute('UPDATE routine_runs SET reason=%s WHERE id=%s', (reason, run['id']))
        return False
    return True


def history(conn, identity, before=None, limit=30, before_id=None):
    rows = conn.execute("""SELECT r.*,t.work_item_id,t.output,t.error,t.status AS execution_status,t.attempts,t.reply_to_message_id
        FROM routine_runs r LEFT JOIN tasks t ON t.id=r.task_id
        WHERE r.routine_id=%s AND (%s::timestamptz IS NULL OR (r.created_at,r.id)<(%s,%s::uuid))
        ORDER BY r.created_at DESC,r.id DESC LIMIT %s""", (identity, before, before, before_id or UUID(int=0), limit)).fetchall()
    from . import work_items
    items = {item['id']: item for item in work_items.list_items(conn, identities=list({r['work_item_id'] for r in rows if r['work_item_id']}))} if any(r['task_id'] for r in rows) else {}
    for row in rows:
        row['display_status'] = items.get(row['work_item_id'], {}).get('status', row['execution_status'] or row['status'])
    return rows


def list_routines(conn, conversation_id=None):
    # shortcut: show at most 200 routines; add cursor pagination when a workspace approaches that count.
    rows = conn.execute(f"SELECT {COLUMNS} FROM {JOIN} WHERE (%s::uuid IS NULL OR d.conversation_id=%s) ORDER BY (d.state='archived'),d.updated_at DESC LIMIT 200", (conversation_id, conversation_id)).fetchall()
    for row in rows:
        row['next_runs'] = preview(row['definition']['schedule'], clock(conn)) if row['state'] == 'active' else []
        row['latest_run'] = next(iter(history(conn, row['id'], limit=1)), None)
        binding = conn.execute('SELECT runtime_job_id,last_tick_at,error,enabled FROM routine_schedulers WHERE bot_id=%s', (row['bot_id'],)).fetchone()
        row['scheduler'] = binding
    return rows


def control(conn, identity, action, expected_revision, key):
    row = get(conn, identity, lock=True)
    if row['revision'] != expected_revision:
        raise HTTPException(409, 'Routine changed. Reload before acting')
    if action == 'run':
        if row['state'] in ('draft', 'archived'):
            raise HTTPException(409, 'Save this routine before running it')
        return admit(conn, row, clock(conn), 'manual', 'manual:' + key, clock(conn))
    if action not in ('pause', 'pause-stop', 'resume', 'archive') or row['state'] == 'archived' or (row['state'] == 'draft' and action != 'archive'):
        raise HTTPException(409, 'This routine cannot perform that action')
    state = {'pause': 'paused', 'pause-stop': 'paused', 'resume': 'active', 'archive': 'archived'}[action]
    upcoming = None
    if action == 'resume':
        definition = RoutineDefinition.model_validate(row['definition'])
        validate_access(conn, definition)
        upcoming = next_at(definition.schedule, clock(conn))
        if not upcoming:
            raise HTTPException(409, 'Edit the one-time schedule to a future time before resuming')
        ensure_binding(conn, row['bot_id'])
    definition = {**row['definition'], 'enabled': state == 'active'}
    conn.execute('INSERT INTO routine_revisions(routine_id,revision,definition,source_message_id) VALUES(%s,%s,%s,%s)', (identity, row['revision'] + 1, Jsonb(definition), row['source_message_id']))
    conn.execute('UPDATE routine_definitions SET revision=revision+1,state=%s,next_run_at=%s,error=NULL,updated_at=now() WHERE id=%s', (state, upcoming, identity))
    if action in ('pause', 'pause-stop', 'archive'):
        cancel_unstarted(conn, identity, 'Routine paused or archived before execution')
    if action == 'pause-stop':
        roots = conn.execute("""SELECT t.* FROM tasks t WHERE t.parent_id IS NULL
            AND (t.message_intent IN ('assignment','update') OR t.execution_kind='peer_message')
            AND t.work_item_id IN (SELECT original.work_item_id FROM routine_runs r JOIN tasks original ON original.id=r.task_id WHERE r.routine_id=%s)
            AND t.status<>ALL(%s) ORDER BY t.id""", (identity, list(service.TERMINAL - {'uncertain'}))).fetchall()
        for task in roots:
            service.cancel(conn, task, 'Owner paused scheduling and stopped current work')
    db.event(conn, 'routine.' + action, conversation_id=row['conversation_id'], data={'routine_id': str(identity)})
    return get(conn, identity)


async def reconcile_schedulers():
    with db.connection() as conn:
        # A bot paused for a takeover is checked again after handback.
        rows = conn.execute("SELECT s.*,b.status,b.desired_state FROM routine_schedulers s JOIN bots b ON b.id=s.bot_id WHERE (s.checked_at IS NULL OR s.checked_at<now()-interval '60 seconds') "
                            "AND s.bot_id NOT IN (SELECT bot_id FROM computer_controls WHERE paused_at IS NOT NULL)").fetchall()
    for binding in rows:
        with db.connection() as conn:
            wanted = bool(conn.execute("SELECT 1 FROM routine_definitions WHERE bot_id=%s AND state='active'", (binding['bot_id'],)).fetchone())
            conn.execute('UPDATE routine_schedulers SET checked_at=now(),enabled=%s WHERE bot_id=%s', (wanted, binding['bot_id']))
        if binding['status'] != 'active' or binding['desired_state'] != 'running':
            continue
        try:
            result = await asyncio.to_thread(native.request, binding['bot_id'], 'PUT', '/routine-scheduler', json={'generation': str(binding['generation']), 'token': decrypt(binding['token_encrypted']), 'enabled': wanted})
            with db.connection() as conn:
                conn.execute('UPDATE routine_schedulers SET runtime_job_id=%s,error=NULL WHERE bot_id=%s AND generation=%s', (result['job_id'], binding['bot_id'], binding['generation']))
        except Exception:
            with db.connection() as conn:
                conn.execute("UPDATE routine_schedulers SET error='Native routine scheduler unavailable; check runtime readiness' WHERE bot_id=%s", (binding['bot_id'],))


def routes(owner):
    router = APIRouter(dependencies=[Depends(owner)])

    @router.get('/api/v1/routines')
    def listing():
        with db.connection() as conn:
            return {'data': list_routines(conn)}

    @router.get('/api/v1/routines/{identity}')
    def by_identity(identity: UUID):
        with db.connection() as conn:
            return get(conn,identity)

    @router.post('/api/v1/routines/preview')
    def schedule_preview(body: Schedule):
        with db.connection() as conn:
            return {'next_runs': preview(body, clock(conn)), 'timing': 'Minute resolution; actual start depends on queue capacity'}

    @router.put('/api/v1/routines/{identity}')
    def update(identity: UUID, body: RoutineDefinition, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            def persist():
                current = get(conn, identity, lock=True)
                if current['replaces_id']:
                    if body.expected_revision != current['revision']:
                        raise HTTPException(409, 'Draft changed. Reload before saving')
                    definition = body.model_copy(update={'expected_revision': current['replaces_revision']})
                    result = save(conn, definition, current['replaces_id'])
                    conn.execute("UPDATE routine_definitions SET state='archived' WHERE id=%s", (identity,))
                    return result
                return save(conn, body, identity)
            return registry.mutation(conn, key, {'action': 'routine.save', 'id': str(identity), 'body': body.model_dump(mode='json')}, persist)

    @router.post('/api/v1/routines/{identity}/{action}')
    def act(identity: UUID, action: str, body: RoutineControl, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            return registry.mutation(conn, key, {'action': 'routine.' + action, 'id': str(identity), 'revision': body.expected_revision}, lambda: control(conn, identity, action, body.expected_revision, key))

    @router.get('/api/v1/routines/{identity}/runs')
    def runs(identity: UUID, before: str | None = None, before_id: UUID | None = None, limit: int = Query(default=30, ge=1, le=100)):
        from datetime import datetime
        try:
            cursor = datetime.fromisoformat(before) if before else None
            if cursor and cursor.tzinfo is None:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'History cursor requires an absolute timestamp') from None
        with db.connection() as conn:
            get(conn, identity)
            return {'data': history(conn, identity, cursor, limit, before_id)}

    return router


def wake(bot_id, generation, token):
    with db.connection() as conn:
        binding = conn.execute('SELECT * FROM routine_schedulers WHERE bot_id=%s', (bot_id,)).fetchone()
        if not binding or str(binding['generation']) != str(generation) or not secrets.compare_digest(binding['token_hash'], credential_hash(token)):
            raise HTTPException(401, 'Routine scheduler authentication required')
        conn.execute('UPDATE routine_schedulers SET last_tick_at=now() WHERE bot_id=%s', (bot_id,))
        return {'checked': tick(conn, bot_id) if binding['enabled'] else 0}


async def run(stop):
    while not stop.is_set():
        try:
            await reconcile_schedulers()
        except Exception:
            import logging
            logging.getLogger('hermbot.routines').exception('Routine scheduler reconciliation failed')
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except TimeoutError:
            pass
