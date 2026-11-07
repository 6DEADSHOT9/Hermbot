"""Obsolete prompts close without fabricating an answer or an external approval."""
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
import pytest

from backend import app, db, inputs, service, worker
from backend.models import AskArgs
from backend.settings import settings


def request_rows(task, *, attempt=None, expired=False):
    with db.connection() as conn:
        question = inputs.ask(conn, service.locked_task(conn, task['id']), AskArgs(prompt='Which file?').model_dump())
        identity = uuid4()
        conn.execute("INSERT INTO approvals(id,task_id,attempt_id,kind,payload,payload_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s,now()+interval '1 day')",
                     (identity, task['id'], attempt['id'] if attempt else None, 'runtime' if attempt else 'action', Jsonb({'command': 'local-test'}), 'a'*64))
        if expired:
            conn.execute("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=%s", (identity,))
            conn.execute("UPDATE input_requests SET expires_at=now()-interval '1 second' WHERE id=%s", (question['input_request_id'],))
    return identity, question['input_request_id']


@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled', 'interrupted', 'uncertain', 'cancelling'])
def test_closed_work_removes_pending_reviews_and_questions_without_a_decision(make_task, state):
    task = make_task()
    review_id, question_id = request_rows(task)
    with db.connection() as conn:
        conn.execute('UPDATE tasks SET status=%s WHERE id=%s', (state, task['id']))
    client = TestClient(app.app, headers={'Authorization': 'Bearer '+settings.owner_token})
    snapshot = client.get('/api/v1/state').json()
    client.close()
    assert not snapshot['input_requests'] and not snapshot['approvals']
    with db.connection() as conn:
        assert inputs.pending(conn, task['conversation_id']) == []
    worker.advance_waiters()
    with db.connection() as conn:
        review = conn.execute('SELECT * FROM approvals WHERE id=%s', (review_id,)).fetchone()
        question = conn.execute('SELECT * FROM input_requests WHERE id=%s', (question_id,)).fetchone()
        assert review['status'] == question['status'] == 'cancelled'
        assert review['decision'] is None and review['decided_at'] is None
        assert question['answer'] is None and question['answered_at'] is None
        assert conn.execute('SELECT status FROM tasks WHERE id=%s', (task['id'],)).fetchone()['status'] == state


def test_expiry_stops_waiting_work_instead_of_resuming_as_if_answered(make_task):
    task = make_task()
    review_id, question_id = request_rows(task, expired=True)
    worker.advance_waiters()
    worker.advance_waiters()
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM approvals WHERE id=%s', (review_id,)).fetchone()['status'] == 'expired'
        question = conn.execute('SELECT * FROM input_requests WHERE id=%s', (question_id,)).fetchone()
        assert question['status'] == 'expired' and question['answer'] is None
        saved = conn.execute('SELECT * FROM tasks WHERE id=%s', (task['id'],)).fetchone()
        assert saved['status'] == 'cancelled' and saved['stop_kind'] == 'review_expired'
        assert saved['continuation'] is None
    assert worker.claim_new() == []


def test_native_review_closes_when_its_attempt_ends_and_cannot_return_late(make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    payload = {'event': 'approval.request', 'request_id': 'native-test', 'command': 'local-test'}
    worker.record_event(attempt, payload)
    worker.settle(attempt['id'], {'status': 'completed', 'output': 'Local test ended'})
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM approvals').fetchone()['status'] == 'cancelled'
        conn.execute("UPDATE tasks SET status='queued' WHERE id=%s", (task['id'],))
    worker.record_event(attempt, {**payload, 'request_id': 'late-replay'})
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM approvals').fetchone()['n'] == 1


def test_budget_pause_preserves_unexpired_questions_and_decided_reviews(make_task):
    task = make_task()
    review_id, question_id = request_rows(task)
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='paused_budget' WHERE id=%s", (task['id'],))
        conn.execute("UPDATE approvals SET status='decided',decision='deny',decided_at=now() WHERE id=%s", (review_id,))
    worker.advance_waiters()
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM input_requests WHERE id=%s', (question_id,)).fetchone()['status'] == 'pending'
        assert conn.execute('SELECT status FROM approvals WHERE id=%s', (review_id,)).fetchone()['status'] == 'decided'
