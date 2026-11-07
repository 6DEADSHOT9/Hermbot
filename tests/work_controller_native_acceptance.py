"""Real Hermes execution, artifact transfer and owner corrections; no connected-app writes."""
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx

from backend.settings import settings

PROOF = Path('/app/test-results/work-controller-native.json')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'uncertain'}


def main():
    mode = sys.argv[1]
    client = httpx.Client(base_url='http://api:8000', headers={'Authorization': 'Bearer ' + settings.owner_token}, timeout=40)
    def call(method, path, body=None):
        response = client.request(method, path, json=body, headers={'Idempotency-Key': str(uuid4())} if method in ('POST', 'PUT') else {})
        response.raise_for_status()
        return response.json()
    def save():
        PROOF.write_text(json.dumps(proof, indent=2))
    def submit(bot, prompt, *, manager=False):
        task = call('POST', '/api/v1/messages', {'bot_id': bot, 'conversation_id': proof['conversation_id'], 'prompt': prompt,
                   **({'managed_bot_ids': ['researcher', 'writer']} if manager else {})})
        proof['tasks'].append(task['id'])
        save()
        return task
    def until(predicate, timeout=300):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            state = call('GET', '/api/v1/state')
            if predicate(state):
                return state
            time.sleep(1)
        raise AssertionError('Native acceptance did not reach the expected state')
    if mode == 'start':
        if PROOF.exists():
            previous = json.loads(PROOF.read_text())
            assert previous.get('fixture_archived'), 'Clean the previous fixture before starting another acceptance run'
        state = call('GET', '/api/v1/state')
        assert not state['protocol_fixture']
        assert not any(t['status'] not in TERMINAL for t in state['tasks'])
        group = call('POST', '/api/v1/groups', {'title': 'Work coordination acceptance ' + str(uuid4())[:8], 'bot_ids': ['researcher', 'writer', 'reviewer']})
        proof = {'complete': False, 'live_model': True, 'external_application_changes': False, 'tasks': [], 'conversation_id': group['id']}
        save()
        task = submit('researcher', 'This is a local execution acceptance task. Make exactly 30 separate native terminal calls, one per number 1 through 30, each printing WORK_ITERATION_<number>. Each assistant turn must invoke exactly one terminal call and wait for its result before planning the next. Do not combine calls into a script or batch. Then create /workspace/work-controller-iterations.txt containing WORK_CONTROLLER_ITERATIONS_OK. Publish that actual file using hermbot_publish_file with text/plain. Finish with result statuses by current requirement keys and actual evidence. Do not delegate, use connected apps, or request review.')
        proof['iteration_task'] = task['id']
        save()
        print('Started real native execution beyond 24 iterations.', flush=True)
        return
    proof = json.loads(PROOF.read_text())
    if mode == 'coordinate':
        detail = call('GET', '/api/v1/tasks/' + proof['iteration_task'])
        assert detail['status'] == 'completed', (detail['status'], detail['error'])
        history = call('GET', '/api/v1/conversations/' + proof.get('iteration_conversation_id',proof['conversation_id']) + '/runtime-history/researcher')['data']
        calls = [c for m in history for c in (m.get('tool_calls') or []) if c['function']['name'] == 'terminal' and 'WORK_ITERATION_' in str(c['function']['arguments'])]
        assert len(calls) == 30, len(calls)
        turns=sum(any(c['function']['name']=='terminal' and 'WORK_ITERATION_' in str(c['function']['arguments']) for c in (m.get('tool_calls') or [])) for m in history)
        assert turns>=30, turns
        proof['native_model_turns']=turns
        assert any(c['function']['name'] == 'hermbot_publish_file' for m in history for c in (m.get('tool_calls') or []))
        proof['native_terminal_calls'] = len(calls)
        save()
        proof['iteration_conversation_id']=proof.get('iteration_conversation_id',proof['conversation_id'])
        group=call('POST','/api/v1/groups',{'title':'Work coordination acceptance '+str(uuid4())[:8],'bot_ids':['researcher','writer','reviewer']})
        proof['conversation_id']=group['id']
        proof['tasks']=[proof['iteration_task']]
        proof['fixture_archived']=False
        save()
        task = submit('reviewer', 'Create a separate local report, independent of prior acceptance work. Coordinate it with Scout and Quill. Ask Scout to run a native terminal command that waits 45 seconds and then publishes evidence containing COORDINATION_EVIDENCE_OK. Ask Quill to read that published evidence and produce a final text report, after Scout finishes. Return the published final report. Do not use connected apps or request review. Assign the required results using their current descriptive keys and preserve the shared work state.', manager=True)
        proof['coordination_task'] = task['id']
        save()
        until(lambda state: any(t['id']==task['id'] and t['status']=='waiting_children' for t in state['tasks']))
        update = submit('writer', 'For the existing local report Sage is coordinating, include the exact line OWNER_REVISION_APPLIED_OK in the final report. Keep the evidence task and existing assignment; this is a revision, not a new report request.')
        proof['revision_task'] = update['id']
        save()
        print('Recorded an owner correction through a different teammate while coordination waits.', flush=True)
        return
    if mode == 'verify':
        until(lambda state: all(any(t['id']==identity and t['status'] in TERMINAL for t in state['tasks']) for identity in proof['tasks']))
        state = call('GET', '/api/v1/state')
        items = [w for w in state['work_items'] if w['conversation_id']==proof['conversation_id']]
        assert len(items) == 1, [(w['title'], w['status']) for w in items]
        coordinated = next(w for w in items if any(t['id']==proof['coordination_task'] for t in w['executions']))
        assert coordinated['revision'] >= 2
        assert coordinated['status'] == 'completed', coordinated['status']
        outputs = []
        for artifact in coordinated['outputs']:
            response = client.get('/api/v1/artifacts/' + artifact['id'])
            response.raise_for_status()
            outputs.append(response.text)
        assert any('OWNER_REVISION_APPLIED_OK' in output for output in outputs)
        assert any('COORDINATION_EVIDENCE_OK' in output for output in outputs)
        scout_history = call('GET', '/api/v1/conversations/' + proof['conversation_id'] + '/runtime-history/researcher')['data']
        published_calls = {c['id'] for m in scout_history for c in (m.get('tool_calls') or []) if c['function']['name'] in ('hermbot_publish', 'hermbot_publish_file')}
        evidence_ids = []
        for message in scout_history:
            if message.get('role') == 'tool' and message.get('tool_call_id') in published_calls:
                receipt = json.loads(message['content'])
                if receipt.get('artifact_id'):
                    evidence_ids.append(receipt['artifact_id'])
        assert evidence_ids, 'Scout must publish a real artifact, not only mention a private file'
        writer_history = call('GET', '/api/v1/conversations/' + proof['conversation_id'] + '/runtime-history/writer')['data']
        reads = [str(c['function']['arguments']) for m in writer_history for c in (m.get('tool_calls') or []) if c['function']['name'] in ('hermbot_read_artifact', 'read_file')]
        assert any(identity in read for identity in evidence_ids for read in reads), 'Quill must read the actual shared evidence artifact'
        proof.update(complete=True, cross_bot_owner_revision=True, shared_artifact_transfer=True,
                     coordination_revision=coordinated['revision'], work_items=len(items)+1)
        save()
        call('DELETE', '/api/v1/groups/' + proof['conversation_id'])
        proof['fixture_archived'] = True
        save()
        print('Real native execution, coordination, cross-bot revision and artifact delivery passed; fixture archived.', flush=True)
    elif mode == 'cleanup':
        state = call('GET', '/api/v1/state')
        for task in state['tasks']:
            if task['conversation_id']==proof['conversation_id'] and task['status'] not in TERMINAL:
                call('POST', '/api/v1/tasks/' + task['id'] + '/stop')
        until(lambda state: not any(t['conversation_id']==proof['conversation_id'] and t['status'] not in TERMINAL for t in state['tasks']))
        call('DELETE', '/api/v1/groups/' + proof['conversation_id'])
        proof['fixture_archived'] = True
        save()


if __name__ == '__main__':
    main()
