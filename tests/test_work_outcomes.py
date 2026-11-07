"""Requested results and real answers survive execution, handoffs and external uncertainty."""
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
import pytest

from backend import app as application, capabilities, connections, db, inputs, outcomes, service, worker, work_items
from backend.models import AskArgs, FinishArgs, OutcomeSpec
from backend.settings import settings
from tests.conftest import row
from tests.test_phase4 import connection


@pytest.fixture
def owner():
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    yield client
    client.close()


def register(task, *specs):
    with db.connection() as conn:
        return outcomes.register(conn, service.locked_task(conn, task['id']), [OutcomeSpec(**s).model_dump() for s in specs])


def question(task, **options):
    with db.connection() as conn:
        result = inputs.ask(conn, service.locked_task(conn, task['id']), AskArgs(prompt='Which recipient?', **options).model_dump())
        return conn.execute('SELECT * FROM input_requests WHERE id=%s', (result['input_request_id'],)).fetchone()


def respond(owner, request, answer='Dhruv', **values):
    return owner.post(f"/api/v1/input-requests/{request['id']}/answer", json={'expected_question_hash': request['question_hash'], 'answer': answer, **values})


def item(owner):
    return owner.get('/api/v1/state').json()['work_items'][0]


def test_typed_answer_resumes_once_without_approval_or_new_work(owner, make_task):
    task = make_task()
    question_row = question(task)
    assert owner.get('/api/v1/state').json()['approvals'] == []
    assert respond(owner, question_row).status_code == 200
    assert respond(owner, question_row).status_code == 200
    assert respond(owner, question_row, 'Someone else').status_code == 409
    assert row(task['id'])['status'] == 'queued'
    assert 'Dhruv' in row(task['id'])['continuation']
    assert 'Owner review decision: once' not in row(task['id'])['continuation']
    assert len(owner.get('/api/v1/state').json()['work_items']) == 1
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM events WHERE kind='input.answered'").fetchone()['n'] == 1


