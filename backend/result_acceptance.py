"""One acceptance boundary for current requirements and their contributions."""
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from . import db, outcomes, service


def assign(conn, parent, child_id, identities, *, responsibility='contribution', required=True, expected_output='', replaces_task_id=None):
    for identity in identities:
        outcome = outcomes.require(conn, parent, identity)
        if not outcome['owner_task_id']:
            conn.execute('UPDATE work_outcomes SET owner_task_id=%s WHERE id=%s', (parent['id'], identity))
            outcome['owner_task_id'] = parent['id']
        if responsibility == 'deliverable':
            if outcome['owner_task_id'] != parent['id']:
                raise HTTPException(403, 'Only the accountable assignment can transfer a deliverable')
            if conn.execute("SELECT 1 FROM result_contributions WHERE outcome_id=%s AND outcome_revision=%s AND status<>'accepted'", (identity, outcome['revision'])).fetchone():
                raise HTTPException(409, 'Resolve existing contributions before transferring this deliverable')
            conn.execute('UPDATE work_outcomes SET owner_task_id=%s WHERE id=%s', (child_id, identity))
        else:
            if not expected_output.strip():
                raise HTTPException(422, 'State the contribution expected from this assignment')
            contribution_required = required
            if replaces_task_id:
                previous = conn.execute('SELECT c.*,t.root_id,t.status AS execution_status FROM result_contributions c JOIN tasks t ON t.id=c.task_id WHERE c.task_id=%s AND c.outcome_id=%s AND c.outcome_revision=%s', (replaces_task_id, identity, outcome['revision'])).fetchone()
                if outcome['owner_task_id'] != parent['id'] or not previous or previous['execution_status'] not in ('failed','interrupted','cancelled') or previous['status'] == 'superseded':
                    raise HTTPException(409, 'The accountable owner may replace only a settled unsuccessful contribution to this current requirement')
                service.require_known_connector_outcomes(conn, previous['root_id'])
                if conn.execute('SELECT 1 FROM attempts WHERE task_id=%s AND status=ANY(%s)', (replaces_task_id, list(service.ACTIVE_ATTEMPTS))).fetchone():
                    raise HTTPException(409, 'Wait for the previous execution to settle before replacing it')
                contribution_required = previous['required']
                conn.execute("UPDATE result_contributions SET required=false,status='superseded',updated_at=now() WHERE task_id=%s AND outcome_id=%s AND outcome_revision=%s", (replaces_task_id, identity, outcome['revision']))
                db.event(conn, 'contribution.replaced', parent, data={'previous_task_id': str(replaces_task_id), 'replacement_task_id': str(child_id), 'outcome_id': str(identity)})
            conn.execute('INSERT INTO result_contributions(task_id,outcome_id,outcome_revision,required,expected_output) VALUES(%s,%s,%s,%s,%s)', (child_id, identity, outcome['revision'], contribution_required, expected_output))


def contribution(conn, task, outcome):
    return conn.execute('SELECT * FROM result_contributions WHERE task_id=%s AND outcome_id=%s AND outcome_revision=%s', (task['id'], outcome['id'], outcome['revision'])).fetchone()


