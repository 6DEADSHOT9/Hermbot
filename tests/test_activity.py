"""Activity separates observed progress from runtime contact and prior execution history."""
from datetime import timedelta

import pytest

from backend import activity, db, service, worker
from tests.test_execution_capabilities import inventory  # noqa: F401


def test_connection_contact_and_admission_are_not_progress(make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        conn.execute('UPDATE attempts SET started_at=now(),last_contact_at=now() WHERE id=%s', (attempt['id'],))
        db.event(conn, 'tool.admitted', task, data={'tool': 'read_file', 'tool_call_id': 'one'})
        first = activity.snapshots(conn, [task['id']])[task['id']]
        assert first['last_contact_at'] and first['last_progress_at'] is None
        assert first['last_tool_event'] == 'tool.admitted'
        db.event(conn, 'tool.started', task, data={'tool': 'read_file'})
        assert activity.snapshots(conn, [task['id']])[task['id']]['last_progress_at'] is None
        db.event(conn, 'tool.completed', task, data={'tool': 'read_file', 'error': False})
        completed = activity.snapshots(conn, [task['id']])[task['id']]
        conn.execute("UPDATE attempts SET last_contact_at=now()+interval '60 seconds' WHERE id=%s", (attempt['id'],))
        later = activity.snapshots(conn, [task['id']])[task['id']]
        assert later['last_progress_at'] == completed['last_progress_at']
        assert later['last_contact_at'] > later['last_progress_at']


def test_activity_cannot_borrow_progress_from_another_task_or_prior_attempt(make_task):
    task, other = make_task(), make_task(bot='writer')
    attempts = worker.claim_new()
    with db.connection() as conn:
        db.event(conn, 'tool.completed', task, data={'tool': 'old_execution'})
        db.event(conn, 'tool.completed', other, data={'tool': 'other_bot'})
        own = next(a for a in attempts if a['task_id'] == task['id'])
        conn.execute('UPDATE attempts SET created_at=%s WHERE id=%s', (service.now() + timedelta(seconds=1), own['id']))
        snapshot = activity.snapshots(conn, [task['id']])[task['id']]
        assert snapshot['last_progress_at'] is None and snapshot['last_tool'] is None


def test_model_capacity_tracks_each_request_without_claiming_progress(make_task):
    task = make_task()
    worker.claim_new()
    with db.connection() as conn:
        for request in ('one', 'two'):
            db.event(conn, 'model.request.waiting', task, data={'request_id': request})
        db.event(conn, 'model.request.active', task, data={'request_id': 'one'})
        snapshot = activity.snapshots(conn, [task['id']])[task['id']]
        assert snapshot['waiting_model_requests'] == 1 and snapshot['last_progress_at'] is None
        db.event(conn, 'model.request.finished', task, data={'request_id': 'two'})
        assert activity.snapshots(conn, [task['id']])[task['id']]['waiting_model_requests'] == 0


@pytest.mark.usefixtures('inventory')
def test_private_context_records_framework_admission_once(make_task):
    from fastapi.testclient import TestClient
    from backend import app as application
    from backend.settings import settings
    task = make_task()
    attempt = worker.claim_new()[0]
    client = TestClient(application.app)
    headers = {'Authorization': 'Bearer ' + settings.bot_token(task['bot_id'])}
    body = {'session_id': attempt['session_id'], 'tool_call_id': 'framework-call', 'tool_name': 'hermbot_prepare'}
    assert client.post('/internal/context', json=body).status_code == 401
    for _ in range(2):
        assert client.post('/internal/context', json=body, headers=headers).status_code == 200
    client.close()
    with db.connection() as conn:
        rows = conn.execute("SELECT data FROM events WHERE task_id=%s AND kind='tool.admitted'", (task['id'],)).fetchall()
        assert len(rows) == 1
        assert rows[0]['data'] == {'tool': 'hermbot_prepare', 'tool_call_id': 'framework-call'}
