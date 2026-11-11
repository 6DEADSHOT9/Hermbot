"""Communication, required inputs and accepted deliverables have distinct effects."""
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend import app as application, connections, coordination, db, outcomes, service, worker, work_controller
from backend.artifacts import ArtifactStore
from backend.models import OutcomeSpec
from backend.settings import settings
from tests.conftest import row
from tests.test_phase4 import connection
from tests.test_runtime_gate import load


@pytest.fixture
def work():
    with db.connection() as conn:
        identity = uuid4()
        conn.execute("INSERT INTO conversations(id,title,kind) VALUES(%s,'Coordination test','group')", (identity,))
        for bot in ('researcher', 'writer', 'reviewer'):
            conn.execute('INSERT INTO participants VALUES(%s,%s)', (identity, bot))
        task = service.create_task(conn, bot_id='researcher', conversation_id=identity, prompt='Combine two researched contributions into a report', request_key=str(uuid4()), orchestration_version=2, coordination_version=1)
        outcomes.register(conn, task, [OutcomeSpec(key='report', title='Combined report', kind='report').model_dump()], owner_revision=True)
        task = service.locked_task(conn, task['id'])
    return task


def invoke(attempt, operation, args, key=None):
    with db.connection() as conn:
        return service.tool_action(conn, attempt['bot_id'], attempt['session_id'], operation, args, key or str(uuid4()))


def delegate(work, bot='writer', **values):
    with db.connection() as conn:
        result = service.delegate(conn, service.locked_task(conn, work['id']), bot, 'Research one defined part', expected_output='One evidenced contribution', **values)
    return UUID(result['task_id'])


def finish(attempt, *, reviewed=(), review='', artifact_id=None):
    return invoke(attempt, 'finish', {'result': 'A substantive researched result', 'results': [{'key': 'report', 'status': 'fulfilled', 'review': review, 'reviewed_task_ids': list(reviewed), **({'artifact_id': artifact_id} if artifact_id else {})}]})


def complete(attempt):
    worker.settle(attempt['id'], {'status': 'completed', 'output': 'A substantive researched result'})


def requirement(work):
    with db.connection() as conn:
        return outcomes.rows(conn, work['work_item_id'])[0]


def test_message_does_not_create_assignment_wait_or_handoff(work):
    attempt = worker.claim_new()[0]
    result = invoke(attempt, 'message', {'bot_id': 'writer', 'prompt': 'Which area are you covering?'}, 'question')
    assert invoke(attempt, 'message', {'bot_id': 'writer', 'prompt': 'Which area are you covering?'}, 'question') == result
    assert row(work['id'])['status'] == 'running'
    assert row(work['id'])['handoffs'] == 0
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM teammate_messages').fetchone()['n'] == 1
    complete(attempt)
    assert row(work['id'])['status'] == 'completed'


def test_busy_recipient_receives_message_without_second_execution(work):
    child = delegate(work)
    attempts = worker.claim_new()
    parent = next(a for a in attempts if a['bot_id'] == 'researcher')
    target = next(a for a in attempts if a['bot_id'] == 'writer')
    message = invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Please share your coverage'})
    assert worker.claim_new() == []
    with db.connection() as conn:
        task = service.locked_task(conn, child)
        inbox = coordination.inbox(conn, task)
        assert str(inbox[0]['id']) == message['message_id']
        assert inbox[0]['status'] == 'pending'
        coordination.delivered(conn, task, [inbox[0]['id']])
        coordination.delivered(conn, task, [inbox[0]['id']])
        assert coordination.inbox(conn, task)[0]['status'] == 'delivered'
        assert conn.execute("SELECT count(*) AS n FROM events WHERE kind='teammate.message_delivered'").fetchone()['n'] == 1
    assert row(child)['status'] == 'running'
    assert target['id']


def test_idle_response_is_internal_and_reply_does_not_spawn_reply_loop(work):
    parent = worker.claim_new()[0]
    message = invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'What is your expertise?'})
    response = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    task = row(response['task_id'])
    assert task['execution_kind'] == 'peer_message'
    assert task['parent_id'] is None
    assert task['grant_id'] is None
    invoke(response, 'message', {'bot_id': 'researcher', 'prompt': 'Writing and synthesis', 'reply_to': UUID(message['message_id'])})
    complete(response)
    complete(parent)
    assert worker.claim_new() == []
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM work_items').fetchone()['n'] == 1


def test_required_reply_releases_slot_and_resumes_once(work):
    parent = worker.claim_new()[0]
    message = invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Which topic?'})
    invoke(parent, 'wait', {'message_ids': [UUID(message['message_id'])]})
    complete(parent)
    assert row(work['id'])['status'] == 'waiting_results'
    response = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    invoke(response, 'message', {'bot_id': 'researcher', 'prompt': 'Topic A', 'reply_to': UUID(message['message_id'])})
    complete(response)
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued'
    assert 'Topic A' in row(work['id'])['continuation']
    resumed = worker.claim_new()[0]
    complete(resumed)
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'completed'


def test_forged_reply_and_delivery_are_rejected(work):
    parent = worker.claim_new()[0]
    message = invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Question'})
    with pytest.raises(HTTPException):
        invoke(parent, 'message', {'bot_id': 'reviewer', 'prompt': 'Forged', 'reply_to': UUID(message['message_id'])})
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            coordination.delivered(conn, row(work['id']), [UUID(message['message_id'])])


def test_contribution_cannot_complete_shared_report(work):
    child_id = delegate(work)
    attempts = worker.claim_new()
    child = next(a for a in attempts if a['task_id'] == child_id)
    finish(child)
    complete(child)
    assert requirement(work)['status'] == 'pending'
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM result_contributions WHERE task_id=%s', (child_id,)).fetchone()['status'] == 'submitted'
        assert conn.execute('SELECT count(*) AS n FROM result_acceptances').fetchone()['n'] == 0


def test_parent_continues_after_delegation_then_waits_for_required_results(work):
    parent = worker.claim_new()[0]
    child_id = delegate(work)
    assert row(work['id'])['status'] == 'running'
    with pytest.raises(HTTPException) as error:
        finish(parent, review='Both contributions combined')
    assert error.value.status_code == 409
    complete(parent)
    assert row(work['id'])['status'] == 'waiting_results'
    child = next(a for a in worker.claim_new() if a['task_id'] == child_id)
    finish(child)
    complete(child)
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued'
    resumed = worker.claim_new()[0]
    finish(resumed, reviewed=[child_id], review='Inspected the contribution and combined its findings against the report request')
    complete(resumed)
    assert row(work['id'])['status'] == 'completed'
    assert requirement(work)['status'] == 'fulfilled'


def test_one_of_two_contributions_cannot_release_final_join(work):
    a, b = delegate(work), delegate(work, 'reviewer')
    attempts = worker.claim_new()
    parent = next(t for t in attempts if t['task_id'] == work['id'])
    complete(parent)
    first = next(t for t in attempts if t['task_id'] == a)
    finish(first)
    complete(first)
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'waiting_results'
    assert requirement(work)['status'] == 'pending'
    second = next(t for t in attempts if t['task_id'] == b)
    finish(second)
    complete(second)
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued'


def test_optional_contribution_does_not_hold_final_result_open(work):
    delegate(work, required=False)
    parent = next(a for a in worker.claim_new() if a['task_id'] == work['id'])
    finish(parent, review='The completed report satisfies the request; optional additional evidence is unnecessary')
    complete(parent)
    assert requirement(work)['status'] == 'fulfilled'
    assert row(work['id'])['status'] == 'completed'


def test_deliverable_transfer_has_one_accountable_assignment(work):
    child_id = delegate(work, responsibility='deliverable')
    assert requirement(work)['owner_task_id'] == child_id
    attempts = worker.claim_new()
    parent = next(a for a in attempts if a['task_id'] == work['id'])
    with pytest.raises(HTTPException):
        finish(parent, review='I claim the transferred result')
    child = next(a for a in attempts if a['task_id'] == child_id)
    finish(child, review='The entire requested report is complete')
    complete(child)
    assert requirement(work)['status'] == 'fulfilled'


