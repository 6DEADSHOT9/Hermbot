"""Synthetic routing and authentication failures; never call the owner's model."""
import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError
from fastapi import HTTPException

from backend import db, inputs, model_auth, model_client, service, work_intake
from backend.models import AskArgs
from tests.test_model_auth import oauth_provider
from tests.test_work_controller import message, work


def evidence():
    return {'latest_owner_message': {'content': 'Synthetic greeting'}, 'background_context': {'work': []},
            'schema': work_intake.Interpretation.model_json_schema()}


def replies(monkeypatch, values):
    requests = []
    async def complete(provider, payload, **kwargs):
        requests.append(json.loads(json.dumps(payload)))
        value = values[min(len(requests) - 1, len(values) - 1)]
        return {'choices': [{'message': value}]}
    monkeypatch.setattr(model_client, 'async_completion', complete)
    return requests


def final(**operation):
    return {'role': 'assistant', 'content': json.dumps({'operations': [operation or {'intent': 'discussion'}]})}


def lookup(arguments):
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'fixture-call', 'type': 'function', 'function': {'name': 'read_context', 'arguments': arguments}}]}


def interpret(**kwargs):
    return asyncio.run(work_intake.interpret(base_url='http://fixture.invalid', api_key='', model='fixture',
                       evidence=evidence(), provider={'auth_kind': 'codex_oauth'}, **kwargs))


def answer_intake(conn, original, text):
    asked = inputs.ask(conn, service.locked_task(conn, original['id']), AskArgs(prompt='Which format?').model_dump())
    question = conn.execute('SELECT * FROM input_requests WHERE id=%s', (asked['input_request_id'],)).fetchone()
    inputs.answer(conn, question['id'], {'answer': text, 'expected_question_hash': question['question_hash']})
    intake = conn.execute('SELECT * FROM tasks WHERE request_key=%s', ('question-answer:' + str(question['id']),)).fetchone()
    return question, intake


def test_actual_context_keeps_the_full_schema(make_task):
    task = make_task()
    with db.connection() as conn:
        value = work_intake.context(conn, task)
    assert value['schema'] == work_intake.Interpretation.model_json_schema()


def test_answer_preserves_independent_assignment():
    original = work()
    text = 'Use Markdown. Separately, create a new budget spreadsheet.'
    with db.connection() as conn:
        question, intake = answer_intake(conn, original, text)
        value = work_intake.Interpretation.model_validate({'operations': [
            {'intent': 'answer_question', 'input_request_id': str(question['id']), 'answer': text},
            {'intent': 'assignment', 'title': 'Budget spreadsheet', 'requirements': [{'key': 'budget', 'title': 'Budget spreadsheet', 'kind': 'artifact'}]}]})
        work_intake.apply(conn, intake['id'], value)
        work_intake.apply(conn, intake['id'], value)
        assert conn.execute('SELECT count(*) AS n FROM work_items').fetchone()['n'] == 2
        assert conn.execute("SELECT work_item_id FROM work_outcomes WHERE key='budget'").fetchone()['work_item_id'] != original['work_item_id']


def test_answer_cancels_only_the_named_work():
    original, separate = work(), work('Create a separate budget')
    text = 'Use Markdown. Cancel the separate budget task.'
    with db.connection() as conn:
        question, intake = answer_intake(conn, original, text)
        value = work_intake.Interpretation.model_validate({'operations': [
            {'intent': 'answer_question', 'input_request_id': str(question['id']), 'answer': text},
            {'intent': 'cancel', 'work_item_id': str(separate['work_item_id'])}]})
        work_intake.apply(conn, intake['id'], value)
        assert service.locked_task(conn, original['id'])['status'] == 'queued'
        assert service.locked_task(conn, separate['id'])['status'] == 'cancelled'


def test_scoped_edit_defaults_only_the_first_unidentified_assignment():
    original = work()
    intake = message('Revise the report. Also create a separate budget.')
    with db.connection() as conn:
        from psycopg.types.json import Jsonb
        conn.execute('UPDATE tasks SET handoff_context=%s WHERE id=%s', (Jsonb({'owner_work_target': str(original['work_item_id'])}), intake['id']))
        value = work_intake.Interpretation.model_validate({'operations': [
            {'intent': 'assignment', 'requirements': [{'key': 'report', 'title': 'Revised report', 'kind': 'report'}]},
            {'intent': 'assignment', 'requirements': [{'key': 'budget', 'title': 'Budget', 'kind': 'artifact'}]}]})
        work_intake.apply(conn, intake['id'], value)
        assert conn.execute('SELECT count(*) AS n FROM work_items').fetchone()['n'] == 2
        assert conn.execute("SELECT work_item_id FROM work_outcomes WHERE key='budget'").fetchone()['work_item_id'] != original['work_item_id']
        assert value.operations[0].intent == 'assignment'


