"""Opt-in native Phase 5.3 proof; no connected-provider mutation or simulated owner answer."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sys
import time
from uuid import uuid4

from .protocol_acceptance import client, state

PATH = Path('/app/test-results/phase53-live.json')
TERMINAL = {'completed','failed','cancelled','interrupted','uncertain'}
OPS = ['delegate','inspect','steer','cancel','reassign']
LABEL = 'Phase 5.3'
INSTRUCTIONS = """This is an owner-requested local recurring-management acceptance test. Coordinate
Scout (researcher) and Quill (writer), using hermbot_delegate, as two required contributions to result
key report. Delegate one task to each with responsibility contribution and a concrete expected_output.
Scout must use an actual terminal calculation of 6*7 and publish a small text artifact containing 42.
Quill must use an actual terminal calculation of 10*11 and publish a small text artifact containing 110.
Each child should submit its contribution with its artifact_id for result key report. Tell each child
to use actual native tools, not delegate or message other bots, and to make no external changes.
Wait for the two actual assignments with hermbot_wait, ending the turn for that genuine wait.
After they settle, read both artifacts with hermbot_read_artifact. Inspect the actual calculations,
then submit one combined report through hermbot_finish.results, key report, including both numbers
and their sum 152. Include a concrete review and both reviewed_task_ids. Do not finish before both
contributions are ready. Do not publish an additional final file. Do not use connected apps, browser
logins, memory edits, lifecycle changes or independently scheduled jobs. Do not request any other
owner input or approval. Keep this test small and use existing scope and registered result keys."""


def save(proof):
    temporary=PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(proof,indent=2,default=str))
    temporary.replace(PATH)


def request(method,path,body=None,key=None):
    result=client.request(method,path,json=body,headers={'Idempotency-Key':key} if key else {})
    result.raise_for_status()
    return result.json()


def start():
    if PATH.exists():
        return status()
    snapshot=state()
    assert not any(t['status'] not in TERMINAL for t in snapshot['tasks']), 'Let existing work settle first'
    marker=uuid4().hex[:12]
    starts=datetime.now(timezone.utc)+timedelta(seconds=90)
    original_routines=request('GET','/api/v1/routines')['data']
    proof={'marker':marker,'created_at':datetime.now(timezone.utc).isoformat(),
        'native_model':True,'external_mutations_requested':False,
        'original_groups':[c['id'] for c in snapshot['conversations'] if c['kind']=='group'],
        'original_bots':{b['id']:{k:b[k] for k in ('revision','status','desired_state','provider_id','model_name','enabled_tools','enabled_skills','memory_mb','cpus')} for b in snapshot['bots']},
        'original_active_routines':{r['id']:hashlib.sha256(json.dumps(r['definition'],sort_keys=True).encode()).hexdigest() for r in original_routines if r['state']=='active'},
        'starts':starts.isoformat()}
    save(proof)
    group=request('POST','/api/v1/groups',{'title':'Phase 5.3 recurring acceptance '+marker,'bot_ids':['researcher','writer','reviewer']})
    proof['conversation_id']=group['id']
    save(proof)
    authority=request('POST','/api/v1/grants',{'manager_id':'reviewer','targets':['researcher','writer'],'operations':OPS,'duration_hours':24,'max_created_bots':0},'phase53-grant:'+marker)
    proof['grant_id']=authority['id']
    save(proof)
    definition={'name':'Phase 5.3 recurring manager '+marker,'bot_id':'reviewer','conversation_id':group['id'],
        'instructions':INSTRUCTIONS,'requirements':[{'key':'report','title':'Reviewed calculations: 42, 110 and 152','kind':'report'}],
        'management':{'grant_id':authority['id'],'targets':['researcher','writer'],'operations':OPS},
        'schedule':{'kind':'interval','timezone':'Asia/Kolkata','at':starts.isoformat(),'interval_minutes':2},
        'active_budget_seconds':900,'start_window_minutes':15,'max_runs_per_day':2,'enabled':True}
    routine=request('POST','/api/v1/routines',definition,'phase53-schedule:'+marker)
    proof['routine_id']=routine['id']
    save(proof)
    waiting={**definition,'name':'Phase 5.3 owner wait '+marker,'enabled':False,'max_runs_per_day':1,
        'instructions':"First call hermbot_ask with answer_type choice, prompt 'May this local-only Phase 5.3 manager demonstration continue?', choices ['Continue local test','Stop local test']. End the turn for the actual owner answer. On Continue local test, execute the following local test. On Stop local test, stop and report that decision. Never supply an answer on behalf of the owner.\n"+INSTRUCTIONS}
    manual=request('POST','/api/v1/routines',waiting,'phase53-owner-wait:'+marker)
    proof['wait_routine_id']=manual['id']
    save(proof)
    run=request('POST',f"/api/v1/routines/{manual['id']}/run",{'expected_revision':manual['revision']},'phase53-wait-run:'+marker)
    proof['wait_run_id']=run['id']
    proof['wait_task_id']=run['task_id']
    save(proof)
    return status()


def status():
    proof=json.loads(PATH.read_text())
    if not proof.get('routine_id'):
        raise AssertionError('Setup stopped before recording fixture IDs; inspect existing marker before recovery')
    scheduled=request('GET',f"/api/v1/routines/{proof['routine_id']}/runs")['data']
    proof['scheduled_runs']=[{k:r.get(k) for k in ('id','task_id','scheduled_at','status','display_status','execution_status','reason')} for r in scheduled]
    if proof.get('wait_task_id'):
        task=request('GET','/api/v1/tasks/'+proof['wait_task_id'])
        proof['wait_status']=task['status']
        proof['pending_questions']=[{k:q[k] for k in ('id','task_id','prompt','choices','question_hash','expires_at')} for q in state().get('input_requests',[]) if q['task_id']==proof['wait_task_id'] and q['status']=='pending']
    if proof.get('trigger_id'):
        proof['event_runs']=request('GET',f"/api/v1/event-triggers/{proof['trigger_id']}/runs")['data']
    proof['checked_at']=datetime.now(timezone.utc).isoformat()
    save(proof)
    print(json.dumps({'marker':proof['marker'],'scheduled_runs':proof['scheduled_runs'],'wait_status':proof.get('wait_status'),
        'pending_questions':proof.get('pending_questions',[]),'event_runs':[(r['id'],r['display_status']) for r in proof.get('event_runs',[])]}),flush=True)


def event():
    proof=json.loads(PATH.read_text())
    assert not proof.get('event_sent'), 'Original event is retained; do not create another occurrence'
    secret='whsec_'+base64.b64encode(secrets.token_bytes(32)).decode()
    source=next(s for s in request('GET','/api/v1/event-sources')['data'] if s['id']==proof['source_id']) if proof.get('source_id') else request('POST','/api/v1/event-sources',{'name':LABEL+' local signed event '+proof['marker'],'provider':'webhook','transport':'http'},'phase53-source:'+proof['marker'])
    proof['source_id']=source['id']
    save(proof)
    request('POST',f"/api/v1/event-sources/{source['id']}/secrets",{'expected_revision':source['revision'],'signing_secret':secret},'phase53-source-secret:'+str(uuid4()))
    for _ in range(15):
        source=next(s for s in request('GET','/api/v1/event-sources')['data'] if s['id']==source['id'])
        if source['initialized_at']:
            break
        time.sleep(1)
    assert source['initialized_at'], 'Source listening baseline is not ready'
    body={'name':LABEL+' event manager '+proof['marker'],'source_id':source['id'],'bot_id':'reviewer',
        'conversation_id':proof['conversation_id'],'instructions':INSTRUCTIONS,
        'requirements':[{'key':'report','title':'Reviewed event calculations: 42, 110 and 152','kind':'report'}],
        'management':{'grant_id':proof['grant_id'],'targets':['researcher','writer'],'operations':OPS},
        'event_types':['custom.event'],'active_budget_seconds':900,'start_window_minutes':15,'max_runs_per_day':1,'enabled':True}
    if proof.get('trigger_id'):
        old=next(t for t in request('GET','/api/v1/event-triggers')['data'] if t['id']==proof['trigger_id'])
        trigger=request('PUT','/api/v1/event-triggers/'+old['id'],{**body,'expected_revision':old['revision']},'phase53-event-update:'+str(uuid4()))
    else:
        trigger=request('POST','/api/v1/event-triggers',body,'phase53-event-trigger:'+proof['marker'])
    proof['trigger_id']=trigger['id']
    save(proof)
    payload=json.dumps({'type':'phase53.local','resource':proof['marker'],'text':'Local recurring-management event acceptance.'}).encode()
    delivery='phase53-event-'+proof['marker']
    timestamp=str(int(time.time()))
    signature=base64.b64encode(hmac.new(base64.b64decode(secret[6:]),delivery.encode()+b'.'+timestamp.encode()+b'.'+payload,hashlib.sha256).digest()).decode()
    response=client.post(source['webhook_path'],content=payload,headers={'Content-Type':'application/json','webhook-id':delivery,'webhook-timestamp':timestamp,'webhook-signature':'v1,'+signature})
    response.raise_for_status()
    proof['event_sent']=True
    proof['event_delivery']=delivery
    save(proof)
    status()


def verify(expected_scheduled_runs=2):
    status()
    from backend import db, execution_policy
    proof=json.loads(PATH.read_text())
    scheduled=[r for r in proof['scheduled_runs'] if r['status']=='admitted']
    assert len(scheduled)==expected_scheduled_runs and len({r['task_id'] for r in scheduled})==expected_scheduled_runs
    event_runs=proof.get('event_runs',[])
    assert len(event_runs)==1 and event_runs[0]['status']=='admitted'
    roots=[r['task_id'] for r in scheduled+event_runs]
    if proof['wait_status']=='completed':
        roots.append(proof['wait_task_id'])
    evidence=[]
    db.pool.open(wait=True)
    try:
        with db.connection() as conn:
            for identity in roots:
                manager=conn.execute('SELECT * FROM tasks WHERE id=%s',(identity,)).fetchone()
                assert manager['status']=='completed'
                assert all(number in manager['output'] for number in ('42','110','152'))
                collaborators=conn.execute('SELECT * FROM tasks WHERE parent_id=%s ORDER BY bot_id',(identity,)).fetchall()
                assert {c['bot_id'] for c in collaborators}=={'researcher','writer'} and len(collaborators)==2
                for child in collaborators:
                    assert child['status']=='completed' and child['grant_id']==manager['grant_id']
                    assert child['management_work_item_id']==manager['management_work_item_id']
                    assert child['routine_run_id']==manager['routine_run_id'] and child['trigger_run_id']==manager['trigger_run_id']
                    assert child['reply_to_message_id']==manager['reply_to_message_id']
                    terminal=conn.execute("SELECT data FROM events WHERE task_id=%s AND kind='tool.completed' AND data->>'tool'='terminal' AND data->>'error'='false'",(child['id'],)).fetchall()
                    expected='42' if child['bot_id']=='researcher' else '110'
                    assert any(re.search(r'(?<!\d)'+expected+r'(?!\d)',json.loads(e['data']['preview']).get('output','')) for e in terminal)
                results=conn.execute('SELECT * FROM work_outcomes WHERE work_item_id=%s AND required',(manager['work_item_id'],)).fetchall()
                assert len(results)==1 and results[0]['status']=='fulfilled'
                contributions=conn.execute('SELECT * FROM result_contributions WHERE outcome_id=%s',(results[0]['id'],)).fetchall()
                assert len(contributions)==2 and all(c['status']=='accepted' for c in contributions)
                artifacts=conn.execute('SELECT id,task_id FROM artifacts WHERE task_id=ANY(%s)',([c['id'] for c in collaborators],)).fetchall()
                assert len(artifacts)==2
                for artifact in artifacts:
                    receipt=conn.execute("SELECT response FROM tool_receipts WHERE task_id=%s AND operation='read_artifact' AND response->>'artifact_id'=%s",(manager['id'],str(artifact['id']))).fetchone()
                    child=next(c for c in collaborators if c['id']==artifact['task_id'])
                    expected='42' if child['bot_id']=='researcher' else '110'
                    assert receipt and re.search(r'(?<!\d)'+expected+r'(?!\d)',receipt['response']['content'])
                attempts=conn.execute('SELECT * FROM attempts WHERE task_id=%s ORDER BY number',(identity,)).fetchall()
                assert len(attempts)>=2 and len({a['session_id'] for a in attempts})==1
                delivery=conn.execute('SELECT id FROM messages WHERE task_id=%s AND source_id=%s AND conversation_id=%s AND reply_to_message_id=%s',(identity,'runtime-result:'+str(attempts[-1]['id']),proof['conversation_id'],manager['reply_to_message_id'])).fetchall()
                assert len(delivery)==1
                evidence.append({'task_id':identity,'work_item_id':str(manager['work_item_id']),'thread':manager['reply_to_message_id'],
                    'session_id':attempts[0]['session_id'],'manager_attempts':len(attempts),'required_results':'fulfilled',
                    'accepted_contributions':[str(c['task_id']) for c in contributions],
                    'artifact_ids':[str(a['id']) for a in artifacts],'native_terminal_verified':True,
                    'manager_read_both_artifacts':True,'final_message_id':delivery[0]['id'],'budget':execution_policy.budget(conn,manager)})
            assert len({r['thread'] for r in evidence})==len(evidence)
            assert len({r['work_item_id'] for r in evidence})==len(evidence)
            assert len({r['session_id'] for r in evidence})==len(evidence)
            assert not conn.execute('SELECT 1 FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.conversation_id=%s',(proof['conversation_id'],)).fetchone()
    finally:
        db.pool.close()
    proof['verified_runs']=evidence
    proof['owner_wait_resumed']=proof['wait_status']=='completed'
    save(proof)
    print(json.dumps({'verified_runs':len(evidence),'owner_wait_resumed':proof['owner_wait_resumed'],'connected_provider_operations':0}),flush=True)


def archive_demonstrations():
    proof=json.loads(PATH.read_text())
    if proof.get('demonstrations_archived'):
        return
    assert len(proof.get('verified_runs',[]))>=3
    for route,key in [('routines','routine_id'),('event-triggers','trigger_id')]:
        row=next(r for r in request('GET','/api/v1/'+route)['data'] if r['id']==proof[key])
        if row['state']!='archived':
            request('POST',f"/api/v1/{route}/{row['id']}/archive",{'expected_revision':row['revision']},'phase53-cleanup:'+key+proof['marker'])
    source=next(s for s in request('GET','/api/v1/event-sources')['data'] if s['id']==proof['source_id'])
    if source['status']!='archived':
        request('POST',f"/api/v1/event-sources/{source['id']}/archive",{'expected_revision':source['revision']},'phase53-cleanup-source:'+proof['marker'])
    proof['demonstrations_archived']=True
    save(proof)
    print('Completed scheduled/event fixtures archived. Owner question remains available.',flush=True)


def cleanup():
    proof=json.loads(PATH.read_text())
    if proof.get('cleanup_complete'):
        return
    assert proof.get('owner_wait_resumed') and len(proof.get('verified_runs',[]))==4, 'Keep the owner question available until the actual decision and verification'
    archive_demonstrations()
    proof=json.loads(PATH.read_text())
    waiting=next(r for r in request('GET','/api/v1/routines')['data'] if r['id']==proof['wait_routine_id'])
    if waiting['state']!='archived':
        request('POST',f"/api/v1/routines/{waiting['id']}/archive",{'expected_revision':waiting['revision']},'phase53-cleanup-wait:'+proof['marker'])
    request('POST',f"/api/v1/grants/{proof['grant_id']}/revoke")
    request('DELETE',f"/api/v1/groups/{proof['conversation_id']}")
    snapshot=state()
    assert set(proof['original_groups'])=={c['id'] for c in snapshot['conversations'] if c['kind']=='group'}
    assert proof['original_bots']=={b['id']:{k:b[k] for k in ('revision','status','desired_state','provider_id','model_name','enabled_tools','enabled_skills','memory_mb','cpus')} for b in snapshot['bots']}
    routines=request('GET','/api/v1/routines')['data']
    assert proof['original_active_routines']=={r['id']:hashlib.sha256(json.dumps(r['definition'],sort_keys=True).encode()).hexdigest() for r in routines if r['state']=='active'}
    proof.update(cleanup_complete=True,original_bots_groups_routines_preserved=True)
    save(proof)
    print('Phase 5.3 fixtures archived; owner bots, groups and routines preserved.',flush=True)


if __name__=='__main__':
    {'start':start,'status':status,'event':event,'verify':verify,'archive-demonstrations':archive_demonstrations,'cleanup':cleanup}[sys.argv[1] if len(sys.argv)>1 else 'status']()