def test_failed_required_contribution_wakes_owner_without_claiming_success(work):
    child_id = delegate(work)
    attempts = worker.claim_new()
    complete(next(a for a in attempts if a['task_id'] == work['id']))
    worker.settle(next(a for a in attempts if a['task_id'] == child_id)['id'], {'status': 'failed', 'error': 'Source unavailable'})
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued'
    assert requirement(work)['status'] != 'fulfilled'
    assert 'failed' in row(work['id'])['continuation']


def test_empty_and_unrelated_waits_are_rejected(work):
    parent = worker.claim_new()[0]
    for args in ({}, {'task_ids': [uuid4()]}, {'message_ids': [uuid4()]}):
        with pytest.raises(HTTPException):
            invoke(parent, 'wait', args)


def test_stale_submission_cannot_fulfill_revised_requirement(work):
    parent = worker.claim_new()[0]
    finish(parent, review='Reviewed current requirement')
    with db.connection() as conn:
        conn.execute('UPDATE work_outcomes SET revision=revision+1 WHERE work_item_id=%s', (work['work_item_id'],))
    complete(parent)
    assert requirement(work)['status'] == 'pending'
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM result_acceptances').fetchone()['n'] == 0


def test_acceptance_is_deduplicated_and_retains_review(work):
    parent = worker.claim_new()[0]
    finish(parent, review='Checked report scope, completeness and supporting evidence')
    complete(parent)
    complete(parent)
    with db.connection() as conn:
        accepted = conn.execute('SELECT * FROM result_acceptances').fetchall()
        assert len(accepted) == 1
        assert accepted[0]['outcome_revision'] == 1
        assert accepted[0]['evidence']['review'].startswith('Checked report')
        assert accepted[0]['evidence']['work_objective'] == work['input']


def test_normal_message_api_enables_new_semantics(work):
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    try:
        response = client.post('/api/v1/messages', headers={'Idempotency-Key': str(uuid4())}, json={'bot_id': 'researcher', 'conversation_id': str(work['conversation_id']), 'prompt': 'Discuss a new topic'})
        assert response.status_code == 202
        assert response.json()['coordination_version'] == 1
    finally:
        client.close()


@pytest.fixture
def store():
    storage = ArtifactStore()
    storage.initialize()
    yield storage
    with db.connection() as conn:
        keys = [r['object_key'] for r in conn.execute('SELECT object_key FROM artifacts')]
    for key in keys:
        storage.client.delete_object(Bucket=storage.bucket, Key=key)


def publish(attempt, store, content='An evidenced research contribution'):
    with db.connection() as conn:
        return service.tool_action(conn, attempt['bot_id'], attempt['session_id'], 'publish', {'name': 'contribution.txt', 'content': content, 'outcome_key': 'report'}, str(uuid4()), store)


def test_publication_is_evidence_and_acceptance_binds_exact_artifact(work, store):
    parent = worker.claim_new()[0]
    artifact = publish(parent, store)
    assert artifact['fulfills_requirement'] is False
    assert requirement(work)['status'] == 'pending'
    finish(parent, review='Read the authored report and checked its findings against the current request', artifact_id=UUID(artifact['artifact_id']))
    complete(parent)
    with db.connection() as conn:
        accepted = conn.execute('SELECT evidence FROM result_acceptances').fetchone()['evidence']
        assert accepted['artifact_sha256'] == artifact['sha256']
        assert accepted['artifact_version'] == artifact['version']


def test_contribution_artifact_must_be_inspected_before_acceptance(work, store):
    child_id = delegate(work)
    attempts = worker.claim_new()
    parent = next(a for a in attempts if a['task_id'] == work['id'])
    child = next(a for a in attempts if a['task_id'] == child_id)
    artifact = publish(child, store)
    finish(child, artifact_id=UUID(artifact['artifact_id']))
    complete(child)
    with pytest.raises(HTTPException) as error:
        finish(parent, reviewed=[child_id], review='Claimed inspection without reading the file')
    assert error.value.status_code == 409
    with db.connection() as conn:
        read = service.tool_action(conn, parent['bot_id'], parent['session_id'], 'read_artifact', {'artifact_id': UUID(artifact['artifact_id'])}, str(uuid4()), store)
        assert 'evidenced research contribution' in read['content']
    finish(parent, reviewed=[child_id], review='Inspected the actual contribution and combined it into the requested report')
    complete(parent)
    assert requirement(work)['status'] == 'fulfilled'


def test_missing_review_cannot_accept_a_file(work, store):
    parent = worker.claim_new()[0]
    artifact = publish(parent, store)
    with pytest.raises(HTTPException) as error:
        finish(parent, artifact_id=UUID(artifact['artifact_id']))
    assert error.value.status_code == 422
    assert requirement(work)['status'] == 'pending'


def test_completed_execution_without_submission_does_not_release_dependency(work):
    a = delegate(work)
    b = delegate(work, 'reviewer', depends_on=[a])
    attempts = worker.claim_new()
    child = next(t for t in attempts if t['task_id'] == a)
    complete(child)
    worker.advance_waiters()
    assert row(b)['status'] == 'blocked'


def test_owner_revision_creates_current_contribution_binding(work):
    child_id = delegate(work)
    with db.connection() as conn:
        outcome = outcomes.rows(conn, work['work_item_id'])[0]
        conn.execute('UPDATE work_outcomes SET revision=2 WHERE id=%s', (outcome['id'],))
        item = conn.execute('UPDATE work_items SET revision=2 WHERE id=%s RETURNING *', (work['work_item_id'],)).fetchone()
        work_controller.command(conn, item, child_id, 'revise', {'instruction': 'Include the revised subject', 'source_message_id': work['owner_message_id']})
        branch = conn.execute('SELECT * FROM result_contributions WHERE task_id=%s AND outcome_revision=2', (child_id,)).fetchone()
        assert branch['status'] == 'pending'
        assert branch['required']


def test_successful_provider_receipt_survives_unreviewed_contribution(work):
    account = connection(provider='slack', actions=['slack.post'])
    with db.connection() as conn:
        outcome = outcomes.register(conn, work, [OutcomeSpec(key='delivery', title='Send final report', kind='connector_effect', target={'connection_id': account['id'], 'action': 'slack.post', 'resource': 'U123'}).model_dump()], owner_revision=True)[-1]
        child = service.delegate(conn, work, 'writer', 'Prepare final message', outcome_ids=[outcome['id']], expected_output='The text to send')
        result = connections.invoke(conn, work, {'connection_id': account['id'], 'action': 'slack.post', 'arguments': {'channel': 'U123', 'text': 'Prepared report'}, 'outcome_id': outcome['id']})
        operation = conn.execute('SELECT * FROM connector_operations WHERE approval_id=%s', (result['approval_id'],)).fetchone()
        connections.finish_operation(conn, operation, 'succeeded', result={'ok': True, 'channel': 'D123', 'ts': '1.2'})
        receipt = conn.execute('SELECT status,result FROM connector_operations WHERE id=%s', (operation['id'],)).fetchone()
        assert receipt['status'] == 'succeeded'
        assert receipt['result']['ts'] == '1.2'
        assert outcomes.rows(conn, work['work_item_id'])[-1]['status'] == 'pending'
        assert child['task_id']


def test_inbox_is_scoped_to_conversation_and_work(work):
    parent = worker.claim_new()[0]
    message = invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Scoped information'})
    with db.connection() as conn:
        other_conversation = conn.execute("SELECT id FROM conversations WHERE bot_id='writer'").fetchone()['id']
        other = service.create_task(conn, bot_id='writer', conversation_id=other_conversation, prompt='Other work', request_key=str(uuid4()), coordination_version=1)
        assert coordination.inbox(conn, other) == []
        assert message['message_id']


