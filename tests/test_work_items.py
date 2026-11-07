"""Conversation intent, work grouping and execution authority remain independent."""
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend import app as application, db, service, worker, work_items, work_intake
from backend.settings import settings
from tests.conftest import row


@pytest.fixture
def owner():
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    yield client
    client.close()


def submit(owner, prompt='What do you think?', **options):
    with db.connection() as conn:
        conversation = conn.execute("SELECT id FROM conversations WHERE bot_id='researcher'").fetchone()['id']
    response = owner.post('/api/v1/messages', headers={'Idempotency-Key': options.pop('key', str(uuid4()))}, json={'bot_id': 'researcher', 'conversation_id': str(conversation), 'prompt': prompt, **options})
    assert response.status_code == 202, response.text
    return response.json()


def route_task(task, intent, **options):
    with db.connection() as conn:
        return work_items.route(conn, service.locked_task(conn, task['id']), {'intent': intent, **options})


def settle(task):
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))


def test_auto_discussion_is_interpreted_before_execution_without_creating_work(owner):
    task = submit(owner)
    assert task['message_intent'] == 'auto' and task['work_item_id'] is None
    assert owner.get('/api/v1/state').json()['work_items'] == []
    assert worker.claim_new() == []
    with db.connection() as conn:
        work_intake.apply(conn, task['id'], work_intake.Interpretation(operations=[work_intake.InterpretationOperation(intent='discussion')]))
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        service.tool_action(conn, 'researcher', attempt['session_id'], 'finish', {'result': 'Thoughts'}, 'finish')
    assert row(task['id'])['pending_result'] == 'Thoughts'
    assert owner.get('/api/v1/state').json()['work_items'] == []


def test_assignment_intake_is_idempotent_and_cannot_be_changed_by_bot(owner):
    task = submit(owner, 'Find appropriate competitions')
    with db.connection() as conn:
        value = work_intake.Interpretation.model_validate({'operations': [{'intent': 'assignment', 'title': 'Competition comparison', 'requirements': [{'key': 'comparison', 'title': 'Comparison', 'kind': 'report'}]}]})
        work_intake.apply(conn, task['id'], value)
        work_intake.apply(conn, task['id'], value)
    attempt = worker.claim_new()[0]
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.tool_action(conn, 'researcher', attempt['session_id'], 'route_message', {'intent': 'assignment'}, 'different-route')
    state = owner.get('/api/v1/state').json()
    assert len(state['work_items']) == 1
    assert state['work_items'][0]['title'] == 'Competition comparison'
    assert str(row(task['id'])['work_item_id']) == state['work_items'][0]['id']


def test_followup_reopens_same_card_status_and_discussion_do_not(owner):
    original = submit(owner, 'Build comparison', intent='assignment')
    settle(original)
    update = submit(owner, 'Include deadlines', intent='update', work_item_id=original['work_item_id'])
    assert update['root_id'] != original['root_id']
    assert update['work_item_id'] == original['work_item_id']
    settle(update)
    status = submit(owner, 'How is it going?', intent='status', work_item_id=original['work_item_id'])
    submit(owner, 'Thanks', intent='discussion')
    items = owner.get('/api/v1/state').json()['work_items']
    assert len(items) == 1 and items[0]['status'] == 'completed'
    assert items[0]['current_task']['id'] == update['id']
    assert len(items[0]['executions']) == 2
    assert status['message_intent'] == 'status'


def test_concurrent_duplicate_message_submission_cannot_create_duplicate_card(owner):
    key = str(uuid4())
    first = submit(owner, 'Create a report', intent='assignment', key=key)
    again = submit(owner, 'Create a report', intent='assignment', key=key)
    assert first['id'] == again['id']
    assert len(owner.get('/api/v1/state').json()['work_items']) == 1
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM messages').fetchone()['n'] == 1


def test_other_conversation_and_ambiguous_reference_are_rejected(owner, make_task):
    other = make_task('writer')
    task = submit(owner, 'Include deadlines')
    for args in ({'work_item_id': other['work_item_id']}, {}):
        with pytest.raises(HTTPException):
            route_task(task, 'update', **args)
    assert row(task['id'])['message_intent'] == 'auto'
    route_task(task, 'discussion')
    assert row(task['id'])['work_item_id'] is None