def test_answer_while_run_ends_waits_then_schedules_once(owner, make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    request = question(task)
    assert respond(owner, request).status_code == 200
    assert row(task['id'])['status'] == 'waiting_input'
    assert worker.claim_new() == []
    worker.settle(attempt['id'], {'status': 'completed', 'output': 'Waiting for the answer'})
    assert row(task['id'])['status'] == 'queued'
    claimed = worker.claim_new()
    assert len(claimed) == 1 and 'Dhruv' in claimed[0]['input']
    assert respond(owner, request).status_code == 200
    assert row(task['id'])['continuation'] is None


@pytest.mark.parametrize('answer', ['', '   '])
def test_empty_answers_cannot_resume(owner, make_task, answer):
    task = make_task()
    request = question(task)
    assert respond(owner, request, answer).status_code == 422
    assert row(task['id'])['status'] == 'waiting_input'


def test_choice_and_hash_validation(owner, make_task):
    task = make_task()
    request = question(task, answer_type='choice', choices=['Person A', 'Person B'])
    assert respond(owner, request, 'once').status_code == 422
    assert respond(owner, request, 'Person A', expected_question_hash='0'*64).status_code == 409
    assert respond(owner, request, 'Person A').status_code == 200


@pytest.mark.parametrize('invalidate', ['stop', 'expiry', 'revision'])
def test_stale_questions_cannot_resume(owner, make_task, invalidate):
    task = make_task()
    outcome = register(task, {'key': 'delivery', 'title': 'Deliver report', 'kind': 'connector_effect'})[0]
    request = question(task, outcome_id=outcome['id'])
    with db.connection() as conn:
        if invalidate == 'stop':
            service.cancel(conn, service.locked_task(conn, task['id']))
        elif invalidate == 'expiry':
            conn.execute("UPDATE input_requests SET expires_at=now()-interval '1 second' WHERE id=%s", (request['id'],))
        else:
            conn.execute('UPDATE work_outcomes SET revision=revision+1 WHERE id=%s', (outcome['id'],))
    assert respond(owner, request).status_code == 409
    assert row(task['id'])['continuation'] is None


def test_legacy_text_review_becomes_question_secure_stays_review(owner, make_task):
    task = make_task()
    with db.connection() as conn:
        result = service.request_review(conn, service.locked_task(conn, task['id']), 'Question', {'input_type': 'text', 'prompt': 'Recipient?'}, 'input')
        assert 'input_request_id' in result
        assert conn.execute('SELECT count(*) AS n FROM approvals').fetchone()['n'] == 0


def test_normal_chat_answer_routes_to_existing_question(owner, make_task):
    task = make_task()
    request = question(task)
    response = owner.post('/api/v1/messages', headers={'Idempotency-Key': str(uuid4())}, json={'bot_id': 'researcher', 'conversation_id': str(task['conversation_id']), 'prompt': 'Person A'})
    reply = response.json()
    with db.connection() as conn:
        routed = work_items.route(conn, service.locked_task(conn, reply['id']), {'intent': 'answer', 'input_request_id': request['id'], 'answer': 'Person A'})
    assert routed['intent'] == 'answer'
    assert row(task['id'])['status'] == 'queued'
    assert row(reply['id'])['message_intent'] == 'answer'
    assert len(owner.get('/api/v1/state').json()['work_items']) == 1


def test_finished_turn_keeps_unfulfilled_delivery_visible(owner, make_task):
    task = make_task()
    report, delivery = register(task,
        {'key': 'report', 'title': 'Research report', 'kind': 'report'},
        {'key': 'dm', 'title': 'Slack delivery', 'kind': 'connector_effect'})
    attempt = worker.claim_new()[0]
    claims = FinishArgs(result='Research ready. Delivery is blocked.', outcomes=[
        {'outcome_id': report['id'], 'expected_revision': 1, 'status': 'fulfilled'},
        {'outcome_id': delivery['id'], 'expected_revision': 1, 'status': 'blocked', 'reason': 'Recipient missing'}]).model_dump()
    with db.connection() as conn:
        service.tool_action(conn, 'researcher', attempt['session_id'], 'finish', claims, 'finish')
    worker.settle(attempt['id'], {'status': 'completed', 'output': 'Research ready'})
    assert row(task['id'])['status'] == 'completed'
    view = item(owner)
    assert view['status'] == 'needs_attention'
    assert view['progress'] == {'fulfilled': 1, 'required': 2}
    assert view['outcomes'][1]['reason'] == 'Recipient missing'


def test_no_registered_results_cannot_vacuously_complete(owner, make_task):
    task = make_task()
    with db.connection() as conn:
        conn.execute('UPDATE work_items SET outcomes_version=1 WHERE id=%s', (task['work_item_id'],))
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))
    assert item(owner)['status'] == 'needs_attention'


def test_matching_slack_receipt_fulfills_delivery_without_history(owner, make_task):
    task = make_task()
    account = connection(provider='slack', actions=['slack.post'])
    delivery = register(task, {'key': 'dm', 'title': 'DM report', 'kind': 'connector_effect', 'target': {'connection_id': account['id'], 'action': 'slack.post', 'resource': 'U123'}})[0]
    with db.connection() as conn:
        result = connections.invoke(conn, service.locked_task(conn, task['id']), {'connection_id': account['id'], 'action': 'slack.post', 'arguments': {'channel': 'U123', 'text': 'Report'}, 'outcome_id': delivery['id']})
        operation = conn.execute('SELECT * FROM connector_operations WHERE approval_id=%s', (result['approval_id'],)).fetchone()
        connections.finish_operation(conn, operation, 'succeeded', result={'ok': True, 'channel': 'D123', 'ts': '1.2'})
    assert item(owner)['status'] == 'completed'
    with db.connection() as conn:
        with pytest.raises(HTTPException):
            outcomes.bind_operation(conn, service.locked_task(conn, task['id']), delivery['id'], account, 'slack.post', {'channel': 'U123', 'text': 'Report', 'thread_ts': ''})
    assert item(owner)['outcomes'][0]['evidence_strength'] == 'provider_receipt'


