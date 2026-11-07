"""Persistent requested results; execution completion alone is not work completion."""
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb

from . import db, service

VERSION = 1


def rows(conn, item_id):
    return conn.execute('SELECT * FROM work_outcomes WHERE work_item_id=%s ORDER BY position,created_at,id', (item_id,)).fetchall()


def assigned(conn, task):
    return conn.execute('SELECT o.*,b.outcome_revision AS assigned_revision FROM work_outcomes o JOIN task_outcomes b ON b.outcome_id=o.id WHERE b.task_id=%s ORDER BY o.created_at,o.id', (task['id'],)).fetchall()


def registration_required(conn, task):
    if not task['work_item_id'] or task['parent_id']:
        return False
    item = conn.execute('SELECT outcomes_version FROM work_items WHERE id=%s', (task['work_item_id'],)).fetchone()
    return bool(item and item['outcomes_version'] and not rows(conn, task['work_item_id']))


def require_handoff(conn, task, identities):
    if not task['work_item_id']:
        return
    item = conn.execute('SELECT outcomes_version FROM work_items WHERE id=%s', (task['work_item_id'],)).fetchone()
    if item and item['outcomes_version'] and not identities:
        raise HTTPException(422, 'Register the requested results with hermbot_prepare.outcomes, then pass the relevant outcome_ids to this handoff. Use IDs from authenticated work context; never ask the owner for internal IDs. No assignment was created.')


def require(conn, task, identity, revision=None):
    outcome = conn.execute('SELECT * FROM work_outcomes WHERE id=%s FOR UPDATE', (identity,)).fetchone()
    if not outcome or outcome['work_item_id'] != task['work_item_id']:
        raise HTTPException(403, 'Outcome does not belong to this work')
    binding = conn.execute('SELECT outcome_revision FROM task_outcomes WHERE task_id=%s AND outcome_id=%s', (task['id'], identity)).fetchone()
    if not binding or binding['outcome_revision'] != outcome['revision'] or (revision is not None and revision != outcome['revision']):
        raise HTTPException(409, 'Outcome changed or was not assigned to this task; inspect current work and prepare again')
    return outcome


