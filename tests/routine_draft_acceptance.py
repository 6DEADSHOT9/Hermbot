"""One real interpreter proposal, kept inactive until an owner save."""
import json
from pathlib import Path
import time
from uuid import uuid4

from .protocol_acceptance import client, state

PATH = Path('/app/test-results/phase5-routines-draft-live.json')
GROUP = '5739adf6-0a86-40a3-916b-d41a75932572'


def main():
    if PATH.exists():
        proof = json.loads(PATH.read_text())
    else:
        marker = uuid4().hex[:8]
        before = client.get('/api/v1/routines').json()['data']
        routine_ids = {r['id'] for r in before}
        work_ids = {w['id'] for w in state()['work_items']}
        request = ('Create an inactive draft routine for Scout in this group named "Weekday arithmetic draft ' + marker + '". '
                   'Schedule it at 09:00 Asia/Kolkata on weekdays. For each run, Scout should write a short report saying '
                   '6 multiplied by 7 is 42. Do not activate it; I will inspect and save it myself. '
                   'Do not contact external services or change files.')
        response = client.post('/api/v1/messages', json={'bot_id': 'researcher', 'conversation_id': GROUP,
                               'prompt': request, 'intent': 'auto'}, headers={'Idempotency-Key': 'routine-draft-proof-' + marker})
        response.raise_for_status()
        proof = {'marker': marker, 'task_id': response.json()['id'], 'routine_ids_before': sorted(routine_ids),
                 'work_ids_before': sorted(work_ids), 'external_changes_requested': False}
        PATH.write_text(json.dumps(proof, indent=2))
    end = time.monotonic() + 300
    while time.monotonic() < end:
        current = state()
        task = next(t for t in current['tasks'] if t['id'] == proof['task_id'])
        routines = client.get('/api/v1/routines').json()['data']
        created = [r for r in routines if r['id'] not in proof['routine_ids_before']]
        proof['task_status'] = task['status']
        proof['intake_status'] = task.get('intake_status')
        proof['drafts'] = [{'id': r['id'], 'state': r['state'], 'name': r['definition']['name'],
                            'next_run_at': r['next_run_at']} for r in created]
        proof['new_work_ids'] = sorted({w['id'] for w in current['work_items']} - set(proof['work_ids_before']))
        PATH.write_text(json.dumps(proof, indent=2))
        if task['status'] in ('completed', 'failed', 'cancelled', 'interrupted', 'uncertain'):
            assert task['status'] == 'completed', task['status']
            assert len(created) == 1 and created[0]['state'] == 'draft' and created[0]['next_run_at'] is None, proof['drafts']
            assert not proof['new_work_ids'], proof['new_work_ids']
            proof['verified'] = True
            PATH.write_text(json.dumps(proof, indent=2))
            print(json.dumps({'verified': True, 'draft_id': created[0]['id']}), flush=True)
            return
        print(json.dumps({'task_status': task['status'], 'intake_status': task.get('intake_status'), 'drafts': len(created)}), flush=True)
        time.sleep(15)
    raise AssertionError('Draft proof incomplete; original task and identity retained, do not resubmit')


if __name__ == '__main__':
    main()