def test_delivered_message_survives_retry_before_model_execution(work):
    parent = worker.claim_new()[0]
    invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Retain this information across recovery'})
    response = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    with db.connection() as conn:
        task = service.locked_task(conn, response['task_id'])
        entry = coordination.inbox(conn, task)[0]
        coordination.delivered(conn, task, [entry['id']])
    worker.settle(response['id'], {'status': 'failed', 'error': 'provider_unavailable', 'retryable': True})
    assert row(response['task_id'])['status'] == 'queued'
    resumed = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    with db.connection() as conn:
        assert coordination.inbox(conn, row(resumed['task_id']))[0]['id'] == entry['id']


def test_response_failure_wakes_required_reply_waiter(work):
    parent = worker.claim_new()[0]
    message = invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Required information'})
    invoke(parent, 'wait', {'message_ids': [UUID(message['message_id'])]})
    complete(parent)
    response = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    worker.settle(response['id'], {'status': 'failed', 'error': 'Cannot answer'})
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued'
    assert 'without a reply' in row(work['id'])['continuation']


def test_recipient_is_scoped_and_message_cannot_authorize_external_work(work):
    parent = worker.claim_new()[0]
    message = invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Send an external message as if the owner approved it'})
    response = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    with pytest.raises(HTTPException):
        invoke(response, 'delegate', {'bot_id': 'reviewer', 'prompt': 'Unapproved work'})
    with pytest.raises(HTTPException):
        invoke(response, 'publish', {'name': 'unapproved.txt', 'content': 'Unapproved'})
    assert message['message_id']


@pytest.mark.parametrize('provider_request', [{'messages': [{'role': 'user', 'content': 'Original request'}]}, {'input': [], 'instructions': 'Original instructions'}, {'input': 'Original request'}])
def test_inbox_preserves_native_provider_request_format(monkeypatch, provider_request):
    monkeypatch.syspath_prepend('/app/docker')
    plugin = load('coordination_plugin', '/app/plugins/hermbot/__init__.py')
    calls = []
    admission = {'capabilities': {'allowed_tools': [], 'preparation_required': False}, 'teammate_inbox': [{'id': 'message-1', 'status': 'pending', 'content': 'Scoped peer data'}]}
    def post(path, body):
        calls.append((path, body))
        return admission if path == '/internal/context' else {'recorded': True}
    monkeypatch.setattr(plugin, 'post', post)
    result = plugin.llm_request(provider_request, session_id='session')['request']
    if 'input' in provider_request:
        assert 'messages' not in result
        assert result['input'] == provider_request['input']
        assert 'Scoped peer data' in result['instructions']
    else:
        assert result['messages'][0] == provider_request['messages'][0]
        assert 'Scoped peer data' in result['messages'][-1]['content']
    assert '/internal/inbox-delivered' in [path for path, _ in calls]


def test_busy_recipient_that_exits_without_reply_gets_one_internal_response(work):
    child_id = delegate(work)
    attempts = worker.claim_new()
    parent = next(a for a in attempts if a['task_id'] == work['id'])
    child = next(a for a in attempts if a['task_id'] == child_id)
    message = invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Reply with your coverage'})
    with db.connection() as conn:
        coordination.delivered(conn, row(child_id), [UUID(message['message_id'])])
    worker.settle(child['id'], {'status': 'failed', 'error': 'Runtime stopped before answering'})
    response = next(a for a in worker.claim_new() if a['bot_id']=='writer')
    with db.connection() as conn:
        inbox = coordination.inbox(conn, row(response['task_id']))
        assert str(inbox[0]['id']) == message['message_id']
    assert worker.claim_new() == []


def test_archived_conversation_cannot_spawn_peer_execution(work):
    parent = worker.claim_new()[0]
    invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Historical message'})
    complete(parent)
    with db.connection() as conn:
        conn.execute('UPDATE conversations SET archived_at=now() WHERE id=%s', (work['conversation_id'],))
    assert worker.claim_new() == []


def test_failed_contribution_can_be_replaced_without_making_it_optional(work):
    original = delegate(work)
    attempts = worker.claim_new()
    worker.settle(next(a for a in attempts if a['task_id']==original)['id'], {'status': 'failed', 'error': 'Failed contribution'})
    replacement = delegate(work, replaces_task_id=original, required=False)
    with db.connection() as conn:
        previous = conn.execute('SELECT * FROM result_contributions WHERE task_id=%s', (original,)).fetchone()
        current = conn.execute('SELECT * FROM result_contributions WHERE task_id=%s', (replacement,)).fetchone()
        assert previous['status'] == 'superseded' and not previous['required']
        assert current['required']
    assert requirement(work)['status'] == 'pending'
    with pytest.raises(HTTPException):
        delegate(work, replaces_task_id=original)


def test_running_contribution_cannot_be_replaced(work):
    original = delegate(work)
    worker.claim_new()
    with pytest.raises(HTTPException):
        delegate(work, replaces_task_id=original)


# A message nobody can ever answer is released with its reason; one that keeps failing is given up and reported.

def other_work(work, bot='reviewer'):
    with db.connection() as conn:
        return service.create_task(conn, bot_id=bot, conversation_id=work['conversation_id'], prompt='Unrelated work for another teammate', request_key=str(uuid4()))


def waiting_on_writer(work):
    parent = worker.claim_new()[0]
    message = invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Which topic?'})
    invoke(parent, 'wait', {'message_ids': [UUID(message['message_id'])]})
    complete(parent)
    assert row(work['id'])['status'] == 'waiting_results'
    return UUID(message['message_id'])


def message_row(identity):
    with db.connection() as conn:
        return conn.execute('SELECT * FROM teammate_messages WHERE id=%s', (identity,)).fetchone()


def events(kind):
    with db.connection() as conn:
        return conn.execute('SELECT data FROM events WHERE kind=%s ORDER BY id', (kind,)).fetchall()


def set_writer(status):  # The test baseline restores every seeded bot before the next test.
    with db.connection() as conn:
        conn.execute('UPDATE bots SET status=%s WHERE id=%s', (status, 'writer'))


def name(bot):
    with db.connection() as conn:
        return conn.execute('SELECT name FROM bots WHERE id=%s', (bot,)).fetchone()['name']


@pytest.mark.parametrize('status,reason', [('deleted', '{} was deleted'), ('archived', '{} is archived')])
def test_a_message_to_a_teammate_who_is_gone_bounces_and_dispatch_continues(work, status, reason):
    message = waiting_on_writer(work)
    set_writer(status)
    unrelated = other_work(work)
    assert [a['task_id'] for a in worker.claim_new()] == [unrelated['id']], 'Another bot still gets its work'
    expected = reason.format(name('writer'))
    bounced = message_row(message)
    assert (bounced['status'], bounced['released'], bounced['reason'], bounced['attempts']) == ('cancelled', 'bounced', expected, 0)
    assert [e['data']['reason'] for e in events('teammate.message_bounced')] == [expected]
    assert events('teammate.message_failed') == []
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued', 'The waiting sender is woken'
    assert expected in row(work['id'])['continuation'], 'and told the real reason'


def test_archiving_a_teammate_bounces_its_messages_at_that_moment(work, monkeypatch):
    from backend import provisioning, registry
    from backend.models import LifecycleRequest
    message = waiting_on_writer(work)
    with db.connection() as conn:
        writer = registry.require_bot(conn, 'writer')
        operation = registry.owner_operation(conn, 'writer', 'archive', str(uuid4()), LifecycleRequest(expected_revision=writer['revision']))

    async def archived(*args, **kwargs):
        return {'state': 'stopped'}
    monkeypatch.setattr(provisioning, 'supervisor', archived)
    import asyncio
    asyncio.run(provisioning.drive(operation))
    bounced = message_row(message)
    assert (bounced['status'], bounced['released'], bounced['reason']) == ('cancelled', 'bounced', f"{name('writer')} is archived")
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued', 'The waiting sender is released without waiting for a dispatch pass'


