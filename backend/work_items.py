"""User-facing assignments are independent of the execution trees that answer messages."""
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from psycopg import errors

from . import db, inputs, service
from .models import WorkMessageRequest

WORK_ROLES = ('assignment', 'update')
ROUTING_INSTRUCTION = """For an unrouted message, call hermbot_route_message FIRST, before any other tool.
Interpret the owner's latest message in context, not by keywords. Discussion, thanks, explanations,
advice and progress questions do not create work. Choose assignment only when the owner asks you to
carry out a concrete piece of work, with a concise title. Choose update for an actual revision to
an existing assignment and status for a progress question. Supply an existing work_item_id only
from this conversation's candidates; neither choice grants access to other executions or apps.
Mentioning bots, attachments, enabling coordination or consulting a tool does not itself mean an
assignment. If the target or intent is ambiguous, choose discussion and clarify. Never create a
replacement assignment just because an update was refused. Once routed, do not route again.
When an owner message answers one pending_questions entry, route answer with that question ID and
the actual answer. This resumes the original task once without a new assignment or action approval.
For discussion/status/steered/answer turns, reply and finish; do not delegate, publish or change external
data. Status context is evidence: do not restart, retry or change the referenced work."""


def require_item(conn, identity, conversation_id):
    item = conn.execute('SELECT * FROM work_items WHERE id=%s AND conversation_id=%s', (identity, conversation_id)).fetchone()
    if not item:
        raise HTTPException(404, 'Work item does not belong to this conversation')
    return item


def link_messages(conn, task, item_id, intent):
    conn.execute('INSERT INTO work_item_messages(message_id,work_item_id,intent) SELECT id,%s,%s FROM messages WHERE task_id=%s AND role=\'user\' ON CONFLICT(message_id) DO UPDATE SET work_item_id=excluded.work_item_id,intent=excluded.intent', (item_id, intent, task['id']))


def new_item(conn, task, title=''):
    identity = uuid4()
    conn.execute('INSERT INTO work_items(id,conversation_id,bot_id,title,objective,active_budget_seconds,orchestration_version) VALUES(%s,%s,%s,%s,%s,%s,%s)', (identity, task['conversation_id'], task['bot_id'], title.strip() or task['input'][:120], task['input'], task['active_budget_seconds'], task.get('orchestration_version', 0)))
    conn.execute("UPDATE tasks SET work_item_id=%s,message_intent='assignment' WHERE id=%s", (identity, task['id']))
    link_messages(conn, task, identity, 'assignment')
    from . import registry
    version = registry.require_bot(conn, task['bot_id'])['readiness'].get('inventory', {}).get('outcomes_version', 0)
    conn.execute('UPDATE work_items SET outcomes_version=%s WHERE id=%s', (version, identity))
    db.event(conn, 'work.created', task, data={'work_item_id': str(identity)})
    return identity


