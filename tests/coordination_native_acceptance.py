"""Local real-model coordination proof; no connected-app operations."""
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx

from backend import db
from backend.settings import settings

PROOF = Path('/app/test-results/coordination-native.json')
BEFORE = Path('/app/test-results/coordination-before.json')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'uncertain'}


def main():
    mode = sys.argv[1]
    db.pool.open(wait=True)
    try:
        with httpx.Client(base_url='http://api:8000', headers={'Authorization': 'Bearer ' + settings.owner_token}, timeout=45) as client:
            def call(method, path, body=None, key=None):
                response = client.request(method, path, json=body, headers={'Idempotency-Key': key or str(uuid4())} if method == 'POST' else {})
                response.raise_for_status()
                return response.json()
            def metadata(state):
                return {'bots': [{k: b[k] for k in ('id','name','role','provider_id','model_name','enabled_tools','enabled_skills','memory_mb','cpus')} for b in state['bots']],
                        'conversations': state['conversations'], 'connections': call('GET', '/api/v1/connections')}
            if mode == 'snapshot':
                assert not BEFORE.exists(), 'Preserve the original deployment snapshot'
                state = call('GET', '/api/v1/state')
                assert all(t['status'] in TERMINAL for t in state['tasks']), 'Wait for current owner work before deployment'
                BEFORE.write_text(json.dumps(metadata(state), indent=2) + '\n')
                print('Recorded nonsecret configuration and conversation metadata.', flush=True)
                return
            if mode == 'start':
                assert not PROOF.exists(), 'Resume the saved fixture instead of duplicating its work'
                state = call('GET', '/api/v1/state')
                assert not state['protocol_fixture']
                assert all(t['status'] in TERMINAL for t in state['tasks'])
                proof = {'complete': False, 'real_model': True, 'external_application_changes': False, 'identity': uuid4().hex[:8], 'request_key': str(uuid4())}
                PROOF.write_text(json.dumps(proof, indent=2) + '\n')
                group = call('POST', '/api/v1/groups', {'title': 'Coordination acceptance ' + proof['identity'], 'bot_ids': ['researcher','writer','reviewer']})
                proof['conversation_id'] = group['id']
                PROOF.write_text(json.dumps(proof, indent=2) + '\n')
                prompt = """Produce one combined local text report using Scout and Quill as REQUIRED CONTRIBUTIONS.
This is an acceptance task; all files are published text artifacts, no connected apps or web research.
Only combined-report.txt is the owner-required deliverable. Branch files are supporting evidence,
not independently requested deliverables. Record exactly that one final artifact requirement.
Delegate Scout's contribution: run a foreground native terminal command that sleeps 8 seconds and
prints SCOUT_CONTRIBUTION_OK, then publish scout-contribution.txt containing that marker and submit
the contribution with hermbot_finish. Delegate Quill's contribution: run a foreground native terminal
command that sleeps 18 seconds and prints QUILL_CONTRIBUTION_OK, then publish quill-contribution.txt
containing that marker and submit the contribution. Each assignment contributes to the same combined
report requirement; use responsibility=contribution, expected_output, and its current requirement key.
After dispatching both, send Quill an informational hermbot_message asking which contribution it is
covering. In Quill's brief tell it to answer this inbox message with hermbot_message using reply_to,
without creating any delegated assignment. Continue independent work yourself: make a native terminal
call printing MANAGER_INDEPENDENT_PROGRESS_OK after sending the message and before waiting.
When you need the contributions, use hermbot_wait for the two assignment references. Once both have
submitted and settled, inspect both actual files using hermbot_read_artifact. Publish combined-report.txt
containing BOTH exact contribution markers and your synthesis. Submit that final artifact with
hermbot_finish.results, a review explaining satisfaction of the current requirement and both
reviewed_task_ids. End the turn after finish. Do not claim completion from the first contribution.
Do not create additional owner deliverables, use connected applications, or request owner approval."""
                task = call('POST', '/api/v1/messages', {'bot_id': 'reviewer', 'conversation_id': group['id'], 'prompt': prompt, 'managed_bot_ids': ['researcher','writer']}, proof['request_key'])
                proof['task_id'] = task['id']
                PROOF.write_text(json.dumps(proof, indent=2) + '\n')
                print('Started the real three-bot acceptance.', flush=True)
                return
            proof = json.loads(PROOF.read_text())
            def save():
                PROOF.write_text(json.dumps(proof, indent=2) + '\n')
            if mode == 'recover':
                state = call('GET', '/api/v1/state')
                assert all(t['status'] in TERMINAL - {'uncertain'} for t in state['tasks'] if t['conversation_id']==proof['conversation_id'])
                proof['failure_reason'] = 'Inbox middleware used Chat Completions fields for a Responses request; corrected and regression tested.'
                failed = PROOF.with_name('coordination-native-failed.json')
                assert not failed.exists(), 'Inspect the failed acceptance before another recovery'
                call('DELETE', '/api/v1/conversations/' + proof['conversation_id'])
                proof['fixture_archived'] = True
                failed.write_text(json.dumps(proof, indent=2) + '\n')
                PROOF.unlink()
                print('Archived the failed local fixture and retained its evidence. A fresh acceptance may now start.', flush=True)
                return
            if mode == 'verify':
                deadline = time.monotonic() + 420
                while time.monotonic() < deadline:
                    state = call('GET', '/api/v1/state')
                    item = next((w for w in state['work_items'] if w['conversation_id'] == proof['conversation_id']), None)
                    if item:
                        contributions = [c for o in item.get('outcomes', []) for c in o.get('contributions', [])]
                        if any(c['status'] == 'submitted' for c in contributions) and any(c['status'] == 'pending' for c in contributions):
                            assert item['status'] != 'completed'
                            proof['partial_result_kept_open'] = True
                            save()
                        if item['status'] == 'completed' and all(t['status'] in TERMINAL for t in state['tasks'] if t['conversation_id'] == proof['conversation_id']):
                            break
                        root = next(t for t in state['tasks'] if t['id'] == proof['task_id'])
                        if root['status'] in TERMINAL and root['status'] != 'completed':
                            raise AssertionError((root['status'], root['error']))
                    time.sleep(1)
                else:
                    raise AssertionError('Retain and inspect the recorded acceptance; do not replay it')
                assert len(item['outcomes']) == 1, [(o['key'],o['kind']) for o in item['outcomes']]
                outcome = item['outcomes'][0]
                assert outcome['acceptance'] and outcome['status'] == 'fulfilled'
                assert len(outcome['contributions']) == 2
                assert all(c['status'] == 'accepted' and c['required'] for c in outcome['contributions'])
                combined = next(a for a in item['outputs'] if a['name'] == 'combined-report.txt')
                response = client.get('/api/v1/artifacts/' + combined['id'])
                response.raise_for_status()
                assert 'SCOUT_CONTRIBUTION_OK' in response.text and 'QUILL_CONTRIBUTION_OK' in response.text
                with db.connection() as conn:
                    messages = conn.execute('SELECT id,sender,recipient,reply_to,status,source_task_id FROM teammate_messages WHERE conversation_id=%s ORDER BY created_at', (proof['conversation_id'],)).fetchall()
                    assert any(m['sender']=='reviewer' and m['recipient']=='writer' for m in messages)
                    assert any(m['sender']=='writer' and m['recipient']=='reviewer' and m['reply_to'] for m in messages)
                    assert conn.execute("SELECT count(*) AS n FROM tasks WHERE conversation_id=%s AND parent_id IS NOT NULL", (proof['conversation_id'],)).fetchone()['n'] == 2
                    assert conn.execute('SELECT count(*) AS n FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.conversation_id=%s', (proof['conversation_id'],)).fetchone()['n'] == 0
                    proof['messages'] = json.loads(json.dumps(messages, default=str))
                    accepted = conn.execute('SELECT evidence FROM result_acceptances WHERE outcome_id=%s AND outcome_revision=%s', (outcome['id'], outcome['revision'])).fetchone()['evidence']
                    assert accepted['artifact_sha256'] and accepted['review']
                    proof['acceptance'] = accepted
                history = call('GET','/api/v1/conversations/' + proof['conversation_id'] + '/runtime-history/reviewer')['data']
                assert any('MANAGER_INDEPENDENT_PROGRESS_OK' in str(m.get('content','')) for m in history if m.get('role') == 'tool')
                proof.update(complete=True, accepted_artifact_id=combined['id'], contribution_count=2, messages_created_no_child_assignments=True, independent_progress=True)
                save()
                print('Real messages, independent work, contribution review and final acceptance passed.', flush=True)
                return
            if mode == 'cleanup':
                assert proof['complete']
                state = call('GET', '/api/v1/state')
                assert all(t['status'] in TERMINAL for t in state['tasks'] if t['conversation_id']==proof['conversation_id'])
                call('DELETE', '/api/v1/conversations/' + proof['conversation_id'])
                state = call('GET', '/api/v1/state')
                assert metadata(state) == json.loads(BEFORE.read_text())
                assert all(t['status'] in TERMINAL for t in state['tasks'])
                proof.update(fixture_archived=True, configuration_preserved=True)
                save()
                print('Archived the fixture; original bots, conversations and connections preserved.', flush=True)
                return
            raise AssertionError('Unknown acceptance mode')
    finally:
        db.pool.close()


if __name__ == '__main__':
    main()
