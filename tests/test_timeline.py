from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from backend import db, inputs, service, timeline, worker
from backend.models import AskArgs
from psycopg.types.json import Jsonb


def read(conversation, **kwargs):
    with db.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return timeline.read(conn, conversation, **kwargs)


def test_status_stays_with_original_request_and_is_not_globally_limited(make_task):
    old = make_task(prompt='Earlier greeting')
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='failed',error='Old authentication failure' WHERE id=%s", (old['id'],))
    new = make_task(prompt='Latest greeting')
    with db.connection() as conn:
        db.message(conn, new, new['bot_id'], 'Hello!', 'new-result')
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (new['id'],))
    page = read(old['conversation_id'])
    assert [e['message']['content'] for e in page['entries'] if e.get('message')] == ['Earlier greeting', 'Latest greeting', 'Hello!']
    assert page['entries'][0]['tasks'][0]['error'] == 'Old authentication failure'
    assert not page['entries'][-1]['tasks']


def test_catchup_keeps_old_pages_and_drains_large_bursts(make_task):
    task = make_task()
    with db.connection() as conn:
        for index in range(148):
            db.message(conn, task, task['bot_id'], f'History {index}', f'history:{index}')
    latest = read(task['conversation_id'])
    older = read(task['conversation_id'], before=latest['entries'][0]['position'])
    ids = {e['id'] for e in latest['entries'] + older['entries']}
    assert len(ids) == 149
    with db.connection() as conn:
        for index in range(125):
            db.message(conn, task, task['bot_id'], f'New {index}', f'new:{index}')
    cursor = latest['cursor']
    while True:
        page = read(task['conversation_id'], after=cursor)
        ids.update(e['id'] for e in page['entries'])
        cursor = page['cursor']
        if not page['has_more']:
            break
    assert len(ids) == 274


def test_stream_is_deduplicated_and_final_reuses_response_identity(make_task):
    task = make_task()
    attempt = worker.claim_new()[0]
    payload = {'event': 'message.delta', 'seq': 1, 'delta': 'Hello'}
    worker.record_event(attempt, payload)
    worker.record_event(attempt, payload)
    first = read(task['conversation_id'])
    response = next(e for e in first['entries'] if e['id'] == f"response:{attempt['id']}")
    assert response['text'] == 'Hello'
    with db.connection() as conn:
        db.message(conn, task, task['bot_id'], 'Hello, final.', f"runtime-result:{attempt['id']}")
        conn.execute("UPDATE attempts SET status='completed' WHERE id=%s", (attempt['id'],))
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))
    final = read(task['conversation_id'], after=first['cursor'])
    entry = next(e for e in final['entries'] if e['id'] == response['id'])
    assert entry['position'] == response['position']
    assert entry['revision'] > response['revision']
    assert entry['message']['content'] == 'Hello, final.'


def test_rollback_and_savepoint_leave_no_timeline_entries(make_task):
    task = make_task()
    before = read(task['conversation_id'])
    with pytest.raises(ValueError), db.connection() as conn:
        db.message(conn, task, 'owner', 'Rolled back', 'rollback', 'user')
        raise ValueError()
    with db.connection() as conn:
        with pytest.raises(ValueError), conn.transaction():
            db.message(conn, task, 'owner', 'Savepoint rollback', 'savepoint', 'user')
            raise ValueError()
    assert read(task['conversation_id']) == before


def test_publication_cursor_cannot_skip_a_later_commit(make_task):
    task = make_task()
    initial = read(task['conversation_id'])
    inserted, release = Event(), Event()

    def slow_transaction():
        with db.connection() as conn:
            db.message(conn, task, 'owner', 'Slow', 'slow', 'user')
            inserted.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(slow_transaction)
        assert inserted.wait(10)
        with db.connection() as conn:
            db.message(conn, task, 'owner', 'Fast', 'fast', 'user')
        fast = read(task['conversation_id'], after=initial['cursor'])
        assert [e['message']['content'] for e in fast['entries'] if e.get('message')] == ['Fast']
        release.set()
        future.result(timeout=10)
    slow = read(task['conversation_id'], after=fast['cursor'])
    assert [e['message']['content'] for e in slow['entries'] if e.get('message')] == ['Slow']


def test_question_and_answer_stay_in_origin_thread(make_task):
    root = make_task()
    task_id = uuid4()
    with db.connection() as conn:
        root_message = conn.execute('SELECT id FROM messages WHERE task_id=%s', (root['id'],)).fetchone()['id']
        task = service.create_task(conn, bot_id=root['bot_id'], conversation_id=root['conversation_id'],
                                   prompt='Thread work', request_key=str(task_id), reply_to_message_id=root_message,
                                   orchestration_version=2)
        question = inputs.ask(conn, service.locked_task(conn, task['id']),
                              {'prompt': 'Which format?', 'answer_type': 'text', 'choices': []})
        saved = conn.execute('SELECT * FROM input_requests WHERE id=%s', (question['input_request_id'],)).fetchone()
        inputs.answer(conn, saved['id'], {'answer': 'Markdown', 'expected_question_hash': saved['question_hash']})
        intake = conn.execute("SELECT * FROM tasks WHERE request_key=%s", (f"question-answer:{saved['id']}",)).fetchone()
        assert intake['reply_to_message_id'] == root_message
    main = read(root['conversation_id'])
    thread = read(root['conversation_id'], thread=root_message)
    assert not any(e.get('question') for e in main['entries'])
    entry = next(e for e in thread['entries'] if e.get('question'))
    assert entry['question']['status'] == 'answered'
    assert entry['question']['answer'] == 'Markdown'
    assert sum(e.get('message', {}).get('content') == 'Markdown' for e in thread['entries']) == 1