def list_items(conn, conversation_id=None, *, identities=None, canonical=False):
    items = conn.execute('SELECT w.* FROM work_items w JOIN conversations c ON c.id=w.conversation_id WHERE ((%s::uuid IS NULL AND c.archived_at IS NULL) OR w.conversation_id=%s OR %s::uuid[] IS NOT NULL) AND (%s::uuid[] IS NULL OR w.id=ANY(%s::uuid[])) ORDER BY w.updated_at DESC,w.created_at DESC', (conversation_id, conversation_id, identities, identities, identities)).fetchall()
    if not items:
        return []
    work_column = 'COALESCE(management_work_item_id,work_item_id)' if canonical else 'work_item_id'
    executions = conn.execute(f'SELECT *,{work_column} AS projection_work_id FROM tasks WHERE {work_column}=ANY(%s) ORDER BY created_at,id', ([w['id'] for w in items],)).fetchall()
    from .connections import task_states
    states = task_states(conn, [t['id'] for t in executions])
    by_item = {item['id']: [] for item in items}
    artifact_work_column = 'COALESCE(t.management_work_item_id,t.work_item_id)' if canonical else 't.work_item_id'
    outputs = conn.execute(f'SELECT DISTINCT ON(a.family_id) a.id,a.task_id,a.bot_id,a.name,a.content_type,a.size,{artifact_work_column} AS work_item_id FROM artifacts a JOIN tasks t ON t.id=a.task_id WHERE {artifact_work_column}=ANY(%s) AND a.conversation_id=t.conversation_id ORDER BY a.family_id,a.version DESC', ([w['id'] for w in items],)).fetchall()
    for execution in executions:
        execution['input_hash'] = service.digest(execution['input'])
        execution['connector_state'] = states.get(execution['id'])
        by_item[execution.pop('projection_work_id')].append(execution)
    for item in items:
        runs = by_item[item['id']]
        assignments = [t for t in runs if (t['message_intent'] in WORK_ROLES or (canonical and t.get('management_work_item_id'))) and not t['parent_id'] and t.get('execution_kind')!='peer_message']
        active = [t for t in assignments if t['status'] not in service.TERMINAL]
        current = (active or assignments or [None])[-1]
        item['current_task'] = current
        item['outputs'] = [a for a in outputs if a['work_item_id'] == item['id']]
        item['executions'] = [t for t in runs if t['message_intent'] in WORK_ROLES or (canonical and t.get('management_work_item_id'))]
        item['status'] = current['status'] if current else 'completed'
        if any(t['connector_state'] in ('uncertain', 'unresolved') for t in runs):
            item['status'] = 'uncertain'
        from .outcomes import projection
        item['status'] = projection(conn, item, item['executions'], item['status'])
        if item.get('management_scope') and item['status'] == 'completed':
            from .recurring_management import pending_messages
            ongoing = [t for t in runs if (t['message_intent'] in WORK_ROLES or (canonical and t.get('management_work_item_id')) or t.get('execution_kind') == 'peer_message') and t['status'] not in service.TERMINAL]
            if ongoing:
                item['status'] = next((t['status'] for t in ongoing if t['status'] in ('cancelling','pausing_budget','paused_budget')), 'waiting_results')
            elif pending_messages(conn, item['id']):
                item['status'] = 'waiting_results'
        item['changes'] = conn.execute('SELECT revision,kind,status,source_message_id,change,created_at FROM work_changes WHERE work_item_id=%s ORDER BY revision DESC LIMIT 20', (item['id'],)).fetchall()
        item['directions'] = conn.execute('SELECT c.id,c.task_id,c.revision,COALESCE(d.status,c.status) AS status,c.error FROM work_commands c LEFT JOIN controls d ON d.id=c.id WHERE c.work_item_id=%s ORDER BY c.created_at DESC LIMIT 30', (item['id'],)).fetchall()
        item['updated_at'] = max([item['updated_at']] + [t['updated_at'] for t in runs if t['message_intent'] in WORK_ROLES])
    return sorted(items, key=lambda w: w['updated_at'], reverse=True)


def context(conn, task):
    # Public conversation data only. Work links never expose private child inputs or bot memory.
    basic = {'intent': task['message_intent'], 'work_item_id': str(task['work_item_id']) if task['work_item_id'] else None,
             'routing_required': task['message_intent'] == 'auto', 'instruction': ROUTING_INSTRUCTION}
    if task['message_intent'] != 'auto':
        basic['instruction'] = ('This message is already routed. Do not call hermbot_route_message again. '
                                'For assignment/update, carry out the recorded work within current permissions. '
                                'For discussion/status/steered, reply and finish without delegating, publishing or changing external data.')
        from . import outcomes
        result = {**basic, 'outcomes': outcomes.context(conn, task), 'questions': inputs.context(conn, task)}
        if task.get('orchestration_version') == 2:
            result['instruction'] = 'This message was interpreted by Hermbot. Carry out current assignment/update requirements; answer discussion/status/steered messages without starting duplicate work. Owner revisions are recorded by the application.'
            result['work'] = conn.execute('SELECT id,title,objective,revision FROM work_items WHERE id=%s', (task['work_item_id'],)).fetchone()
            result['owner_instructions'] = conn.execute("SELECT DISTINCT m.id,m.content,m.created_at FROM work_changes c JOIN messages m ON m.id=c.source_message_id WHERE c.work_item_id=%s ORDER BY m.id DESC LIMIT 15", (task['work_item_id'],)).fetchall()
            result['authorization'] = conn.execute('SELECT a.outcome_id,a.action,a.resource,a.resource_label,a.instruction_quote,a.source_message_id FROM work_authorizations a JOIN work_outcomes o ON o.id=a.outcome_id AND o.revision=a.outcome_revision WHERE a.work_item_id=%s AND a.revoked_at IS NULL', (task['work_item_id'],)).fetchall()
        return result
    items = [w for w in list_items(conn, task['conversation_id']) if w['current_task']]
    recent = conn.execute('SELECT m.id,m.author,m.role,left(m.content,1200) AS content,w.work_item_id FROM messages m LEFT JOIN work_item_messages w ON w.message_id=m.id WHERE m.conversation_id=%s AND m.id<=(SELECT max(id) FROM messages WHERE task_id=%s AND role=\'user\') ORDER BY m.id DESC LIMIT 12', (task['conversation_id'], task['id'])).fetchall()
    return {**basic,
            'candidates': [{'id': str(w['id']), 'title': w['title'], 'objective': w['objective'][:2000], 'status': w['status'], 'bot_id': w['bot_id'], 'hidden': w['hidden_at'] is not None} for w in items[:30]],
            'recent_messages': list(reversed(recent)),
            'pending_questions': inputs.pending(conn, task['conversation_id'])}