def test_active_revision_is_steered_once_without_parallel_assignment(owner):
    original = submit(owner, 'Create report', intent='assignment')
    revision = submit(owner, 'Keep the report short', intent='update', work_item_id=original['work_item_id'])
    assert revision['message_intent'] == 'steered'
    with db.connection() as conn:
        controls = conn.execute('SELECT * FROM controls WHERE task_id=%s', (original['id'],)).fetchall()
    assert len(controls) == 1 and controls[0]['input'] == 'Owner direction: Keep the report short'
    assert len(owner.get('/api/v1/state').json()['work_items'][0]['executions']) == 1


@pytest.mark.parametrize('intent', ['discussion', 'status'])
def test_conversation_response_cannot_delegate_publish_or_mutate(owner, make_task, intent):
    from tests.test_phase4 import connection
    item = make_task()
    settle(item)
    task = submit(owner, 'Talk about it', intent=intent, **({'work_item_id': str(item['work_item_id'])} if intent == 'status' else {}))
    attempt = worker.claim_new()[0]
    account = connection(provider='google', actions=['gmail.send'])
    calls = [('delegate', {'bot_id': 'writer', 'prompt': 'Do work'}), ('publish', {'name': 'hidden', 'content': 'work'}), ('connector', {'connection_id': account['id'], 'action': 'gmail.send', 'arguments': {'to': 'test@example.com', 'subject': 'test', 'body': 'test'}})]
    for operation, args in calls:
        with pytest.raises(HTTPException) as error:
            with db.connection() as conn:
                service.tool_action(conn, 'researcher', attempt['session_id'], operation, args, operation)
        assert error.value.status_code == 409
    assert row(task['id'])['work_item_id'] == (item['work_item_id'] if intent == 'status' else None)
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM connector_operations').fetchone()['n'] == 0


def test_unknown_external_outcome_blocks_new_revision_and_correction(owner, make_task):
    from tests.test_phase4 import connection, proposal
    task = make_task()
    proposal(task, connection())
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))
        conn.execute("UPDATE connector_operations SET status='unresolved' WHERE task_id=%s", (task['id'],))
    with db.connection() as conn:
        conversation = task['conversation_id']
    result = owner.post('/api/v1/messages', headers={'Idempotency-Key': str(uuid4())}, json={'bot_id': 'researcher', 'conversation_id': str(conversation), 'prompt': 'Try again', 'intent': 'update', 'work_item_id': str(task['work_item_id'])})
    assert result.status_code == 409
    assert owner.post('/api/v1/work-items/messages', json={'task_id': str(task['id']), 'intent': 'discussion'}).status_code == 409
    assert owner.get('/api/v1/state').json()['work_items'][0]['status'] == 'uncertain'


def test_delegation_shares_card_not_authority(owner, make_task):
    task = make_task('researcher', manager=True)
    with db.connection() as conn:
        child = service.delegate(conn, service.locked_task(conn, task['id']), 'writer', 'Write result')
    child = row(child['task_id'])
    assert child['work_item_id'] == task['work_item_id']
    assert child['root_id'] == task['root_id'] and child['grant_id'] == task['grant_id']
    assert len(owner.get('/api/v1/state').json()['work_items']) == 1


def test_owner_correction_hide_and_restore_preserve_history_and_execution(owner):
    task = submit(owner, 'A discussion', intent='assignment')
    settle(task)
    before = owner.get('/api/v1/conversations/' + task['conversation_id']).json()['messages']
    assert owner.post('/api/v1/work-items/'+task['work_item_id']+'/hide').status_code == 200
    assert row(task['id'])['status'] == 'completed'
    assert owner.post('/api/v1/work-items/'+task['work_item_id']+'/restore').status_code == 200
    assert owner.post('/api/v1/work-items/messages', json={'task_id': task['id'], 'intent': 'discussion'}).status_code == 200
    state = owner.get('/api/v1/state').json()
    assert all(w['hidden_at'] for w in state['work_items'])
    assert owner.post('/api/v1/work-items/messages', json={'task_id': task['id'], 'intent': 'assignment'}).status_code == 200
    after = owner.get('/api/v1/conversations/' + task['conversation_id']).json()['messages']
    assert [m['content'] for m in after] == [m['content'] for m in before]
    assert row(task['id'])['status'] == 'completed' and row(task['id'])['attempts'] == 0