def test_reviews_are_durable_and_expired_or_terminal_actions_are_disabled(make_task):
    task = make_task()
    review_id = uuid4()
    with db.connection() as conn:
        question = inputs.ask(conn, service.locked_task(conn, task['id']), AskArgs(prompt='A durable question').model_dump())
        conn.execute("INSERT INTO approvals(id,task_id,kind,payload,payload_hash,expires_at) VALUES(%s,%s,'action',%s,'hash',now()+interval '1 day')", (review_id, task['id'], Jsonb({'summary': 'A durable review'})))
    initial = read(task['conversation_id'])
    assert next(e for e in initial['entries'] if e.get('question'))['actionable']
    assert next(e for e in initial['entries'] if e.get('review'))['actionable']
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='cancelled' WHERE id=%s", (task['id'],))
        conn.execute("UPDATE input_requests SET status='cancelled' WHERE id=%s", (question['input_request_id'],))
        conn.execute("UPDATE approvals SET status='expired' WHERE id=%s", (review_id,))
    final = read(task['conversation_id'], after=initial['cursor'])
    for entry in final['entries']:
        if entry.get('question') or entry.get('review'):
            assert not entry['actionable']
            assert entry['position'] == next(e['position'] for e in initial['entries'] if e['id'] == entry['id'])


def test_peer_outputs_are_classified_and_failed_output_is_not_duplicated(make_task):
    task = make_task()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET execution_kind='peer_message',owner_message_id=NULL,status='failed',error='Needs access' WHERE id=%s", (task['id'],))
        db.message(conn, task, task['bot_id'], 'Internal teammate exchange', 'peer-result')
    page = read(task['conversation_id'])
    outputs = [e for e in page['entries'] if e.get('message', {}).get('role') == 'assistant']
    assert len(outputs) == 1 and outputs[0]['origin'] == 'coordination'


def test_thread_change_emits_tombstone_and_does_not_delete_history(make_task):
    task = make_task()
    before = read(task['conversation_id'])
    with db.connection() as conn:
        root = conn.execute('SELECT id FROM messages WHERE task_id=%s', (task['id'],)).fetchone()['id']
        db.message(conn, task, 'owner', 'Move into thread', 'move', 'user')
    initial = read(task['conversation_id'])
    moved = next(e for e in initial['entries'] if e['message']['content'] == 'Move into thread')
    with db.connection() as conn:
        conn.execute('UPDATE messages SET reply_to_message_id=%s WHERE source_id=%s', (root, 'move'))
    changes = read(task['conversation_id'], after=initial['cursor'])
    assert next(e for e in changes['entries'] if e['id'] == moved['id'])['deleted']
    thread = read(task['conversation_id'], thread=root)
    assert any(e.get('message', {}).get('content') == 'Move into thread' for e in thread['entries'])
    assert before['entries'][0]['id'] in {e['id'] for e in thread['entries']}


def test_read_api_requires_owner_and_rejects_foreign_thread(make_task):
    from fastapi.testclient import TestClient
    from backend.app import app
    from backend.settings import settings
    task = make_task()
    other = make_task(bot='writer')
    with db.connection() as conn:
        root = conn.execute('SELECT id FROM messages WHERE task_id=%s', (other['id'],)).fetchone()['id']
    client = TestClient(app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    try:
        path = f"/api/v1/conversations/{task['conversation_id']}/timeline"
        assert client.get(path).status_code == 200
        assert client.get(path + f'?thread={root}').status_code == 404
        assert client.get(path + '?before=1&after=0').status_code == 422
    finally:
        client.close()
    client = TestClient(app)
    try:
        assert client.get(path).status_code == 401
    finally:
        client.close()


@pytest.mark.parametrize('clarification', ['', 'Which section should I clarify?'])
def test_answer_intake_records_receipt_without_running_acknowledgement(make_task, clarification):
    from backend import work_intake
    task = make_task()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET orchestration_version=2,message_intent='discussion',work_item_id=NULL WHERE id=%s", (task['id'],))
        origin = service.locked_task(conn, task['id'])
        question = inputs.ask(conn, origin, AskArgs(prompt='Which format?').model_dump())
        saved = conn.execute('SELECT * FROM input_requests WHERE id=%s', (question['input_request_id'],)).fetchone()
        inputs.answer(conn, saved['id'], {'answer': 'Markdown', 'expected_question_hash': saved['question_hash']})
        intake = conn.execute("SELECT * FROM tasks WHERE request_key=%s", (f"question-answer:{saved['id']}",)).fetchone()
        assert intake['handoff_context']['owner_work_target'] is None
        interpretation = work_intake.Interpretation.model_validate({'operations': [{'intent': 'answer_question', 'input_request_id': str(saved['id']), 'answer': 'Markdown'}], 'clarification': clarification})
        work_intake.apply(conn, intake['id'], interpretation)
        work_intake.apply(conn, intake['id'], interpretation)
        assert service.locked_task(conn, intake['id'])['status'] == ('queued' if clarification else 'completed')
        assert conn.execute('SELECT count(*) AS n FROM attempts WHERE task_id=%s', (intake['id'],)).fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM tasks WHERE request_key=%s', (f"question-answer:{saved['id']}",)).fetchone()['n'] == 1