def test_wrong_recipient_and_receipt_cannot_fulfill(owner, make_task):
    task = make_task()
    account = connection(provider='slack', actions=['slack.post'])
    outcome = register(task, {'key': 'dm', 'title': 'DM report', 'kind': 'connector_effect', 'target': {'connection_id': account['id'], 'action': 'slack.post', 'resource': 'U123'}})[0]
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            outcomes.bind_operation(conn, service.locked_task(conn, task['id']), outcome['id'], account, 'slack.post', {'channel': 'U999', 'text': 'Report', 'thread_ts': ''})
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            outcomes.validate_claims(conn, service.locked_task(conn, task['id']), [{'outcome_id': outcome['id'], 'expected_revision': 1, 'status': 'fulfilled', 'operation_id': uuid4()}])


@pytest.mark.parametrize('complete', [True, False])
def test_retry_respects_completed_results_after_failed_readback(owner, make_task, complete):
    task = make_task()
    account = connection(provider='slack', actions=['slack.post'])
    specs = [{'key': 'dm', 'title': 'DM report', 'kind': 'connector_effect',
              'target': {'connection_id': account['id'], 'action': 'slack.post', 'resource': 'U123'}}]
    if not complete:
        specs.append({'key': 'report', 'title': 'Remaining report', 'kind': 'report'})
    delivery = register(task, *specs)[0]
    with db.connection() as conn:
        proposed = connections.invoke(conn, service.locked_task(conn, task['id']),
                                      {'connection_id': account['id'], 'action': 'slack.post',
                                       'arguments': {'channel': 'U123', 'text': 'Report'},
                                       'outcome_id': delivery['id']})
        approval = conn.execute('SELECT * FROM approvals WHERE id=%s', (proposed['approval_id'],)).fetchone()
    assert owner.post(f"/api/v1/approvals/{approval['id']}",
                      json={'decision': 'once', 'expected_payload_hash': approval['payload_hash']}).status_code == 200
    with db.connection() as conn:
        operation = conn.execute('SELECT * FROM connector_operations WHERE approval_id=%s', (approval['id'],)).fetchone()
        connections.finish_operation(conn, operation, 'succeeded', result={'ok': True, 'channel': 'D123', 'ts': '1.2'})
        conn.execute("UPDATE tasks SET status='failed',error='Optional read-back failed' WHERE id=%s", (task['id'],))
    assert item(owner)['status'] == ('completed' if complete else 'needs_attention')
    before = len(owner.get('/api/v1/state').json()['tasks'])
    response = owner.post(f"/api/v1/tasks/{task['id']}/retry", headers={'Idempotency-Key': str(uuid4())}, json={})
    assert response.status_code == (409 if complete else 202), response.text
    if complete:
        assert 'already complete' in response.json()['detail']
    after = owner.get('/api/v1/state').json()
    assert len(after['tasks']) == before + (0 if complete else 1)
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM connector_operations').fetchone()['n'] == 1
        assert outcomes.rows(conn, task['work_item_id'])[0]['status'] == 'fulfilled'


def test_uncertain_optional_effect_prevents_completion_even_after_stop(owner, make_task):
    task = make_task()
    account = connection(provider='slack', actions=['slack.post'])
    report, delivery = register(task, {'key': 'report', 'title': 'Report', 'kind': 'report'}, {'key': 'dm', 'title': 'Optional send', 'kind': 'connector_effect', 'required': False})
    with db.connection() as conn:
        outcomes.record(conn, report, task['id'], 'execution', 'example', 'model_reported')
        result = connections.invoke(conn, service.locked_task(conn, task['id']), {'connection_id': account['id'], 'action': 'slack.post', 'arguments': {'channel': 'U123', 'text': 'Report'}, 'outcome_id': delivery['id']})
        operation = conn.execute('SELECT * FROM connector_operations WHERE approval_id=%s', (result['approval_id'],)).fetchone()
        connections.finish_operation(conn, operation, 'uncertain', error='Response lost')
        service.cancel(conn, service.locked_task(conn, task['id']))
    assert item(owner)['status'] == 'uncertain'