def validate(conn, task, outcome, claim):
    if outcome['owner_task_id'] != task['id']:
        if not contribution(conn, task, outcome):
            raise HTTPException(403, 'This assignment has no contribution or completion responsibility for this result')
        return  # A contribution submission cannot fulfill the shared requirement.
    if claim['status'] != 'fulfilled':
        return
    from .recurring_management import scope, pending_messages
    frozen = scope(conn, task)
    if frozen and task['bot_id'] == frozen['manager_id'] and not task['parent_id']:
        live = conn.execute("""SELECT 1 FROM tasks t WHERE t.work_item_id=%s AND t.id<>%s
            AND (t.message_intent IN ('assignment','update') OR t.execution_kind='peer_message')
            AND (t.status<>ALL(%s) OR EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=t.id AND a.status=ANY(%s))) LIMIT 1""",
            (task['work_item_id'], task['id'], list(service.TERMINAL - {'uncertain'}), list(service.ACTIVE_ATTEMPTS) + ['reserved'])).fetchone()
        if live or pending_messages(conn, task['work_item_id']):
            raise HTTPException(409, 'Wait for or explicitly stop remaining occurrence work before accepting the final deliverable')
    required = conn.execute('SELECT c.*,t.status AS execution_status FROM result_contributions c JOIN tasks t ON t.id=c.task_id WHERE c.outcome_id=%s AND c.outcome_revision=%s AND c.required', (outcome['id'], outcome['revision'])).fetchall()
    if any(c['status'] not in ('submitted', 'accepted') or c['execution_status'] != 'completed' for c in required):
        raise HTTPException(409, 'Required contributions are unfinished; continue independent work or wait for their results')
    reviewed = set(map(str, claim.get('reviewed_task_ids', [])))
    known = {str(c['task_id']): c for c in conn.execute('SELECT * FROM result_contributions WHERE outcome_id=%s AND outcome_revision=%s', (outcome['id'], outcome['revision']))}
    if reviewed - known.keys():
        raise HTTPException(422, 'Review only current contributions to this requirement')
    if any(c['status'] != 'accepted' and str(c['task_id']) not in reviewed for c in required):
        raise HTTPException(409, 'Review each required contribution and include its assignment reference in reviewed_task_ids')
    for identity in reviewed:
        submission = known[identity]['submission']
        artifact_id = submission.get('artifact_id') or (submission.get('reference') if submission.get('kind') == 'artifact' else None)
        if artifact_id and not conn.execute("SELECT 1 FROM tool_receipts WHERE task_id=%s AND operation='read_artifact' AND response->>'artifact_id'=%s", (task['id'], str(artifact_id))).fetchone():
            raise HTTPException(409, 'Inspect each contributed artifact with hermbot_read_artifact before accepting it')
    if outcome['kind'] in ('report', 'artifact', 'execution_check') and not claim.get('review', '').strip():
        raise HTTPException(422, 'Record how the submitted result meets the current requirement; existence alone is insufficient')
    if outcome['kind'] == 'native_effect':
        raise HTTPException(409, 'An external change through native tools requires owner reconciliation with actual delivery evidence')
    if outcome['kind'] == 'execution_check' and not claim.get('artifact_id'):
        observed = conn.execute("SELECT 1 FROM events e JOIN attempts a ON a.task_id=e.task_id WHERE a.task_id=%s AND a.number=%s AND e.created_at>=a.created_at AND e.kind='tool.completed' AND e.data->>'tool' NOT LIKE 'hermbot_%%'", (task['id'], task['attempts'])).fetchone()
        if not observed:
            raise HTTPException(409, 'An execution check needs actual tool execution evidence or a published result artifact')
    if outcome['kind'] == 'report' and not claim.get('artifact_id') and not task.get('pending_result'):
        # The finish response is the report when no separately published file was requested.
        raise HTTPException(422, 'Submit the report content or a published report artifact')