def route(conn, task, args, *, owner=False, intake=False):
    if task['parent_id']:
        raise HTTPException(409, 'Delegated assignments inherit their work item')
    if not owner and task['message_intent'] != 'auto':
        raise HTTPException(409, 'This message is already routed; continue its recorded intent')
    intent, identity = args['intent'], args.get('work_item_id')
    if intent == 'answer':
        if owner or not args.get('input_request_id') or not args.get('answer'):
            raise HTTPException(422, 'Answer routing requires the pending question and actual owner answer')
        from . import inputs
        question = conn.execute('SELECT q.* FROM input_requests q JOIN tasks t ON t.id=q.task_id WHERE q.id=%s AND t.conversation_id=%s', (args['input_request_id'], task['conversation_id'])).fetchone()
        if not question:
            raise HTTPException(403, 'Question does not belong to this conversation')
        try:
            with conn.transaction():
                origin = conn.execute('SELECT root_id FROM tasks WHERE id=%s', (question['task_id'],)).fetchone()
                conn.execute('SELECT id FROM tasks WHERE id=%s FOR UPDATE NOWAIT', (origin['root_id'],))
        except errors.LockNotAvailable:
            raise HTTPException(409, 'The original task is settling; answer shortly after it finishes this turn')
        answer_text = task['input'] if question['answer_type'] == 'text' else args['answer']
        inputs.answer(conn, question['id'], {'expected_question_hash': question['question_hash'], 'answer': answer_text}, source='owner_message', task=task)
        conn.execute("UPDATE tasks SET message_intent='answer',work_item_id=%s WHERE id=%s", (question['work_item_id'], task['id']))
        if question['work_item_id']:
            link_messages(conn, task, question['work_item_id'], 'answer')
        # The durable answer receipt is the acknowledgement; only the original work resumes.
        if intake:
            conn.execute("UPDATE tasks SET status='completed',output=NULL,updated_at=now() WHERE id=%s", (task['id'],))
        return {'intent': 'answer', 'work_item_id': str(question['work_item_id']) if question['work_item_id'] else None, 'instruction': 'The actual owner answer was recorded. The original task continues under its recorded authorization.'}
    if intent == 'assignment' and identity and not owner:
        raise HTTPException(422, 'Use update for existing work; a new assignment has no work_item_id')
    if intent in ('update', 'status') and not identity and not (intake and intent == 'status'):
        raise HTTPException(422, 'Choose the existing work item or clarify in the conversation')
    item = require_item(conn, identity, task['conversation_id']) if identity else None
    if owner and (task['status'] not in service.TERMINAL or conn.execute('SELECT 1 FROM attempts WHERE task_id=%s AND status=ANY(%s)', (task['id'], list(service.ACTIVE_ATTEMPTS) + ['reserved'])).fetchone()):
        raise HTTPException(409, 'Wait for this response to settle before changing its work-list association')
    if owner:
        service.require_known_connector_outcomes(conn, task['root_id'])
    if intent == 'assignment':
        if identity or task['work_item_id']:
            identity = identity or task['work_item_id']
            conn.execute("UPDATE tasks SET work_item_id=%s,message_intent='assignment' WHERE id=%s", (identity, task['id']))
            if owner:
                conn.execute('UPDATE work_items SET hidden_at=NULL WHERE id=%s', (identity,))
            if owner and args.get('title', '').strip():
                conn.execute('UPDATE work_items SET title=%s WHERE id=%s', (args['title'].strip(), identity))
        else:
            identity = new_item(conn, task, args.get('title', ''))
    elif intent == 'update':
        try:
            with conn.transaction():
                conn.execute('SELECT id FROM work_items WHERE id=%s FOR UPDATE NOWAIT', (identity,))
        except errors.LockNotAvailable:
            raise HTTPException(409, 'This work item is updating; clarify or retry the direction shortly')
        # Multiple roots can share a card, never authority. Unknown effects fence all new revisions.
        if not intake and conn.execute("SELECT 1 FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.work_item_id=%s AND o.status IN ('dispatching','uncertain','unresolved')", (identity,)).fetchone():
            raise HTTPException(409, 'Verify the previous application outcome before revising this work')
        if not owner and not intake and conn.execute("SELECT 1 FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.work_item_id=%s AND o.status IN ('pending','queued')", (identity,)).fetchone():
            raise HTTPException(409, 'Edit or resolve the existing application draft before revising this work; no second change will be prepared')
        if not intake and conn.execute("SELECT 1 FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE t.work_item_id=%s AND a.status='uncertain'", (identity,)).fetchone():
            raise HTTPException(409, 'Reconcile the previous execution before revising this work')
        previous = conn.execute("SELECT * FROM tasks WHERE work_item_id=%s AND parent_id IS NULL AND message_intent=ANY(%s) AND id<>%s AND status<>ALL(%s) ORDER BY created_at DESC LIMIT 1", (identity, list(WORK_ROLES), task['id'], list(service.TERMINAL))).fetchone()
        if intake and not previous:
            # Continue settled work with its responsible, available participant. Keep the
            # original owner message as provenance rather than creating another instruction.
            responsible = conn.execute("SELECT b.id FROM bots b JOIN participants p ON p.bot_id=b.id WHERE b.id=%s AND p.conversation_id=%s AND b.status='active' AND b.desired_state='running'", (item['bot_id'], task['conversation_id'])).fetchone()
            if responsible and not task['grant_id']:
                grant = conn.execute("SELECT g.id FROM tasks t JOIN grants g ON g.id=t.grant_id WHERE t.work_item_id=%s AND g.manager_id=%s AND g.revoked_at IS NULL AND g.expires_at>now() ORDER BY t.created_at DESC LIMIT 1", (identity, responsible['id'])).fetchone()
                conn.execute('UPDATE tasks SET bot_id=%s,grant_id=%s WHERE id=%s', (responsible['id'], grant['id'] if grant else None, task['id']))
                task = service.locked_task(conn, task['id'])
            elif task['grant_id']:
                conn.execute('UPDATE work_items SET bot_id=%s WHERE id=%s', (task['bot_id'], identity))
        if previous and not owner:
            # Do not hold one root while waiting for another root held by a concurrent tool call.
            try:
                with conn.transaction():
                    conn.execute('SELECT id FROM tasks WHERE id=%s FOR UPDATE NOWAIT', (previous['id'],))
            except errors.LockNotAvailable:
                raise HTTPException(409, 'This assignment is updating; clarify or retry the direction shortly')
            previous = service.locked_task(conn, previous['id'])
            if previous['status'] not in service.TERMINAL:
                if not intake and (previous['bot_id'] != task['bot_id'] or task['grant_id'] != previous['grant_id']):
                    raise HTTPException(409, 'Use Redirect on the existing assignment; this reply cannot acquire its management authority')
                if not intake and (task['attachment_ids'] or task['library_artifact_ids'] or task['skill_refs']):
                    raise HTTPException(409, 'Wait for the assignment to settle before revising its files or skills')
                if not intake:
                    service.check_grant(conn, previous)
                    service.steer(conn, previous, task['input'])
                intent = 'steered'
        conn.execute('UPDATE work_items SET hidden_at=NULL,updated_at=now() WHERE id=%s', (identity,))
        conn.execute('UPDATE tasks SET work_item_id=%s,message_intent=%s WHERE id=%s', (identity, intent, task['id']))
        from . import registry
        version = registry.require_bot(conn, task['bot_id'])['readiness'].get('inventory', {}).get('outcomes_version', 0)
        if version and not owner:
            conn.execute('UPDATE work_items SET outcomes_version=%s WHERE id=%s', (version, identity))
        if intent == 'update' and not owner:
            # Revisions receive latest published outputs already visible in this conversation,
            # not another bot's private files, original input grants or connection credentials.
            from .library import visible_in_conversation
            outputs = conn.execute('SELECT DISTINCT ON (a.family_id) a.id FROM artifacts a JOIN tasks t ON t.id=a.task_id WHERE t.work_item_id=%s ORDER BY a.family_id,a.version DESC', (identity,)).fetchall()
            for output in outputs:
                if visible_in_conversation(conn, output['id'], task['conversation_id']):
                    conn.execute('INSERT INTO artifact_access(artifact_id,task_id) VALUES(%s,%s) ON CONFLICT DO NOTHING', (output['id'], task['id']))
    else:
        # Owner correction changes display associations only; it never stops or replays execution.
        if owner:
            conn.execute('DELETE FROM work_item_messages WHERE message_id IN (SELECT id FROM messages WHERE task_id=%s)', (task['id'],))
        conn.execute('UPDATE tasks SET work_item_id=%s,message_intent=%s WHERE id=%s', (identity, intent, task['id']))
    if identity:
        link_messages(conn, task, identity, intent)
        if not owner and intent == 'update':
            from .recurring_management import inherit
            inherit(conn, service.locked_task(conn, task['id']))
    if owner:
        conn.execute('UPDATE tasks SET work_item_id=%s,message_intent=%s WHERE root_id=%s', (identity, intent, task['id']))
        conn.execute('DELETE FROM work_item_messages WHERE message_id IN (SELECT m.id FROM messages m JOIN tasks t ON t.id=m.task_id WHERE t.root_id=%s)', (task['id'],))
        if identity:
            conn.execute('INSERT INTO work_item_messages(message_id,work_item_id,intent) SELECT m.id,%s,%s FROM messages m JOIN tasks t ON t.id=m.task_id WHERE t.root_id=%s AND m.role=\'user\'', (identity, intent, task['id']))
        if task['work_item_id'] and task['work_item_id'] != identity:
            conn.execute("UPDATE work_items SET hidden_at=now() WHERE id=%s AND NOT EXISTS(SELECT 1 FROM tasks WHERE work_item_id=%s AND message_intent=ANY(%s))", (task['work_item_id'], task['work_item_id'], list(WORK_ROLES)))
    db.event(conn, 'message.routed', task, data={'intent': intent, 'work_item_id': str(identity) if identity else None})
    result = {'intent': intent, 'work_item_id': str(identity) if identity else None}
    if intent in ('discussion', 'status', 'steered'):
        result['instruction'] = 'Reply to the owner and finish this response. Do not start or restart work, delegate, publish artifacts or change external data.'
    if intent == 'steered':
        result['instruction'] += ' The direction was delivered once to the existing assignment.'
    if item:
        projection = next(w for w in list_items(conn, task['conversation_id']) if w['id'] == item['id'])
        result['work_status'] = {'title': item['title'], 'status': projection['status'], 'assignments': [{'bot_id': t['bot_id'], 'status': t['status'], 'error': t['error']} for t in projection['executions']]}
        result['granted_outputs'] = conn.execute('SELECT a.id,a.name,a.content_type,a.version FROM artifacts a JOIN artifact_access g ON g.artifact_id=a.id WHERE g.task_id=%s ORDER BY a.created_at DESC', (task['id'],)).fetchall()
        if result['granted_outputs']:
            result['output_instruction'] = 'Read the granted published outputs with hermbot_read_artifact before revising. IDs grant these file versions only; they do not grant private state or app access.'
    return result