def test_handoff_inherits_selected_results_without_connection_credentials(owner, make_task):
    task = make_task(manager=True)
    account = connection(provider='slack', actions=['slack.post'])
    outcome = register(task, {'key': 'dm', 'title': 'Send report', 'kind': 'connector_effect', 'target': {'connection_id': account['id'], 'action': 'slack.post', 'resource': 'U123'}})[0]
    with db.connection() as conn:
        child = service.delegate(conn, service.locked_task(conn, task['id']), 'writer', 'Prepare report', outcome_ids=[outcome['id']])
        context = outcomes.context(conn, service.locked_task(conn, child['task_id']))
        assert len(context) == 1
        assert 'connection_id' not in context[0]['target']
        assert connections.accessible(conn, 'writer') == []


def test_cyclic_requirements_and_silent_changes_rejected(make_task):
    task = make_task()
    with pytest.raises(HTTPException):
        register(task, {'key': 'a', 'title': 'A', 'kind': 'report', 'depends_on': ['b']}, {'key': 'b', 'title': 'B', 'kind': 'report', 'depends_on': ['a']})
    register(task, {'key': 'a', 'title': 'A', 'kind': 'report'})
    with pytest.raises(HTTPException):
        register(task, {'key': 'a', 'title': 'Changed', 'kind': 'report', 'expected_revision': 1})