@pytest.mark.parametrize('arguments', ['{"before_message_id":"not-a-number"}', '{', '{"work_item_id":"not-a-uuid"}'])
def test_lookup_argument_errors_are_repaired(monkeypatch, arguments):
    requests = replies(monkeypatch, [lookup(arguments), lookup('{"query":"earlier"}'), final()])
    lookups = []
    def read(args):
        lookups.append(args)
        return {'work': None, 'messages': []}
    result = interpret(lookup=read)
    assert result.operations[0].intent == 'discussion'
    assert len(lookups) == 1 and lookups[0]['query'] == 'earlier'
    repair = requests[1]['messages'][-1]
    assert repair['role'] == 'tool' and repair['tool_call_id'] == 'fixture-call'
    assert json.loads(repair['content'])['error'] == 'invalid_lookup_arguments'


def test_empty_assignment_is_corrected_before_application(monkeypatch):
    requests = replies(monkeypatch, [final(intent='assignment', title='Report'),
                       final(intent='assignment', title='Report', requirements=[{'key': 'report', 'title': 'Report', 'kind': 'report'}])])
    result = interpret()
    assert result.operations[0].requirements[0].key == 'report'
    assert len(requests) == 2
    assert 'concrete required results' in requests[1]['messages'][-1]['content']
    with pytest.raises(ValidationError):
        work_intake.Interpretation.model_validate({'operations': [{'intent': 'assignment'}]})


def test_invalid_output_is_not_reported_as_missing_context(monkeypatch):
    replies(monkeypatch, [{'role': 'assistant', 'content': '{"private-field-canary":"private-value-canary"}'}])
    with pytest.raises(work_intake.InterpretationError) as caught:
        interpret()
    error = caught.value
    assert error.code == 'invalid_output'
    assert 'more context' not in str(error)
    assert 'private-field-canary' not in json.dumps(error.validation)
    assert 'private-value-canary' not in json.dumps(error.validation)
    assert any('operations' in detail['loc'] for detail in error.validation)


def test_lookup_exhaustion_is_distinct_from_argument_failure(monkeypatch):
    replies(monkeypatch, [lookup('{}')])
    with pytest.raises(work_intake.InterpretationError) as caught:
        interpret(lookup=lambda args: {'work': None, 'messages': []})
    assert caught.value.code == 'lookup_exhausted'


def test_lookup_validation_exhaustion_is_distinct(monkeypatch):
    replies(monkeypatch, [lookup('{"before_message_id":0}')])
    with pytest.raises(work_intake.InterpretationError) as caught:
        interpret(lookup=lambda args: pytest.fail('Invalid arguments must not be used'))
    assert caught.value.code == 'invalid_lookup_arguments'


def test_failed_intake_records_safe_cause_without_blind_outer_retries(monkeypatch):
    task = message('Synthetic greeting')
    provider = oauth_provider()
    with db.connection() as conn:
        previous = conn.execute('SELECT provider_id FROM bots WHERE id=%s', (task['bot_id'],)).fetchone()['provider_id']
        conn.execute('UPDATE bots SET provider_id=%s WHERE id=%s', (provider['id'], task['bot_id']))
    requests = replies(monkeypatch, [{'role': 'assistant', 'content': '{"private-field-canary":"private-value-canary"}'}])
    try:
        assert asyncio.run(work_intake.advance_one())
        assert not asyncio.run(work_intake.advance_one())
        assert len(requests) == 4
    finally:
        with db.connection() as conn:
            conn.execute('UPDATE bots SET provider_id=%s WHERE id=%s', (previous, task['bot_id']))
    with db.connection() as conn:
        saved = service.locked_task(conn, task['id'])
        event = conn.execute("SELECT data FROM events WHERE task_id=%s AND kind='message.interpretation_failed'", (task['id'],)).fetchone()['data']
    assert saved['intake_status'] == 'failed' and saved['intake_attempts'] == 1
    assert event['code'] == 'invalid_output' and event['stage'] == 'interpretation'
    assert 'private-field-canary' not in json.dumps(event)
    assert 'private-value-canary' not in json.dumps(event)


@pytest.mark.parametrize('code', ['invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused'])
def test_terminal_refresh_failure_requires_reconnect(code):
    provider = oauth_provider()
    model_auth.record_failure(provider['id'], model_auth.BrokerFailure(code), provider['auth_epoch'])
    saved = model_auth.provider(provider['id'])
    assert saved['connection_status'] == 'reauth_required' and not saved['plan_enabled']
    response = httpx.Response(503, request=httpx.Request('POST', 'http://fixture.invalid'), json={'detail': code})
    with pytest.raises(model_client.ModelConnectionError, match='Reconnect') as caught:
        model_client.result(response)
    assert caught.value.code == code


