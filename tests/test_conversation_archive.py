"""Conversation removal retains evidence and fences new or unfinished work."""
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend import app as application, db, service
from backend.settings import settings


@pytest.fixture
def owner():
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    yield client
    client.close()


def group_task(owner):
    group = owner.post('/api/v1/groups', json={'title': 'Archive fixture', 'bot_ids': ['researcher', 'writer']}).json()
    task = owner.post('/api/v1/tasks', headers={'Idempotency-Key': str(uuid4())}, json={'bot_id': 'researcher', 'conversation_id': group['id'], 'prompt': 'Local fixture'}).json()
    return group, task


def test_group_removal_preserves_history_blocks_new_work_and_can_restore(owner):
    group, task = group_task(owner)
    other = owner.post('/api/v1/groups', json={'title': 'Keep this group', 'bot_ids': ['researcher', 'writer']}).json()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))
    messages = owner.get('/api/v1/conversations/' + group['id']).json()['messages']
    assert owner.delete('/api/v1/groups/' + group['id']).status_code == 200
    assert owner.delete('/api/v1/groups/' + group['id']).status_code == 200
    visible = {g['id'] for g in owner.get('/api/v1/state').json()['conversations']}
    assert group['id'] not in visible and other['id'] in visible
    assert owner.get('/api/v1/conversations/' + group['id']).json()['messages'] == messages
    assert owner.get('/api/v1/tasks/' + task['id']).json()['status'] == 'completed'
    response = owner.post('/api/v1/tasks', headers={'Idempotency-Key': str(uuid4())}, json={'bot_id': 'researcher', 'conversation_id': group['id'], 'prompt': 'New work'})
    assert response.status_code == 409
    assert owner.put('/api/v1/groups/' + group['id'], json={'title': 'Changed', 'bot_ids': ['researcher', 'writer']}).status_code == 404
    assert owner.post('/api/v1/conversations/' + group['id'] + '/restore').status_code == 200
    assert group['id'] in {g['id'] for g in owner.get('/api/v1/state').json()['conversations']}


@pytest.mark.parametrize('busy', ['task', 'native_execution', 'connector_proposal'])
def test_removal_rejects_unsettled_work(owner, busy):
    group, task = group_task(owner)
    if busy == 'connector_proposal':
        from tests.test_phase4 import connection, proposal
        proposal(task, connection())
    with db.connection() as conn:
        if busy != 'task':
            conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))
        if busy == 'native_execution':
            conn.execute("INSERT INTO attempts(id,task_id,bot_id,number,session_id,status,idempotency_key,input) VALUES (%s,%s,'researcher',1,%s,'running',%s,'fixture')", (uuid4(), task['id'], str(uuid4()), str(uuid4())))
    assert owner.delete('/api/v1/groups/' + group['id']).status_code == 409
    assert group['id'] in {g['id'] for g in owner.get('/api/v1/state').json()['conversations']}


def test_pending_review_is_cancelled_before_removal_without_effect(owner):
    from tests.test_phase4 import connection, proposal
    group, task = group_task(owner)
    proposal(task, connection())
    assert owner.post('/api/v1/tasks/' + task['id'] + '/stop').status_code == 200
    assert owner.delete('/api/v1/groups/' + group['id']).status_code == 200
    with db.connection() as conn:
        operation = conn.execute('SELECT status,result,lease_until FROM connector_operations WHERE task_id=%s', (task['id'],)).fetchone()
        assert operation['status'] == 'cancelled' and operation['result'] is None and operation['lease_until'] is None


def test_owner_only_removal_and_active_dm_protection(owner):
    group, task = group_task(owner)
    public = TestClient(application.app)
    try:
        assert public.delete('/api/v1/groups/' + group['id']).status_code == 401
    finally:
        public.close()
    with db.connection() as conn:
        dm = conn.execute("SELECT id FROM conversations WHERE bot_id='researcher' AND kind='dm'").fetchone()['id']
    assert owner.delete('/api/v1/groups/' + str(dm)).status_code == 404
    assert owner.delete('/api/v1/conversations/' + str(dm)).status_code == 409
    with db.connection() as conn:
        conn.execute("UPDATE bots SET status='deleted',desired_state='stopped' WHERE id='researcher'")
        service.cancel(conn, service.locked_task(conn, task['id']))
    assert owner.delete('/api/v1/conversations/' + str(dm)).status_code == 200
    assert str(dm) not in {g['id'] for g in owner.get('/api/v1/state').json()['conversations']}