def test_waiting_input_cannot_execute_more_work(make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    question(task)
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.tool_action(conn, task['bot_id'], attempt['session_id'], 'delegate', {'bot_id': 'writer', 'prompt': 'Keep going'}, 'delegate')
    with db.connection() as conn:
        view = capabilities.manifest(conn, task['bot_id'], service.locked_task(conn, task['id']))
        assert 'hermbot_delegate' not in view['allowed_tools']


def test_owner_edit_replaces_exact_recipient_and_invalidates_old_draft(owner, make_task):
    task = make_task()
    account = connection(provider='slack', actions=['slack.post'])
    outcome = register(task, {'key': 'dm', 'title': 'Send report', 'kind': 'connector_effect', 'target': {'resource': 'U123', 'display_name': 'Old recipient'}})[0]
    with db.connection() as conn:
        proposed = connections.invoke(conn, service.locked_task(conn, task['id']), {'connection_id': account['id'], 'action': 'slack.post', 'arguments': {'channel': 'U123', 'text': 'Report'}, 'outcome_id': outcome['id']})
        approval = conn.execute('SELECT * FROM approvals WHERE id=%s', (proposed['approval_id'],)).fetchone()
    payload = {**approval['payload']['action'], 'arguments': {'channel': 'U999', 'text': 'Updated report'}}
    edited = owner.put(f"/api/v1/approvals/{approval['id']}", headers={'Idempotency-Key': str(uuid4())}, json={'expected_payload_hash': approval['payload_hash'], 'summary': 'Updated recipient', 'payload': payload})
    assert edited.status_code == 200, edited.text
    assert owner.post(f"/api/v1/approvals/{approval['id']}", json={'decision': 'once', 'expected_payload_hash': approval['payload_hash']}).status_code == 409
    assert item(owner)['outcomes'][0]['revision'] == 2
    assert item(owner)['outcomes'][0]['target']['resource'] == 'U999'
    assert 'display_name' not in item(owner)['outcomes'][0]['target']


def test_malformed_owner_edit_preserves_original_review_and_outcome(owner, make_task):
    task = make_task()
    account = connection(provider='slack', actions=['slack.post'])
    outcome = register(task, {'key': 'dm', 'title': 'Send report', 'kind': 'connector_effect',
                              'target': {'resource': 'U123'}})[0]
    with db.connection() as conn:
        proposed = connections.invoke(conn, service.locked_task(conn, task['id']),
                                      {'connection_id': account['id'], 'action': 'slack.post',
                                       'arguments': {'channel': 'U123', 'text': 'Report'},
                                       'outcome_id': outcome['id']})
        approval = conn.execute('SELECT * FROM approvals WHERE id=%s', (proposed['approval_id'],)).fetchone()
    response = owner.put(f"/api/v1/approvals/{approval['id']}",
                         headers={'Idempotency-Key': str(uuid4())},
                         json={'expected_payload_hash': approval['payload_hash'], 'summary': 'Malformed draft',
                               'payload': {**approval['payload']['action'], 'action': None}})
    assert response.status_code == 422
    current = item(owner)['outcomes'][0]
    assert current['revision'] == 1 and current['target']['resource'] == 'U123'
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM approvals WHERE id=%s', (approval['id'],)).fetchone()['status'] == 'pending'
        assert conn.execute('SELECT count(*) AS n FROM connector_operations').fetchone()['n'] == 1


def test_owner_can_remove_unneeded_requirement_but_not_unresolved_write(owner, make_task):
    task = make_task()
    outcome = register(task, {'key': 'dm', 'title': 'Send report', 'kind': 'connector_effect'})[0]
    request = question(task, outcome_id=outcome['id'])
    response = owner.post(f"/api/v1/outcomes/{outcome['id']}/drop", json={'expected_revision': 1})
    assert response.status_code == 200
    assert item(owner)['status'] == 'cancelled'
    assert respond(owner, request).status_code == 409
    worker.advance_waiters()
    assert row(task['id'])['status'] == 'queued'
    assert item(owner)['outcomes'][0]['status'] == 'cancelled'


def test_native_uncertainty_requires_owner_evidence_and_blocks_connector_fallback(owner, make_task):
    task = make_task()
    native, connector = register(task, {'key': 'native', 'title': 'Native delivery', 'kind': 'native_effect'}, {'key': 'fallback', 'title': 'Connector delivery', 'kind': 'connector_effect', 'required': False})
    with db.connection() as conn:
        conn.execute("UPDATE work_outcomes SET status='uncertain' WHERE id=%s", (native['id'],))
    account = connection(provider='slack', actions=['slack.post'])
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            outcomes.bind_operation(conn, service.locked_task(conn, task['id']), connector['id'], account, 'slack.post', {'channel': 'U123', 'text': 'Report'})
    response = owner.post(f"/api/v1/outcomes/{native['id']}/reconcile", json={'expected_revision': 1, 'result': 'fulfilled', 'evidence': 'I checked the sent message in the destination.'})
    assert response.status_code == 200
    assert item(owner)['status'] == 'completed'
    assert item(owner)['outcomes'][0]['evidence_strength'] == 'owner_attested'


def test_failed_required_helper_does_not_erase_completed_result(owner, make_task):
    task = make_task(manager=True)
    report, check = register(task, {'key': 'report', 'title': 'Report', 'kind': 'report'}, {'key': 'check', 'title': 'Required check', 'kind': 'report'})
    with db.connection() as conn:
        outcomes.record(conn, report, task['id'], 'execution', 'example', 'model_reported')
        child = service.delegate(conn, service.locked_task(conn, task['id']), 'writer', 'Check the report', outcome_ids=[check['id']])
    attempts = worker.claim_new()
    helper = next(a for a in attempts if str(a['task_id']) == child['task_id'])
    worker.settle(helper['id'], {'status': 'failed', 'error': 'Check failed'})
    assert item(owner)['status'] == 'needs_attention'
    assert item(owner)['progress']['fulfilled'] == 1


def test_owner_update_retains_prior_requirements_and_new_turn_cannot_hide_them(owner, make_task):
    task = make_task()
    register(task, {'key': 'report', 'title': 'Report', 'kind': 'report'})
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))
    response = owner.post('/api/v1/messages', headers={'Idempotency-Key': str(uuid4())}, json={'bot_id': task['bot_id'], 'conversation_id': str(task['conversation_id']), 'prompt': 'Also DM it', 'intent': 'update', 'work_item_id': str(task['work_item_id'])})
    updated = response.json()
    register(updated, {'key': 'dm', 'title': 'Delivery', 'kind': 'connector_effect'})
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (updated['id'],))
    view = item(owner)
    assert len(view['outcomes']) == 2 and view['status'] != 'completed'
    assert len(owner.get('/api/v1/state').json()['work_items']) == 1