def test_stale_refresh_failure_cannot_disconnect_a_new_login():
    provider = oauth_provider()
    model_auth.record_failure(provider['id'], model_auth.BrokerFailure('token_expired'), provider['auth_epoch'] - 1)
    assert model_auth.provider(provider['id'])['connection_status'] == 'connected'


def test_unknown_provider_error_stays_private():
    error = model_client.ModelConnectionError('private-provider-canary')
    assert error.code == 'request_failed'
    assert 'private-provider-canary' not in str(error)


@pytest.mark.parametrize('intent', ['update', 'cancel'])
def test_scoped_edit_keeps_explicit_other_work_target(intent):
    original, separate = work(), work('Separate report')
    intake = message('Change the separate report')
    with db.connection() as conn:
        from psycopg.types.json import Jsonb
        conn.execute('UPDATE tasks SET handoff_context=%s WHERE id=%s', (Jsonb({'owner_work_target': str(original['work_item_id'])}), intake['id']))
        value = work_intake.Interpretation.model_validate({'operations': [{'intent': intent, 'work_item_id': str(separate['work_item_id']), 'objective': 'Revised report'}]})
        work_intake.apply(conn, intake['id'], value)
        assert conn.execute('SELECT revision FROM work_items WHERE id=%s', (original['work_item_id'],)).fetchone()['revision'] == 1
        assert service.locked_task(conn, intake['id'])['work_item_id'] == separate['work_item_id']


@pytest.mark.parametrize('reply,code', [
    ({'role': 'assistant', 'refusal': 'private-refusal-canary'}, 'model_refused'),
    ({'role': 'assistant', 'tool_calls': [{'id': 'fixture-call', 'function': {'name': 'execute', 'arguments': '{}'}}]}, 'unexpected_lookup'),
    ({'role': 'assistant', 'tool_calls': [{'function': {'name': 'read_context', 'arguments': '{}'}}]}, 'unexpected_lookup'),
])
def test_unsupported_model_replies_never_execute_lookups(monkeypatch, reply, code):
    replies(monkeypatch, [reply])
    with pytest.raises(work_intake.InterpretationError) as caught:
        interpret(lookup=lambda args: pytest.fail('This reply cannot authorize a lookup'))
    assert caught.value.code == code
    assert 'private-refusal-canary' not in str(caught.value)


def test_api_key_intake_uses_the_same_lookup_repair(monkeypatch):
    requests = []
    original = httpx.AsyncClient
    def respond(request):
        requests.append(json.loads(request.content))
        reply = lookup('{"before_message_id":0}') if len(requests) == 1 else final()
        return httpx.Response(200, json={'choices': [{'message': reply}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    result = asyncio.run(work_intake.interpret(base_url='http://fixture.invalid', api_key='synthetic', model='fixture',
                         evidence=evidence(), lookup=lambda args: pytest.fail('Invalid arguments cannot execute')))
    assert result.operations[0].intent == 'discussion'
    assert json.loads(requests[1]['messages'][-1]['content'])['error'] == 'invalid_lookup_arguments'


@pytest.mark.parametrize('application_error', [False, True])
def test_failure_events_distinguish_timeouts_and_application_rejection(monkeypatch, application_error):
    task = message('Synthetic request')
    async def complete(**kwargs):
        if not application_error:
            raise TimeoutError('private-timeout-canary')
        return work_intake.Interpretation.model_validate({'operations': [{'intent': 'discussion'}]})
    def reject(conn, task_id, *args, **kwargs):
        conn.execute("UPDATE tasks SET continuation='uncommitted' WHERE id=%s", (task_id,))
        raise HTTPException(422, 'private-rejection-canary')
    monkeypatch.setattr(work_intake, 'interpret', complete)
    monkeypatch.setattr(work_intake, 'apply', reject)
    with db.connection() as conn:
        conn.execute('UPDATE tasks SET intake_attempts=2 WHERE id=%s', (task['id'],))
    assert asyncio.run(work_intake.advance_one())
    with db.connection() as conn:
        saved = service.locked_task(conn, task['id'])
        event = conn.execute("SELECT data FROM events WHERE task_id=%s AND kind='message.interpretation_failed'", (task['id'],)).fetchone()['data']
    assert saved['intake_status'] == 'failed' and saved['continuation'] != 'uncommitted'
    assert event['stage'] == ('application' if application_error else 'interpretation')
    assert event['code'] == ('application_rejected' if application_error else 'timeout')
    assert event['application_status'] == (422 if application_error else None)
    assert 'private-' not in json.dumps(event) and 'private-' not in saved['error']
