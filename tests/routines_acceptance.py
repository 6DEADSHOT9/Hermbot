"""Opt-in local native routine proof. No connected-provider changes are requested."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

from .protocol_acceptance import client, state

PATH = Path('/app/test-results/phase5-routines-live.json')


def main():
    if PATH.exists():
        proof = json.loads(PATH.read_text())
    else:
        snapshot = state()
        assert not any(t['status'] not in ('completed','failed','cancelled','interrupted','uncertain') for t in snapshot['tasks'])
        marker = str(uuid4())
        group = client.post('/api/v1/groups', json={'title':'Routine acceptance '+marker[:8], 'bot_ids':['researcher','writer']})
        group.raise_for_status()
        starts = datetime.now(timezone.utc) + timedelta(seconds=90)
        definition = {'name':'Native routine acceptance '+marker[:8], 'bot_id':'researcher', 'conversation_id':group.json()['id'],
            'instructions':'For this local scheduling acceptance, answer with one sentence containing: routine proof, 6 multiplied by 7 is 42. Use the registered result key report to submit this sentence. Do not use external services, delegate, or change anything.',
            'schedule':{'kind':'interval','timezone':'Asia/Kolkata','at':starts.isoformat(),'interval_minutes':2},
            'requirements':[{'key':'report','title':'The requested arithmetic sentence','kind':'report'}],
            'active_budget_seconds':120,'start_window_minutes':10,'max_runs_per_day':2}
        created = client.post('/api/v1/routines',json=definition,headers={'Idempotency-Key':'routine-proof:'+marker})
        created.raise_for_status()
        proof = {'marker':marker,'routine_id':created.json()['id'],'conversation_id':group.json()['id'],'starts':starts.isoformat(),'external_changes_requested':False,'native_model':True,'created':True}
        PATH.write_text(json.dumps(proof,indent=2))
    end = time.monotonic()+480
    while time.monotonic()<end:
        response=client.get('/api/v1/routines/'+proof['routine_id']+'/runs')
        response.raise_for_status()
        runs=response.json()['data']
        listing=client.get('/api/v1/routines')
        listing.raise_for_status()
        routine=next(r for r in listing.json()['data'] if r['id']==proof['routine_id'])
        proof['scheduler']=routine['scheduler']
        proof['runs']=[{k:r[k] for k in ('id','scheduled_at','origin','status','display_status','task_id','reason','execution_status')} for r in runs]
        PATH.write_text(json.dumps(proof,indent=2))
        done=[r for r in runs if r['display_status']=='completed']
        if len(done)>=2:
            assert all('42' in (r['output'] or '') and 'routine proof' in (r['output'] or '').lower() for r in done)
            assert routine['scheduler']['last_tick_at'] and not routine['scheduler']['error']
            proof['two_native_scheduled_runs_verified']=True
            proof['completed']=True
            PATH.write_text(json.dumps(proof,indent=2))
            print(json.dumps({'complete':True,'routine_id':proof['routine_id'],'runs':len(done)}),flush=True)
            return
        print(json.dumps({'routine_id':proof['routine_id'],'scheduler_error':routine['scheduler']['error'] if routine['scheduler'] else 'not yet installed','runs':[(r['display_status'],r['reason']) for r in runs]}),flush=True)
        time.sleep(15)
    raise AssertionError('Native routine proof incomplete; original identity retained, do not resubmit')


if __name__=='__main__':
    main()
