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