def test_a_message_that_keeps_failing_backs_off_then_is_given_up_and_reported(work, monkeypatch):
    from backend import attention
    message = waiting_on_writer(work)
    expired = coordination.execution_policy.expired

    def broken(task):  # Fails only for this message's source task, as a stored row the code can't read would.
        if task['id'] == work['id']:
            raise RuntimeError('a stored row this code cannot read')
        return expired(task)
    monkeypatch.setattr(coordination.execution_policy, 'expired', broken)
    unrelated = other_work(work)
    assert [a['task_id'] for a in worker.claim_new()] == [unrelated['id']], 'One bad message never stops dispatch'
    first = message_row(message)
    assert (first['status'], first['attempts']) == ('pending', 1)
    assert first['last_error'].startswith('RuntimeError') and first['not_before'] > service.now()
    worker.claim_new()
    assert message_row(message)['attempts'] == 1, 'It waits out its backoff'
    for _ in range(2):
        with db.connection() as conn:
            conn.execute("UPDATE teammate_messages SET not_before=now()-interval '1 second' WHERE id=%s", (message,))
        worker.claim_new()
    given_up = message_row(message)
    assert (given_up['status'], given_up['released'], given_up['attempts']) == ('cancelled', 'failed', 3)
    assert given_up['reason'] == 'Could not be delivered after 3 attempts (RuntimeError)'
    assert len(events('teammate.message_failed')) == 1
    with db.connection() as conn:
        attention.project(conn, 'message', message)
        item = conn.execute("SELECT * FROM attention_items WHERE subject_type='message' AND subject_id=%s", (message,)).fetchone()
    assert item and item['active'] and item['needs_owner'] and item['category'] == 'problems'
    assert item['title'] == f"A message from {name('researcher')} to {name('writer')} couldn't be delivered"
    assert 'stored row' not in item['summary'], 'Error text stays out of the owner preview'
    assert item['refs']['task_id'] == str(work['id']) and item['refs']['conversation_id'] == str(work['conversation_id'])
    monkeypatch.undo()
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued' and 'Could not be delivered after 3 attempts' in row(work['id'])['continuation']


def test_an_outage_backs_off_without_counting_against_the_message(work, monkeypatch):
    from psycopg import errors
    message = waiting_on_writer(work)
    expired = coordination.execution_policy.expired

    def busy(task):
        if task['id'] == work['id']:
            raise errors.LockNotAvailable('another transaction holds the row')
        return expired(task)
    monkeypatch.setattr(coordination.execution_policy, 'expired', busy)
    worker.claim_new()
    held = message_row(message)
    assert (held['status'], held['attempts']) == ('pending', 0) and held['not_before'] > service.now()


# A given-up message's alert always has a way out: Resend or Done, and it leaves "Needs you" once its work is handled.

def give_up(work, monkeypatch, *, wait=True):
    parent = worker.claim_new()[0]
    sent = invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Which topic?'})
    message = UUID(sent['message_id'])
    if wait:
        invoke(parent, 'wait', {'message_ids': [message]})
    complete(parent)
    expired = coordination.execution_policy.expired

    def broken(task):
        if task['id'] == work['id']:
            raise RuntimeError('a stored row this code cannot read')
        return expired(task)
    monkeypatch.setattr(coordination.execution_policy, 'expired', broken)
    for _ in range(3):
        with db.connection() as conn:
            conn.execute("UPDATE teammate_messages SET not_before=NULL WHERE id=%s", (message,))
        worker.claim_new()
    monkeypatch.undo()
    assert message_row(message)['released'] == 'failed'
    return message


def alert(message):
    from backend import attention
    with db.connection() as conn:
        attention.project(conn, 'message', message)
        return conn.execute("SELECT * FROM attention_items WHERE subject_type='message' AND subject_id=%s", (message,)).fetchone()


def counts():
    from backend import attention
    with db.connection() as conn:
        return attention.page(conn, 'needs')['needs_owner']


def resolve(message, action, revision=None):
    """Answer an alert as the owner would, for the delivery the alert shows unless another revision is given."""
    revision = message_row(message)['delivery_revision'] if revision is None else revision
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})  # Not a context manager: that would run shutdown.
    try:
        return client.post(f'/api/v1/teammate-messages/{message}/resolve', json={'action': action, 'expected_revision': revision}, headers={'Idempotency-Key': str(uuid4())})
    finally:
        client.close()


def test_done_closes_the_alert_and_cannot_be_repeated(work, monkeypatch):
    message = give_up(work, monkeypatch)
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued', 'The waiting sender is woken and its work goes on'
    item = alert(message)
    assert item['active'] and item['needs_owner'] and item['refs']['message_id'] == str(message)
    assert 'Resend it, or choose Done' in item['summary'] and counts() == 1
    response = resolve(message, 'done')
    assert response.status_code == 200 and response.json() == {'id': str(message), 'action': 'done'}
    assert not alert(message)['active'] and counts() == 0, 'Done closes the item'
    assert message_row(message)['owner_action'] == 'done'
    again = resolve(message, 'done')
    assert again.status_code == 409 and again.json()['detail'] == 'This message was already handled'


def test_resend_puts_the_message_back_and_a_new_failure_alerts_again(work, monkeypatch):
    message = give_up(work, monkeypatch)
    first = alert(message)
    assert resolve(message, 'resend').status_code == 200
    resent = message_row(message)
    assert (resent['status'], resent['attempts'], resent['released'], resent['reason'], resent['owner_action']) == ('pending', 0, None, None, 'resend')
    assert not alert(message)['active'] and counts() == 0
    expired = coordination.execution_policy.expired
    monkeypatch.setattr(coordination.execution_policy, 'expired', lambda task: (_ for _ in ()).throw(RuntimeError('again')) if task['id'] == work['id'] else expired(task))
    for _ in range(3):
        with db.connection() as conn:
            conn.execute("UPDATE teammate_messages SET not_before=NULL WHERE id=%s", (message,))
        worker.claim_new()
    monkeypatch.undo()
    again = alert(message)
    assert message_row(message)['owner_action'] is None
    assert again['active'] and again['needs_owner'] and again['version'] > first['version'], 'A new failure is a new alert'


def test_an_open_alert_always_offers_resend_and_a_doomed_resend_comes_back_with_the_reason(work, monkeypatch):
    message = give_up(work, monkeypatch)
    worker.advance_waiters()
    with db.connection() as conn:  # The owner stops the sender's work.
        service.cancel(conn, service.locked_task(conn, work['id']), 'Stopped by owner')
    item = alert(message)
    assert item['active'] and item['needs_owner'] and counts() == 1, 'Nothing predicts whether Resend will work'
    assert resolve(message, 'resend').status_code == 200, 'Resend is accepted; the scheduler is the only judge'
    assert not alert(message)['active']
    worker.claim_new()
    reason = f"{name('researcher')}'s task was stopped"
    back = message_row(message)
    assert (back['status'], back['released'], back['reason'], back['owner_action']) == ('cancelled', 'dropped', reason, 'resend')
    item = alert(message)
    assert item['active'] and item['needs_owner'] and item['summary'] == f"{reason}. Resend it, or choose Done if you'll handle it yourself.", 'It comes back with the reason'
    assert resolve(message, 'done').status_code == 200 and not alert(message)['active'] and counts() == 0


def test_a_message_given_up_after_its_work_finished_still_offers_both_actions(work, monkeypatch):
    message = give_up(work, monkeypatch, wait=False)
    assert row(work['id'])['status'] == 'completed'
    item = alert(message)
    assert item['active'] and item['needs_owner'] and item['summary'].endswith("Resend it, or choose Done if you'll handle it yourself.")


def test_a_resend_to_a_deleted_teammate_comes_back_with_the_reason(work, monkeypatch):
    message = give_up(work, monkeypatch)
    worker.advance_waiters()
    alert(message)
    set_writer('deleted')
    assert resolve(message, 'resend').status_code == 200
    worker.claim_new()
    reason = f"{name('writer')} was deleted"
    assert (message_row(message)['released'], message_row(message)['reason']) == ('bounced', reason)
    assert alert(message)['summary'] == f"{reason}. Resend it, or choose Done if you'll handle it yourself."
    assert resolve(message, 'done').status_code == 200


