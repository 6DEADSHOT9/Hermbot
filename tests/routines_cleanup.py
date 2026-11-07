"""Archive only Phase 5.1 fixtures after local acceptance; retain all history."""
import json
from pathlib import Path

from .protocol_acceptance import client, state

PATH = Path('/app/test-results/phase5-routines-cleanup.json')
LIVE = json.loads(Path('/app/test-results/phase5-routines-live.json').read_text())
DRAFT = json.loads(Path('/app/test-results/phase5-routines-draft-live.json').read_text())
RESTART = json.loads(Path('/app/test-results/phase5-routines-restart.json').read_text())


def main():
    assert LIVE['completed'] and DRAFT['verified'] and RESTART['verified']
    group_id = LIVE['conversation_id']
    routine_ids = [LIVE['routine_id'], DRAFT['drafts'][0]['id']]
    current = state()
    related = [t for t in current['tasks'] if t['conversation_id'] == group_id]
    assert related and all(t['status'] in ('completed','failed','cancelled','interrupted','uncertain') for t in related)
    assert not any(t['status'] == 'uncertain' for t in related)
    proof = {'fixture_group_id': group_id, 'routines': routine_ids,
             'completed_task_ids': [t['id'] for t in related if t['status'] == 'completed'],
             'external_changes_requested': False}
    for identity in routine_ids:
        listing = client.get('/api/v1/routines')
        listing.raise_for_status()
        row = next(r for r in listing.json()['data'] if r['id'] == identity)
        if row['state'] != 'archived':
            result = client.post(f'/api/v1/routines/{identity}/archive', json={'expected_revision': row['revision']},
                                 headers={'Idempotency-Key':'phase5-cleanup-archive-'+identity})
            result.raise_for_status()
    if any(c['id'] == group_id for c in state()['conversations']):
        response = client.delete('/api/v1/groups/' + group_id)
        response.raise_for_status()
    listing = client.get('/api/v1/routines')
    listing.raise_for_status()
    assert all(next(r for r in listing.json()['data'] if r['id']==identity)['state']=='archived' for identity in routine_ids)
    response = client.get(f'/api/v1/routines/{LIVE["routine_id"]}/runs')
    response.raise_for_status()
    runs = response.json()['data']
    assert sum(r['display_status']=='completed' for r in runs) == 3
    assert all('42' in (r['output'] or '') for r in runs if r['display_status']=='completed')
    proof['archived'] = True
    proof['completed_run_count'] = 3
    proof['group_hidden_from_roster'] = not any(c['id']==group_id for c in state()['conversations'])
    PATH.write_text(json.dumps(proof, indent=2))
    print(json.dumps({'archived':True,'completed_run_count':3,'group_hidden_from_roster':proof['group_hidden_from_roster']}))


if __name__=='__main__':
    main()
