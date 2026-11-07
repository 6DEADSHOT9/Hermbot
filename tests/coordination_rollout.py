"""Upgrade idle local runtimes through the existing owner lifecycle API."""
import json
from pathlib import Path
import time

import httpx

from backend.settings import settings

PROOF = Path('/app/test-results/coordination-rollout-providerfix.json')


def main():
    proof = json.loads(PROOF.read_text()) if PROOF.exists() else {'complete': False, 'operations': {}}
    with httpx.Client(base_url='http://api:8000', headers={'Authorization': 'Bearer ' + settings.owner_token}, timeout=40) as client:
        state_response = client.get('/api/v1/state')
        state_response.raise_for_status()
        state = state_response.json()
        assert all(t['status'] in ('completed','failed','cancelled','interrupted','uncertain') for t in state['tasks'])
        for bot_id in ('researcher', 'writer', 'reviewer'):
            if bot_id in proof['operations']:
                continue
            bot = next(b for b in state['bots'] if b['id'] == bot_id)
            response = client.post('/api/v1/bots/' + bot_id + '/start', json={'expected_revision': bot['revision'], 'interrupt': False}, headers={'Idempotency-Key': PROOF.stem + '-' + bot_id})
            response.raise_for_status()
            proof['operations'][bot_id] = response.json()['id']
            PROOF.write_text(json.dumps(proof, indent=2) + '\n')
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            response = client.get('/api/v1/state')
            response.raise_for_status()
            state = response.json()
            operations = [o for o in state['operations'] if o['id'] in proof['operations'].values()]
            ready = all(b['readiness'].get('compatible') for b in state['bots'] if b['id'] in proof['operations'])
            if len(operations) == 3 and all(o['status'] == 'completed' for o in operations) and ready:
                proof['complete'] = True
                PROOF.write_text(json.dumps(proof, indent=2) + '\n')
                print('All three runtimes upgraded and ready.', flush=True)
                return
            assert not any(o['status'] in ('failed','uncertain') for o in operations), [(o['bot_id'],o['status']) for o in operations]
            time.sleep(1)
        raise AssertionError('Inspect the recorded lifecycle operations before retrying')


if __name__ == '__main__':
    main()