# One delivery rule for the scheduler, Resend and the alert; drops record their reason; a lost resend is reported.

def helper_message(work):
    child = delegate(work)
    helper = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    sent = invoke(helper, 'message', {'bot_id': 'reviewer', 'prompt': 'Can you check this?'})
    return child, helper, UUID(sent['message_id'])


def stop(child, helper):
    with db.connection() as conn:  # The owner stops the helper; its manager keeps running.
        service.cancel(conn, service.locked_task(conn, child), 'Stopped by owner')
    worker.settle(helper['id'], {'status': 'cancelled', 'output': ''})


def fail_three_times(work_task, message, monkeypatch):
    expired = coordination.execution_policy.expired
    monkeypatch.setattr(coordination.execution_policy, 'expired',
                        lambda task: (_ for _ in ()).throw(RuntimeError('x')) if task['id'] == work_task else expired(task))
    for _ in range(3):
        with db.connection() as conn:
            conn.execute("UPDATE teammate_messages SET not_before=NULL WHERE id=%s", (message,))
        worker.claim_new()
    monkeypatch.undo()


def test_a_message_from_a_stopped_task_is_dropped_with_the_reason(work):
    child, helper, message = helper_message(work)
    stop(child, helper)
    worker.claim_new()
    dropped = message_row(message)
    reason = f"{name('writer')}'s task was stopped"
    assert (dropped['status'], dropped['released'], dropped['reason']) == ('cancelled', 'dropped', reason)
    assert [e['data']['reason'] for e in events('teammate.message_dropped')] == [reason]
    assert alert(message) is None, 'An ordinary drop is recorded, not raised to the owner'


def test_a_resend_from_a_stopped_helper_comes_back_with_the_reason(work, monkeypatch):
    child, helper, message = helper_message(work)
    fail_three_times(child, message, monkeypatch)
    stop(child, helper)
    assert row(work['id'])['status'] not in service.TERMINAL, 'The manager is still running'
    assert alert(message)['needs_owner'], 'Still offered: nothing predicts the outcome'
    assert resolve(message, 'resend').status_code == 200
    worker.claim_new()
    reason = f"{name('writer')}'s task was stopped"
    item = alert(message)
    assert item['active'] and item['needs_owner'] and item['summary'] == f"{reason}. Resend it, or choose Done if you'll handle it yourself."
    assert item['title'] == f"Your resent message from {name('writer')} to {name('reviewer')} couldn't be delivered"


def test_a_resend_that_cannot_go_through_is_reported_and_can_be_resent_again(work, monkeypatch):
    child, helper, message = helper_message(work)
    fail_three_times(child, message, monkeypatch)
    assert alert(message)['needs_owner'], 'The owner sees it in "Needs you"'
    assert resolve(message, 'resend').status_code == 200
    assert not alert(message)['active']
    stop(child, helper)  # Stopped in the moment after Resend was accepted.
    worker.claim_new()
    lost = message_row(message)
    assert (lost['status'], lost['released'], lost['owner_action']) == ('cancelled', 'dropped', 'resend')
    item = alert(message)
    assert item['active'] and item['needs_owner'] and item['read_version'] < item['version'], 'Back in "Needs you", unread'
    assert resolve(message, 'resend').status_code == 200, 'Resend stays available'
    worker.claim_new()
    assert alert(message)['active'], 'and comes back again while it still cannot be delivered'
    assert resolve(message, 'done').status_code == 200 and not alert(message)['active']
    assert resolve(message, 'done').status_code == 409


# A cancelled message always carries its reason (enforced by the database); alerts open the sending task's thread.

def test_the_database_refuses_a_cancelled_message_without_a_reason(work):
    from psycopg import errors
    _, _, message = helper_message(work)
    with pytest.raises(errors.CheckViolation):
        with db.connection() as conn:
            conn.execute("UPDATE teammate_messages SET status='cancelled' WHERE id=%s", (message,))
    assert message_row(message)['status'] == 'pending'


def test_stopping_managed_recurring_work_drops_its_messages_with_the_reason():
    from tests.test_recurring_management import managed
    authority, _, _, task = managed()
    with db.connection() as conn:
        sent = coordination.send(conn, task, {'bot_id': 'writer', 'prompt': 'Explain the existing evidence only.'})
        message = UUID(sent['message_id'])
        conn.execute("UPDATE teammate_messages SET owner_action='resend',owner_action_at=now() WHERE id=%s", (message,))  # The owner resent it.
    application.revoke_grant(authority['id'])
    dropped = message_row(message)
    reason = 'The recurring work it belonged to was stopped: Owner revoked management authority'
    assert (dropped['status'], dropped['released'], dropped['reason'], dropped['owner_action']) == ('cancelled', 'dropped', reason, 'resend')
    item = alert(message)
    assert item['active'] and item['needs_owner'] and item['summary'] == f"{reason}. Resend it, or choose Done if you'll handle it yourself.", 'The lost resend is reported'


def test_a_message_alert_opens_the_sending_tasks_thread(work, monkeypatch):
    from backend import attention
    with db.connection() as conn:  # The work runs in a thread of the conversation.
        thread = conn.execute("SELECT id FROM messages WHERE task_id=%s", (work['id'],)).fetchone()['id']
        conn.execute('UPDATE tasks SET reply_to_message_id=%s WHERE id=%s', (thread, work['id']))
    message = give_up(work, monkeypatch)
    item = alert(message)
    with db.connection() as conn:
        expected = attention.task_refs(conn, conn.execute('SELECT * FROM tasks WHERE id=%s', (work['id'],)).fetchone())
    assert item['refs'] == {**{k: v for k, v in expected.items()}, 'message_id': str(message), 'delivery_revision': 1}
    assert item['refs']['thread_id'] == thread
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    try:
        detail = client.get(f"/api/v1/attention/{item['id']}").json()
    finally:
        client.close()
    assert detail['thread']['id'] == thread, 'Open work goes to the thread, not the conversation root'


# A resend is exactly a fresh send: every per-delivery field is reset in one place, checked against the real columns.

