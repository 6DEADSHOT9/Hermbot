"""Recurring work retains authority and aggregate limits across every execution path."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
import pytest

from backend import app as application, coordination, db, execution_policy, recurring_management
from backend import registry, routines, service, triggers, worker, work_controller, work_items
from backend.models import GrantRequest
from backend.routine_models import RecurringManagement
from tests.conftest import row
from tests.test_event_triggers import setup_source, setup_trigger
from tests.test_routines import definition, invoke, saved


def grant(**changes):
    with db.connection() as conn:
        return registry.create_grant(conn, GrantRequest.model_validate({'manager_id':'researcher',
            'targets':['writer','reviewer'], 'operations':['delegate','inspect','steer','cancel','reassign','create'], **changes}))


def selection(authority, **changes):
    return {'grant_id':str(authority['id']), 'targets':['writer'],
            'operations':['delegate','inspect','steer','cancel','reassign'], **changes}


def managed(**changes):
    authority = grant()
    routine = saved(management=selection(authority), **changes)
    run = invoke(routine)
    return authority, routine, run, row(run['task_id'])


def peer(task):
    with db.connection() as conn:
        coordination.send(conn, task, {'bot_id':'writer','prompt':'Explain the existing evidence only.'})
        coordination.schedule_inboxes(conn)
        return conn.execute("SELECT * FROM tasks WHERE execution_kind='peer_message'").fetchone()


def test_optional_binding_retains_single_bot_behavior_and_rejects_unknown_authority():
    ordinary = saved()
    task = row(invoke(ordinary)['task_id'])
    with db.connection() as conn:
        assert not recurring_management.scope(conn, task) and not task['grant_id']
    with pytest.raises(HTTPException):
        saved(management=selection({'id':uuid4()}))


@pytest.mark.parametrize('change', ['wrong_manager','task_grant','revoked','expired','target','operation','self'])
def test_invalid_saved_scope_never_creates_work(change):
    authority = grant()
    selected = selection(authority)
    with db.connection() as conn:
        if change == 'wrong_manager':
            conn.execute("UPDATE grants SET manager_id='reviewer' WHERE id=%s", (authority['id'],))
        elif change == 'task_grant':
            conn.execute("UPDATE grants SET kind='task' WHERE id=%s", (authority['id'],))
        elif change == 'revoked':
            conn.execute('UPDATE grants SET revoked_at=now() WHERE id=%s', (authority['id'],))
        elif change == 'expired':
            conn.execute("UPDATE grants SET expires_at=now()-interval '1 second' WHERE id=%s", (authority['id'],))
        elif change == 'target':
            conn.execute("UPDATE grants SET targets='[]' WHERE id=%s", (authority['id'],))
        elif change == 'operation':
            conn.execute("UPDATE grants SET operations='[\"delegate\"]' WHERE id=%s", (authority['id'],))
        else:
            selected['targets'] = ['researcher']
    with pytest.raises((HTTPException, ValueError)):
        saved(management=selected)
    with db.connection() as conn:
        assert not conn.execute('SELECT 1 FROM routine_definitions').fetchone()


def test_scope_is_normalized_and_lifecycle_cannot_be_added():
    selected = RecurringManagement(grant_id=uuid4(),targets=['writer','writer'],operations=['delegate','delegate'])
    assert selected.targets == ['writer'] and selected.operations == ['delegate']
    with pytest.raises(ValueError):
        RecurringManagement(grant_id=uuid4(),targets=['writer'],operations=['create'])


def test_snapshot_limits_broad_grant_and_cannot_fall_back_to_ungranted_work():
    authority, routine, run, task = managed()
    with db.connection() as conn:
        frozen = recurring_management.scope(conn, task)
        assert frozen['source_message_id'] == routine['source_message_id']
        assert frozen['origin']['routine']['run_id'] == str(run['id'])
        assert frozen['initiating_user'] == 'owner'
        effective = service.check_grant(conn, task)
        assert effective['targets'] == ['writer'] and 'create' not in effective['operations']
        child = service.delegate(conn, task, 'writer','Supply evidence.',expected_output='Evidence for the report')
    assert row(UUID(child['task_id']))['grant_id'] == authority['id']
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.delegate(conn, task,'reviewer','Outside saved scope',expected_output='Evidence')
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.check_grant(conn, {**task,'grant_id':None})
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.check_grant(conn,task,operation='create',manager='researcher')


def test_peer_reply_inherits_scope_thread_and_budget():
    authority, routine, run, task = managed(active_budget_seconds=120)
    manager_attempt = worker.claim_new()[0]
    response = peer(task)
    assert response['grant_id'] == authority['id'] and response['routine_run_id'] == run['id']
    assert response['reply_to_message_id'] == task['reply_to_message_id']
    reply_attempt = next(a for a in worker.claim_new() if a['bot_id']=='writer')
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET active_seconds=40,accounted_at=now()-interval '15 seconds' WHERE id=%s", (manager_attempt['id'],))
        conn.execute('UPDATE attempts SET active_seconds=30,accounted_at=NULL WHERE id=%s', (reply_attempt['id'],))
        assert 84 <= execution_policy.budget(conn, task)['used_seconds'] <= 87
        assert execution_policy.budget(conn, response) == execution_policy.budget(conn, task)
        execution_policy.pause(conn, task)
    assert row(response['id'])['status'] == 'pausing_budget'
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET status='completed',accounted_at=NULL")
        conn.execute("UPDATE tasks SET status='paused_budget' WHERE work_item_id=%s", (task['work_item_id'],))
        execution_policy.continue_work(conn, row(task['id']))
    assert row(response['id'])['status']=='queued'


def test_pending_peer_blocks_overlap_and_stop_cancels_whole_occurrence():
    _, routine, _, task = managed()
    response = peer(task)
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))
        assert routines.unfinished(conn, routine['id'])
        service.cancel(conn, task)
    assert row(response['id'])['status']=='cancelled'


def test_revocation_closes_peer_execution_and_pauses_future_definition():
    authority, routine, _, task = managed()
    response = peer(task)
    application.revoke_grant(authority['id'])
    worker.advance_waiters()
    assert row(task['id'])['status']=='cancelled' and row(response['id'])['status']=='cancelled'
    with db.connection() as conn:
        assert routines.get(conn,routine['id'])['state']=='paused'
        assert not conn.execute("SELECT 1 FROM teammate_messages WHERE status='pending'").fetchone()


def test_expiry_before_claim_stops_run_without_auto_renewal():
    authority, routine, _, task = managed()
    grant()  # Another valid grant must not replace the saved one.
    with db.connection() as conn:
        conn.execute("UPDATE grants SET expires_at=now()-interval '1 second' WHERE id=%s", (authority['id'],))
    worker.advance_waiters()
    assert worker.claim_new()==[] and row(task['id'])['status']=='cancelled'
    with db.connection() as conn:
        assert routines.get(conn,routine['id'])['state']=='paused'


def test_owner_revision_retains_occurrence_and_selected_scope():
    authority, _, run, task = managed()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='failed' WHERE id=%s", (task['id'],))
        revision = service.create_task(conn,bot_id='researcher',conversation_id=task['conversation_id'],
            prompt='Clarify the existing report.',request_key=str(uuid4()),message_intent='auto',orchestration_version=2)
        work_items.route(conn,revision,{'intent':'update','work_item_id':task['work_item_id']},intake=True)
        work_controller.apply(conn,service.locked_task(conn,revision['id']),task['work_item_id'],objective='Clarified report')
    revised = row(revision['id'])
    assert revised['routine_run_id']==run['id'] and revised['grant_id']==authority['id']
    with db.connection() as conn:
        assert service.check_grant(conn,revised)['targets']==['writer']


def test_pause_after_start_does_not_cancel_later_peer_in_same_occurrence():
    _, routine, _, task = managed()
    worker.claim_new()
    with db.connection() as conn:
        routines.control(conn,routine['id'],'pause',routine['revision'],'pause')
    response = peer(task)
    assert any(a['task_id']==response['id'] for a in worker.claim_new())


def test_duplicate_tick_after_revision_keeps_original_occurrence():
    _, routine, _, task = managed()
    instant = service.now()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))
        original = routines.admit(conn,routine,instant,'scheduled','first-revision-key',instant)
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (original['task_id'],))
        changed = routines.save(conn,definition(management=routine['definition']['management'],name='Edited name',expected_revision=1),routine['id'])
        duplicate = routines.admit(conn,changed,instant,'scheduled','second-revision-key',instant)
        assert duplicate['id']==original['id']


def test_event_manager_uses_same_binding_and_invalid_grant_pauses_trigger():
    src = setup_source()
    authority = grant()
    trigger = setup_trigger(src,management=selection(authority))
    from backend.trigger_models import EventData
    with db.connection() as conn:
        entry = EventData(event_type='custom.event',key='manager-event',resource='sample',summary='Sample',occurred_at=service.now())
        triggers.ingest(conn,src,entry,'manager-delivery',service.digest(entry.model_dump(mode='json')))
        conn.execute('UPDATE trigger_runs SET ready_at=now()')
        triggers.admit_pending(conn)
        run = conn.execute("SELECT * FROM trigger_runs WHERE trigger_id=%s", (trigger['id'],)).fetchone()
        assert run['status']=='admitted'
        task = service.locked_task(conn,run['task_id'])
        assert recurring_management.scope(conn,task)['origin']['event_trigger']['run_id']==str(run['id'])
        conn.execute('UPDATE grants SET revoked_at=now() WHERE id=%s', (authority['id'],))
    worker.advance_waiters()
    with db.connection() as conn:
        assert triggers.get(conn,trigger['id'])['state']=='paused'


def test_grant_dispatch_and_revocation_share_lock_order():
    authority, _, _, task = managed()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.connection() as conn:
            current=service.locked_task(conn,task['id'])
            service.check_grant(conn,current)
            stopped=pool.submit(application.revoke_grant,authority['id'])
        assert stopped.result(timeout=10)['revoked']
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.check_grant(conn,task)


def test_shared_scope_is_frozen_after_routine_edit():
    authority, routine, _, task = managed()
    worker.claim_new()
    with db.connection() as conn:
        routines.save(conn,definition(management=selection(authority,targets=['reviewer']),expected_revision=1),routine['id'])
        assert service.check_grant(conn,task)['targets']==['writer']
        assert row(task['id'])['status'] not in service.TERMINAL


def test_current_grant_target_removal_stops_contributor_authority():
    authority, _, _, task = managed()
    response=peer(task)
    with db.connection() as conn:
        conn.execute('UPDATE grants SET targets=%s WHERE id=%s', (Jsonb(['reviewer']),authority['id']))
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.check_grant(conn,response)


def test_followup_with_expired_authority_cannot_become_ungranted_work():
    authority, _, _, task = managed()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='failed' WHERE id=%s", (task['id'],))
        conn.execute('UPDATE grants SET expires_at=%s WHERE id=%s', (service.now()-timedelta(seconds=1),authority['id']))
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            followup=service.create_task(conn,bot_id='researcher',conversation_id=task['conversation_id'],
                prompt='Continue the report.',request_key=str(uuid4()),message_intent='auto',orchestration_version=2)
            work_items.route(conn,followup,{'intent':'update','work_item_id':task['work_item_id']},intake=True)


def test_cosmetic_message_classification_cannot_widen_scope_or_reset_budget():
    _, _, _, task = managed()
    attempt=worker.claim_new()[0]
    with db.connection() as conn:
        conn.execute('UPDATE attempts SET active_seconds=19,accounted_at=NULL WHERE id=%s', (attempt['id'],))
        classified={**task,'message_intent':'discussion','work_item_id':None}
        assert service.check_grant(conn,classified)['targets']==['writer']
        assert execution_policy.budget(conn,classified)['used_seconds']==19


def test_pause_and_stop_is_atomic_and_leaves_unrelated_work_untouched(make_task):
    _, routine, _, task=managed()
    response=peer(task)
    unrelated=make_task('reviewer')
    with db.connection() as conn:
        result=routines.control(conn,routine['id'],'pause-stop',routine['revision'],'stop-this')
        assert result['state']=='paused' and result['next_run_at'] is None
    assert row(task['id'])['status']=='cancelled' and row(response['id'])['status']=='cancelled'
    assert row(unrelated['id'])['status']=='queued'


def test_manager_wait_releases_capacity_for_real_contributor():
    _, _, _, task=managed()
    with db.connection() as conn:
        conn.execute('UPDATE workspace_settings SET max_active=1')
    attempt=worker.claim_new()[0]
    with db.connection() as conn:
        child=service.delegate(conn,row(task['id']),'writer','Execute the calculation.',expected_output='Calculation evidence')
        coordination.wait(conn,row(task['id']),{'task_ids':[UUID(child['task_id'])]})
    assert worker.claim_new()==[]
    worker.settle(attempt['id'],{'status':'completed','output':'Waiting for the actual contribution.'})
    claimed=worker.claim_new()
    assert len(claimed)==1 and str(claimed[0]['task_id'])==child['task_id']


def test_optional_running_work_prevents_premature_final_acceptance():
    from backend import result_acceptance
    _, _, _, task=managed()
    with db.connection() as conn:
        service.delegate(conn,task,'writer','Optional evidence.',expected_output='Evidence',required=False)
        outcome=conn.execute('SELECT * FROM work_outcomes WHERE work_item_id=%s', (task['work_item_id'],)).fetchone()
    with pytest.raises(HTTPException,match='remaining occurrence work'):
        with db.connection() as conn:
            result_acceptance.validate(conn,task,outcome,{'status':'fulfilled'})


def test_state_and_inspection_return_current_work_and_saved_scope():
    from tests.test_event_triggers import client
    _, _, _, task=managed()
    owner=client()
    snapshot=owner.get('/api/v1/state')
    assert snapshot.status_code==200
    assert any(t['id']==str(task['id']) for t in snapshot.json()['tasks'])
    detail=owner.get('/api/v1/tasks/'+str(task['id']))
    assert detail.status_code==200
    assert detail.json()['management_scope']['targets']==['writer']


def test_selected_cancel_permission_does_not_require_inspection():
    authority=grant()
    routine=saved(management=selection(authority,operations=['delegate','cancel']))
    task=row(invoke(routine)['task_id'])
    with db.connection() as conn:
        child=service.delegate(conn,task,'writer','Supply evidence.',expected_output='Evidence')
        assert service.inspect_task(conn,task,UUID(child['task_id']),operation='cancel')['status']=='queued'
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.inspect_task(conn,task,UUID(child['task_id']))


def test_budget_continuation_keeps_canonical_occurrence_after_classification():
    _, _, _, task=managed(active_budget_seconds=120)
    with db.connection() as conn:
        allowance=registry.workspace(conn)['active_budget_seconds']
        conn.execute("UPDATE tasks SET status='paused_budget',stop_kind='budget',message_intent='discussion',work_item_id=NULL WHERE id=%s", (task['id'],))
        execution_policy.continue_work(conn,service.locked_task(conn,task['id']))
        assert execution_policy.budget(conn,service.locked_task(conn,task['id']))['limit_seconds']==120+allowance


def test_manager_prose_without_accepted_results_is_a_definite_failure():
    _, _, _, task=managed()
    attempt=worker.claim_new()[0]
    worker.settle(attempt['id'],{'status':'completed','output':'Everything is done.'})
    current=row(task['id'])
    assert current['status']=='failed' and 'accepted required results' in current['error']
    with db.connection() as conn:
        assert recurring_management.definite_failure(conn,current)
        assert conn.execute('SELECT status FROM work_outcomes WHERE work_item_id=%s', (task['work_item_id'],)).fetchone()['status']=='failed'
        conn.execute("UPDATE work_outcomes SET status='fulfilled' WHERE work_item_id=%s", (task['work_item_id'],))
        assert not recurring_management.definite_failure(conn,current)


def test_reclassification_cannot_detach_live_work_from_overlap_or_stop():
    _, routine, _, task=managed()
    response=peer(task)
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed',message_intent='discussion',work_item_id=NULL WHERE id=%s", (task['id'],))
        assert routines.unfinished(conn,routine['id'])
        service.cancel(conn,service.locked_task(conn,task['id']))
    assert row(response['id'])['status']=='cancelled'


@pytest.mark.parametrize('changed_scope',[False,True])
def test_incremental_window_uses_semantic_scope_across_authority_renewal(changed_scope):
    authority,routine,run,task=managed(window_mode='since_success')
    replacement=grant()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))
        conn.execute("UPDATE work_outcomes SET status='fulfilled' WHERE work_item_id=%s", (task['work_item_id'],))
        updated=routines.save(conn,definition(window_mode='since_success',management=selection(replacement,targets=['reviewer'] if changed_scope else ['writer']),expected_revision=1),routine['id'])
    next_run=invoke(updated)
    assert next_run['context']['previous_successful_run_id']==(None if changed_scope else str(run['id']))


def test_peer_admission_skips_a_locked_occurrence_before_taking_authority():
    _, _, _, task=managed()
    with db.connection() as conn:
        coordination.send(conn,task,{'bot_id':'writer','prompt':'Explain existing evidence only.'})
    def admit():
        with db.connection() as other:
            coordination.schedule_inboxes(other)
            return other.execute("SELECT count(*) AS n FROM tasks WHERE execution_kind='peer_message'").fetchone()['n']
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.connection() as conn:
            service.locked_task(conn,task['id'])
            assert pool.submit(admit).result(timeout=3)==0
    assert admit()==1
