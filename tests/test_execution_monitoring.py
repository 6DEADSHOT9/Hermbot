"""Continuous execution, explicit budgets and recommendation races."""
import asyncio
from datetime import timedelta
import json
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
from psycopg.types.json import Jsonb
import pytest

from backend import app as application, connections, db, decisions, execution_monitor, execution_policy, inputs, outcomes, service, worker
from backend.models import AskArgs, OutcomeSpec
from backend.settings import settings
from tests.conftest import row
from tests.test_phase4 import connection, decide, effect, operations, proposal


@pytest.fixture
def owner():
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    yield client
    client.close()


def configure_decisions(conn, *, actions=True):
    provider = conn.execute('SELECT id,models,revision FROM providers ORDER BY created_at LIMIT 1').fetchone()
    conn.execute("UPDATE workspace_settings SET decision_mode='chat',decision_provider_id=%s,decision_model=%s,decision_actions=%s", (provider['id'], provider['models'][0], actions))


def running_attempt(task):
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET status='running',runtime_run_id='test-existing-run',started_at=now(),accounted_at=now() WHERE id=%s", (attempt['id'],))
        return conn.execute('SELECT * FROM attempts WHERE id=%s', (attempt['id'],)).fetchone()


def queue_assessment(task, attempt, *, actions=True):
    with db.connection() as conn:
        configure_decisions(conn, actions=actions)
        for _ in range(3):
            db.event(conn, 'tool.completed', row(task['id']), data={'tool': 'browser_exec', 'error': True})
        execution_monitor.queue(conn, service.locked_task(conn, task['id']), attempt)
    return execution_monitor.claim()


def test_long_healthy_run_keeps_same_execution(monkeypatch, make_task):
    task = make_task()
    attempt = running_attempt(task)
    assert task['deadline'] is None
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET created_at=now()-interval '12 minutes',accounted_at=now()-interval '12 minutes' WHERE id=%s", (attempt['id'],))
    class Harness:
        def __init__(self, bot_id):
            pass

        async def status(self, run_id):
            assert run_id == 'test-existing-run'
            return {'status': 'running'}

        async def stop(self, run_id):
            pytest.fail('A healthy execution was stopped by elapsed time')

        async def events(self, run_id):
            for event in []:
                yield event
    monkeypatch.setattr(worker, 'HermesClient', Harness)
    asyncio.run(worker.drive(attempt))
    worker.STREAMS.pop(attempt['id'], None)
    assert row(task['id'])['status'] == 'running'
    assert row(task['id'])['attempts'] == 1
    with db.connection() as conn:
        assert execution_policy.budget(conn, row(task['id']))['used_seconds'] >= 720


def test_accounting_excludes_observed_wait_and_is_idempotent(make_task):
    task = make_task()
    attempt = running_attempt(task)
    start = service.now()
    with db.connection() as conn:
        conn.execute('UPDATE attempts SET accounted_at=%s WHERE id=%s', (start, attempt['id']))
        execution_policy.account(conn, attempt['id'], running=False, at=start + timedelta(seconds=20))
        execution_policy.account(conn, attempt['id'], running=False, at=start + timedelta(hours=2))
        execution_policy.account(conn, attempt['id'], at=start + timedelta(hours=2))
        execution_policy.account(conn, attempt['id'], running=False, at=start + timedelta(hours=2, seconds=10))
        execution_policy.account(conn, attempt['id'], running=False, at=start + timedelta(hours=2, seconds=10))
        assert execution_policy.budget(conn, row(task['id']))['used_seconds'] == 30


def test_normal_continuations_do_not_exhaust_recovery_allowance(make_task):
    task = make_task()
    for turn in range(10):
        attempt = worker.claim_new()[0]
        with db.connection() as conn:
            conn.execute("UPDATE tasks SET status='waiting_approval',continuation=%s WHERE id=%s", (f'Continue stage {turn}', task['id']))
        worker.settle(attempt['id'], {'status': 'completed', 'output': 'Waiting for receipt'})
        assert row(task['id'])['status'] == 'queued'
    assert row(task['id'])['attempts'] == 10
    assert row(task['id'])['recovery_failures'] == 0


