"""Opt-in composed native exit proof; reuse prior actual owner-answer evidence."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from . import recurring_management_acceptance as native

PATH = Path('/app/test-results/phase5-exit-live.json')
native.PATH = PATH
native.LABEL = 'Phase 5 exit'
native.INSTRUCTIONS = native.INSTRUCTIONS.replace('Phase 5.3', 'Phase 5 exit')
request = native.request


def start():
    if PATH.exists():
        return native.status()
    snapshot = native.state()
    assert all(t['status'] in native.TERMINAL for t in snapshot['tasks']), 'Owner work must settle first'
    earlier = json.loads(Path('/app/test-results/phase54-live.json').read_text())
    assert earlier['native_verified']['actual_owner_answer_verified']
    marker = uuid4().hex[:12]
    starts = datetime.now(timezone.utc) + timedelta(seconds=90)
    proof = {
        'marker': marker, 'created_at': datetime.now(timezone.utc).isoformat(),
        'native_model': True, 'external_mutations_requested': False,
        'owner_answer_simulated': False, 'wait_status': None,
        'owner_delay_evidence': 'phase54-live.json',
        'original_groups': [c['id'] for c in snapshot['conversations'] if c['kind'] == 'group'],
        'original_bots': {b['id']: {k: b[k] for k in ('revision','status','desired_state','provider_id','model_name','enabled_tools','enabled_skills','memory_mb','cpus')} for b in snapshot['bots']},
        'original_active_routines': {r['id']: hashlib.sha256(json.dumps(r['definition'],sort_keys=True).encode()).hexdigest() for r in request('GET','/api/v1/routines')['data'] if r['state']=='active'},
        'starts': starts.isoformat(),
    }
    native.save(proof)
    group = request('POST','/api/v1/groups',{'title':'Phase 5 exit acceptance '+marker,'bot_ids':['researcher','writer','reviewer']})
    proof['conversation_id'] = group['id']
    native.save(proof)
    authority = request('POST','/api/v1/grants',{'manager_id':'reviewer','targets':['researcher','writer'],'operations':native.OPS,'duration_hours':24,'max_created_bots':0},'phase5-exit-grant:'+marker)
    proof['grant_id'] = authority['id']
    native.save(proof)
    routine = request('POST','/api/v1/routines',{
        'name':'Phase 5 exit scheduled manager '+marker,'bot_id':'reviewer','conversation_id':group['id'],
        'instructions':native.INSTRUCTIONS,
        'requirements':[{'key':'report','title':'Reviewed exit calculations: 42, 110 and 152','kind':'report'}],
        'management':{'grant_id':authority['id'],'targets':['researcher','writer'],'operations':native.OPS},
        'schedule':{'kind':'interval','timezone':'Asia/Kolkata','at':starts.isoformat(),'interval_minutes':60},
        'active_budget_seconds':900,'start_window_minutes':15,'max_runs_per_day':1,'enabled':True,
    },'phase5-exit-schedule:'+marker)
    proof['routine_id'] = routine['id']
    native.save(proof)
    native.status()


def replay():
    """Redeliver the identical signed local event through the real HTTP endpoint."""
    from backend import db
    from backend.secrets import decrypt

    proof = json.loads(PATH.read_text())
    assert proof['event_sent']
    source = next(s for s in request('GET','/api/v1/event-sources')['data'] if s['id']==proof['source_id'])
    db.pool.open(wait=True)
    try:
        with db.connection() as conn:
            secret = json.loads(decrypt(conn.execute('SELECT secret_encrypted FROM event_sources WHERE id=%s',(source['id'],)).fetchone()['secret_encrypted']))['signing_secret']
    finally:
        db.pool.close()
    payload = json.dumps({'type':'phase53.local','resource':proof['marker'],'text':'Local recurring-management event acceptance.'}).encode()
    delivery, timestamp = proof['event_delivery'], str(int(time.time()))
    signature = base64.b64encode(hmac.new(base64.b64decode(secret[6:]),delivery.encode()+b'.'+timestamp.encode()+b'.'+payload,hashlib.sha256).digest()).decode()
    response = native.client.post(source['webhook_path'],content=payload,headers={'Content-Type':'application/json','webhook-id':delivery,'webhook-timestamp':timestamp,'webhook-signature':'v1,'+signature})
    response.raise_for_status()
    proof['replays_sent'] = proof.get('replays_sent',0)+1
    native.save(proof)
    native.status()


def verify():
    from backend import attention, db

    # The reused verifier closes its database pool; run it in its own process.
    subprocess.run([sys.executable,'-c','from tests.phase5_exit_acceptance import native; native.verify(expected_scheduled_runs=1)'],check=True)
    proof = json.loads(PATH.read_text())
    assert len(proof['verified_runs']) == 2 and proof.get('replays_sent',0) >= 2
    assert proof.get('restart_verified'), 'Record the actual restart comparison first'
    db.pool.open(wait=True)
    try:
        with db.connection() as conn:
            previous = json.loads(Path('/app/test-results/phase54-live.json').read_text())
            question = conn.execute('SELECT status,answer_source,answer FROM input_requests WHERE task_id=%s',(previous['task_id'],)).fetchone()
            assert question['status']=='answered' and question['answer_source']=='owner' and question['answer']=='Continue local test'
            sessions = conn.execute('SELECT DISTINCT session_id FROM attempts WHERE task_id=%s',(previous['task_id'],)).fetchall()
            assert len(sessions)==1 and sessions[0]['session_id']==previous['native_verified']['session_id']
            proof['owner_delay_verified'] = {'evidence':'phase54-live.json','actual_owner_answer':True,'same_native_session':True,'reused_unchanged_path':True}
            duplicate = conn.execute('SELECT duplicate_count FROM trigger_deliveries WHERE source_id=%s AND delivery_key=%s',(proof['source_id'],proof['event_delivery'])).fetchone()
            assert duplicate['duplicate_count'] >= 2
            for run in proof['verified_runs']:
                attention.project(conn,'work',run['work_item_id'])
                item = conn.execute("SELECT id,version,refs FROM attention_items WHERE subject_type='work' AND subject_id=%s AND purpose='result' AND active",(run['work_item_id'],)).fetchone()
                assert item and item['refs']['conversation_id'] == proof['conversation_id']
                run['attention_id'] = str(item['id'])
            proof['verified_duplicate_deliveries'] = duplicate['duplicate_count']
    finally:
        db.pool.close()
    native.save(proof)
    print(json.dumps({'verified_native_runs':2,'duplicate_deliveries':proof['verified_duplicate_deliveries'],'result_attention_ids':[r['attention_id'] for r in proof['verified_runs']]}))


def archive_definitions():
    proof = json.loads(PATH.read_text())
    assert len(proof.get('verified_runs',[])) == 2
    for route, key in [('routines','routine_id'),('event-triggers','trigger_id')]:
        row = next(r for r in request('GET','/api/v1/'+route)['data'] if r['id']==proof[key])
        if row['state'] != 'archived':
            request('POST',f"/api/v1/{route}/{row['id']}/archive",{'expected_revision':row['revision']},'phase5-exit-cleanup:'+key+proof['marker'])
    source = next(s for s in request('GET','/api/v1/event-sources')['data'] if s['id']==proof['source_id'])
    if source['status'] != 'archived':
        request('POST',f"/api/v1/event-sources/{source['id']}/archive",{'expected_revision':source['revision']},'phase5-exit-cleanup-source:'+proof['marker'])
    proof['future_fixture_runs_disabled'] = True
    native.save(proof)
    print('Only the completed exit fixture schedule, event trigger and local source archived.')


def cleanup():
    proof = json.loads(PATH.read_text())
    assert len(proof.get('verified_runs',[])) == 2 and proof.get('closed_tab_result_navigation_verified')
    snapshot = native.state()
    fixture_tasks = {r['task_id'] for r in proof['verified_runs']} | {t for r in proof['verified_runs'] for t in r['accepted_contributions']} | {proof['closed_tab_task_id']}
    actual_tasks = [t for t in snapshot['tasks'] if t['conversation_id']==proof['conversation_id']]
    assert {t['id'] for t in actual_tasks}==fixture_tasks and all(t['status'] in native.TERMINAL for t in actual_tasks), 'Inspect new or unfinished fixture-group work before cleanup'
    archive_definitions()
    proof = json.loads(PATH.read_text())
    request('POST',f"/api/v1/grants/{proof['grant_id']}/revoke")
    request('DELETE',f"/api/v1/groups/{proof['conversation_id']}")
    snapshot = native.state()
    assert set(proof['original_groups']).issubset(c['id'] for c in snapshot['conversations'])
    assert proof['original_bots'] == {b['id']:{k:b[k] for k in ('revision','status','desired_state','provider_id','model_name','enabled_tools','enabled_skills','memory_mb','cpus')} for b in snapshot['bots']}
    routines = request('GET','/api/v1/routines')['data']
    assert proof['original_active_routines'] == {r['id']:hashlib.sha256(json.dumps(r['definition'],sort_keys=True).encode()).hexdigest() for r in routines if r['state']=='active'}
    proof['cleanup_complete'] = True
    native.save(proof)
    print('Only Phase 5 exit fixtures archived/revoked; original groups, bots and active routines preserved.')


if __name__ == '__main__':
    {'start':start,'status':native.status,'event':native.event,'replay':replay,'verify':verify,'archive-definitions':archive_definitions,'cleanup':cleanup}[sys.argv[1] if len(sys.argv)>1 else 'status']()