def test_every_message_column_is_either_reset_on_resend_or_deliberately_kept():
    with db.connection() as conn:
        columns = {r['column_name'] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='teammate_messages'").fetchall()}
    groups = [set(coordination.REQUEUE), set(coordination.ADVANCE), coordination.KEPT]
    assert sum(map(len, groups)) == len(set().union(*groups)), 'A column is in more than one group'
    classified = set().union(*groups)
    assert columns == classified, (
        'Classify every teammate_messages column in coordination.REQUEUE (reset on resend), coordination.ADVANCE '
        f'(advanced on resend) or coordination.KEPT: unclassified {sorted(columns - classified)}, gone {sorted(classified - columns)}')


def test_a_resent_message_matches_a_freshly_sent_one_on_every_delivery_field(work, monkeypatch):
    message = give_up(work, monkeypatch)
    with db.connection() as conn:  # Leave stale links from an earlier delivery on it.
        stale = conn.execute("SELECT id FROM tasks WHERE id=%s", (work['id'],)).fetchone()['id']
        conn.execute("UPDATE teammate_messages SET response_task_id=%s,delivered_at=now() WHERE id=%s", (stale, message))
        before = conn.execute('SELECT delivery_revision FROM teammate_messages WHERE id=%s', (message,)).fetchone()['delivery_revision']
        coordination.requeue(conn, message)
        fresh = coordination.send(conn, service.locked_task(conn, work['id']), {'bot_id': 'writer', 'prompt': 'A fresh message'})
        rows = {r['id']: r for r in conn.execute('SELECT * FROM teammate_messages WHERE id=ANY(%s)', ([message, UUID(fresh['message_id'])],)).fetchall()}
    resent, new = rows[message], rows[UUID(fresh['message_id'])]
    assert {c: resent[c] for c in coordination.REQUEUE} == {c: new[c] for c in coordination.REQUEUE}
    assert resent['last_error'] and resent['content'] == 'Which topic?', 'History and content are kept'
    assert (resent['delivery_revision'], new['delivery_revision']) == (before + 1, 1), 'A resend is a new delivery of the same message'


def operate(action, monkeypatch, settle_first=None):
    import asyncio
    from backend import provisioning, registry
    from backend.models import LifecycleRequest
    with db.connection() as conn:
        writer = registry.require_bot(conn, 'writer')
        operation = registry.owner_operation(conn, 'writer', action, str(uuid4()), LifecycleRequest(expected_revision=writer['revision'], interrupt=True))
    if settle_first:
        worker.settle(settle_first['id'], {'status': 'cancelled', 'output': ''})

    async def done(*args, **kwargs):
        return {'state': 'stopped' if action == 'archive' else 'running'}
    monkeypatch.setattr(provisioning, 'supervisor', done)
    asyncio.run(provisioning.drive(operation))
    monkeypatch.undo()


def test_a_second_resend_after_archive_and_restore_is_delivered(work, monkeypatch):
    message = give_up(work, monkeypatch)
    worker.advance_waiters()
    alert(message)
    assert resolve(message, 'resend').status_code == 200
    first = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')  # The resend gets a reply task.
    operate('archive', monkeypatch, settle_first=first)
    bounced = message_row(message)
    assert (bounced['released'], bounced['owner_action']) == ('bounced', 'resend') and alert(message)['active']
    operate('restore', monkeypatch)
    with db.connection() as conn:  # The worker's probe marks the restored runtime ready.
        conn.execute("UPDATE bots SET readiness='{\"compatible\": true}' WHERE id='writer'")
    item = alert(message)
    assert item['active'] and item['needs_owner'], 'After the restore, the alert still offers Resend'
    assert resolve(message, 'resend').status_code == 200
    second = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    resent = message_row(message)
    assert resent['response_task_id'] == second['task_id'] != first['task_id'], 'A new reply task answers the second resend'


# Every message is checked on its own before a bot sees it, whether it starts a reply task, joins one, or is listed.

def test_a_batch_attaches_only_messages_that_pass_their_own_check(work):
    child = delegate(work, bot='reviewer')
    attempts = worker.claim_new()
    manager = next(a for a in attempts if a['bot_id'] == 'researcher')
    helper = next(a for a in attempts if a['bot_id'] == 'reviewer')
    stopped = UUID(invoke(helper, 'message', {'bot_id': 'writer', 'prompt': 'From the helper'})['message_id'])
    with db.connection() as conn:
        service.cancel(conn, service.locked_task(conn, child), 'Stopped by owner')
    worker.settle(helper['id'], {'status': 'cancelled', 'output': ''})
    paused = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'Still in its retry pause'})['message_id'])
    live = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'From the manager'})['message_id'])
    with db.connection() as conn:
        conn.execute("UPDATE teammate_messages SET not_before=now()+interval '1 hour',attempts=1 WHERE id=%s", (paused,))
        conn.execute("UPDATE teammate_messages SET created_at=now()-interval '1 minute' WHERE id=%s", (live,))  # The live one leads the group.
    reply = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    assert message_row(live)['response_task_id'] == reply['task_id']
    dropped = message_row(stopped)
    assert (dropped['status'], dropped['released'], dropped['reason']) == ('cancelled', 'dropped', f"{name('reviewer')}'s task was stopped")
    waiting = message_row(paused)
    assert (waiting['status'], waiting['response_task_id']) == ('pending', None), 'It waits out its pause'
    with db.connection() as conn:
        shown = [m['content'] for m in coordination.inbox(conn, service.locked_task(conn, reply['task_id']))]
    assert shown == ['From the manager']


def test_a_busy_bots_inbox_lists_only_messages_that_may_go_now(work):
    child = delegate(work)
    manager = next(a for a in worker.claim_new() if a['bot_id'] == 'researcher')
    message = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'Please share your coverage'})['message_id'])

    def listed():
        with db.connection() as conn:
            return [m['id'] for m in coordination.inbox(conn, service.locked_task(conn, child))]
    assert listed() == [message]
    with db.connection() as conn:
        conn.execute("UPDATE teammate_messages SET not_before=now()+interval '1 hour' WHERE id=%s", (message,))
    assert listed() == [], 'Hidden during its retry pause'
    with db.connection() as conn:
        conn.execute("UPDATE teammate_messages SET not_before=NULL WHERE id=%s", (message,))
    assert listed() == [message]


def test_one_message_that_cannot_be_checked_does_not_fail_its_batch(work, monkeypatch):
    manager = worker.claim_new()[0]
    first = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'First'})['message_id'])
    second = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'Second'})['message_id'])
    blocker = coordination.delivery_blocker

    def broken(conn, message, source):
        if message['id'] == second:
            raise RuntimeError('a stored row this code cannot read')
        return blocker(conn, message, source)
    monkeypatch.setattr(coordination, 'delivery_blocker', broken)
    reply = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    assert message_row(first)['response_task_id'] == reply['task_id'] and message_row(first)['attempts'] == 0
    bad = message_row(second)
    assert (bad['status'], bad['attempts'], bad['response_task_id']) == ('pending', 1, None), 'Counted against itself only'


# Paused messages can't crowd eligible ones out of an inbox; a message that can never go is released even while
# paused; each delivery gets its own event; an action from an older view of an alert is refused.

def test_paused_messages_cannot_hide_an_eligible_one_from_the_inbox(work):
    child = delegate(work)
    manager = next(a for a in worker.claim_new() if a['bot_id'] == 'researcher')
    eligible = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'The one that may go now'})['message_id'])
    with db.connection() as conn:  # It is the oldest; 35 newer ones are all in their retry pause.
        conn.execute("UPDATE teammate_messages SET created_at=now()-interval '1 hour' WHERE id=%s", (eligible,))
        columns = [r['column_name'] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='teammate_messages' "
                                                          "AND column_name NOT IN ('id','created_at','not_before','attempts')").fetchall()]
        listed = ','.join(columns)
        conn.execute(f"INSERT INTO teammate_messages(id,created_at,not_before,attempts,{listed}) "
                     f"SELECT gen_random_uuid(),now()-make_interval(secs=>n),now()+interval '1 hour',1,{listed} FROM teammate_messages, generate_series(1,35) n WHERE id=%s",
                     (eligible,))
        shown = [m['id'] for m in coordination.inbox(conn, service.locked_task(conn, child))]
    assert shown == [eligible]


def test_a_paused_message_from_a_stopped_sender_is_released_at_once(work):
    child = delegate(work, bot='reviewer')
    attempts = worker.claim_new()
    manager = next(a for a in attempts if a['bot_id'] == 'researcher')
    helper = next(a for a in attempts if a['bot_id'] == 'reviewer')
    stopped = UUID(invoke(helper, 'message', {'bot_id': 'writer', 'prompt': 'From the helper'})['message_id'])
    with db.connection() as conn:  # It failed once and is in its retry pause when its sender is stopped.
        conn.execute("UPDATE teammate_messages SET not_before=now()+interval '1 hour',attempts=1 WHERE id=%s", (stopped,))
        service.cancel(conn, service.locked_task(conn, child), 'Stopped by owner')
    worker.settle(helper['id'], {'status': 'cancelled', 'output': ''})
    live = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'From the manager'})['message_id'])
    reply = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    assert message_row(live)['response_task_id'] == reply['task_id']
    dropped = message_row(stopped)
    assert (dropped['status'], dropped['released'], dropped['reason']) == ('cancelled', 'dropped', f"{name('reviewer')}'s task was stopped"), (
        'It can never be delivered, so it is not left waiting out its pause')