def test_claims_batch_handles_dependencies_without_order_dependence(make_task):
    task = make_task()
    first, second = register(task, {'key': 'a', 'title': 'A', 'kind': 'report'}, {'key': 'b', 'title': 'B', 'kind': 'report', 'depends_on': ['a']})
    claims = [{'outcome_id': second['id'], 'expected_revision': 1, 'status': 'fulfilled'}, {'outcome_id': first['id'], 'expected_revision': 1, 'status': 'fulfilled'}]
    with db.connection() as conn:
        fresh = service.locked_task(conn, task['id'])
        fresh['pending_outcomes'] = claims
        outcomes.settle(conn, fresh, 'completed', uuid4())
        assert all(o['status'] == 'fulfilled' for o in outcomes.rows(conn, task['work_item_id']))


def test_native_policy_is_shared_and_tool_contracts_match():
    from backend.policy import NATIVE_INSTRUCTION
    from backend.models import ARGUMENT_MODELS
    from plugins.hermbot import TOOLS, NATIVE_INSTRUCTION as plugin_policy
    assert NATIVE_INSTRUCTION == plugin_policy and NATIVE_INSTRUCTION in worker.POLICY
    assert set(TOOLS) == set(ARGUMENT_MODELS)
    for name in ('prepare', 'delegate', 'connector', 'finish', 'publish', 'ask', 'route_message'):
        assert set(TOOLS[name][1]) == set(ARGUMENT_MODELS[name].model_fields)


def test_repreparation_preserves_resolved_target_metadata(make_task):
    task = make_task()
    account = connection(provider='slack', actions=['slack.post'])
    spec = {'key':'dm','title':'Send report','kind':'connector_effect','target':{'resource':'U123'}}
    outcome = register(task, spec)[0]
    with db.connection() as conn:
        outcomes.bind_operation(conn, service.locked_task(conn, task['id']), outcome['id'], account, 'slack.post', {'channel':'U123','text':'Report'})
    registered = register(task, spec)
    assert registered[0]['target']['connection_id'] == str(account['id'])
    assert registered[0]['revision'] == 1


def test_bad_legacy_question_returns_validation_error_not_server_error(make_task):
    task = make_task()
    with pytest.raises(HTTPException) as error:
        with db.connection() as conn:
            service.request_review(conn, service.locked_task(conn, task['id']), 'Question', {'input_type':'text','answer_type':'unsupported'}, 'input')
    assert error.value.status_code == 422


def test_empty_contract_cannot_be_admitted_as_prepared_work(make_task):
    task = make_task()
    with db.connection() as conn:
        conn.execute('UPDATE work_items SET outcomes_version=1 WHERE id=%s', (task['work_item_id'],))
    with pytest.raises(HTTPException) as error:
        with db.connection() as conn:
            capabilities.prepare(conn, service.locked_task(conn, task['id']), {'skills': [], 'tools': []})
    assert error.value.status_code == 422
    assert 'hermbot_prepare.outcomes' in error.value.detail
    assert row(task['id'])['execution_plan'] == {}