def register(conn, task, specs, *, owner_revision=False):
    if not task['work_item_id']:
        return []
    conn.execute('SELECT id FROM work_items WHERE id=%s FOR UPDATE', (task['work_item_id'],))
    if task['parent_id'] and specs:
        raise HTTPException(409, 'Helpers use their assigned outcomes; only the owner-facing task records requirements')
    if task.get('orchestration_version') == 2 and specs and not owner_revision:
        existing = {o['key']: o for o in rows(conn, task['work_item_id'])}
        if any(s['key'] not in existing for s in specs):
            raise HTTPException(409, 'Owner requirements are recorded by message intake. Consult the current work context instead of adding requirements.')
        return context(conn, task)
    if len({s['key'] for s in specs}) != len(specs):
        raise HTTPException(422, 'Outcome keys must be unique')
    existing = {o['key']: o for o in rows(conn, task['work_item_id'])}
    keys = set(existing) | {s['key'] for s in specs}
    graph = {key: o['dependencies'] for key, o in existing.items()}
    graph.update({s['key']: s.get('depends_on', []) for s in specs})
    visited = set()
    def visit(key, path):
        if key in path:
            raise HTTPException(422, 'Outcome dependencies contain a cycle')
        if key in visited:
            return
        for dependency in graph.get(key, []):
            if dependency not in keys:
                raise HTTPException(422, 'Outcome dependency is unknown')
            visit(dependency, path | {key})
        visited.add(key)
    for key in graph:
        visit(key, set())
    source = conn.execute("SELECT id FROM messages WHERE id=%s AND role='user' AND author='owner'", (task.get('owner_message_id'),)).fetchone()
    if not source:
        source = conn.execute("SELECT id FROM messages WHERE task_id=%s AND role='user' ORDER BY id DESC LIMIT 1", (task['id'],)).fetchone()
    for spec in specs:
        old = existing.get(spec['key'])
        target = {k: str(v) for k, v in spec.get('target', {}).items() if v}
        if old and not owner_revision:
            # Repreparation may omit target fields resolved by a helper or an approved draft.
            target = {**old['target'], **target}
        values = (spec['title'].strip(), spec['kind'], spec.get('required', True), target, spec.get('depends_on', []))
        if old:
            before = (old['title'], old['kind'], old['required'], old['target'], old['dependencies'])
            if before != values:
                if spec.get('expected_revision') != old['revision']:
                    raise HTTPException(409, 'Supply the current outcome revision before changing its requirements')
                if not owner_revision and (task['message_intent'] != 'update' or not source or task['id'] == old['source_task_id']):
                    raise HTTPException(409, 'Changing a recorded requirement needs a new owner update')
                if old['required'] and not values[2] and not owner_revision:
                    raise HTTPException(409, 'A required result cannot be silently made optional')
                if old['status'] == 'fulfilled' and old['kind'] in ('connector_effect', 'native_effect'):
                    raise HTTPException(409, 'A completed external action is immutable; record a newly requested action separately')
                if owner_revision:
                    from .work_controller import invalidate_operations
                    retained_scopes = conn.execute('SELECT * FROM work_authorizations WHERE outcome_id=%s AND outcome_revision=%s AND revoked_at IS NULL', (old['id'], old['revision'])).fetchall() if old['kind'] == values[1] == 'connector_effect' and old['target'] == target else []
                    invalidate_operations(conn, old['id'])
                    conn.execute('UPDATE work_authorizations SET revoked_at=now() WHERE outcome_id=%s AND revoked_at IS NULL', (old['id'],))
                if not owner_revision and conn.execute("SELECT 1 FROM connector_operations WHERE outcome_id=%s AND status IN ('pending','queued','dispatching','uncertain','unresolved')", (old['id'],)).fetchone():
                    raise HTTPException(409, 'Resolve or edit the existing external draft before changing this outcome')
                conn.execute("UPDATE work_outcomes SET title=%s,kind=%s,required=%s,target=%s,dependencies=%s,revision=revision+1,source_task_id=%s,source_message_id=%s,status='pending',reason='',evidence_strength='',updated_at=now() WHERE id=%s",
                             (*values[:3], Jsonb(target), Jsonb(values[4]), task['id'], source['id'], old['id']))
                for scope in retained_scopes if owner_revision else []:
                    conn.execute('INSERT INTO work_authorizations(id,work_item_id,outcome_id,outcome_revision,source_message_id,action,resource,resource_label,instruction_quote) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                 (uuid4(), scope['work_item_id'], old['id'], old['revision']+1, scope['source_message_id'], scope['action'], scope['resource'], scope['resource_label'], scope['instruction_quote']))
            elif spec.get('expected_revision') not in (None, old['revision']):
                raise HTTPException(409, 'This outcome revision is stale')
        else:
            if not source:
                raise HTTPException(409, 'Requirements must originate from an owner message')
            position = conn.execute('SELECT COALESCE(max(position),0)+1 AS n FROM work_outcomes WHERE work_item_id=%s', (task['work_item_id'],)).fetchone()['n']
            conn.execute('INSERT INTO work_outcomes(id,work_item_id,key,title,kind,required,target,dependencies,source_task_id,source_message_id,position) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                         (uuid4(), task['work_item_id'], spec['key'], *values[:3], Jsonb(target), Jsonb(values[4]), task['id'], source['id'], position))
    if specs:
        conn.execute('UPDATE work_items SET outcomes_version=%s,updated_at=now() WHERE id=%s', (VERSION, task['work_item_id']))
    if not task['parent_id']:
        for outcome in rows(conn, task['work_item_id']):
            conn.execute('INSERT INTO task_outcomes VALUES(%s,%s,%s) ON CONFLICT(task_id,outcome_id) DO UPDATE SET outcome_revision=excluded.outcome_revision', (task['id'], outcome['id'], outcome['revision']))
            if task.get('coordination_version') == 1 and not outcome['owner_task_id']:
                accountable = conn.execute("SELECT id FROM tasks WHERE work_item_id=%s AND execution_kind='work' AND parent_id IS NULL AND message_intent IN ('assignment','update') AND status<>ALL(%s) ORDER BY created_at LIMIT 1", (task['work_item_id'], list(service.TERMINAL))).fetchone()
                conn.execute('UPDATE work_outcomes SET owner_task_id=%s WHERE id=%s', (accountable['id'] if accountable else task['id'], outcome['id']))
    return context(conn, task)


def resolve(conn, task, identity=None, key=None, *, kind=None, action=None, optional=False):
    if identity:
        return identity
    candidates = [o for o in assigned(conn, task) if o['status'] not in ('fulfilled', 'cancelled')
                  and (not key or o['key'] == key) and (not kind or o['kind'] in kind)
                  and (not action or not o['target'].get('action') or o['target']['action'] == action)]
    if len(candidates) == 1:
        return candidates[0]['id']
    if key or (task.get('orchestration_version') == 2 and not optional):
        raise HTTPException(422, 'Choose a matching requirement key from current work context; the operation is ambiguous or no longer required')
    return None


def share(conn, parent, child_id, identities):
    for identity in identities:
        outcome = require(conn, parent, identity)
        conn.execute('INSERT INTO task_outcomes VALUES(%s,%s,%s)', (child_id, identity, outcome['revision']))


def context(conn, task):
    values = assigned(conn, task)
    if not task['parent_id'] and task['work_item_id']:
        values = rows(conn, task['work_item_id'])
    result = []
    for value in values:
        target = dict(value['target'])
        identity = target.get('connection_id')
        if identity and not conn.execute("SELECT 1 FROM connections WHERE id=%s AND bot_ids ? %s", (identity, task['bot_id'])).fetchone():
            target.pop('connection_id', None)
        result.append({k: value[k] for k in ('id', 'key', 'title', 'kind', 'required', 'revision', 'status', 'reason', 'evidence_strength', 'dependencies')} | {'target': target})
        if value['owner_task_id']:
            from .result_acceptance import context as acceptance_context
            result[-1].update(acceptance_context(conn, value))
            result[-1]['responsibility'] = 'deliverable' if value['owner_task_id'] == task['id'] else 'contribution'
    return result


def dependencies_ready(conn, outcome):
    known = {o['key']: o['status'] for o in rows(conn, outcome['work_item_id'])}
    return all(known.get(key) == 'fulfilled' for key in outcome['dependencies'])


def edit_target(conn, task, old_payload, new_payload):
    """Only the owner's exact draft editor can change a pending action's target."""
    identity = old_payload.get('outcome_id')
    if not identity:
        return new_payload
    if new_payload.get('outcome_id') not in (None, identity):
        raise HTTPException(409, 'An edited action must retain its original required result')
    from . import connections
    from .connector_catalog import validate
    from uuid import UUID
    try:
        connection_id = UUID(str(new_payload['connection_id']))
        action, arguments = new_payload['action'], new_payload['arguments']
    except (KeyError, ValueError, TypeError):
        raise HTTPException(422, 'An edited connector draft requires a connection, action and arguments')
    connection = connections.require(conn, connection_id)
    connections.authorize(connection, task['bot_id'], action)
    _, _, resource = validate(connection, action, arguments)
    connections.check_resource(connection, action, resource)
    outcome = require(conn, task, identity, old_payload.get('outcome_revision'))
    if outcome['status'] in ('fulfilled', 'uncertain', 'cancelled'):
        raise HTTPException(409, 'This result cannot be edited as a pending draft')
    target = {'connection_id': str(connection['id']), 'action': new_payload['action'], 'resource': resource}
    if any(outcome['target'].get(k) != v for k,v in target.items()):
        conn.execute("UPDATE work_outcomes SET target=%s,revision=revision+1,status='pending',reason='',updated_at=now() WHERE id=%s", (Jsonb(target), outcome['id']))
        conn.execute('UPDATE task_outcomes SET outcome_revision=outcome_revision+1 WHERE task_id=%s AND outcome_id=%s', (task['id'], outcome['id']))
        db.event(conn, 'work.target_edited', task, data={'outcome_id': str(outcome['id'])})
        outcome['revision'] += 1
    if task.get('orchestration_version') == 2 and any(old_payload.get(key) != new_payload.get(key) for key in ('connection_id', 'action', 'arguments')):
        from .work_controller import record_owner_action
        record_owner_action(conn, task, 'draft_edited', 'The owner edited the external action draft. Inspect its current destination and content before continuing.')
    return {**new_payload, 'outcome_id': identity, 'outcome_revision': outcome['revision']}


def bind_operation(conn, task, identity, connection, action, args):
    if task.get('orchestration_version') != 2 and conn.execute("SELECT 1 FROM work_outcomes WHERE work_item_id=%s AND status='uncertain'", (task['work_item_id'],)).fetchone():
        raise HTTPException(409, 'Reconcile the uncertain external result before proposing another delivery')
    if task.get('orchestration_version') != 2 and conn.execute("SELECT 1 FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.work_item_id=%s AND o.status IN ('dispatching','uncertain','unresolved')", (task['work_item_id'],)).fetchone():
        raise HTTPException(409, 'Wait for or reconcile the existing external operation before proposing another change')
    item = conn.execute('SELECT outcomes_version FROM work_items WHERE id=%s', (task['work_item_id'],)).fetchone()
    if not identity:
        if item and item['outcomes_version']:
            raise HTTPException(422, 'Associate this external change with its connector_effect outcome_id from authenticated work context. If no result was assigned, the parent must repair preparation and the handoff; never ask the owner for internal IDs.')
        return None
    outcome = require(conn, task, identity)
    if outcome['kind'] != 'connector_effect' or outcome['status'] in ('fulfilled', 'cancelled', 'uncertain'):
        raise HTTPException(409, 'Choose an unfinished connector effect; completed effects are never replayed')
    if conn.execute("SELECT 1 FROM connector_operations WHERE outcome_id=%s AND status IN ('pending','queued','dispatching','uncertain','unresolved')", (identity,)).fetchone():
        raise HTTPException(409, 'This result already has an external operation; wait for its receipt or edit its draft')
    if not dependencies_ready(conn, outcome):
        raise HTTPException(409, 'Required dependency results are not ready for this external change')
    from .connector_catalog import validate
    _, _, resource = validate(connection, action, args)
    target = outcome['target']
    actual = {'connection_id': str(connection['id']), 'action': action, 'resource': resource}
    if task.get('orchestration_version') == 2:
        if target.get('connection_name') and target['connection_name'].casefold() != connection['name'].casefold():
            raise HTTPException(409, 'This action uses a different account from the requested connection name')
        recorded_connection = target.get('connection_id')
        if recorded_connection and not conn.execute('SELECT 1 FROM connections WHERE id=%s', (recorded_connection,)).fetchone():
            target = {**target, 'connection_id': str(connection['id'])}
            conn.execute('UPDATE work_outcomes SET target=%s WHERE id=%s', (Jsonb(target), outcome['id']))
    if any(target.get(k) and target[k] != v for k, v in actual.items()):
        raise HTTPException(409, 'This action or recipient differs from the recorded outcome; resolve the owner requirement first')
    if any(not target.get(k) for k, v in actual.items() if v):
        target = {**target, **actual}
        conn.execute('UPDATE work_outcomes SET target=%s,updated_at=now() WHERE id=%s', (Jsonb(target), identity))
    return outcome['revision']


def validate_claims(conn, task, claims):
    if len({str(c['outcome_id']) for c in claims}) != len(claims):
        raise HTTPException(422, 'Report each outcome once')
    prospective = {str(c['outcome_id']) for c in claims if c['status'] == 'fulfilled'}
    values = {o['key']: o for o in rows(conn, task['work_item_id'])} if task['work_item_id'] else {}
    for claim in claims:
        outcome = require(conn, task, claim['outcome_id'], claim['expected_revision'])
        if outcome['status'] == 'cancelled':
            raise HTTPException(409, 'The owner removed this requirement; do not execute it')
        if outcome['status'] == 'uncertain' and outcome['kind'] == 'native_effect':
            raise HTTPException(409, 'Native external uncertainty needs owner reconciliation, not another model claim')
        if claim['status'] != 'fulfilled':
            if not claim.get('reason', '').strip():
                raise HTTPException(422, 'Explain the unfinished result')
            continue
        if outcome['owner_task_id']:
            from .result_acceptance import validate
            validate(conn, task, outcome, claim)
            if outcome['owner_task_id'] != task['id']:
                if claim.get('artifact_id') and not service.artifact_visible(conn, claim['artifact_id'], task):
                    raise HTTPException(403, 'Publish the contribution artifact before submitting it')
                continue
        if any(values[key]['status'] != 'fulfilled' and str(values[key]['id']) not in prospective for key in outcome['dependencies']):
            raise HTTPException(409, 'Outcome dependencies are unfinished')
        if outcome['kind'] == 'connector_effect':
            operation = conn.execute('SELECT * FROM connector_operations WHERE id=%s', (claim.get('operation_id'),)).fetchone()
            shared_receipt = bool(operation and outcome['owner_task_id'] == task['id'] and conn.execute('SELECT 1 FROM result_contributions WHERE task_id=%s AND outcome_id=%s AND outcome_revision=%s', (operation['task_id'], outcome['id'], outcome['revision'])).fetchone())
            if not operation or operation['outcome_id'] != outcome['id'] or operation['outcome_revision'] != outcome['revision'] or operation['status'] != 'succeeded' or (not shared_receipt and (operation['task_id'] != task['id'] or operation['bot_id'] != task['bot_id'])):
                raise HTTPException(409, 'A matching successful connector operation is required as delivery evidence')
        elif outcome['kind'] == 'artifact':
            artifact = service.artifact_visible(conn, claim['artifact_id'], task) if claim.get('artifact_id') else None
            if not artifact or artifact.get('conversation_id') != task['conversation_id']:
                raise HTTPException(403, 'Publish or explicitly share the artifact before claiming it was delivered')


def supporting_evidence(conn, outcome, task_id, kind, reference, strength):
    conn.execute('INSERT INTO outcome_evidence(id,outcome_id,outcome_revision,task_id,kind,reference,strength) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                 (uuid4(), outcome['id'], outcome['revision'], task_id, kind, str(reference), strength))


def record(conn, outcome, task_id, kind, reference, strength, *, claim=None, attempt_id=None):
    if outcome.get('owner_task_id'):
        from .result_acceptance import record as accept
        return accept(conn, outcome, task_id, kind, reference, strength, claim=claim, attempt_id=attempt_id)
    supporting_evidence(conn, outcome, task_id, kind, reference, strength)
    conn.execute("UPDATE work_outcomes SET status='fulfilled',reason='',evidence_strength=%s,updated_at=now() WHERE id=%s AND revision=%s", (strength, outcome['id'], outcome['revision']))
    return True


def effect_settled(conn, operation, status):
    if not operation.get('outcome_id'):
        return
    outcome = conn.execute('SELECT * FROM work_outcomes WHERE id=%s FOR UPDATE', (operation['outcome_id'],)).fetchone()
    if outcome['revision'] != operation['outcome_revision']:
        return
    if status == 'succeeded':
        record(conn, outcome, operation['task_id'], 'connector_operation', operation['id'], 'provider_receipt')
    elif outcome['status'] != 'fulfilled':
        state = 'uncertain' if status in ('uncertain', 'unresolved') else 'blocked'
        conn.execute('UPDATE work_outcomes SET status=%s,reason=%s,updated_at=now() WHERE id=%s', (state, 'External action: ' + status, outcome['id']))


def settle(conn, task, runtime_status, attempt_id):
    if runtime_status != 'completed':
        if task.get('orchestration_version') == 2 and task['parent_id']:
            db.event(conn, 'work.assignment_unfinished', task, data={'attempt_id': str(attempt_id), 'status': runtime_status})
            return  # A helper failure is evidence for replanning, not failure of every shared result.
        for outcome in assigned(conn, task):
            if outcome['status'] not in ('fulfilled', 'uncertain', 'cancelled') and outcome['assigned_revision'] == outcome['revision']:
                conn.execute("UPDATE work_outcomes SET status='failed',reason=%s,updated_at=now() WHERE id=%s", ('Execution ' + runtime_status + ': ' + (task.get('error') or 'Required result was not produced'), outcome['id']))
        return
    claims = task.get('pending_outcomes', [])
    # Only apply claims after the actual run settles. Stale claims never rewrite new requirements.
    try:
        with conn.transaction():
            validate_claims(conn, task, claims)
            for claim in claims:
                outcome = require(conn, task, claim['outcome_id'], claim['expected_revision'])
                if outcome['status'] == 'fulfilled':
                    continue
                if claim['status'] == 'fulfilled':
                    kind = 'connector_operation' if outcome['kind'] == 'connector_effect' else 'artifact' if outcome['kind'] == 'artifact' else 'execution'
                    reference = claim.get('operation_id') or claim.get('artifact_id') or attempt_id
                    strength = 'provider_receipt' if kind == 'connector_operation' else 'artifact_exists' if kind == 'artifact' else 'model_reported'
                    record(conn, outcome, task['id'], kind, reference, strength, claim=claim, attempt_id=attempt_id)
                else:
                    if outcome.get('owner_task_id') and outcome['owner_task_id'] != task['id']:
                        conn.execute('UPDATE result_contributions SET status=%s,submission=%s,updated_at=now() WHERE task_id=%s AND outcome_id=%s AND outcome_revision=%s', (claim['status'], Jsonb(serialized(claim)), task['id'], outcome['id'], outcome['revision']))
                    else:
                        conn.execute('UPDATE work_outcomes SET status=%s,reason=%s,updated_at=now() WHERE id=%s', (claim['status'], claim['reason'], outcome['id']))
    except HTTPException:
        db.event(conn, 'work.evidence_rejected', task, data={'outcome_ids': [str(c['outcome_id']) for c in claims]})


def projection(conn, item, executions, fallback):
    values = rows(conn, item['id'])
    for value in values:
        if value['owner_task_id']:
            from .result_acceptance import context as acceptance_context
            value.update(acceptance_context(conn, value))
    item['outcomes'] = values
    item['progress'] = {'fulfilled': sum(o['status'] == 'fulfilled' for o in values if o['required'] and o['status'] != 'cancelled'), 'required': sum(o['required'] for o in values if o['status'] != 'cancelled')}
    if fallback == 'uncertain' or any(o['status'] == 'uncertain' for o in values):
        return 'uncertain'
    if fallback in ('pausing_budget', 'paused_budget') and not all(o['status'] == 'fulfilled' for o in values if o['required']):
        return fallback
    if not item['outcomes_version']:
        return fallback
    if conn.execute("SELECT 1 FROM input_requests WHERE work_item_id=%s AND status='pending' AND expires_at>now()", (item['id'],)).fetchone():
        return 'needs_answer'
    unfinished = conn.execute("SELECT o.status FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.work_item_id=%s AND o.status IN ('pending','queued','dispatching','uncertain','unresolved')", (item['id'],)).fetchall()
    if any(o['status'] in ('uncertain', 'unresolved') for o in unfinished):
        return 'uncertain'
    if any(o['status'] == 'pending' for o in unfinished) or (fallback == 'waiting_approval' and not unfinished):
        return 'needs_approval'
    if unfinished:
        return 'working'
    required = [o for o in values if o['required'] and o['status'] != 'cancelled']
    if required and all(o['status'] == 'fulfilled' for o in required):
        return 'completed'
    if values and all(o['status'] == 'cancelled' for o in values):
        return 'cancelled'
    if fallback in ('cancelled', 'cancelling', 'interrupted'):
        return fallback
    if any(o['status'] in ('blocked', 'failed') for o in required):
        return 'needs_attention'
    if any(t['status'] not in service.TERMINAL for t in executions):
        return 'working' if values else 'planning'
    return 'needs_attention'


def serialized(value):
    return json.loads(json.dumps(value, default=str))


def routes(owner):
    from uuid import UUID
    from .models import OutcomeReconcileRequest, OutcomeRevisionRequest
    router = APIRouter(dependencies=[Depends(owner)])

    def lock(conn, identity, revision):
        value = conn.execute('SELECT * FROM work_outcomes WHERE id=%s', (identity,)).fetchone()
        if not value:
            raise HTTPException(404, 'Outcome not found')
        task = service.locked_task(conn, value['source_task_id'])
        conn.execute('SELECT id FROM work_items WHERE id=%s FOR UPDATE', (value['work_item_id'],))
        value = conn.execute('SELECT * FROM work_outcomes WHERE id=%s FOR UPDATE', (identity,)).fetchone()
        if value['revision'] != revision:
            raise HTTPException(409, 'This result changed; reload before updating it')
        return task, value

    @router.post('/api/v1/outcomes/{identity}/drop')
    def drop(identity: UUID, body: OutcomeRevisionRequest):
        with db.connection() as conn:
            task, value = lock(conn, identity, body.expected_revision)
            if value['status'] == 'fulfilled':
                raise HTTPException(409, 'A completed result remains part of the history')
            if task.get('orchestration_version') == 2:
                from . import work_controller
                work_controller.invalidate_operations(conn, identity)
                conn.execute("UPDATE work_outcomes SET status='cancelled',reason='Removed by owner',revision=revision+1,updated_at=now() WHERE id=%s", (identity,))
                conn.execute('UPDATE work_authorizations SET revoked_at=now() WHERE outcome_id=%s AND revoked_at IS NULL', (identity,))
                conn.execute("UPDATE input_requests SET status='cancelled' WHERE outcome_id=%s AND status='pending'", (identity,))
                work_controller.record_owner_action(conn, task, 'requirement_removed', 'The owner removed the requirement: ' + value['title'])
                return {'status': 'cancelled'}
            if value['status'] == 'uncertain' or conn.execute("SELECT 1 FROM connector_operations WHERE outcome_id=%s AND status IN ('pending','queued','dispatching','uncertain','unresolved')", (identity,)).fetchone():
                raise HTTPException(409, 'Resolve the existing external draft or outcome before removing this requirement')
            conn.execute("UPDATE work_outcomes SET status='cancelled',reason='Removed by owner',revision=revision+1,updated_at=now() WHERE id=%s", (identity,))
            conn.execute("UPDATE input_requests SET status='cancelled' WHERE outcome_id=%s AND status='pending'", (identity,))
            db.event(conn, 'work.requirement_removed', task, data={'outcome_id': str(identity)})
            return {'status': 'cancelled'}

    @router.post('/api/v1/outcomes/{identity}/reconcile')
    def reconcile(identity: UUID, body: OutcomeReconcileRequest):
        with db.connection() as conn:
            task, value = lock(conn, identity, body.expected_revision)
            if value['kind'] != 'native_effect' or value['status'] != 'uncertain':
                raise HTTPException(409, 'Use the connector operation review for connector effects; only uncertain native effects use this form')
            if body.result == 'fulfilled':
                record(conn, value, value.get('owner_task_id') or task['id'], 'owner_reconciliation', service.digest(body.model_dump()), 'owner_attested', claim={'review': body.evidence})
            else:
                conn.execute('UPDATE work_outcomes SET status=%s,reason=%s,updated_at=now() WHERE id=%s', (body.result, body.evidence, identity))
            db.event(conn, 'work.native_reconciled', task, data={'outcome_id': str(identity), 'result': body.result, 'evidence': body.evidence})
            return {'status': body.result}

    return router