def test_shared_budget_counts_children_and_preserves_results(owner, make_task):
    task = make_task()
    parent = running_attempt(task)
    with db.connection() as conn:
        specs = outcomes.register(conn, service.locked_task(conn, task['id']), [OutcomeSpec(key='draft', title='Draft', kind='report').model_dump(), OutcomeSpec(key='delivery', title='Delivery', kind='connector_effect').model_dump()])
        conn.execute("UPDATE work_outcomes SET status='fulfilled',evidence_strength='artifact_exists' WHERE id=%s", (specs[0]['id'],))
        child = service.delegate(conn, service.locked_task(conn, task['id']), 'writer', 'Deliver report', outcome_ids=[specs[1]['id']])
        conn.execute('UPDATE attempts SET active_seconds=1800,accounted_at=NULL WHERE id=%s', (parent['id'],))
    child_attempt = worker.claim_new()[0]
    with db.connection() as conn:
        conn.execute('UPDATE attempts SET active_seconds=1801 WHERE id=%s', (child_attempt['id'],))
        assert execution_policy.budget(conn, row(child['task_id']))['remaining_seconds'] == 0
        execution_policy.pause(conn, row(task['id']))
    assert owner.post(f"/api/v1/tasks/{task['id']}/continue", headers={'Idempotency-Key': str(uuid4())}).status_code == 409
    for attempt in (parent, child_attempt):
        worker.settle(attempt['id'], {'status': 'cancelled'})
    assert row(task['id'])['status'] == 'paused_budget'
    state = owner.get('/api/v1/state').json()
    item = state['work_items'][0]
    assert item['status'] == 'paused_budget'
    assert item['progress'] == {'fulfilled': 1, 'required': 2}
    key = str(uuid4())
    response = owner.post(f"/api/v1/tasks/{task['id']}/continue", headers={'Idempotency-Key': key})
    assert response.status_code == 200
    assert owner.post(f"/api/v1/tasks/{task['id']}/continue", headers={'Idempotency-Key': key}).status_code == 200
    with db.connection() as conn:
        assert execution_policy.budget(conn, row(task['id']))['limit_seconds'] == 7200
        assert conn.execute('SELECT status FROM work_outcomes WHERE id=%s', (specs[0]['id'],)).fetchone()['status'] == 'fulfilled'
    assert row(child['task_id'])['status'] == 'queued'
    assert row(task['id'])['status'] == 'waiting_children'


def test_owner_stop_overrides_budget_pause_and_survives_settlement(owner, make_task):
    task = make_task()
    attempt = running_attempt(task)
    with db.connection() as conn:
        execution_policy.pause(conn, row(task['id']))
        service.cancel(conn, row(task['id']))
    worker.settle(attempt['id'], {'status': 'cancelled', 'error': None})
    assert row(task['id'])['error'] == 'Stopped by owner'
    assert row(task['id'])['stop_kind'] == 'owner'
    assert owner.post(f"/api/v1/tasks/{task['id']}/continue", headers={'Idempotency-Key': str(uuid4())}).status_code == 409


def test_waiting_question_has_independent_expiry(make_task):
    task = make_task()
    with db.connection() as conn:
        question = inputs.ask(conn, task, AskArgs(prompt='Which file?').model_dump())
        expiry = conn.execute('SELECT expires_at FROM input_requests WHERE id=%s', (question['input_request_id'],)).fetchone()['expires_at']
    assert expiry > service.now() + timedelta(hours=23)
    worker.advance_waiters()
    assert row(task['id'])['status'] == 'waiting_input'