def test_a_dropped_message_is_never_shown_again(work):
    child = delegate(work, bot='reviewer')
    helper = next(a for a in worker.claim_new() if a['bot_id'] == 'reviewer')
    unread = UUID(invoke(helper, 'message', {'bot_id': 'writer', 'prompt': 'Not read before the drop'})['message_id'])
    reply = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')  # A reply task is linked to it.
    assert message_row(unread)['response_task_id'] == reply['task_id']
    read = UUID(invoke(helper, 'message', {'bot_id': 'writer', 'prompt': 'Read before the drop'})['message_id'])

    def shown():
        with db.connection() as conn:
            return [m['content'] for m in coordination.inbox(conn, service.locked_task(conn, reply['task_id']))]
    assert shown() == ['Read before the drop', 'Not read before the drop']
    with db.connection() as conn:
        coordination.delivered(conn, service.locked_task(conn, reply['task_id']), [read])
    with db.connection() as conn:  # The owner stops the helper while its messages are still open.
        service.cancel(conn, service.locked_task(conn, child), 'Stopped by owner')
    worker.settle(helper['id'], {'status': 'cancelled', 'output': ''})
    assert shown() == ['Read before the drop'], 'The first read releases the unread one with its reason; the read one stays as history'
    assert (message_row(unread)['status'], message_row(unread)['released']) == ('cancelled', 'dropped')
    with db.connection() as conn:  # The read one is dropped too (here by the release every stop path uses).
        coordination.release(conn, message_row(read), f"{name('reviewer')}'s task was stopped", 'dropped')
    assert shown() == [], 'Neither comes back on later reads'


def test_each_delivery_of_a_message_records_its_own_event(work):
    child = delegate(work)
    attempts = worker.claim_new()
    manager = next(a for a in attempts if a['bot_id'] == 'researcher')
    message = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'Please share your coverage'})['message_id'])
    with db.connection() as conn:
        coordination.delivered(conn, service.locked_task(conn, child), [message])
    with db.connection() as conn:  # The same attempt acknowledges again (a retried request).
        coordination.delivered(conn, service.locked_task(conn, child), [message])
    assert len(events('teammate.message_delivered')) == 1, 'A repeated acknowledgement is one delivery'
    with db.connection() as conn:  # The owner resends it, and a new attempt of the same task receives it.
        coordination.requeue(conn, message)
        conn.execute("UPDATE attempts SET status='failed' WHERE task_id=%s AND number=1", (child,))
        conn.execute("INSERT INTO attempts(id,task_id,bot_id,number,session_id,status,idempotency_key,input,response) "
                     "SELECT gen_random_uuid(),task_id,bot_id,number+1,session_id,'running',gen_random_uuid()::text,input,response FROM attempts WHERE task_id=%s AND number=1", (child,))
        conn.execute('UPDATE tasks SET attempts=2 WHERE id=%s', (child,))
    with db.connection() as conn:
        coordination.delivered(conn, service.locked_task(conn, child), [message])
    assert len(events('teammate.message_delivered')) == 2, 'The second delivery is recorded too'


def test_an_action_from_an_older_view_of_the_alert_is_refused(work, monkeypatch):
    message = give_up(work, monkeypatch)
    assert alert(message)['refs']['delivery_revision'] == 1
    assert resolve(message, 'resend', revision=1).status_code == 200
    fail_three_times(work['id'], message, monkeypatch)
    item = alert(message)
    assert item['active'] and item['refs']['delivery_revision'] == 2
    stale = resolve(message, 'done', revision=1)  # A second tab still shows the first failure.
    assert stale.status_code == 409 and stale.json()['detail'] == 'This alert changed since you opened it. Check the latest failure, then choose again.'
    assert message_row(message)['owner_action'] is None and alert(message)['active'], 'Nothing changed'
    assert resolve(message, 'done', revision=2).status_code == 200 and not alert(message)['active']


def attention_module():
    from backend import attention
    return attention


def test_the_same_failure_after_a_resend_is_a_new_alert(work, monkeypatch):
    message = give_up(work, monkeypatch)
    worker.advance_waiters()
    set_writer('deleted')
    assert resolve(message, 'resend').status_code == 200
    worker.claim_new()
    first = alert(message)
    assert first['active'] and first['summary'].startswith(f"{name('writer')} was deleted")
    with db.connection() as conn:  # Resent while another process held the alert's projection lock, so it is next projected
        # only after the next failure, which has the same recipient, reason and last error.
        monkeypatch.setattr(attention_module(), 'project', lambda *args, **kwargs: None)
        coordination.resolve_failed(conn, message, 'resend', message_row(message)['delivery_revision'])
        monkeypatch.undo()
    worker.claim_new()
    again = alert(message)
    assert again['active'] and again['summary'] == first['summary']
    assert again['version'] > first['version'] and again['read_version'] < again['version'], 'It comes back as new, unread'


# A message whose inbox check fails is counted, paused and given up like any other failure, and can't hide a healthy one.

def fail_checks_for(monkeypatch, ids):
    blocker = coordination.delivery_blocker

    def broken(conn, message, source):
        if message['id'] in ids:
            raise RuntimeError('a stored row this code cannot read')
        return blocker(conn, message, source)
    monkeypatch.setattr(coordination, 'delivery_blocker', broken)


def read_inbox(task):
    with db.connection() as conn:
        return [m['id'] for m in coordination.inbox(conn, service.locked_task(conn, task))]


def clear_pauses(message):
    with db.connection() as conn:
        conn.execute('UPDATE teammate_messages SET not_before=NULL WHERE id=%s', (message,))


def test_failing_inbox_checks_are_counted_and_cannot_hide_a_healthy_message(work, monkeypatch):
    child = delegate(work)
    manager = next(a for a in worker.claim_new() if a['bot_id'] == 'researcher')
    healthy = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'The healthy one'})['message_id'])
    with db.connection() as conn:  # It is the oldest; 32 newer copies all fail their check.
        conn.execute("UPDATE teammate_messages SET created_at=now()-interval '1 hour' WHERE id=%s", (healthy,))
        columns = [r['column_name'] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='teammate_messages' "
                                                          "AND column_name NOT IN ('id','created_at','not_before','attempts')").fetchall()]
        listed = ','.join(columns)
        failing = {r['id'] for r in conn.execute(f"INSERT INTO teammate_messages(id,created_at,not_before,attempts,{listed}) "
                                                 f"SELECT gen_random_uuid(),now()-make_interval(secs=>n),NULL,0,{listed} FROM teammate_messages, generate_series(1,32) n WHERE id=%s "
                                                 "RETURNING id", (healthy,)).fetchall()}
    fail_checks_for(monkeypatch, failing)

    def failing_rows():
        return [message_row(identity) for identity in failing]
    assert read_inbox(child) == [], 'Nothing is shown while the newest 30 fail'
    counted = [m for m in failing_rows() if m['attempts'] == 1]
    assert len(counted) == 30, 'Every check that failed is counted against its message'
    assert all(m['not_before'] > service.now() and m['last_error'] for m in counted), 'Each is paused and keeps its error'
    assert read_inbox(child) == [healthy], 'The healthy message is no longer hidden'
    assert [m['attempts'] for m in failing_rows()] == [1] * 32, 'The other two failed once, and the paused ones were left alone'


def test_three_failed_inbox_checks_give_the_message_up_and_alert_the_owner(work, monkeypatch):
    child = delegate(work)
    manager = next(a for a in worker.claim_new() if a['bot_id'] == 'researcher')
    message = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'This one cannot be checked'})['message_id'])
    fail_checks_for(monkeypatch, {message})
    for _ in range(3):
        clear_pauses(message)
        read_inbox(child)
    given_up = message_row(message)
    assert (given_up['status'], given_up['released']) == ('cancelled', 'failed')
    assert given_up['reason'].startswith('Could not be delivered after 3 attempts'), given_up['reason']
    assert len(events('teammate.message_failed')) == 1, 'The owner is told once'
    item = alert(message)
    assert item['active'] and item['needs_owner'], 'The alert is in "Needs you"'