def test_unrouted_context_has_only_conversation_candidates_and_thread_history(owner, make_task):
    other = make_task('writer')
    task = submit(owner, 'Why?')
    with db.connection() as conn:
        context = work_items.context(conn, service.locked_task(conn, task['id']))
    assert context['routing_required']
    assert str(other['work_item_id']) not in {w['id'] for w in context['candidates']}
    assert context['recent_messages'][-1]['content'] == 'Why?'


def test_followup_cannot_acquire_active_manager_authority(owner, make_task):
    original = make_task(manager=True)
    task = submit(owner, 'Change the team report')
    with pytest.raises(HTTPException) as error:
        route_task(task, 'update', work_item_id=original['work_item_id'])
    assert error.value.status_code == 409
    assert row(task['id'])['grant_id'] is None and row(task['id'])['message_intent'] == 'auto'
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM controls').fetchone()['n'] == 0


def test_owner_groups_settled_assignments_without_replaying_them(owner):
    first = submit(owner, 'Draft report', intent='assignment')
    second = submit(owner, 'Make it longer', intent='assignment')
    settle(first)
    settle(second)
    response = owner.post('/api/v1/work-items/messages', json={'task_id': second['id'], 'intent': 'update', 'work_item_id': first['work_item_id']})
    assert response.status_code == 200
    visible = [w for w in owner.get('/api/v1/state').json()['work_items'] if w['current_task'] and not w['hidden_at']]
    assert len(visible) == 1 and len(visible[0]['executions']) == 2
    assert row(second['id'])['root_id'] != row(first['id'])['root_id']
    assert row(second['id'])['attempts'] == 0 and row(second['id'])['status'] == 'completed'


def test_revision_grants_only_published_conversation_outputs(owner):
    from backend import library
    from backend.artifacts import ArtifactStore
    original = submit(owner, 'Draft report', intent='assignment')
    store = ArtifactStore()
    store.initialize()
    with db.connection() as conn:
        task = service.locked_task(conn, original['id'])
        public = library.save(conn, store, name='public.txt', data=b'public output', content_type='text/plain', task=task, conversation_id=task['conversation_id'])
        private = library.save(conn, store, name='private.txt', data=b'private input', content_type='text/plain', task=task)
    settle(original)
    revision = submit(owner, 'Revise report', intent='update', work_item_id=original['work_item_id'])
    with db.connection() as conn:
        grants = [r['artifact_id'] for r in conn.execute('SELECT artifact_id FROM artifact_access WHERE task_id=%s', (revision['id'],))]
    assert public['id'] in grants and private['id'] not in grants
    outputs = owner.get('/api/v1/state').json()['work_items'][0]['outputs']
    assert str(public['id']) in {a['id'] for a in outputs}
    assert str(private['id']) not in {a['id'] for a in outputs}


def test_history_grouping_can_restore_original_ids_without_execution(owner):
    first = submit(owner, 'Draft report', intent='assignment')
    second = submit(owner, 'Make it longer', intent='assignment')
    settle(first)
    settle(second)
    assert owner.post('/api/v1/work-items/messages', json={'task_id': second['id'], 'intent': 'update', 'work_item_id': first['work_item_id']}).status_code == 200
    assert owner.post('/api/v1/work-items/messages', json={'task_id': second['id'], 'intent': 'assignment', 'work_item_id': second['work_item_id']}).status_code == 200
    assert str(row(second['id'])['work_item_id']) == second['work_item_id']
    assert row(second['id'])['message_intent'] == 'assignment' and row(second['id'])['attempts'] == 0


def test_followup_does_not_supersede_or_duplicate_pending_external_draft(owner, make_task):
    from tests.test_phase4 import connection, proposal
    original = make_task()
    proposal(original, connection())
    task = submit(owner, 'Change that draft')
    with pytest.raises(HTTPException) as error:
        route_task(task, 'update', work_item_id=original['work_item_id'])
    assert error.value.status_code == 409
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM connector_operations').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM controls').fetchone()['n'] == 0