@pytest.mark.parametrize('invalidate', ['owner_stop', 'progress', 'direction', 'policy', 'provider', 'budget', 'computer', 'approval'])
def test_stale_decision_cannot_change_execution(make_task, invalidate):
    task = make_task()
    attempt = running_attempt(task)
    assessment = queue_assessment(task, attempt)
    with db.connection() as conn:
        if invalidate == 'owner_stop':
            service.cancel(conn, row(task['id']))
        elif invalidate == 'progress':
            db.event(conn, 'artifact.published', row(task['id']), data={'name': 'report'})
        elif invalidate == 'direction':
            service.steer(conn, row(task['id']), 'Change the research focus')
        elif invalidate == 'policy':
            conn.execute('UPDATE workspace_settings SET execution_policy_revision=execution_policy_revision+1')
        elif invalidate == 'provider':
            conn.execute('UPDATE providers SET revision=revision+1 WHERE id=%s', (assessment['provider_id'],))
        elif invalidate == 'budget':
            conn.execute('UPDATE attempts SET active_seconds=3600 WHERE id=%s', (attempt['id'],))
        elif invalidate == 'computer':
            conn.execute("INSERT INTO computer_controls(bot_id,holder) VALUES(%s,'human')", (task['bot_id'],))
        elif invalidate == 'approval':
            payload = {'request_id': 'native-review'}
            conn.execute("INSERT INTO approvals(id,task_id,attempt_id,kind,payload,payload_hash,expires_at) VALUES(%s,%s,%s,'runtime',%s,%s,now()+interval '24 hours')", (uuid4(), task['id'], attempt['id'], Jsonb(payload), service.digest(payload)))
    execution_monitor.apply(assessment['id'], decisions.Result('investigate', .99, {}))
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM execution_assessments WHERE id=%s', (assessment['id'],)).fetchone()['status'] == 'discarded'
        assert conn.execute("SELECT count(*) AS n FROM controls WHERE source='monitor'").fetchone()['n'] == 0


@pytest.mark.parametrize('actions,confidence,expected', [(True,.99,1), (True,.6,0), (False,.99,0)])
def test_decision_guidance_is_bounded_and_never_stops(make_task, actions, confidence, expected):
    task = make_task()
    attempt = running_attempt(task)
    assessment = queue_assessment(task, attempt, actions=actions)
    execution_monitor.apply(assessment['id'], decisions.Result('investigate', confidence, {}))
    execution_monitor.apply(assessment['id'], decisions.Result('investigate', confidence, {}))
    assert row(task['id'])['status'] == 'running'
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM controls WHERE source='monitor'").fetchone()['n'] == expected
        execution_monitor.queue(conn, row(task['id']), attempt)
        assert conn.execute('SELECT count(*) AS n FROM execution_assessments').fetchone()['n'] == 1


def test_decision_outage_does_not_change_work(make_task):
    task = make_task()
    attempt = running_attempt(task)
    assessment = queue_assessment(task, attempt)
    execution_monitor.apply(assessment['id'], error='Decision assessment unavailable (TimeoutError)')
    assert row(task['id'])['status'] == 'running'


def test_pre_tool_recovery_is_bounded_and_post_tool_failure_is_not_replayed(make_task):
    task = make_task()
    for _ in range(2):
        attempt = worker.claim_new()[0]
        worker.settle(attempt['id'], {'status': 'failed', 'error': 'provider_unavailable'})
        assert row(task['id'])['status'] == 'queued'
    attempt = worker.claim_new()[0]
    worker.settle(attempt['id'], {'status': 'failed', 'error': 'provider_unavailable'})
    assert row(task['id'])['status'] == 'failed'
    task = make_task(bot='writer')
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        db.event(conn, 'tool.started', row(task['id']), data={'tool': 'terminal'})
    worker.settle(attempt['id'], {'status': 'failed', 'error': 'provider_unavailable'})
    assert row(task['id'])['status'] == 'failed'
    assert row(task['id'])['recovery_failures'] == 0


