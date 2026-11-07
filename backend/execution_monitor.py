"""Durable, asynchronous execution assessments above the harness API."""
import asyncio
import json
import logging
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import db, decisions, execution_policy, registry, service
from .secrets import decrypt

SILENCE_SECONDS = 180
COOLDOWN_SECONDS = 120
logger = logging.getLogger('hermbot.execution_monitor')


def evidence(conn, task, attempt):
    events = conn.execute("SELECT id,kind,data,created_at FROM events WHERE task_id=%s AND kind IN ('tool.started','tool.completed','artifact.published','work.prepared','work.outcome_recorded','message.delta') AND created_at>=%s ORDER BY id DESC LIMIT 30", (task['id'], attempt['created_at'])).fetchall()
    latest = events[0]['created_at'] if events else attempt['started_at'] or attempt['created_at']
    calls = []
    for event in events:
        if event['kind'] != 'tool.completed':
            continue
        data = event['data']
        failed = bool(data.get('error'))
        try:
            result = json.loads(data.get('preview') or '{}')
            failed |= isinstance(result, dict) and (result.get('success') is False or bool(result.get('error')) or result.get('exit_code', 0) not in (0, None))
        except (ValueError, TypeError):
            pass
        calls.append({'tool': str(data.get('tool', 'unknown'))[:80], 'failed': failed})
    repeated = len(calls) >= 3 and all(c['failed'] and c['tool'] == calls[0]['tool'] for c in calls[:3])
    silent = (service.now() - latest).total_seconds() >= SILENCE_SECONDS
    outcomes = conn.execute('SELECT key,kind,status,revision FROM work_outcomes WHERE work_item_id=%s ORDER BY key', (task['work_item_id'],)).fetchall() if task['work_item_id'] else []
    last_tool = next((e for e in events if e['kind'] in ('tool.started', 'tool.completed')), None)
    # Raw reasoning and tool output are deliberately excluded. Provider keys and
    # private teammate memory never enter an assessment snapshot.
    snapshot = {'task_id': str(task['id']), 'attempt_id': str(attempt['id']), 'objective': task['input'][:2000],
                'task_status': task['status'], 'last_event_id': events[0]['id'] if events else None,
                'last_observed_tool': {'name': str(last_tool['data'].get('tool', 'unknown'))[:80], 'state': 'started' if last_tool['kind'] == 'tool.started' else 'completed'} if last_tool else None,
                'recent_tools': calls[:10], 'outcomes': outcomes, 'budget': execution_policy.budget(conn, task),
                'trigger': 'repeated_tool_failures' if repeated else 'unexplained_inactivity' if silent else None}
    snapshot['owner_direction'] = str((conn.execute("SELECT id FROM controls WHERE task_id=%s AND source='owner' ORDER BY created_at DESC LIMIT 1", (task['id'],)).fetchone() or {}).get('id', ''))
    # Timers and budget counters are inputs, not new evidence justifying a new request
    # every poll. Cooldown + stable event identity prevent repeated assessments.
    fingerprint = service.digest({k: v for k, v in snapshot.items() if k != 'budget'})
    return snapshot, fingerprint