def require_work(task):
    if task['message_intent'] not in WORK_ROLES or not task['work_item_id']:
        raise HTTPException(409, 'This is a conversation response. Route an actual owner assignment before starting work; otherwise reply and finish.')


def routes(owner):
    router = APIRouter(dependencies=[Depends(owner)])

    @router.post('/api/v1/work-items/messages')
    def associate(body: WorkMessageRequest):
        with db.connection() as conn:
            task = service.locked_task(conn, body.task_id)
            return route(conn, task, body.model_dump(), owner=True)

    @router.post('/api/v1/work-items/{identity}/hide')
    def hide(identity: str):
        return visibility(identity, True)

    @router.post('/api/v1/work-items/{identity}/restore')
    def restore(identity: str):
        return visibility(identity, False)

    def visibility(identity, hidden):
        from uuid import UUID
        try:
            identity = UUID(identity)
        except ValueError:
            raise HTTPException(422, 'Invalid work item ID')
        with db.connection() as conn:
            item = conn.execute('UPDATE work_items SET hidden_at=CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s RETURNING id,conversation_id', (hidden, identity)).fetchone()
            if not item:
                raise HTTPException(404, 'Work item not found')
            db.event(conn, 'work.visibility', conversation_id=item['conversation_id'], data={'work_item_id': str(identity), 'hidden': hidden})
        return {'id': identity, 'hidden': hidden}

    return router