@pytest.mark.parametrize('mode', ['decisions', 'chat'])
def test_decision_adapter_typed_payload(monkeypatch, mode):
    def respond(request):
        payload = json.loads(request.content)
        assert request.headers['Authorization'] == 'Bearer test-key'
        if mode == 'decisions':
            assert request.url.path == '/v1/decisions'
            assert payload['questions'][0]['name'] == 'execution'
            answer = {'answers': [{'type': 'choice', 'name': 'execution', 'choice': 'continue', 'confidence': .9}]}
        else:
            assert request.url.path == '/v1/chat/completions'
            answer = {'choices': [{'message': {'content': '{"choice":"continue","confidence":0.9}'}}]}
        return httpx.Response(200, json=answer)
    original = httpx.AsyncClient
    monkeypatch.setattr(decisions.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    result = asyncio.run(decisions.assess(base_url='https://example.invalid/v1', api_key='test-key', model='test', mode=mode, evidence={'recent_tools': []}))
    assert result.recommendation == 'continue'


@pytest.mark.parametrize('answer', [{'choice':'stop','confidence':1}, {'choice':'continue','confidence':float('nan')}, {'choice':'continue','confidence':True}, {'type':'refusal'}])
def test_invalid_answers_are_not_executable(answer):
    with pytest.raises(ValueError):
        decisions.parse_answer(answer)


def test_deadline_and_expired_authority_remain_enforced(make_task):
    task = make_task(manager=True)
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET deadline=now()-interval '1 second' WHERE id=%s", (task['id'],))
    worker.claim_new()
    assert row(task['id'])['stop_kind'] == 'deadline'
    task = make_task(manager=True)
    with db.connection() as conn:
        execution_policy.pause(conn, row(task['id']))
        conn.execute("UPDATE grants SET expires_at=now()-interval '1 second' WHERE id=%s", (task['grant_id'],))
        with pytest.raises(HTTPException):
            execution_policy.continue_work(conn, row(task['id']))


def test_inflight_external_effect_fences_budget_continue(make_task):
    task = make_task()
    with db.connection() as conn:
        spec = outcomes.register(conn, task, [OutcomeSpec(key='send', title='Send', kind='native_effect').model_dump()])[0]
        conn.execute("UPDATE work_outcomes SET status='uncertain' WHERE id=%s", (spec['id'],))
        execution_policy.pause(conn, row(task['id']))
        with pytest.raises(HTTPException):
            execution_policy.continue_work(conn, row(task['id']))


def test_paused_connector_draft_keeps_approval_and_dispatches_once_after_continue(owner, make_task, monkeypatch):
    task = make_task()
    calls = effect(monkeypatch)
    review = proposal(task, connection())
    assert decide(owner, review).status_code == 200
    with db.connection() as conn:
        execution_policy.pause(conn, row(task['id']))
    assert connections.drive_one() is False
    assert operations()[0]['status'] == 'queued'
    assert calls == []
    response = owner.post(f"/api/v1/tasks/{task['id']}/continue", headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 200 and response.json()['status'] == 'waiting_approval'
    assert connections.drive_one() is True
    assert connections.drive_one() is False
    assert operations()[0]['status'] == 'succeeded'
    assert len(calls) == 1


@pytest.mark.parametrize('complete', [True, False])
def test_completion_racing_budget_preserves_receipts_and_pauses_only_unfinished_work(owner, make_task, complete):
    task = make_task()
    attempt = running_attempt(task)
    with db.connection() as conn:
        specs = outcomes.register(conn, task, [OutcomeSpec(key='report', title='Report', kind='report').model_dump(), OutcomeSpec(key='delivery', title='Delivery', kind='connector_effect').model_dump()])
        if complete:
            conn.execute("UPDATE work_outcomes SET status='fulfilled',evidence_strength='provider_receipt' WHERE id=%s", (specs[1]['id'],))
        claims = [{'outcome_id': str(specs[0]['id']), 'expected_revision': 1, 'status': 'fulfilled'}]
        conn.execute('UPDATE tasks SET pending_result=%s,pending_outcomes=%s WHERE id=%s', ('The report is ready', Jsonb(claims), task['id']))
        execution_policy.pause(conn, row(task['id']))
    worker.settle(attempt['id'], {'status': 'completed'})
    assert row(task['id'])['status'] == ('completed' if complete else 'paused_budget')
    with db.connection() as conn:
        assert outcomes.rows(conn, task['work_item_id'])[0]['status'] == 'fulfilled'
    assert owner.get('/api/v1/state').json()['work_items'][0]['status'] == ('completed' if complete else 'paused_budget')
    if complete:
        assert row(task['id'])['stop_reason'] is None


def test_budget_interruption_of_unconfirmed_native_effect_needs_reconciliation(owner, make_task):
    task = make_task()
    attempt = running_attempt(task)
    with db.connection() as conn:
        specs = outcomes.register(conn, task, [OutcomeSpec(key='send', title='Send', kind='native_effect').model_dump()])
        db.event(conn, 'tool.started', task, data={'tool': 'browser_exec'})
        execution_policy.pause(conn, row(task['id']))
    worker.settle(attempt['id'], {'status': 'cancelled'})
    with db.connection() as conn:
        assert outcomes.rows(conn, task['work_item_id'])[0]['status'] == 'uncertain'
    assert owner.post(f"/api/v1/tasks/{task['id']}/continue", headers={'Idempotency-Key': str(uuid4())}).status_code == 409
    response = owner.post(f"/api/v1/outcomes/{specs[0]['id']}/reconcile", json={'expected_revision': 1, 'result': 'failed', 'evidence': 'I verified that the message was never sent at the destination.'})
    assert response.status_code == 200
    assert owner.post(f"/api/v1/tasks/{task['id']}/continue", headers={'Idempotency-Key': str(uuid4())}).status_code == 200


def test_stopped_queued_assessment_does_not_call_provider(monkeypatch, make_task):
    task = make_task()
    attempt = running_attempt(task)
    assessment = queue_assessment(task, attempt)
    with db.connection() as conn:
        conn.execute("UPDATE execution_assessments SET status='queued' WHERE id=%s", (assessment['id'],))
        service.cancel(conn, row(task['id']))
    async def forbidden(**kw):
        pytest.fail('A stale assessment should not contact the provider')
    monkeypatch.setattr(decisions, 'assess', forbidden)
    asyncio.run(execution_monitor.drive_one())
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM execution_assessments WHERE id=%s', (assessment['id'],)).fetchone()['status'] == 'discarded'


def test_restart_claim_reconnects_same_attempt_without_new_dispatch(make_task):
    task = make_task()
    attempt = running_attempt(task)
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET worker_id='disconnected',lease_until=now()-interval '1 second' WHERE id=%s", (attempt['id'],))
    claimed = worker.claim_existing()
    assert len(claimed) == 1
    assert claimed[0]['id'] == attempt['id'] and claimed[0]['runtime_run_id'] == 'test-existing-run'
    assert worker.claim_new() == []
    assert row(task['id'])['attempts'] == 1


def test_expired_owner_deadline_does_not_invalidate_identical_submission_receipt(make_task, monkeypatch):
    task = make_task()
    key = str(uuid4())
    deadline = service.now() + timedelta(minutes=1)
    kwargs = dict(bot_id=task['bot_id'], conversation_id=task['conversation_id'], prompt='Explicit deadline', request_key=key, deadline=deadline)
    with db.connection() as conn:
        original = service.create_task(conn, **kwargs)
    monkeypatch.setattr(service, 'now', lambda: deadline + timedelta(seconds=1))
    with db.connection() as conn:
        conn.execute('UPDATE tasks SET deadline=now()-interval \'1 second\' WHERE id=%s', (original['id'],))
        assert service.create_task(conn, **kwargs)['id'] == original['id']


def test_assessment_retains_current_native_operation_metadata(make_task):
    task = make_task()
    attempt = running_attempt(task)
    with db.connection() as conn:
        db.event(conn, 'tool.started', task, data={'tool': 'terminal', 'preview': 'private tool arguments'})
        evidence, _ = execution_monitor.evidence(conn, task, attempt)
        assert evidence['last_observed_tool'] == {'name': 'terminal', 'state': 'started'}
        assert 'private tool arguments' not in json.dumps(evidence)


def test_legacy_workspace_save_preserves_execution_configuration(owner):
    with db.connection() as conn:
        configure_decisions(conn)
        conn.execute('UPDATE workspace_settings SET active_budget_seconds=7200')
    configuration = owner.get('/api/v1/settings').json()['workspace']
    legacy = {name: configuration[name] for name in ('timezone', 'default_provider_id', 'permitted_tools', 'max_bots', 'max_active')}
    response = owner.put('/api/v1/settings', json=legacy)
    assert response.status_code == 200
    assert response.json()['active_budget_seconds'] == 7200
    assert response.json()['decision_mode'] == 'chat'
    assert response.json()['decision_actions'] is True


@pytest.mark.parametrize('coverage,expected', [({}, 'failed'), ({'event_stream_complete': True, 'event_stream_gap': True}, 'failed'), ({'event_stream_complete': True, 'event_stream_gap': False}, 'queued')])
def test_accepted_failure_needs_complete_tool_event_coverage_before_recovery(make_task, coverage, expected):
    task = make_task()
    attempt = running_attempt(task)
    with db.connection() as conn:
        conn.execute('UPDATE attempts SET response=response||%s WHERE id=%s', (Jsonb(coverage), attempt['id']))
    worker.settle(attempt['id'], {'status': 'failed', 'error': 'provider_unavailable'})
    assert row(task['id'])['status'] == expected


def test_monitor_guidance_does_not_start_another_execution_when_run_finishes(make_task):
    task = make_task()
    attempt = running_attempt(task)
    assessment = queue_assessment(task, attempt)
    execution_monitor.apply(assessment['id'], decisions.Result('investigate', .99, {}))
    worker.settle(attempt['id'], {'status': 'completed', 'output': 'The requested report is complete'})
    assert row(task['id'])['status'] == 'completed'
    assert row(task['id'])['attempts'] == 1
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM controls WHERE source='monitor'").fetchone()['status'] == 'discarded'


def test_monitor_guidance_rejected_by_ending_run_is_discarded(make_task):
    task = make_task()
    attempt = running_attempt(task)
    assessment = queue_assessment(task, attempt)
    execution_monitor.apply(assessment['id'], decisions.Result('investigate', .99, {}))
    class Harness:
        async def steer(self, run_id, prompt):
            response = httpx.Response(409, request=httpx.Request('POST', 'https://example.invalid/steer'))
            raise httpx.HTTPStatusError('Run is ending', request=response.request, response=response)
    asyncio.run(worker.deliver_decisions(Harness(), attempt))
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM controls WHERE source='monitor'").fetchone()['status'] == 'discarded'


def test_owner_direction_supersedes_pending_monitor_guidance(make_task):
    task = make_task()
    attempt = running_attempt(task)
    assessment = queue_assessment(task, attempt)
    execution_monitor.apply(assessment['id'], decisions.Result('investigate', .99, {}))
    with db.connection() as conn:
        service.steer(conn, row(task['id']), 'Use the revised source material')
        assert conn.execute("SELECT status FROM controls WHERE source='monitor'").fetchone()['status'] == 'discarded'
        assert conn.execute("SELECT status FROM controls WHERE source='owner'").fetchone()['status'] == 'pending'