def queue(conn, task, attempt):
    config = registry.workspace(conn)
    if config['decision_mode'] == 'disabled' or task['status'] != 'running' or task['message_intent'] not in ('assignment', 'update'):
        return
    if conn.execute("SELECT 1 FROM approvals WHERE task_id=%s AND status='pending'", (task['id'],)).fetchone():
        return
    if conn.execute("SELECT 1 FROM execution_assessments WHERE attempt_id=%s AND (status IN ('queued','evaluating') OR created_at>now()-interval '120 seconds')", (attempt['id'],)).fetchone():
        return
    snapshot, fingerprint = evidence(conn, task, attempt)
    if not snapshot['trigger']:
        return
    provider = conn.execute('SELECT revision FROM providers WHERE id=%s', (config['decision_provider_id'],)).fetchone()
    if not provider:
        return
    conn.execute('INSERT INTO execution_assessments(id,task_id,attempt_id,evidence_hash,evidence,policy_revision,provider_id,provider_revision,model,mode) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                 (uuid4(), task['id'], attempt['id'], fingerprint, Jsonb(snapshot), config['execution_policy_revision'], config['decision_provider_id'], provider['revision'], config['decision_model'], config['decision_mode']))


def claim():
    with db.connection() as conn:
        conn.execute("UPDATE execution_assessments SET status='unavailable',error='Assessment worker disconnected; execution remains under normal controls',updated_at=now() WHERE status='evaluating' AND lease_until<now()")
        return conn.execute("UPDATE execution_assessments SET status='evaluating',lease_until=now()+interval '30 seconds' WHERE id IN (SELECT id FROM execution_assessments WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *").fetchone()


def current(conn, assessment, task, attempt, config, provider):
    if not attempt:
        return False
    _, fingerprint = evidence(conn, task, attempt)
    if task['status'] != 'running' or attempt['status'] != 'running' or fingerprint != assessment['evidence_hash'] or config['execution_policy_revision'] != assessment['policy_revision'] or not provider or provider['revision'] != assessment['provider_revision'] or config['decision_provider_id'] != assessment['provider_id'] or config['decision_mode'] != assessment['mode'] or config['decision_model'] != assessment['model'] or execution_policy.expired(task) or execution_policy.budget(conn, task)['remaining_seconds'] <= 0:
        return False
    if conn.execute("SELECT 1 FROM computer_controls WHERE bot_id=%s AND holder='human'", (task['bot_id'],)).fetchone():
        return False
    if conn.execute("SELECT 1 FROM approvals WHERE task_id=%s AND status='pending'", (task['id'],)).fetchone():
        return False
    try:
        service.check_grant(conn, task)
    except Exception:
        return False
    return True


def apply(identity, result=None, error=None):
    with db.connection() as conn:
        initial = conn.execute('SELECT * FROM execution_assessments WHERE id=%s', (identity,)).fetchone()
        if not initial:
            return
        task = service.locked_task(conn, initial['task_id'])
        assessment = conn.execute('SELECT * FROM execution_assessments WHERE id=%s FOR UPDATE', (identity,)).fetchone()
        if assessment['status'] != 'evaluating':
            return
        attempt = conn.execute('SELECT * FROM attempts WHERE id=%s', (assessment['attempt_id'],)).fetchone()
        config = registry.workspace(conn)
        provider = conn.execute('SELECT revision FROM providers WHERE id=%s', (assessment['provider_id'],)).fetchone()
        stale = not current(conn, assessment, task, attempt, config, provider)
        status = 'discarded' if stale else 'unavailable' if error else 'observed'
        if result and not stale and result.confidence >= .85 and config['decision_actions']:
            status = 'applied'
            if result.recommendation != 'continue':
                # Guidance stays in this admitted execution. It conveys no new owner
                # authority and never approves, stops, delegates or repeats an effect.
                text = ('Hermbot execution monitor: review repeated failures and current state, choose an available alternative and continue the original request.' if result.recommendation == 'investigate' else 'Hermbot execution monitor: check whether specific owner information is actually missing. If so, use hermbot_ask with the exact missing information; otherwise continue.')
                previous = conn.execute("SELECT 1 FROM controls WHERE task_id=%s AND source='monitor' AND created_at>now()-interval '120 seconds'", (task['id'],)).fetchone()
                if not previous:
                    conn.execute("INSERT INTO controls(id,task_id,attempt_id,input,source) VALUES(%s,%s,%s,%s,'monitor')", (uuid4(), task['id'], assessment['attempt_id'], text))
        conn.execute('UPDATE execution_assessments SET status=%s,recommendation=%s,confidence=%s,error=%s,usage=%s,updated_at=now() WHERE id=%s',
                     (status, result.recommendation if result else None, result.confidence if result else None, error, Jsonb(result.usage if result else {}), identity))
        db.event(conn, 'execution.assessed', task, data={'assessment_id': str(identity), 'status': status, 'recommendation': result.recommendation if result else None, 'confidence': result.confidence if result else None, 'error': error})


async def drive_one():
    assessment = await asyncio.to_thread(claim)
    if not assessment:
        return
    with db.connection() as conn:
        task = service.locked_task(conn, assessment['task_id'])
        attempt = conn.execute('SELECT * FROM attempts WHERE id=%s', (assessment['attempt_id'],)).fetchone()
        provider = conn.execute('SELECT * FROM providers WHERE id=%s', (assessment['provider_id'],)).fetchone()
        valid = current(conn, assessment, task, attempt, registry.workspace(conn), provider)
    if not valid:
        apply(assessment['id'], error='Execution or decision configuration changed before assessment')
        return
    try:
        result = await decisions.assess(base_url=provider['base_url'], api_key=decrypt(provider['secret_encrypted']), model=assessment['model'], mode=assessment['mode'], evidence=assessment['evidence'], **({'provider': provider} if assessment['mode'] == 'responses' else {}))
    except Exception as exc:
        # Never persist arbitrary HTTP error bodies or request headers.
        from .model_client import ModelConnectionError
        apply(assessment['id'], error=str(exc) if isinstance(exc, ModelConnectionError) else 'Decision assessment unavailable (' + type(exc).__name__ + ')')
    else:
        apply(assessment['id'], result)


async def run(stop):
    while not stop.is_set():
        try:
            await drive_one()
        except Exception as exc:
            # Monitoring failure cannot terminate the execution worker.
            logger.info('Execution monitor unavailable (%s)', type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass
