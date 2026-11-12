"""Communication, required inputs and accepted deliverables have distinct effects."""
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import psycopg
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
    with db.connection() as conn:  # The sender can't acknowledge a message addressed to another bot: it is skipped.
        assert coordination.delivered(conn, row(work['id']), [UUID(message['message_id'])]) == []
    assert message_row(UUID(message['message_id']))['status'] == 'pending'


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
    with db.connection() as conn:  # The writer read the message, then its reply failed. (An unread one is retried.)
        coordination.delivered(conn, service.locked_task(conn, response['task_id']), [UUID(message['message_id'])])
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
    assert shown() == ['Not read before the drop', 'Read before the drop'], 'The message this reply task was started for comes first'
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


# A link to a reply task is a claim that ends with the task; a waiting task resumes whenever nothing it names is pending.

def reply_attempt():
    attempt = next((a for a in worker.claim_new() if a['bot_id'] == 'writer'), None)
    assert attempt, 'The message gets a reply task'
    return attempt


def stop_reply(attempt):
    with db.connection() as conn:
        service.cancel(conn, service.locked_task(conn, attempt['task_id']), 'Stopped by owner')
    worker.settle(attempt['id'], {'status': 'cancelled', 'output': ''})


def writer_attempts():
    with db.connection() as conn:
        return conn.execute("SELECT task_id FROM attempts WHERE bot_id='writer' ORDER BY created_at").fetchall()


def test_a_linked_message_its_reply_task_missed_gets_a_new_reply_task(work, monkeypatch):
    message = waiting_on_writer(work)
    first = reply_attempt()
    fail_checks_for(monkeypatch, {message})
    read_inbox(first['task_id'])
    monkeypatch.undo()
    complete(first)  # The reply finished without reading the message.
    clear_pauses(message)
    worker.claim_new()
    held = message_row(message)
    assert (held['status'], held['response_task_id'], held['attempts']) == ('pending', None, 2), 'The missed message is unlinked and counted'
    assert held['not_before'] is not None and held['not_before'] > service.now(), 'It waits out a pause'
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'waiting_results', 'The sender is not told "without a reply" for a message nobody read'
    clear_pauses(message)
    worker.claim_new()
    again = message_row(message)
    assert again['response_task_id'] and again['response_task_id'] != first['task_id'], 'A new reply task has the message'
    assert len(writer_attempts()) == 2


def test_a_reply_task_that_keeps_failing_before_reading_gives_the_message_up(work):
    message = waiting_on_writer(work)
    tasks = []
    for _ in range(3):
        clear_pauses(message)
        reply = reply_attempt()
        tasks.append(reply['task_id'])
        worker.settle(reply['id'], {'status': 'failed', 'error': 'Cannot start'})
        clear_pauses(message)
        worker.claim_new()  # Reclaims the message from the failed reply task.
    given_up = message_row(message)
    assert (given_up['status'], given_up['released']) == ('cancelled', 'failed')
    assert given_up['reason'] == f"Could not be delivered after 3 attempts ({name('writer')}'s reply ended before reading it)"
    assert len(set(tasks)) == 3, 'Each try had its own reply task'
    item = alert(message)
    assert item and item['active'] and item['needs_owner'], 'The owner is told'
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued' and given_up['reason'] in row(work['id'])['continuation']


def test_a_reply_the_owner_stopped_is_not_restarted(work):
    message = waiting_on_writer(work)
    reply = reply_attempt()
    stop_reply(reply)
    worker.claim_new()
    dropped = message_row(message)
    assert (dropped['status'], dropped['released']) == ('cancelled', 'dropped')
    assert dropped['reason'] == f"{name('writer')}'s reply was stopped"
    clear_pauses(message)
    worker.claim_new()
    assert len(writer_attempts()) == 1, 'No new reply task for a stopped reply'
    assert alert(message) is None, 'A stop is not a failure to report'
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued' and dropped['reason'] in row(work['id'])['continuation']


def woken_by_failure(work, monkeypatch, *, extra=False):
    """The sender sends to the writer (and waits on a child that never finishes if asked), the message fails and wakes
    the sender, who is claimed again. Returns the message, the new attempt and the wait arguments."""
    child = delegate(work, bot='reviewer') if extra else None
    parent = next(a for a in worker.claim_new() if a['task_id'] == work['id'])
    message = UUID(invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Which topic?'})['message_id'])
    args = {'message_ids': [message], **({'task_ids': [child]} if extra else {})}
    invoke(parent, 'wait', args)
    complete(parent)
    fail_three_times(work['id'], message, monkeypatch)
    assert message_row(message)['released'] == 'failed'
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued'
    return message, next(a for a in worker.claim_new() if a['task_id'] == work['id']), args


def test_the_same_failure_after_a_resend_wakes_the_sender_again(work, monkeypatch):
    message, sender, args = woken_by_failure(work, monkeypatch)
    assert resolve(message, 'resend').status_code == 200
    invoke(sender, 'wait', args)
    complete(sender)
    assert row(work['id'])['status'] == 'waiting_results'
    fail_three_times(work['id'], message, monkeypatch)
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued', 'The second failure of the same message is news to the sender'


def test_the_same_failure_after_a_resend_wakes_the_sender_while_another_wait_is_pending(work, monkeypatch):
    message, sender, args = woken_by_failure(work, monkeypatch, extra=True)
    assert resolve(message, 'resend').status_code == 200
    invoke(sender, 'wait', args)
    complete(sender)
    fail_three_times(work['id'], message, monkeypatch)
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued', 'The resend failed again, so the sender hears it even though the child is still running'


def test_waiting_on_a_message_that_already_failed_resumes_at_once(work, monkeypatch):
    message, sender, args = woken_by_failure(work, monkeypatch)
    invoke(sender, 'wait', args)
    complete(sender)
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued', 'Nothing is left to wait for'


def test_waiting_on_a_task_that_already_failed_resumes_at_once(work):
    child = delegate(work)
    attempts = worker.claim_new()
    parent = next(a for a in attempts if a['task_id'] == work['id'])
    invoke(parent, 'wait', {'task_ids': [child]})
    complete(parent)
    worker.settle(next(a for a in attempts if a['task_id'] == child)['id'], {'status': 'failed', 'error': 'Source unavailable'})
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued'
    again = next(a for a in worker.claim_new() if a['task_id'] == work['id'])
    invoke(again, 'wait', {'task_ids': [child]})
    complete(again)
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued', 'The child already failed, so waiting on it returns at once'


def test_a_task_parked_without_naming_anything_is_woken_only_once_per_change(work):
    child = delegate(work)
    attempts = worker.claim_new()
    complete(next(a for a in attempts if a['task_id'] == work['id']))  # Parked for its required result, no wait rows.
    worker.settle(next(a for a in attempts if a['task_id'] == child)['id'], {'status': 'failed', 'error': 'Source unavailable'})
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued'
    complete(next(a for a in worker.claim_new() if a['task_id'] == work['id']))  # It ends its turn without repairing anything.
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM coordination_waits WHERE task_id=%s', (work['id'],)).fetchone()['n'] == 0
    for _ in range(2):
        worker.advance_waiters()
        assert row(work['id'])['status'] == 'waiting_results', 'Nothing changed since it last looked, so it stays parked'


# A reply the owner resent is judged by the scheduler too: it waits only while its recipient has work that can read it.

def sent_reply(work, monkeypatch, *, give_up_reply=True):
    sender = worker.claim_new()[0]  # The researcher asks the writer and keeps running.
    question = UUID(invoke(sender, 'message', {'bot_id': 'writer', 'prompt': 'What is your expertise?'})['message_id'])
    answerer = next(a for a in worker.claim_new() if a['bot_id'] == 'writer')
    reply = UUID(invoke(answerer, 'message', {'bot_id': 'researcher', 'prompt': 'Writing and synthesis', 'reply_to': question})['message_id'])
    complete(answerer)
    if give_up_reply:
        fail_checks_for(monkeypatch, {reply})
        for _ in range(3):
            clear_pauses(reply)
            read_inbox(work['id'])
        monkeypatch.undo()
        given_up = message_row(reply)
        assert given_up['released'] == 'failed' and alert(reply)['active'], 'Three failed checks give the reply up'
    return sender, reply


def finish_sender(work, sender):
    complete(sender)
    assert row(work['id'])['status'] in service.TERMINAL, 'The researcher has no unfinished work left'


def no_work_reason():
    return f"{name('researcher')} has no unfinished work in this conversation to receive it"


def assert_dropped_again(reply):
    back = message_row(reply)
    assert (back['status'], back['released'], back['reason'], back['owner_action']) == ('cancelled', 'dropped', no_work_reason(), 'resend')
    item = alert(reply)
    assert item['active'] and item['needs_owner'] and item['summary'].startswith(no_work_reason()) and item['title'].startswith('Your resent message'), 'It comes back with the reason'


def test_a_resent_reply_whose_recipient_has_finished_comes_back_with_the_reason(work, monkeypatch):
    sender, reply = sent_reply(work, monkeypatch)
    finish_sender(work, sender)
    assert resolve(reply, 'resend').status_code == 200 and not alert(reply)['active']
    worker.claim_new()
    assert_dropped_again(reply)
    assert resolve(reply, 'done').status_code == 200 and not alert(reply)['active'], 'Done closes it'


def test_a_resent_reply_whose_recipient_is_still_working_waits_and_is_then_read(work, monkeypatch):
    sender, reply = sent_reply(work, monkeypatch)
    assert resolve(reply, 'resend').status_code == 200
    worker.claim_new()
    assert message_row(reply)['status'] == 'pending' and not alert(reply)['active'], 'Its recipient is still working, so it waits'
    assert reply in read_inbox(work['id']), 'The recipient reads it in its inbox'
    with db.connection() as conn:
        coordination.delivered(conn, service.locked_task(conn, work['id']), [reply])
    assert message_row(reply)['status'] == 'delivered'
    worker.claim_new()
    assert message_row(reply)['status'] == 'delivered' and not alert(reply)['active'], 'A read reply stays read'


def test_a_resent_reply_is_reported_when_its_recipient_ends_without_reading_it(work, monkeypatch):
    sender, reply = sent_reply(work, monkeypatch)
    assert resolve(reply, 'resend').status_code == 200
    worker.claim_new()
    assert message_row(reply)['status'] == 'pending' and not alert(reply)['active']
    finish_sender(work, sender)  # It ends without reading the reply.
    worker.claim_new()
    assert_dropped_again(reply)


def test_a_resent_reply_never_starts_a_task(work, monkeypatch):
    sender, reply = sent_reply(work, monkeypatch)
    with db.connection() as conn:
        before = conn.execute('SELECT bot_id,count(*) AS n FROM tasks GROUP BY bot_id ORDER BY bot_id').fetchall()
    assert resolve(reply, 'resend').status_code == 200
    for _ in range(2):
        worker.claim_new()
    finish_sender(work, sender)
    for _ in range(2):
        assert worker.claim_new() == [], 'Nothing is started for a reply'
    with db.connection() as conn:
        assert conn.execute('SELECT bot_id,count(*) AS n FROM tasks GROUP BY bot_id ORDER BY bot_id').fetchall() == before, 'No task appeared for either bot'


def test_a_reply_the_owner_did_not_resend_is_left_alone(work, monkeypatch):
    sender, reply = sent_reply(work, monkeypatch, give_up_reply=False)
    finish_sender(work, sender)
    for _ in range(3):
        worker.claim_new()
    assert message_row(reply)['status'] == 'pending' and message_row(reply)['released'] is None
    assert not alert(reply) or not alert(reply)['active'], 'Nothing is raised for a reply nobody resent'


# Every state a message can be in behaves as the lifecycle map says, cell by cell: one case per row of its tables, one
# check per column (each rebuilds its own state, so a scheduler pass or a wake-up never disturbs another column).

def reader_task(work):  # A task of the recipient that gets a teammate inbox, in the message's work; finished, so it is only read from.
    with db.connection() as conn:
        task = service.create_task(conn, bot_id='writer', conversation_id=work['conversation_id'], prompt='Read the teammate inbox', request_key=str(uuid4()),
                                   orchestration_version=2, coordination_version=1)
        conn.execute("UPDATE tasks SET work_item_id=%s,status='completed' WHERE id=%s", (work['work_item_id'], task['id']))
    return task['id']


def task_without_inbox(work, bot='researcher'):  # An unfinished task that never gets a teammate inbox (coordination_version 0).
    with db.connection() as conn:
        task = service.create_task(conn, bot_id=bot, conversation_id=work['conversation_id'], prompt='Work that reads no teammate inbox', request_key=str(uuid4()))
        conn.execute('UPDATE tasks SET work_item_id=%s WHERE id=%s', (work['work_item_id'], task['id']))
    return task['id']


def read_by(attempt, *messages):
    with db.connection() as conn:
        return coordination.delivered(conn, service.locked_task(conn, attempt['task_id']), list(messages))


def task_count():
    with db.connection() as conn:
        return conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n']


def asked_and_answered(work, *, text='Writing and synthesis', wait=True, finish=True):
    """The researcher asks the writer (and parks waiting for the answer), the writer reads the question and replies."""
    asker = worker.claim_new()[0]
    question = UUID(invoke(asker, 'message', {'bot_id': 'writer', 'prompt': 'What is your expertise?'})['message_id'])
    if wait:
        invoke(asker, 'wait', {'message_ids': [question]})
        complete(asker)
    answerer = reply_attempt()
    read_by(answerer, question)
    reply = UUID(invoke(answerer, 'message', {'bot_id': 'researcher', 'prompt': text, 'reply_to': question})['message_id'])
    if finish:
        complete(answerer)
    return {'message': reply, 'question': question, 'asker': asker, 'answerer': answerer, 'text': text, 'reader': work['id']}


def fail_reply_checks(work, monkeypatch, reply):
    fail_checks_for(monkeypatch, {reply})
    for _ in range(3):
        clear_pauses(reply)
        read_inbox(work['id'])
    monkeypatch.undo()
    assert message_row(reply)['released'] == 'failed', 'Three failed checks give the reply up'


def original_pending(work, monkeypatch):
    return {'message': waiting_on_writer(work)}


def original_paused(work, monkeypatch):
    message = waiting_on_writer(work)
    with db.connection() as conn:
        conn.execute("UPDATE teammate_messages SET not_before=now()+interval '1 hour',attempts=1 WHERE id=%s", (message,))
    return {'message': message}


def original_linked_live(work, monkeypatch):
    message = waiting_on_writer(work)
    return {'message': message, 'reader': reply_attempt()['task_id']}


def original_linked_ended(work, monkeypatch, *, stopped=False):
    message = waiting_on_writer(work)
    reply = reply_attempt()
    (stop_reply if stopped else complete)(reply)
    return {'message': message, 'reader': reply['task_id']}


def original_resent(work, monkeypatch):
    message = give_up(work, monkeypatch)
    assert resolve(message, 'resend').status_code == 200
    return {'message': message}


def original_delivered(work, monkeypatch):
    message = waiting_on_writer(work)
    reply = reply_attempt()
    read_by(reply, message)
    return {'message': message, 'reader': reply['task_id'], 'reply_attempt': reply}


def original_read_but_unanswered(work, monkeypatch):
    info = original_delivered(work, monkeypatch)
    complete(info['reply_attempt'])
    return info


def original_answered(work, monkeypatch):
    info = original_delivered(work, monkeypatch)
    invoke(info['reply_attempt'], 'message', {'bot_id': 'researcher', 'prompt': 'Topic A', 'reply_to': info['message']})
    return {**info, 'text': 'Topic A'}


def original_given_up(work, monkeypatch):
    return {'message': give_up(work, monkeypatch)}


def original_bounced(work, monkeypatch):
    message, reader = waiting_on_writer(work), reader_task(work)
    set_writer('archived')
    worker.claim_new()
    return {'message': message, 'reader': reader}


def original_dropped(work, monkeypatch):
    message, reader = waiting_on_writer(work), reader_task(work)
    with db.connection() as conn:
        conn.execute('UPDATE conversations SET archived_at=now() WHERE id=%s', (work['conversation_id'],))
    worker.claim_new()
    return {'message': message, 'reader': reader}


def original_lost_after_resend(work, monkeypatch):
    message = give_up(work, monkeypatch)
    assert resolve(message, 'resend').status_code == 200
    reader = reader_task(work)
    set_writer('archived')
    worker.claim_new()
    return {'message': message, 'reader': reader}


def original_done(work, monkeypatch):
    message = give_up(work, monkeypatch)
    assert alert(message)['active'], 'The owner saw the alert'
    assert resolve(message, 'done').status_code == 200
    return {'message': message}


def reply_pending(work, monkeypatch):
    return asked_and_answered(work)


def reply_paused(work, monkeypatch):
    info = asked_and_answered(work)
    with db.connection() as conn:
        conn.execute("UPDATE teammate_messages SET not_before=now()+interval '1 hour',attempts=1 WHERE id=%s", (info['message'],))
    return info


def reply_resent_with_a_reader(work, monkeypatch):
    info = asked_and_answered(work)  # The researcher is parked: an unfinished task that gets an inbox.
    fail_reply_checks(work, monkeypatch, info['message'])
    assert resolve(info['message'], 'resend').status_code == 200
    return info


def reply_resent_without_a_reader(work, monkeypatch, *, other_task=False):
    info = asked_and_answered(work, wait=False)
    fail_reply_checks(work, monkeypatch, info['message'])
    assert resolve(info['message'], 'resend').status_code == 200
    finish_sender(work, info['asker'])
    if other_task:
        task_without_inbox(work)
    return {**info, 'settle': worker.claim_new}  # The owner's actions exist once the scheduler has dropped it.


def reply_delivered(work, monkeypatch):
    info = asked_and_answered(work)
    with db.connection() as conn:
        coordination.delivered(conn, service.locked_task(conn, work['id']), [info['message']])
    return info


def reply_given_up(work, monkeypatch):
    info = asked_and_answered(work)
    fail_reply_checks(work, monkeypatch, info['message'])
    return info


def reply_dropped(work, monkeypatch):
    info = asked_and_answered(work)
    with db.connection() as conn:
        conn.execute('UPDATE conversations SET archived_at=now() WHERE id=%s', (work['conversation_id'],))
    read_inbox(work['id'])
    assert message_row(info['message'])['released'] == 'dropped'
    return info


def reply_done(work, monkeypatch):
    info = reply_given_up(work, monkeypatch)
    assert alert(info['message'])['active'], 'The owner saw the alert'
    assert resolve(info['message'], 'done').status_code == 200
    return info


def archived_reason():
    return f"{name('writer')} is archived"


def given_up_reason():
    return 'Could not be delivered after 3 attempts (RuntimeError)'


def stopped_reply_reason():
    return f"{name('writer')}'s reply was stopped"


def conversation_reason():
    return 'The conversation was archived'


# Cells: inbox shown|hidden; scheduler untouched|reply-task|reclaimed|dropped; waiter waits|told-reason|told-no-reply|handed|told-lost;
# owner accepted|refused (Resend and Done); alert open|open-resent|closed|none (none: no open alert); open_work yes|no (originals only).
# A cell that is not applicable, or can't be built honestly, is left out.
CASES = {
    'original-1-pending': (original_pending, dict(inbox='shown', scheduler='reply-task', waiter='waits', owner='refused', alert='none', open_work=True)),
    'original-2-paused': (original_paused, dict(inbox='hidden', scheduler='untouched', waiter='waits', owner='refused', alert='none', open_work=True)),
    'original-3-linked-to-live-reply-task': (original_linked_live, dict(inbox='shown', scheduler='untouched', waiter='waits', owner='refused', alert='none', open_work=False)),
    'original-4-linked-to-ended-reply-task': (original_linked_ended, dict(inbox='shown', scheduler='reclaimed', waiter='waits', owner='refused', alert='none', open_work=True)),
    'original-4-linked-to-stopped-reply-task': (lambda work, monkeypatch: original_linked_ended(work, monkeypatch, stopped=True),
                                                dict(inbox='shown', scheduler='dropped', waiter='waits', owner='refused', alert='none', open_work=True, reason=stopped_reply_reason)),
    'original-5-resent': (original_resent, dict(inbox='shown', scheduler='reply-task', waiter='waits', owner='refused', alert='none', open_work=True)),
    'original-6-delivered': (original_delivered, dict(inbox='shown', scheduler='untouched', waiter='waits', owner='refused', alert='none', open_work=False)),
    'original-7-read-but-unanswered': (original_read_but_unanswered, dict(scheduler='untouched', waiter='told-no-reply', owner='refused', alert='none', open_work=False)),
    'original-8-answered': (original_answered, dict(inbox='shown', scheduler='untouched', waiter='handed', owner='refused', alert='none', open_work=False)),
    'original-9-given-up': (original_given_up, dict(inbox='hidden', scheduler='untouched', waiter='told-reason', owner='accepted', alert='open', open_work=False, reason=given_up_reason)),
    'original-10-bounced': (original_bounced, dict(inbox='hidden', scheduler='untouched', waiter='told-reason', owner='refused', alert='none', open_work=False, reason=archived_reason)),
    'original-10-dropped': (original_dropped, dict(inbox='hidden', scheduler='untouched', waiter='told-reason', owner='refused', alert='none', open_work=False, reason=conversation_reason)),
    'original-11-lost-after-resend': (original_lost_after_resend, dict(inbox='hidden', scheduler='untouched', waiter='told-reason', owner='accepted', alert='open-resent', open_work=False, reason=archived_reason)),
    'original-12-done': (original_done, dict(inbox='hidden', scheduler='untouched', waiter='told-reason', owner='refused', alert='closed', open_work=False, reason=given_up_reason)),
    'reply-1-pending': (reply_pending, dict(inbox='shown', scheduler='untouched', waiter='handed', owner='refused', alert='none')),
    'reply-2-paused': (reply_paused, dict(inbox='hidden', scheduler='untouched', waiter='waits', owner='refused', alert='none')),
    'reply-5-resent-reader-unfinished': (reply_resent_with_a_reader, dict(inbox='shown', scheduler='untouched', waiter='handed', owner='refused', alert='none')),
    'reply-5-resent-no-task': (reply_resent_without_a_reader, dict(scheduler='dropped', owner='accepted', alert='open-resent', reason=no_work_reason)),
    'reply-5-resent-only-tasks-without-an-inbox': (lambda work, monkeypatch: reply_resent_without_a_reader(work, monkeypatch, other_task=True),
                                                   dict(scheduler='dropped', owner='accepted', alert='open-resent', reason=no_work_reason)),
    'reply-6-delivered': (reply_delivered, dict(inbox='shown', scheduler='untouched', waiter='handed', owner='refused', alert='none')),
    'reply-9-given-up': (reply_given_up, dict(inbox='hidden', scheduler='untouched', waiter='told-lost', owner='accepted', alert='open')),
    'reply-10-dropped': (reply_dropped, dict(inbox='hidden', scheduler='untouched', waiter='told-lost', owner='refused', alert='none')),
    'reply-12-done': (reply_done, dict(inbox='hidden', scheduler='untouched', waiter='told-lost', owner='refused', alert='closed')),
}
COLUMNS = ('inbox', 'scheduler', 'waiter', 'owner', 'alert', 'open_work')
CELLS = [pytest.param(case, column, id=f'{case}-{column}') for case, (_, cells) in CASES.items() for column in COLUMNS if column in cells]


def settled(info):
    if info.get('settle'):
        info['settle']()


def check_inbox(work, info, expected):
    listed = read_inbox(info.get('reader') or reader_task(work))
    assert (info['message'] in listed) == (expected == 'shown'), f"The inbox should have {expected} the message, it lists {listed}"


def check_scheduler(work, info, expected, reason):
    identity = info['message']
    before, tasks = message_row(identity), task_count()
    worker.claim_new()
    after = message_row(identity)
    if expected == 'untouched':
        keys = ('status', 'response_task_id', 'attempts', 'not_before', 'released', 'reason')
        assert [after[k] for k in keys] == [before[k] for k in keys], 'The scheduler leaves it as it was'
        assert task_count() == tasks, 'and starts nothing'
    elif expected == 'reply-task':
        assert after['status'] == 'pending' and after['response_task_id'], 'A reply task is linked to it'
        assert after['attempts'] == 0 and task_count() == tasks + 1
        assert row(after['response_task_id'])['execution_kind'] == 'peer_message'
    elif expected == 'reclaimed':
        assert (after['status'], after['response_task_id'], after['attempts']) == ('pending', None, before['attempts'] + 1), 'It is unlinked and counted'
        assert after['not_before'] and after['not_before'] > service.now(), 'and paused'
        assert task_count() == tasks
    elif expected == 'dropped':
        assert (after['status'], after['released'], after['reason']) == ('cancelled', 'dropped', reason()), f"It is dropped with its reason, not {after['status']} {after['reason']}"


def check_waiter(work, info, expected, reason):
    assert row(work['id'])['status'] == 'waiting_results', 'The sender starts out parked'
    worker.advance_waiters()
    after = row(work['id'])
    if expected == 'waits':
        assert after['status'] == 'waiting_results', 'It keeps waiting'
        return
    assert after['status'] == 'queued', f"It should be resumed with {expected}, it is {after['status']}"
    told = after['continuation']
    if expected == 'told-reason':
        assert reason() in told, 'It is told the real reason'
    elif expected == 'told-no-reply':
        assert 'without a reply' in told
    elif expected == 'handed':
        assert info['text'] in told, 'It is handed the reply'
    elif expected == 'told-lost':
        assert "answer couldn't be delivered" in told and message_row(info['message'])['reason'] in told, 'It is told why the answer was lost'
        assert info['text'] not in told, 'and is not handed the text'


def check_owner(info, expected):
    settled(info)
    identity = info['message']
    current = message_row(identity)
    assert bool(coordination.awaiting_owner(current)) == (expected == 'accepted'), 'The message awaits the owner exactly when its actions are offered'
    for action in ('resend', 'done'):
        with db.connection() as conn:
            with conn.transaction():
                try:
                    coordination.resolve_failed(conn, identity, action, current['delivery_revision'])
                    outcome = 'accepted'
                except HTTPException as refusal:
                    assert refusal.status_code == 409
                    outcome = 'refused'
                raise psycopg.Rollback()
        assert outcome == expected, f'{action} was {outcome}, it should be {expected}'


def check_alert(info, expected):
    settled(info)
    item = alert(info['message'])
    if expected == 'none':
        assert not (item and item['active']), 'No alert is open'
    elif expected == 'closed':
        assert item and not item['active'], 'The alert exists and is closed'
    else:
        assert item and item['active'] and item['needs_owner'], 'The alert is open in "Needs you"'
        assert item['title'].startswith('Your resent message') == (expected == 'open-resent')


def check_open_work(info, expected):
    with db.connection() as conn:
        found = conn.execute('SELECT 1 FROM teammate_messages m WHERE m.id=%s AND ' + service.OPEN_TEAMMATE_MESSAGE, (info['message'],)).fetchone()
    assert bool(found) == expected, 'It is counted as open work' if expected else 'It is not counted as open work'


@pytest.mark.parametrize('case,column', CELLS)
def test_every_message_state_behaves_as_the_map_says(work, monkeypatch, case, column):
    build, cells = CASES[case]
    info = build(work, monkeypatch)
    expected, reason = cells[column], cells.get('reason')
    if column == 'inbox':
        check_inbox(work, info, expected)
    elif column == 'scheduler':
        check_scheduler(work, info, expected, reason)
    elif column == 'waiter':
        check_waiter(work, info, expected, reason)
    elif column == 'owner':
        check_owner(info, expected)
    elif column == 'alert':
        check_alert(info, expected)
    else:
        check_open_work(info, expected)


# Each fix has its own test.

@pytest.mark.parametrize('breaks', ['release', 'dirty'])
def test_a_failure_that_cannot_be_recorded_never_stops_the_dispatch_pass(work, monkeypatch, breaks):
    from backend import attention
    parent = worker.claim_new()[0]
    failing = UUID(invoke(parent, 'message', {'bot_id': 'reviewer', 'prompt': 'Cannot be checked'})['message_id'])  # Sorts first in the pass.
    healthy = UUID(invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Healthy'})['message_id'])
    with db.connection() as conn:
        conn.execute('UPDATE teammate_messages SET attempts=2 WHERE id=%s', (failing,))  # Its next failure is its third.
    fail_checks_for(monkeypatch, {failing})
    release, dirty = coordination.release, attention.dirty

    def cannot_release(conn, message, *rest):
        if message['id'] == failing:
            raise RuntimeError('the give-up cannot be recorded')
        return release(conn, message, *rest)

    def cannot_alert(conn, kind, identity):
        if identity == failing:
            raise RuntimeError('the alert cannot be recorded')
        return dirty(conn, kind, identity)
    if breaks == 'release':
        monkeypatch.setattr(coordination, 'release', cannot_release)
    else:
        monkeypatch.setattr(attention, 'dirty', cannot_alert)
    try:
        claimed = worker.claim_new()
    except Exception as exc:
        pytest.fail(f'One message that cannot record its failure stopped the dispatch pass: {exc!r}')
    assert [a['bot_id'] for a in claimed] == ['writer'], 'The healthy message to another bot gets its reply task in the same pass'
    assert message_row(healthy)['response_task_id'] == claimed[0]['task_id']
    held = message_row(failing)
    assert (held['status'], held['attempts']) == ('pending', 2), 'Nothing of the failed recording was kept'
    wait = (held['not_before'] - service.now()).total_seconds()
    assert coordination.RECORDING_PAUSE - 10 < wait <= coordination.RECORDING_PAUSE, f'It is paused about {coordination.RECORDING_PAUSE} seconds, not {wait}'


def test_a_waiter_whose_only_answer_was_given_up_and_marked_done_is_told_it_was_lost(work, monkeypatch):
    info = asked_and_answered(work, text='The answer text')
    fail_reply_checks(work, monkeypatch, info['message'])
    assert resolve(info['message'], 'done').status_code == 200
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued'
    told = row(work['id'])['continuation']
    assert f"{name('writer')}'s answer couldn't be delivered" in told and given_up_reason() in told
    assert 'The answer text' not in told, 'It is not handed the text of an answer it may not see'


def test_a_waiter_whose_answer_is_paused_keeps_waiting_and_gets_the_text_when_the_pause_ends(work):
    info = asked_and_answered(work, text='The answer text')
    with db.connection() as conn:
        conn.execute("UPDATE teammate_messages SET not_before=now()+interval '1 hour',attempts=1 WHERE id=%s", (info['message'],))
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'waiting_results', 'The answer is still in its retry pause'
    clear_pauses(info['message'])
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued' and 'The answer text' in row(work['id'])['continuation']


def test_a_waiter_is_given_the_second_answer_when_the_first_was_cancelled(work, monkeypatch):
    info = asked_and_answered(work, text='First answer')
    fail_reply_checks(work, monkeypatch, info['message'])
    with db.connection() as conn:
        coordination.send(conn, service.locked_task(conn, info['answerer']['task_id']), {'bot_id': 'researcher', 'prompt': 'Second answer', 'reply_to': info['question']})
    worker.advance_waiters()
    assert row(work['id'])['status'] == 'queued'
    told = row(work['id'])['continuation']
    assert 'Second answer' in told and 'First answer' not in told


def test_a_resent_reply_is_not_kept_for_a_recipient_whose_only_task_gets_no_inbox(work, monkeypatch):
    info = reply_resent_without_a_reader(work, monkeypatch, other_task=True)
    worker.claim_new()
    assert_dropped_again(info['message'])


def test_work_counts_as_unfinished_while_its_message_is_unlinked_or_its_reply_task_ended_but_not_while_one_is_live():
    from backend import recurring_management
    from tests.test_recurring_management import managed
    _, _, _, task = managed()
    with db.connection() as conn:
        coordination.send(conn, task, {'bot_id': 'writer', 'prompt': 'Explain the existing evidence only.'})
        message = conn.execute('SELECT id FROM teammate_messages').fetchone()['id']

    def pending():
        with db.connection() as conn:
            return recurring_management.pending_messages(conn, task['work_item_id'])
    assert pending(), 'Unlinked: still open'
    with db.connection() as conn:
        coordination.schedule_inboxes(conn)
        linked = conn.execute('SELECT response_task_id FROM teammate_messages WHERE id=%s', (message,)).fetchone()['response_task_id']
    assert linked and not pending(), 'Linked to a live reply task: that task counts instead'
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='failed' WHERE id=%s", (linked,))
    assert pending(), 'Linked to a reply task that ended, before the scheduler has reclaimed it: still open'


def test_a_reply_is_still_shown_when_the_task_that_wrote_it_was_stopped_afterwards(work):
    info = asked_and_answered(work, finish=False)
    stop_reply(info['answerer'])
    assert info['message'] in read_inbox(work['id']), 'The answer was already written'
    assert message_row(info['message'])['status'] == 'pending'


def test_a_reply_is_still_shown_when_the_task_that_wrote_it_is_past_its_deadline(work):
    info = asked_and_answered(work)
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET deadline=now()-interval '1 hour' WHERE id=%s", (info['answerer']['task_id'],))
    assert info['message'] in read_inbox(work['id'])
    assert message_row(info['message'])['status'] == 'pending'


def test_an_original_message_from_a_stopped_sender_is_still_dropped(work):
    child, helper, message = helper_message(work)
    stop(child, helper)
    worker.claim_new()
    assert message_row(message)['status'] == 'cancelled' and message_row(message)['reason'] == f"{name('writer')}'s task was stopped"


@pytest.mark.parametrize('stale', ['cancelled', 'another-bot'])
def test_acknowledging_skips_a_stale_id_and_records_the_rest_without_counting(work, stale):
    parent = worker.claim_new()[0]
    valid = UUID(invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Valid'})['message_id'])
    reply = reply_attempt()
    other = UUID(invoke(parent, 'message', {'bot_id': 'writer' if stale == 'cancelled' else 'reviewer', 'prompt': 'Stale'})['message_id'])
    if stale == 'cancelled':
        with db.connection() as conn:
            conn.execute("UPDATE teammate_messages SET status='cancelled',released='dropped',reason='Dropped' WHERE id=%s", (other,))
    with db.connection() as conn:
        before = {r['id']: (r['attempts'], r['not_before']) for r in conn.execute('SELECT id,attempts,not_before FROM teammate_messages').fetchall()}
    recorded = read_by(reply, other, valid)
    assert recorded == [valid], 'Exactly the valid one is recorded'
    assert message_row(valid)['status'] == 'delivered' and message_row(other)['status'] != 'delivered'
    assert [e['data']['message_id'] for e in events('teammate.message_delivered')] == [str(valid)], 'One delivered event'
    with db.connection() as conn:
        after = {r['id']: (r['attempts'], r['not_before']) for r in conn.execute('SELECT id,attempts,not_before FROM teammate_messages').fetchall()}
    assert after == before, 'Acknowledging changes no count and no pause'


def test_a_reply_task_gets_at_most_thirty_messages_and_the_rest_get_the_next_without_a_failure(work):
    parent = worker.claim_new()[0]
    sent = [UUID(invoke(parent, 'message', {'bot_id': 'writer', 'prompt': f'message {n}'})['message_id']) for n in range(32)]
    with db.connection() as conn:
        for n, identity in enumerate(sent):  # Oldest first, in the order sent.
            conn.execute('UPDATE teammate_messages SET created_at=now()-make_interval(secs=>%s) WHERE id=%s', (100 - n, identity))
    first = reply_attempt()
    linked = [m for m in sent if message_row(m)['response_task_id'] == first['task_id']]
    assert linked == sent[:30], 'The oldest thirty are linked'
    assert [(message_row(m)['response_task_id'], message_row(m)['attempts']) for m in sent[30:]] == [(None, 0)] * 2, 'The other two wait, uncounted'
    newer = UUID(invoke(parent, 'message', {'bot_id': 'writer', 'prompt': 'Sent after'})['message_id'])
    assert set(read_inbox(first['task_id'])) == set(linked), 'Its inbox lists the messages linked to it before newer ones'
    read_by(first, *linked)
    complete(first)
    second = reply_attempt()
    assert second['task_id'] != first['task_id']
    assert [message_row(m)['response_task_id'] for m in sent[30:] + [newer]] == [second['task_id']] * 3, 'The rest get a second reply task'
    assert all(message_row(m)['attempts'] == 0 and not message_row(m)['last_error'] for m in sent + [newer]), 'Nothing was counted as a failure'
    assert events('teammate.message_failed') == []


def test_a_resent_message_that_was_read_is_no_longer_marked_resent_and_a_later_bounce_raises_no_alert(work, monkeypatch):
    message = give_up(work, monkeypatch)
    assert resolve(message, 'resend').status_code == 200
    assert message_row(message)['owner_action'] == 'resend'
    reply = reply_attempt()
    read_by(reply, message)
    read = message_row(message)
    assert (read['status'], read['owner_action'], read['owner_action_at']) == ('delivered', None, None), 'Reading it did what the resend asked'
    with db.connection() as conn:
        coordination.bounce_recipient(conn, 'writer', f"{name('writer')} is archived")
    bounced = message_row(message)
    assert (bounced['status'], bounced['released']) == ('cancelled', 'bounced')
    item = alert(message)
    assert not coordination.awaiting_owner(bounced) and not (item and item['active']), 'An ordinary bounce is not "your resent message"'


def test_the_scheduler_has_partial_indexes_on_the_live_messages_and_on_replies(work):
    with db.connection() as conn:
        assert {r['indexname'] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE tablename='teammate_messages'").fetchall()} >= {'teammate_messages_live', 'teammate_messages_reply_to'}
        parent = service.locked_task(conn, work['id'])
        question = UUID(coordination.send(conn, parent, {'bot_id': 'writer', 'prompt': 'Question'})['message_id'])
        columns = ','.join(r['column_name'] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='teammate_messages' "
                                                                   "AND column_name NOT IN ('id','created_at','status','released','reason','not_before','attempts')").fetchall())
        with conn.transaction():  # A table with plenty of old finished rows, as a long history has.
            conn.execute(f"INSERT INTO teammate_messages(id,created_at,status,released,reason,{columns}) SELECT gen_random_uuid(),now()-make_interval(secs=>n+10),'cancelled','dropped','Old',{columns} "
                         "FROM teammate_messages,generate_series(1,3000) n WHERE id=%s", (question,))
            conn.execute('ANALYZE teammate_messages')
            conn.execute('SET LOCAL enable_seqscan=off')
            live = ' '.join(r['QUERY PLAN'] for r in conn.execute("EXPLAIN SELECT * FROM teammate_messages WHERE status IN ('pending','delivered') AND reply_to IS NULL ORDER BY created_at").fetchall())
            replies = ' '.join(r['QUERY PLAN'] for r in conn.execute(f"EXPLAIN SELECT * FROM teammate_messages WHERE reply_to='{uuid4()}'").fetchall())
            raise psycopg.Rollback()
    assert 'teammate_messages_live' in live, live
    assert 'teammate_messages_reply_to' in replies, replies


def test_replies_and_acknowledgements_use_only_the_shared_paths():
    import ast
    from pathlib import Path
    functions = {top.name: top for top in ast.parse(Path(coordination.__file__).read_text()).body if isinstance(top, ast.FunctionDef)}

    def calls(name):
        return {node.func.id for node in ast.walk(functions[name]) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert 'answer' in calls('advance'), 'The waiter takes its answer from answer()'
    assert not [node.value for node in ast.walk(functions['advance']) if isinstance(node, ast.Constant) and isinstance(node.value, str) and 'reply_to' in node.value], \
        'advance reads replies only through answer'
    assert 'checked' in calls('answer') and 'checked' in calls('inbox'), 'Both go through the shared check'
    assert not {'inbox', 'checked'} & calls('delivered'), 'Acknowledging neither recomputes the inbox nor checks, counts or releases'


# Random sequences of everything that can happen to a message, with the rules checked after every step.

STOPPED = {'cancelled', 'cancelling', 'interrupted'}


@pytest.mark.parametrize('seed', range(60))
def test_random_sequences_never_break_the_delivery_rules(work, monkeypatch, seed):
    import random
    from backend import attention
    rng = random.Random(seed)
    child = delegate(work, bot='reviewer')
    attempts = worker.claim_new()
    senders = {'manager': next(a for a in attempts if a['bot_id'] == 'researcher'), 'helper': next(a for a in attempts if a['bot_id'] == 'reviewer')}
    sources = {'manager': work['id'], 'helper': child}
    broken, raised, sent, log = set(), set(), [], []
    unrecordable, unrecorded = set(), set()  # Sources whose messages can't record a release, and the messages that hit that in this step.
    blocker, release = coordination.delivery_blocker, coordination.release

    def flaky(conn, message, source):
        if message['source_task_id'] in broken:
            raised.add(message['id'])
            raise RuntimeError('injected failure')
        return blocker(conn, message, source)
    monkeypatch.setattr(coordination, 'delivery_blocker', flaky)

    def unreleasable(conn, message, *rest):
        if message['source_task_id'] in unrecordable:
            unrecorded.add(message['id'])
            raise RuntimeError('injected failure while recording')
        return release(conn, message, *rest)
    monkeypatch.setattr(coordination, 'release', unreleasable)

    def dispatch():
        try:
            return worker.claim_new()
        except Exception as exc:
            pytest.fail(f'{log}: a dispatch pass failed because of one message: {exc!r}')

    def rows():
        with db.connection() as conn:
            return {r['id']: r for r in conn.execute('SELECT m.*, t.status AS source_status, r.status AS reply_status FROM teammate_messages m JOIN tasks t ON t.id=m.source_task_id LEFT JOIN tasks r ON r.id=m.response_task_id').fetchall()}

    def writer_status():
        with db.connection() as conn:
            return conn.execute("SELECT status FROM bots WHERE id='writer'").fetchone()['status']

    def step():
        raised.clear()
        unrecorded.clear()
        live = [k for k in senders if row(sources[k])['status'] not in STOPPED | set(service.TERMINAL)]
        # Weighted so sequences reach the hard states: three failures in a row, giving up, resends and bounces.
        choices = ['schedule'] * 4 + ['finish_reply'] * 2 + ['toggle_failure', 'toggle_recording', 'archive', 'restore'] + ['resolve'] * 3 + ['inbox_read'] * 2
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
            dispatch()
            for identity, after in rows().items():
                old = before.get(identity)
                if old and old['response_task_id'] is None and after['response_task_id'] is not None:  # Attached in this pass.
                    assert not (old['not_before'] and old['not_before'] > now), f'{log}: attached during its retry pause'
                    assert old['source_status'] not in STOPPED, f'{log}: attached from a stopped sender'
                    assert old['source_task_id'] not in broken, f'{log}: attached without passing its check'
                    assert status == 'active', f'{log}: attached while the recipient was {status}'
            for identity, after in rows().items():  # A message a failed check kept (and paused) is still counted as one that failed.
                assert not (after['status'] == 'pending' and after['reply_status'] in service.TERMINAL and identity not in raised | unrecorded), f'{log}: a message stayed claimed by a reply task that ended'
        elif action == 'finish_reply':
            with db.connection() as conn:
                running = conn.execute("SELECT id FROM attempts WHERE bot_id='writer' AND status=ANY(%s)", (list(service.ACTIVE_ATTEMPTS),)).fetchall()
            for attempt in running:
                worker.settle(attempt['id'], rng.choice([{'status': 'completed', 'output': 'Answered'}, {'status': 'failed', 'error': 'Cannot answer'}]))  # Neither reads the message.
        elif action == 'inbox_read':
            with db.connection() as conn:
                running = conn.execute("SELECT task_id FROM attempts WHERE bot_id='writer' AND status=ANY(%s)", (list(service.ACTIVE_ATTEMPTS),)).fetchall()
            for attempt in running:
                with db.connection() as conn:
                    coordination.inbox(conn, service.locked_task(conn, attempt['task_id']))
        elif action == 'toggle_failure':
            broken.symmetric_difference_update({sources[rng.choice(list(sources))]})
        elif action == 'toggle_recording':
            unrecordable.symmetric_difference_update({sources[rng.choice(list(sources))]})
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
            if identity in unrecorded:  # Its failure couldn't be recorded: it is paused so it isn't retried every pass, and dispatch went on.
                assert r['status'] != 'cancelled' and r['not_before'] and r['not_before'] > now, f'{log}: a failure that could not be recorded did not pause the message'
            elif identity in raised:
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


# Whatever happens to the messages a task waits on, it never stays parked once nothing it named can still arrive.

@pytest.mark.parametrize('seed', range(30))
def test_random_waits_always_resume(work, monkeypatch, seed):
    import random
    rng = random.Random(seed)
    sender = work['id']
    mine = {'attempt': worker.claim_new()[0]}  # None while the sender is parked or queued.
    sent, log, broken = [], [], [False]
    blocker = coordination.delivery_blocker

    def flaky(conn, message, source):
        if broken[0] and (message['source_task_id'] == sender or message['reply_to']):  # Replies can fail their checks too.
            raise RuntimeError('injected failure')
        return blocker(conn, message, source)
    monkeypatch.setattr(coordination, 'delivery_blocker', flaky)

    def writers():
        with db.connection() as conn:
            return conn.execute("SELECT id,task_id,bot_id,session_id FROM attempts WHERE bot_id='writer' AND status=ANY(%s)", (list(service.ACTIVE_ATTEMPTS),)).fetchall()

    def step():
        with db.connection() as conn:
            open_alerts = [r['id'] for r in conn.execute('SELECT * FROM teammate_messages').fetchall() if coordination.awaiting_owner(r)]
        choices = ['schedule'] * 3 + ['reply'] * 6 + ['toggle_failure'] * 3 + ['advance'] * 2 + ['resolve'] * 4 * bool(open_alerts)
        choices += ['read_inbox'] * 3 if row(sender)['status'] not in service.TERMINAL else []  # Its own work reads the replies it got, or fails to.
        choices += ['end'] * 3 if mine['attempt'] and len(log) > 12 else []  # Finishes without waiting, so nothing is left to read a later reply.
        choices += ['send_wait'] * 3 if mine['attempt'] else []
        choices += ['wait_again'] * 2 if mine['attempt'] and sent else []  # Waits on messages already sent, nothing new.
        action = rng.choice(choices)
        log.append(action)
        if action == 'send_wait':
            attempt = mine['attempt']
            sent.append(UUID(invoke(attempt, 'message', {'bot_id': 'writer', 'prompt': f'message {len(sent)}'})['message_id']))
            waited = {sent[-1]} | ({rng.choice(sent[:-1])} if len(sent) > 1 and rng.random() < 0.5 else set())
            invoke(attempt, 'wait', {'message_ids': sorted(waited)})
            complete(attempt)
            mine['attempt'] = None
        elif action == 'read_inbox':
            for _ in range(rng.choice([1, 3])):
                with db.connection() as conn:
                    conn.execute('UPDATE teammate_messages SET not_before=NULL')
                read_inbox(sender)
        elif action == 'end':
            complete(mine['attempt'])
            mine['attempt'] = None
        elif action == 'wait_again':
            invoke(mine['attempt'], 'wait', {'message_ids': rng.sample(sent, rng.randint(1, min(2, len(sent))))})
            complete(mine['attempt'])
            mine['attempt'] = None
        elif action == 'schedule':
            with db.connection() as conn:
                conn.execute('UPDATE teammate_messages SET not_before=NULL')
            mine['attempt'] = next((a for a in worker.claim_new() if a['task_id'] == sender), mine['attempt'])
            with db.connection() as conn:  # A resent reply is pending only while its recipient has unfinished work (unless its check just failed).
                stranded = conn.execute("SELECT m.id FROM teammate_messages m WHERE m.status='pending' AND m.reply_to IS NOT NULL AND m.owner_action='resend' "
                                        "AND NOT EXISTS(SELECT 1 FROM tasks t WHERE t.bot_id=m.recipient AND t.conversation_id=m.conversation_id AND t.status<>ALL(%s))",
                                        (list(service.TERMINAL),)).fetchall()
            assert broken[0] or not stranded, f'{log}: a resent reply is pending with nobody left to read it'
        elif action == 'reply':
            for attempt in writers():
                kind = rng.choice(['unread', 'failed', 'read', 'read', 'read', 'replied', 'replied', 'replied'])
                if kind == 'replied':  # A real reply to a message this task was started for, sent before it is settled.
                    with db.connection() as conn:
                        asked = conn.execute('SELECT m.id FROM teammate_messages m WHERE m.response_task_id=%s AND m.status=%s AND m.reply_to IS NULL '
                                             'AND NOT EXISTS(SELECT 1 FROM teammate_messages x WHERE x.reply_to=m.id)', (attempt['task_id'], 'pending')).fetchall()
                    if asked:
                        invoke(attempt, 'message', {'bot_id': 'researcher', 'prompt': f'reply {len(log)}', 'reply_to': rng.choice(asked)['id']})
                if kind == 'read':
                    with db.connection() as conn:
                        task = service.locked_task(conn, attempt['task_id'])
                        stale = [r['id'] for r in conn.execute("SELECT id FROM teammate_messages WHERE status='cancelled' ORDER BY created_at DESC LIMIT 1").fetchall()] or [uuid4()]  # A cancelled message's id when there is one, else one nobody has.
                        shown = [m['id'] for m in coordination.inbox(conn, task) if m['status'] == 'pending']
                        recorded = coordination.delivered(conn, task, shown + stale)  # The stale id is skipped, never failing the rest.
                    assert set(recorded) == set(shown), f'{log}: acknowledging with a stale id did not record exactly the shown messages'
                worker.settle(attempt['id'], {'status': 'failed', 'error': 'Cannot answer'} if kind == 'failed' else {'status': 'completed', 'output': 'Read, no reply'})
        elif action == 'toggle_failure':
            broken[0] = not broken[0]
        elif action == 'resolve':
            identity = rng.choice(open_alerts)
            with db.connection() as conn:
                coordination.resolve_failed(conn, identity, rng.choice(['resend', 'done']), message_row(identity)['delivery_revision'])
        else:
            worker.advance_waiters()

    def unsettled(identity):  # Written out here, not through the product's helpers.
        with db.connection() as conn:
            r = conn.execute('SELECT m.status,t.status AS reply_status FROM teammate_messages m LEFT JOIN tasks t ON t.id=m.response_task_id WHERE m.id=%s', (identity,)).fetchone()
            replies = conn.execute('SELECT status,(not_before IS NOT NULL AND not_before>now()) AS paused FROM teammate_messages WHERE reply_to=%s', (identity,)).fetchall()
        if replies:  # An answer that was read, or can be read now, settles it; so does one that was released. A paused one is still on its way.
            usable = any(x['status'] == 'delivered' or (x['status'] == 'pending' and not x['paused']) for x in replies)
            return not usable and any(x['status'] == 'pending' and x['paused'] for x in replies)
        return r['status'] == 'pending' or (r['status'] == 'delivered' and (r['reply_status'] is None or r['reply_status'] not in service.TERMINAL))

    def check():
        with db.connection() as conn:
            for r in conn.execute("SELECT id,released,reason FROM teammate_messages WHERE status='cancelled'").fetchall():
                assert r['released'] and r['reason'], f'{log}: cancelled without a reason'
        if log[-1] == 'advance' and row(sender)['status'] == 'waiting_results':
            with db.connection() as conn:
                waits = [w['message_id'] for w in conn.execute('SELECT message_id FROM coordination_waits WHERE task_id=%s AND message_id IS NOT NULL', (sender,)).fetchall()]
            assert any(unsettled(w) for w in waits), f'{log}: parked with nothing left to wait for'

    for _ in range(40):
        step()
        check()