def record(conn, outcome, task_id, kind, reference, strength, *, claim=None, attempt_id=None):
    """Called by outcomes.record for every fulfillment, including provider receipts."""
    task = conn.execute('SELECT * FROM tasks WHERE id=%s', (task_id,)).fetchone()
    claim = dict(claim or {})
    if kind == 'artifact' and not claim:
        outcomes.supporting_evidence(conn, outcome, task_id, kind, reference, strength)
        return False
    if outcome['owner_task_id'] != task_id:
        branch = contribution(conn, task, outcome)
        if branch:
            payload = {**claim, 'reference': str(reference), 'kind': kind, 'strength': strength, 'result': task.get('pending_result') or task.get('output') or ''}
            conn.execute("UPDATE result_contributions SET status='submitted',submission=%s,updated_at=now() WHERE task_id=%s AND outcome_id=%s AND outcome_revision=%s", (Jsonb(outcomes.serialized(payload)), task_id, outcome['id'], outcome['revision']))
            outcomes.supporting_evidence(conn, outcome, task_id, kind, reference, strength)
            db.event(conn, 'contribution.submitted', task, data={'outcome_id': str(outcome['id']), 'revision': outcome['revision']})
            return False
        raise HTTPException(403, 'Only the accountable assignment may accept the deliverable')
    if kind == 'connector_operation':
        operation = conn.execute('SELECT * FROM connector_operations WHERE id=%s', (reference,)).fetchone()
        if not operation or operation['outcome_id'] != outcome['id'] or operation['outcome_revision'] != outcome['revision'] or operation['status'] != 'succeeded':
            raise HTTPException(409, 'A matching current provider receipt is required')
        # Retain provider delivery even when acceptance of the shared requirement must wait.
        if not claim:
            outcomes.supporting_evidence(conn, outcome, task_id, kind, reference, strength)
            unreviewed = conn.execute("SELECT 1 FROM result_contributions WHERE outcome_id=%s AND outcome_revision=%s AND required AND status<>'accepted'", (outcome['id'], outcome['revision'])).fetchone()
            if unreviewed or not outcomes.dependencies_ready(conn, outcome):
                return False  # Preserve the successful receipt even when final acceptance must wait.
            claim = {'status': 'fulfilled', 'review': 'Matching provider receipt', 'operation_id': str(reference)}
    if kind != 'owner_reconciliation':
        validate(conn, task, outcome, {**claim, 'status': 'fulfilled'})
    artifact_id = claim.get('artifact_id')
    artifact = service.artifact_visible(conn, artifact_id, task) if artifact_id else None
    if artifact_id and (not artifact or artifact['conversation_id'] != task['conversation_id']):
        raise HTTPException(403, 'The submitted artifact is not available to this assignment')
    work = conn.execute('SELECT objective,revision FROM work_items WHERE id=%s', (outcome['work_item_id'],)).fetchone()
    evidence = {'kind': kind, 'reference': str(reference), 'strength': strength, 'review': claim.get('review', ''),
                'reviewed_task_ids': list(map(str, claim.get('reviewed_task_ids', []))),
                'artifact_id': str(artifact_id) if artifact_id else None,
                'artifact_sha256': artifact['sha256'] if artifact else None,
                'artifact_version': artifact['version'] if artifact else None,
                'requirement': {k: outcomes.serialized(outcome[k]) for k in ('key', 'title', 'kind', 'target', 'dependencies', 'revision')},
                'work_objective': work['objective'], 'work_revision': work['revision'],
                'review_actor': 'owner' if kind == 'owner_reconciliation' else task['bot_id'],
                'response': task.get('pending_result') or task.get('output') or ''}
    conn.execute('INSERT INTO result_acceptances(id,outcome_id,outcome_revision,task_id,attempt_id,evidence) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(outcome_id,outcome_revision) DO NOTHING',
                 (uuid4(), outcome['id'], outcome['revision'], task_id, attempt_id, Jsonb(evidence)))
    for identity in claim.get('reviewed_task_ids', []):
        conn.execute("UPDATE result_contributions SET status='accepted',reviewed_by=%s,updated_at=now() WHERE task_id=%s AND outcome_id=%s AND outcome_revision=%s AND status='submitted'", (task_id, identity, outcome['id'], outcome['revision']))
    outcomes.supporting_evidence(conn, outcome, task_id, kind, reference, strength)
    conn.execute("UPDATE work_outcomes SET status='fulfilled',reason='',evidence_strength=%s,updated_at=now() WHERE id=%s AND revision=%s", ('reviewed_artifact' if artifact else strength, outcome['id'], outcome['revision']))
    db.event(conn, 'result.accepted', task, data={'outcome_id': str(outcome['id']), 'revision': outcome['revision'], 'evidence_strength': strength})
    return True


def context(conn, outcome):
    acceptance = conn.execute('SELECT task_id,attempt_id,evidence,created_at FROM result_acceptances WHERE outcome_id=%s AND outcome_revision=%s', (outcome['id'], outcome['revision'])).fetchone()
    if acceptance:
        acceptance['evidence'] = {k: v for k, v in acceptance['evidence'].items() if k not in ('requirement', 'response', 'work_objective')}
    return {'owner_task_id': outcome['owner_task_id'], 'contributions': conn.execute('SELECT c.task_id,t.bot_id,c.required,c.expected_output,c.status,c.submission,c.reviewed_by FROM result_contributions c JOIN tasks t ON t.id=c.task_id WHERE c.outcome_id=%s AND c.outcome_revision=%s ORDER BY t.created_at', (outcome['id'], outcome['revision'])).fetchall(), 'acceptance': acceptance}