def test_an_outage_during_an_inbox_check_pauses_without_counting(work, monkeypatch):
    from psycopg import errors
    child = delegate(work)
    manager = next(a for a in worker.claim_new() if a['bot_id'] == 'researcher')
    message = UUID(invoke(manager, 'message', {'bot_id': 'writer', 'prompt': 'During an outage'})['message_id'])
    blocker = coordination.delivery_blocker

    def busy(conn, other, source):
        if other['id'] == message:
            raise errors.LockNotAvailable('another transaction holds the row')
        return blocker(conn, other, source)
    monkeypatch.setattr(coordination, 'delivery_blocker', busy)
    assert read_inbox(child) == []
    held = message_row(message)
    assert (held['status'], held['attempts']) == ('pending', 0) and held['not_before'] is not None and held['not_before'] > service.now(), 'Paused, not counted'


def test_a_failing_message_linked_to_the_running_reply_is_given_up_and_its_sender_told(work, monkeypatch):
    message = waiting_on_writer(work)
    reply = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    assert message_row(message)['response_task_id'] == reply['task_id']
    fail_checks_for(monkeypatch, {message})
    for _ in range(3):
        clear_pauses(message)
        read_inbox(reply['task_id'])
    assert message_row(message)['released'] == 'failed', 'Given up even though a reply task is already linked to it'
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued' and 'Could not be delivered after 3 attempts' in row(work['id'])['continuation']


def test_every_reader_goes_through_the_shared_check_and_the_counting():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(coordination.__file__).read_text())
    found = {'delivery_check': set(), 'record_failure': set()}
    for top in tree.body:
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(top):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in found:
                    found[node.func.id].add(top.name)
    assert found['delivery_check'] == {'checked'}, f"Every reader must go through checked/isolated; delivery_check is called from {found['delivery_check']}"
    assert found['record_failure'] == {'isolated'}, f"Every reader must go through checked/isolated; record_failure is called from {found['record_failure']}"


# Random sequences of everything that can happen to a message, with the rules checked after every step.

STOPPED = {'cancelled', 'cancelling', 'interrupted'}


@pytest.mark.parametrize('seed', range(60))
def test_random_sequences_never_break_the_delivery_rules(work, monkeypatch, seed):
    import random
    import psycopg
    from backend import attention
    rng = random.Random(seed)
    child = delegate(work, bot='reviewer')
    attempts = worker.claim_new()
    senders = {'manager': next(a for a in attempts if a['bot_id'] == 'researcher'), 'helper': next(a for a in attempts if a['bot_id'] == 'reviewer')}
    sources = {'manager': work['id'], 'helper': child}
    broken, raised, sent, log = set(), set(), [], []
    blocker = coordination.delivery_blocker

    def flaky(conn, message, source):
        if message['source_task_id'] in broken:
            raised.add(message['id'])
            raise RuntimeError('injected failure')
        return blocker(conn, message, source)
    monkeypatch.setattr(coordination, 'delivery_blocker', flaky)

    def rows():
        with db.connection() as conn:
            return {r['id']: r for r in conn.execute('SELECT m.*, t.status AS source_status FROM teammate_messages m JOIN tasks t ON t.id=m.source_task_id').fetchall()}

    def writer_status():
        with db.connection() as conn:
            return conn.execute("SELECT status FROM bots WHERE id='writer'").fetchone()['status']

    def step():
        raised.clear()
        live = [k for k in senders if row(sources[k])['status'] not in STOPPED | set(service.TERMINAL)]
        # Weighted so sequences reach the hard states: three failures in a row, giving up, resends and bounces.
        choices = ['schedule'] * 4 + ['finish_reply'] * 2 + ['toggle_failure', 'archive', 'restore'] + ['resolve'] * 3 + ['inbox_read'] * 2
        choices += ['send'] * 3 if live and writer_status() == 'active' else []  # Sending to an archived bot is refused at send time.
        choices += ['stop_helper'] if 'helper' in live else []
        action = rng.choice(choices)
        log.append(action)
        if action == 'send':
            who = rng.choice(live)
            sent.append(UUID(invoke(senders[who], 'message', {'bot_id': 'writer', 'prompt': f'message {len(sent)}'})['message_id']))
        elif action == 'schedule':  # Retry pauses run out, then a scheduler pass.
            with db.connection() as conn:
                conn.execute('UPDATE teammate_messages SET not_before=NULL WHERE not_before>now()')
            before, status, now = rows(), writer_status(), service.now()
            worker.claim_new()
            for identity, after in rows().items():
                old = before.get(identity)
                if old and old['response_task_id'] is None and after['response_task_id'] is not None:  # Attached in this pass.
                    assert not (old['not_before'] and old['not_before'] > now), f'{log}: attached during its retry pause'
                    assert old['source_status'] not in STOPPED, f'{log}: attached from a stopped sender'
                    assert old['source_task_id'] not in broken, f'{log}: attached without passing its check'
                    assert status == 'active', f'{log}: attached while the recipient was {status}'
        elif action == 'finish_reply':
            with db.connection() as conn:
                running = conn.execute("SELECT id FROM attempts WHERE bot_id='writer' AND status=ANY(%s)", (list(service.ACTIVE_ATTEMPTS),)).fetchall()
            for attempt in running:
                worker.settle(attempt['id'], {'status': 'completed', 'output': 'Answered'})
        elif action == 'inbox_read':
            with db.connection() as conn:
                running = conn.execute("SELECT task_id FROM attempts WHERE bot_id='writer' AND status=ANY(%s)", (list(service.ACTIVE_ATTEMPTS),)).fetchall()
            for attempt in running:
                with db.connection() as conn:
                    coordination.inbox(conn, service.locked_task(conn, attempt['task_id']))
        elif action == 'toggle_failure':
            broken.symmetric_difference_update({sources[rng.choice(list(sources))]})
        elif action in ('archive', 'restore'):
            set_writer('archived' if action == 'archive' else 'active')
        elif action == 'stop_helper':
            stop(child, senders['helper'])
        elif action == 'resolve':
            open_alerts = [i for i, r in rows().items() if coordination.awaiting_owner(r)]
            if open_alerts:
                identity = rng.choice(open_alerts)
                current = rows()[identity]['delivery_revision']
                stale = current > 1 and rng.random() < 0.3  # Sometimes from a tab that still shows an earlier delivery.
                with db.connection() as conn:
                    if stale:
                        with pytest.raises(HTTPException) as refused:
                            coordination.resolve_failed(conn, identity, rng.choice(['resend', 'done']), rng.randrange(1, current))
                        assert refused.value.status_code == 409, f'{log}: a stale action was not refused'
                    else:
                        coordination.resolve_failed(conn, identity, rng.choice(['resend', 'done']), current)
                after = rows()[identity]
                assert after['delivery_revision'] == current + (not stale and after['owner_action'] == 'resend'), f'{log}: revision moved wrongly'

    def check():
        now = service.now()
        for identity, r in rows().items():
            if identity in raised:
                assert r['status'] == 'cancelled' or (r['not_before'] and r['not_before'] > now), f'{log}: a failed check was not counted'
            if r['status'] == 'cancelled':
                assert r['released'] in ('failed', 'bounced', 'dropped') and r['reason'], f'{log}: cancelled without a reason'
            if coordination.awaiting_owner(r):
                with db.connection() as conn:
                    attention.project(conn, 'message', identity)
                    item = conn.execute("SELECT active,needs_owner FROM attention_items WHERE subject_type='message' AND subject_id=%s", (identity,)).fetchone()
                assert item and item['active'] and item['needs_owner'], f'{log}: an open alert is not in "Needs you"'
                for action in ('resend', 'done'):  # Every button an open alert shows is accepted.
                    with db.connection() as conn:
                        with conn.transaction():
                            coordination.resolve_failed(conn, identity, action, r['delivery_revision'])
                            raise psycopg.Rollback()
        assert len(rows()) == len(sent), f'{log}: a message went missing'

    for _ in range(40):
        step()
        check()
