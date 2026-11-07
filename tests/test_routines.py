"""Occurrence admission and actual work/receipt integration, never synthetic provider acceptance."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend import app as application, db, routines, service, worker, work_intake
from backend.routine_models import RoutineDefinition, Schedule
from backend.routine_schedule import next_at
from backend.settings import settings

UTC = timezone.utc


def definition(**changes):
    with db.connection() as conn:
        chat = conn.execute("SELECT id FROM conversations WHERE bot_id='researcher'").fetchone()['id']
    return RoutineDefinition.model_validate({'name': 'Daily evidence', 'bot_id': 'researcher', 'conversation_id': str(chat),
        'instructions': 'Read the supplied evidence and return a report.', 'schedule': {'kind': 'daily', 'timezone': 'Asia/Kolkata', 'hour': 9},
        'requirements': [{'key': 'report', 'title': 'Evidence report', 'kind': 'report'}], **changes})


def saved(**changes):
    with db.connection() as conn:
        return routines.save(conn, definition(**changes))


def invoke(row, key=None):
    with db.connection() as conn:
        return routines.control(conn, row['id'], 'run', row['revision'], key or str(uuid4()))


def test_schedule_timezone_interval_month_end_and_dst():
    after = datetime(2026, 3, 7, 15, tzinfo=UTC)
    daily = Schedule(kind='daily', timezone='America/New_York', hour=9)
    assert next_at(daily, after) == datetime(2026, 3, 8, 13, tzinfo=UTC)
    nonexistent = daily.model_copy(update={'hour': 2, 'minute': 30})
    assert next_at(nonexistent, after) == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)
    repeated = daily.model_copy(update={'hour': 1, 'minute': 30})
    first = next_at(repeated, datetime(2026, 11, 1, tzinfo=UTC))
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert next_at(repeated, first).day == 2
    monthly = Schedule(kind='monthly', month_day=31)
    assert next_at(monthly, datetime(2026, 4, 1, tzinfo=UTC)).month == 5
    assert next_at(monthly.model_copy(update={'last_day': True}), datetime(2026, 4, 1, tzinfo=UTC)).day == 30
    interval = Schedule(kind='interval', at=after, interval_minutes=60)
    assert next_at(interval, after + timedelta(minutes=121)) == after + timedelta(hours=3)


def test_schedule_preview_accepts_an_unfinished_routine_form():
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    response = client.post('/api/v1/routines/preview', json={'kind': 'daily', 'timezone': 'Asia/Kolkata', 'hour': 9})
    assert response.status_code == 200
    assert len(response.json()['next_runs']) == 5


def test_manual_admission_real_outcomes_fresh_threads_and_replay():
    routine = saved()
    first = invoke(routine, 'same')
    assert invoke(routine, 'same')['id'] == first['id']
    task = worker.claim_new()[0]
    with db.connection() as conn:
        source = conn.execute('SELECT * FROM tasks WHERE id=%s', (first['task_id'],)).fetchone()
        assert source['orchestration_version'] == 2 and source['intake_status'] == 'applied'
        assert source['owner_message_id'] == routine['source_message_id']
        assert conn.execute('SELECT key FROM work_outcomes WHERE work_item_id=%s', (source['work_item_id'],)).fetchone()['key'] == 'report'
        conn.execute("UPDATE attempts SET status='completed' WHERE id=%s", (task['id'],))
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (source['id'],))
    second = invoke(routine)
    new = worker.claim_new()[0]
    assert new['session_id'] != task['session_id']
    assert second['task_id'] != first['task_id']
    assert routines.RoutineDefinition.model_validate(routine['definition']).schedule.kind == 'daily'


def test_overlap_waiting_for_owner_and_stopped_uncertain_effect_never_replay():
    routine = saved()
    first = invoke(routine)
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='waiting_input' WHERE id=%s", (first['task_id'],))
    skipped = invoke(routine)
    assert skipped['status'] == 'skipped' and 'Earlier run' in skipped['reason']
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='cancelled' WHERE id=%s", (first['task_id'],))
        conn.execute("UPDATE work_outcomes SET status='uncertain' WHERE source_task_id=%s", (first['task_id'],))
    assert invoke(routine)['status'] == 'skipped'


def test_two_concurrent_manual_admissions_create_one_task():
    routine = saved()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(routine, 'concurrent'), range(2)))
    assert results[0]['id'] == results[1]['id']
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == 1


def test_edit_or_pause_before_first_claim_cancels_queued_work():
    routine = saved()
    run = invoke(routine)
    with db.connection() as conn:
        routines.save(conn, definition(name='Changed', expected_revision=1), routine['id'])
        assert conn.execute('SELECT status FROM tasks WHERE id=%s', (run['task_id'],)).fetchone()['status'] == 'cancelled'
    assert worker.claim_new() == []
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM tasks WHERE id=%s', (run['task_id'],)).fetchone()['status'] == 'cancelled'
    # Changes to a started execution are not implied by editing the routine.
    current = saved()
    invoke(current)
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        routines.control(conn, current['id'], 'pause', 1, 'pause')
        assert conn.execute('SELECT status FROM attempts WHERE id=%s', (attempt['id'],)).fetchone()['status'] == 'dispatching'


def test_manual_run_preserves_future_once_and_paused_state():
    future = service.now() + timedelta(days=1)
    routine = saved(schedule={'kind': 'once', 'at': future.isoformat()})
    first = invoke(routine)
    with db.connection() as conn:
        assert routines.get(conn, routine['id'])['next_run_at'] == future
        paused = routines.control(conn, routine['id'], 'pause', 1, 'pause')
    assert paused['state'] == 'paused'
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM tasks WHERE id=%s', (first['task_id'],)).fetchone()['status'] == 'cancelled'
    assert invoke(paused)['status'] == 'admitted'  # Run now is allowed while scheduling is paused.


def test_expired_queued_start_and_daily_cap():
    routine = saved(max_runs_per_day=1)
    run = invoke(routine)
    with db.connection() as conn:
        conn.execute("UPDATE routine_runs SET start_before=now()-interval '1 second' WHERE id=%s", (run['id'],))
    assert worker.claim_new() == []
    assert 'Daily run limit' in invoke(routine)['reason']


def test_duplicate_scheduled_ticks_and_missed_once():
    routine = saved(schedule={'kind': 'once', 'at': (service.now() + timedelta(minutes=1)).isoformat()})
    with db.connection() as conn:
        conn.execute("UPDATE routine_definitions SET next_run_at=now()-interval '1 day' WHERE id=%s", (routine['id'],))
        routines.tick(conn, 'researcher')
        routines.tick(conn, 'researcher')
        runs = routines.history(conn, routine['id'])
        assert all(run['status'] == 'skipped' for run in runs)
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == 0


def test_scoped_wake_token_and_owner_api_replay():
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    body = definition().model_dump(mode='json')
    headers = {'Idempotency-Key': 'save-one'}
    first = client.post('/api/v1/routines', json=body, headers=headers)
    assert first.status_code == 202, first.text
    assert client.post('/api/v1/routines', json=body, headers=headers).json()['id'] == first.json()['id']
    assert TestClient(application.app).get('/api/v1/routines').status_code == 401
    with db.connection() as conn:
        binding = conn.execute('SELECT * FROM routine_schedulers').fetchone()
    from backend.secrets import decrypt
    path = f"/internal/routine-wake/researcher/{binding['generation']}"
    assert client.post(path).status_code == 401  # Owner bearer is not a scheduler capability.
    assert client.post(path, headers={'Authorization': 'Bearer ' + decrypt(binding['token_encrypted'])}).status_code == 200
    assert 'token' not in client.get('/api/v1/routines').text


def test_chat_proposal_creates_inactive_draft_without_work_or_new_owner_authority():
    body = definition(bot_id='scout')  # The owner used the teammate's display name.
    with db.connection() as conn:
        old_name = conn.execute("SELECT name FROM bots WHERE id='researcher'").fetchone()['name']
        conn.execute("UPDATE bots SET name='Scout' WHERE id='researcher'")
        task = service.create_task(conn, bot_id='researcher', conversation_id=body.conversation_id,
            prompt='Set up a daily report', request_key=str(uuid4()), message_intent='auto', orchestration_version=2)
        proposal = work_intake.Interpretation.model_validate({'operations': [{'intent': 'routine', 'routine': {
            'instruction_quote': 'Set up a daily report', 'definition': body.model_dump(mode='json')}}]})
        work_intake.apply(conn, task['id'], proposal)
        assert conn.execute('SELECT count(*) AS n FROM work_items').fetchone()['n'] == 0
        draft = conn.execute('SELECT * FROM routine_definitions').fetchone()
        assert draft['state'] == 'draft' and draft['bot_id'] == 'researcher' and draft['next_run_at'] is None
        assert conn.execute("SELECT count(*) AS n FROM messages WHERE role='user'").fetchone()['n'] == 1
        assert routines.tick(conn, 'researcher') == 0
        conn.execute('UPDATE bots SET name=%s WHERE id=%s', (old_name, 'researcher'))


def test_invalid_external_authority_and_cycle_rejected():
    with pytest.raises(ValueError):
        definition(requested_effects=[{'outcome_key': 'report', 'action': 'slack.post', 'resource': 'C123', 'instruction_quote': 'Not in owner instructions'}])
    with pytest.raises(ValueError):
        definition(requirements=[{'key': 'report', 'title': 'Report', 'kind': 'report', 'depends_on': ['report']}])


def test_archived_destination_and_edited_revision_have_identical_work_and_history_status():
    routine = saved()
    run = invoke(routine)
    with db.connection() as conn:
        conn.execute('UPDATE conversations SET archived_at=now() WHERE id=%s', (routine['conversation_id'],))
    assert worker.claim_new() == []
    with db.connection() as conn:
        assert routines.history(conn, routine['id'])[0]['display_status'] == 'cancelled'
        assert conn.execute('SELECT status FROM tasks WHERE id=%s', (run['task_id'],)).fetchone()['status'] == 'cancelled'


def test_future_occurrence_survives_manual_run_and_next_native_tick_is_unique():
    routine = saved(schedule={'kind': 'interval', 'at': (service.now() + timedelta(minutes=1)).isoformat(), 'interval_minutes': 60})
    with db.connection() as conn:
        conn.execute("UPDATE routine_definitions SET next_run_at=now()-interval '1 second' WHERE id=%s", (routine['id'],))
        assert routines.tick(conn, 'researcher') == 1
        assert routines.tick(conn, 'researcher') == 0
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == 1
        assert routines.get(conn, routine['id'])['next_run_at'] > routines.clock(conn)


def test_current_receipt_succeeds_even_when_later_execution_fails(monkeypatch):
    from backend import connections, connector_providers, outcomes
    from tests.test_phase4 import connection
    account = connection()
    instructions = 'Create the daily issue in owner/repo.'
    routine = saved(instructions=instructions,
        requirements=[{'key': 'issue', 'title': 'Daily issue', 'kind': 'connector_effect', 'target': {'action': 'github.create_issue', 'resource': 'owner/repo'}}],
        requested_effects=[{'outcome_key': 'issue', 'action': 'github.create_issue', 'resource': 'owner/repo', 'instruction_quote': instructions}])
    run = invoke(routine)
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        task = service.locked_task(conn, run['task_id'])
        outcome = outcomes.rows(conn, task['work_item_id'])[0]
        action = connections.invoke(conn, task, {'connection_id': account['id'], 'action': 'github.create_issue', 'arguments': {'repo': 'owner/repo','title':'Daily','body':'Daily content'}, 'outcome_id': outcome['id'], 'summary':'Daily issue'})
        assert action['authorization'] == 'owner_request'
        conn.execute("UPDATE attempts SET status='completed' WHERE id=%s", (attempt['id'],))
    monkeypatch.setattr(connector_providers, 'execute', lambda *a: {'id': 101, 'number': 1})
    connections.drive_one()
    with db.connection() as conn:
        op = conn.execute('SELECT * FROM connector_operations WHERE id=%s', (action['operation_id'],)).fetchone()
        assert op['status'] == 'succeeded'
        conn.execute("UPDATE tasks SET status='failed' WHERE id=%s", (task['id'],))
        assert outcomes.rows(conn, task['work_item_id'])[0]['status'] == 'fulfilled'
        assert conn.execute('SELECT result FROM connector_operations WHERE id=%s', (op['id'],)).fetchone()['result']['id'] == 101


def test_review_mode_keeps_recurring_requested_action_pending():
    from backend import connections, outcomes
    from tests.test_phase4 import connection
    account = connection()
    instruction = 'Create an issue in owner/repo every morning.'
    routine = saved(instructions=instruction,
        requirements=[{'key': 'issue', 'title': 'Issue', 'kind': 'connector_effect', 'target': {'action': 'github.create_issue', 'resource': 'owner/repo'}}],
        requested_effects=[{'outcome_key': 'issue', 'action': 'github.create_issue', 'resource': 'owner/repo', 'instruction_quote': instruction}])
    run = invoke(routine)
    with db.connection() as conn:
        conn.execute("UPDATE workspace_settings SET external_action_mode='review'")
        task = service.locked_task(conn, run['task_id'])
        outcome = outcomes.rows(conn, task['work_item_id'])[0]
        action = connections.invoke(conn, task, {'connection_id': account['id'], 'action': 'github.create_issue', 'arguments': {'repo': 'owner/repo','title':'Review me','body':'No automatic effect'}, 'outcome_id': outcome['id'], 'summary':'Exact review'})
        assert action['status'] == 'waiting_approval'
        assert conn.execute('SELECT status FROM connector_operations').fetchone()['status'] == 'pending'


def test_chat_edit_draft_does_not_mutate_active_routine_until_saved():
    routine = saved()
    body = definition(name='Revised',expected_revision=1)
    with db.connection() as conn:
        task = service.create_task(conn, bot_id='researcher', conversation_id=body.conversation_id,
            prompt='Change the daily report', request_key=str(uuid4()), message_intent='auto', orchestration_version=2)
        interpretation = work_intake.Interpretation.model_validate({'operations': [{'intent': 'routine', 'routine': {
            'instruction_quote': 'Change the daily report', 'routine_id': str(routine['id']), 'definition': body.model_dump(mode='json')}}]})
        work_intake.apply(conn, task['id'], interpretation)
        assert routines.get(conn, routine['id'])['revision'] == 1
        draft = conn.execute("SELECT * FROM routine_definitions WHERE state='draft'").fetchone()
    client = TestClient(application.app,headers={'Authorization':'Bearer '+settings.owner_token})
    response = client.put(f"/api/v1/routines/{draft['id']}", json=body.model_copy(update={'expected_revision':1}).model_dump(mode='json'), headers={'Idempotency-Key':'activate-revision'})
    assert response.status_code == 200, response.text
    assert response.json()['id'] == str(routine['id']) and response.json()['revision'] == 2


def test_history_cursor_does_not_drop_occurrences_with_equal_timestamps():
    routine = saved()
    for i in range(4):
        invoke(routine, str(i))
    with db.connection() as conn:
        conn.execute('UPDATE routine_runs SET created_at=now()')
        page = routines.history(conn, routine['id'], limit=2)
        older = routines.history(conn, routine['id'], before=page[-1]['created_at'], before_id=page[-1]['id'], limit=2)
        assert len({r['id'] for r in page + older}) == 4


def test_incremental_window_is_frozen_and_advances_only_after_verified_success():
    routine = saved(window_mode='since_success', initial_lookback_minutes=90)
    first = invoke(routine)
    with db.connection() as conn:
        stored = conn.execute('SELECT context FROM routine_runs WHERE id=%s', (first['id'],)).fetchone()['context']
        assert datetime.fromisoformat(stored['window_start']) == first['scheduled_at'] - timedelta(minutes=90)
        assert datetime.fromisoformat(stored['window_end']) == first['scheduled_at']
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (first['task_id'],))
    # A completed execution without verified outcomes must not advance the cursor.
    second = invoke(routine)
    with db.connection() as conn:
        context = conn.execute('SELECT context FROM routine_runs WHERE id=%s', (second['id'],)).fetchone()['context']
        assert context['previous_successful_run_id'] is None
        conn.execute("UPDATE tasks SET status='failed' WHERE id=%s", (second['task_id'],))
        conn.execute("UPDATE work_outcomes SET status='fulfilled' WHERE source_task_id=%s", (first['task_id'],))
    third = invoke(routine)
    with db.connection() as conn:
        context = conn.execute('SELECT context FROM routine_runs WHERE id=%s', (third['id'],)).fetchone()['context']
        assert context['previous_successful_run_id'] == str(first['id'])
        assert datetime.fromisoformat(context['window_start']) == first['scheduled_at']
        assert conn.execute('SELECT handoff_context FROM tasks WHERE id=%s', (third['task_id'],)).fetchone()['handoff_context']['routine'] == context
        assert conn.execute('SELECT context FROM routine_runs WHERE id=%s', (first['id'],)).fetchone()['context'] == stored


def test_changed_instructions_reset_incremental_cursor():
    routine = saved(window_mode='since_success')
    run = invoke(routine)
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (run['task_id'],))
        conn.execute("UPDATE work_outcomes SET status='fulfilled' WHERE source_task_id=%s", (run['task_id'],))
        updated = routines.save(conn, definition(instructions='Report different evidence.', window_mode='since_success', expected_revision=1), routine['id'])
    following = invoke(updated)
    with db.connection() as conn:
        assert conn.execute('SELECT context FROM routine_runs WHERE id=%s', (following['id'],)).fetchone()['context']['previous_successful_run_id'] is None


@pytest.mark.parametrize('catch_up,admitted', [('skip', False), ('latest', True)])
def test_downtime_policy_admits_at_most_latest_within_window(catch_up, admitted):
    anchor = service.now() - timedelta(hours=2)
    routine = saved(schedule={'kind': 'interval', 'at': anchor.isoformat(), 'interval_minutes': 2}, catch_up=catch_up)
    with db.connection() as conn:
        conn.execute('UPDATE routine_definitions SET next_run_at=%s WHERE id=%s', (anchor, routine['id']))
        routines.tick(conn, 'researcher')
        runs = routines.history(conn, routine['id'])
        assert sum(r['status'] == 'admitted' for r in runs) == int(admitted)
        assert len(runs) <= 2
        assert routines.tick(conn, 'researcher') == 0
        if admitted:
            assert next(r['scheduled_at'] for r in runs if r['status'] == 'admitted') >= routines.clock(conn) - timedelta(minutes=2)


def test_restored_destination_does_not_silently_resume_schedule():
    routine = saved()
    with db.connection() as conn:
        conn.execute('UPDATE conversations SET archived_at=now() WHERE id=%s', (routine['conversation_id'],))
        conn.execute("UPDATE routine_definitions SET next_run_at=now()-interval '1 second' WHERE id=%s", (routine['id'],))
        routines.tick(conn, 'researcher')
        assert routines.get(conn, routine['id'])['state'] == 'paused'
        conn.execute('UPDATE conversations SET archived_at=NULL WHERE id=%s', (routine['conversation_id'],))
        assert routines.tick(conn, 'researcher') == 0
        assert routines.control(conn, routine['id'], 'resume', 1, 'resume-restored')['state'] == 'active'


def test_mismatched_recurring_destination_is_rejected():
    with pytest.raises(ValueError, match='destination'):
        definition(instructions='Create a daily issue in owner/repo.',
            requirements=[{'key': 'issue', 'title': 'Issue', 'kind': 'connector_effect', 'target': {'action': 'github.create_issue', 'resource': 'owner/repo'}}],
            requested_effects=[{'outcome_key': 'issue', 'action': 'github.create_issue', 'resource': 'other/repo', 'instruction_quote': 'Create a daily issue'}])


def test_owner_archiving_and_restoring_group_requires_explicit_routine_resume():
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    created = client.post('/api/v1/groups', json={'title': 'Routine group', 'bot_ids': ['researcher', 'writer']})
    assert created.status_code in (200, 201), created.text
    group_id = created.json()['id']
    with db.connection() as conn:
        routine = routines.save(conn, definition(conversation_id=group_id))
    assert client.delete(f'/api/v1/groups/{group_id}').status_code == 200
    assert client.post(f'/api/v1/conversations/{group_id}/restore').status_code == 200
    with db.connection() as conn:
        row = routines.get(conn, routine['id'])
        assert row['state'] == 'paused' and row['next_run_at'] is None
        assert routines.tick(conn, 'researcher') == 0


def test_three_consecutive_definite_failures_pause_future_occurrences():
    routine = saved()
    for i in range(3):
        run = invoke(routine, f'failed-{i}')
        with db.connection() as conn:
            conn.execute("UPDATE tasks SET status='failed' WHERE id=%s", (run['task_id'],))
    with db.connection() as conn:
        conn.execute("UPDATE routine_definitions SET next_run_at=now()-interval '1 second' WHERE id=%s", (routine['id'],))
        routines.tick(conn, 'researcher')
        current = routines.get(conn, routine['id'])
        assert current['state'] == 'paused' and current['next_run_at'] is None
        assert 'Three consecutive' in current['error']
        assert len(routines.history(conn, routine['id'])) == 3


def test_chat_context_lists_only_destination_conversation_routines():
    local = saved()
    with db.connection() as conn:
        other_chat = conn.execute("SELECT id FROM conversations WHERE bot_id='writer'").fetchone()['id']
        other = routines.save(conn, definition(bot_id='writer', conversation_id=str(other_chat)))
        task = service.create_task(conn, bot_id='researcher', conversation_id=local['conversation_id'],
                                   prompt='Discuss schedules', request_key=str(uuid4()), message_intent='auto', orchestration_version=2)
        context = work_intake.context(conn, task)
        assert {row['id'] for row in context['saved_routines']} == {str(local['id'])}
        assert str(other['id']) not in str(context['saved_routines'])


def test_active_routines_remain_visible_ahead_of_archived_history():
    older_active = saved(name='Still active')
    recently_archived = saved(name='Archived fixture')
    with db.connection() as conn:
        routines.control(conn, recently_archived['id'], 'archive', 1, 'archive-list')
        rows = routines.list_routines(conn)
        assert [r['id'] for r in rows[:2]] == [older_active['id'], recently_archived['id']]
