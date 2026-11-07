"""Continue the same local routine after worker/API restart; submit only local arithmetic."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from .protocol_acceptance import client

PATH = Path('/app/test-results/phase5-routines-restart.json')
ROUTINE = '421a0c64-443c-453f-9cee-64d04417bac9'


def current():
    response = client.get('/api/v1/routines')
    response.raise_for_status()
    return next(r for r in response.json()['data'] if r['id'] == ROUTINE)


def main():
    proof = json.loads(PATH.read_text()) if PATH.exists() else {'routine_id': ROUTINE, 'started_at': datetime.now(timezone.utc).isoformat(), 'external_changes_requested': False}
    if not PATH.exists():
        PATH.write_text(json.dumps(proof, indent=2))
    row = current()
    if row['state'] == 'active':
        response = client.post(f'/api/v1/routines/{ROUTINE}/pause', json={'expected_revision': row['revision']},
                               headers={'Idempotency-Key': 'phase5-restart-pause'})
        response.raise_for_status()
        row = response.json()
    if row['revision'] == 2 and row['state'] == 'paused':
        definition = {**row['definition'], 'enabled': False, 'max_runs_per_day': 3, 'expected_revision': row['revision']}
        response = client.put(f'/api/v1/routines/{ROUTINE}', json=definition,
                              headers={'Idempotency-Key': 'phase5-restart-cap-three'})
        response.raise_for_status()
        row = response.json()
    assert row['revision'] == 3 and row['state'] == 'paused'
    proof['revision'] = row['revision']
    if not proof.get('manual_run_id'):
        response = client.post(f'/api/v1/routines/{ROUTINE}/run', json={'expected_revision': row['revision']},
                               headers={'Idempotency-Key': 'phase5-restart-manual-run'})
        response.raise_for_status()
        run = response.json()
        proof['manual_run_id'] = run['id']
        proof['manual_task_id'] = run['task_id']
        PATH.write_text(json.dumps(proof, indent=2))
    assert proof['manual_task_id']
    end = time.monotonic() + 300
    while time.monotonic() < end:
        response = client.get(f'/api/v1/routines/{ROUTINE}/runs')
        response.raise_for_status()
        run = next(r for r in response.json()['data'] if r['id'] == proof['manual_run_id'])
        proof['manual_status'] = run['display_status']
        proof['scheduler'] = current()['scheduler']
        PATH.write_text(json.dumps(proof, indent=2))
        if run['display_status'] in ('completed','failed','cancelled','interrupted','uncertain'):
            assert run['display_status'] == 'completed'
            assert '42' in (run['output'] or '') and 'routine proof' in (run['output'] or '').lower()
            proof['verified'] = True
            PATH.write_text(json.dumps(proof, indent=2))
            print(json.dumps({'verified': True, 'manual_run_id': run['id']}), flush=True)
            return
        print(json.dumps({'manual_status': run['display_status']}), flush=True)
        time.sleep(15)
    raise AssertionError('Post-restart run incomplete; original identity retained, do not resubmit')


if __name__ == '__main__':
    main()