def test_earlier_ready_plan_cannot_bypass_missing_required_results(make_task):
    task = make_task()
    with db.connection() as conn:
        conn.execute('UPDATE work_items SET outcomes_version=1 WHERE id=%s', (task['work_item_id'],))
        fresh = service.locked_task(conn, task['id'])
        view = capabilities.manifest(conn, task['bot_id'], fresh)
        plan = {'ready': True, 'attempt': task['attempts'], 'fingerprint': view['fingerprint']}
        conn.execute('UPDATE tasks SET execution_plan=%s WHERE id=%s', (Jsonb(plan), task['id']))
        checked = capabilities.manifest(conn, task['bot_id'], service.locked_task(conn, task['id']))
        assert checked['outcomes_registration_required'] and checked['preparation_required']
        assert not checked['prepared'] and 'hermbot_delegate' not in checked['allowed_tools']


def test_unmapped_handoff_is_rejected_before_creating_a_helper(make_task):
    task = make_task(manager=True)
    result = register(task, {'key': 'delivery', 'title': 'Deliver the report', 'kind': 'connector_effect'})[0]
    with pytest.raises(HTTPException) as error:
        with db.connection() as conn:
            service.delegate(conn, service.locked_task(conn, task['id']), 'writer', 'Prepare a delivery draft')
    assert error.value.status_code == 422 and 'outcome_ids' in error.value.detail
    with db.connection() as conn:
        assert not conn.execute('SELECT 1 FROM tasks WHERE parent_id=%s', (task['id'],)).fetchone()
        assert service.locked_task(conn, task['id'])['handoffs'] == 0
        child = service.delegate(conn, service.locked_task(conn, task['id']), 'writer',
                                 'Prepare a delivery draft', outcome_ids=[result['id']])
        helper = service.locked_task(conn, child['task_id'])
        assert not outcomes.registration_required(conn, helper)
        assert outcomes.context(conn, helper)[0]['id'] == result['id']


def test_upgraded_legacy_work_registers_and_hands_off_a_review_to_connected_peer(make_task):
    task = make_task(bot='reviewer', manager=True)
    account = connection(provider='slack', actions=['slack.post'])
    with db.connection() as conn:
        conn.execute('UPDATE work_items SET outcomes_version=1 WHERE id=%s', (task['work_item_id'],))
        conn.execute('UPDATE connections SET bot_ids=%s WHERE id=%s', (Jsonb(['writer']), account['id']))
        conn.execute('UPDATE bots SET readiness=%s',
                     (Jsonb({'compatible': True, 'inventory': {'version': 1, 'outcomes_version': 1,
                                                               'toolsets': [], 'skills': []}}),))
        prepared = capabilities.prepare(conn, service.locked_task(conn, task['id']),
                                        {'skills': [], 'tools': ['hermbot_delegate'],
                                         'outcomes': [OutcomeSpec(key='delivery', title='Slack delivery',
                                                                 kind='connector_effect',
                                                                 target={'resource': 'U123'}).model_dump()]})
        assert prepared['ready']
        outcome = prepared['outcomes'][0]
        child = service.delegate(conn, service.locked_task(conn, task['id']), 'writer',
                                 'Prepare an exact Slack review', outcome_ids=[outcome['id']])
        helper = service.locked_task(conn, child['task_id'])
        helper_plan = capabilities.prepare(conn, helper, {'skills': [], 'tools': ['hermbot_review'],
                                                        'connectors': [{'connection_id': account['id'],
                                                                        'action': 'slack.post'}]})
        assert helper_plan['ready'] and helper_plan['outcomes'][0]['id'] == outcome['id']
        draft = service.request_review(conn, helper, 'Send the report',
                                       {'connection_id': str(account['id']), 'action': 'slack.post',
                                        'arguments': {'channel': 'U123', 'text': 'Prepared report'},
                                        'outcome_id': str(outcome['id'])}, 'connector')
        operation = conn.execute('SELECT * FROM connector_operations WHERE approval_id=%s',
                                 (draft['approval_id'],)).fetchone()
        assert operation['status'] == 'pending' and operation['outcome_id'] == outcome['id']
        assert operation['bot_id'] == 'writer' and operation['result'] is None
