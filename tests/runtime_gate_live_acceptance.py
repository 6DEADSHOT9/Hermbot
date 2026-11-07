"""Real-model parallel native tools and foreground takeover; local fixtures only."""
import base64
import json
from pathlib import Path
import shlex
import sys
import time
from uuid import uuid4

from fastapi import HTTPException
import httpx

from backend import db, native
from backend.settings import settings
from tests.native_tools_acceptance import native_result

PROOF = Path('/app/test-results/tool-gate-live.json')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'uncertain'}


def main():
    mode = sys.argv[1]
    db.pool.open(wait=True)
    try:
        with httpx.Client(base_url='http://api:8000', headers={'Authorization': 'Bearer ' + settings.owner_token}, timeout=85) as client:
            def call(method, path, body=None, key=None):
                response = client.request(method, path, json=body, headers={'Idempotency-Key': key or str(uuid4())} if method in ('POST', 'PUT') else {})
                response.raise_for_status()
                return response.json()
            def save():
                PROOF.write_text(json.dumps(proof, indent=2) + '\n')
            def until(predicate, seconds=480):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    result = predicate()
                    if result:
                        return result
                    time.sleep(1)
                raise AssertionError('Acceptance state has not arrived; retain and inspect the recorded run before recovery')
            def settled(task_id):
                task = call('GET', '/api/v1/tasks/' + task_id)
                return task if task['status'] in TERMINAL else None
            if mode == 'start':
                assert not PROOF.exists(), 'Resume the recorded fixture; never duplicate submissions'
                state = call('GET', '/api/v1/state')
                assert not state['protocol_fixture']
                assert all(t['status'] in TERMINAL for t in state['tasks'])
                assert all(b['readiness'].get('compatible') for b in state['bots'] if b['id'] in ('researcher', 'writer', 'reviewer'))
                identity = uuid4().hex[:8]
                proof = {'complete': False, 'real_model': True, 'external_application_changes': False, 'identity': identity, 'prefix': '/workspace/tool-gate-' + identity}
                save()
                group = call('POST', '/api/v1/groups', {'title': 'Tool gate acceptance ' + identity, 'bot_ids': ['researcher', 'writer', 'reviewer']})
                proof['conversation_id'] = group['id']
                save()
                setup = "from pathlib import Path; p=Path(" + repr(proof['prefix']) + "); p.mkdir(); [(p/f'file-{i}.txt').write_text(f'TOOL_GATE_FILE_{i}_CONTENT\\n') for i in range(6)]"
                paths = ', '.join(proof['prefix'] + f'/file-{i}.txt' for i in range(6))
                prompt = 'Local native concurrency acceptance. Run this exact setup in one terminal call: ' + 'python -c ' + shlex.quote(setup) + '. Then in ONE assistant turn issue six read_file calls, one for each path: ' + paths + '. Include two search_files calls in that same batch, searching TOOL_GATE_FILE and CONTENT in the fixture directory. Use the native read/search tools, not terminal replacements. Verify the six file markers and finish with TOOL_GATE_PARALLEL_OK and fulfilled result status. Temporary fixture files are inputs, not requested published outputs. Do not delegate, publish artifacts, use connected apps or request reviews.'
                proof['parallel_key'] = str(uuid4())
                save()
                task = call('POST', '/api/v1/messages', {'bot_id': 'writer', 'conversation_id': group['id'], 'prompt': prompt}, proof['parallel_key'])
                proof['parallel_task'] = task['id']
                save()
                print('Started real native parallel-tool check.', flush=True)
                return
            proof = json.loads(PROOF.read_text())
            if mode == 'recover-takeover':
                previous = call('GET', '/api/v1/tasks/' + proof['takeover_task'])
                assert previous['status'] in TERMINAL and all(e['status'] in TERMINAL for e in previous['executions'])
                history = proof.setdefault('takeover_history', [])
                assert len(history) < 2, 'Inspect runner timing before another recovery'
                history.append({'task_id': proof.pop('takeover_task'), 'status': previous['status'],
                                'reason': 'Runner setup missed the foreground window; the local task settled before control could be measured.'})
                proof['foreground_prefix'] = proof['prefix'] + '/takeover-' + uuid4().hex[:8]
                save()
                mode = 'takeover'
            if mode == 'takeover':
                task = until(lambda: settled(proof['parallel_task']))
                assert task['status'] == 'completed' and 'TOOL_GATE_PARALLEL_OK' in (task['output'] or ''), (task['status'], task['error'])
                history = call('GET', '/api/v1/conversations/' + proof['conversation_id'] + '/runtime-history/writer')['data']
                receipts = {m['tool_call_id']: m for m in history if m.get('role') == 'tool' and m.get('tool_call_id')}
                batches = [[c for c in m.get('tool_calls') or [] if c['function']['name'] in ('read_file', 'search_files')] for m in history]
                batch = next(calls for calls in batches if sum(c['function']['name'] == 'read_file' for c in calls) >= 6 and sum(c['function']['name'] == 'search_files' for c in calls) >= 2)
                for call_info in batch:
                    value = native_result(receipts[call_info['id']]['content'])
                    assert not value.get('error'), value
                for index in range(6):
                    assert any('TOOL_GATE_FILE_' + str(index) + '_CONTENT' in receipts[c['id']]['content'] for c in batch if c['function']['name'] == 'read_file')
                completions = [e for e in task['events'] if e['kind'] == 'tool.completed' and e['data'].get('tool') in ('read_file', 'search_files')]
                assert len(completions) >= 8
                durations = [float(e['data']['duration']) for e in completions]
                assert max(durations) < 10, durations
                proof['parallel_verified'] = {'batch_size': len(batch), 'durations_seconds': durations}
                save()
                if not proof.get('takeover_task'):
                    prefix = proof.get('foreground_prefix', proof['prefix'])
                    code = "from pathlib import Path; import time; p=Path(" + repr(prefix) + "); p.mkdir(parents=True,exist_ok=True); c=p/'count'; c.write_text(str(int(c.read_text())+1) if c.exists() else '1'); (p/'started').write_text('started'); time.sleep(12); print('TOOL_GATE_FOREGROUND_DONE')"
                    prompt = 'Local computer takeover acceptance. Execute exactly one foreground terminal call with this command and timeout at least 20 seconds: python -c ' + shlex.quote(code) + '. Do not background it or issue other tools in that batch. If the owner takes control, end your turn and wait for handback. On resume, preserve the completed terminal result and NEVER rerun this command. Finish with TOOL_GATE_TAKEOVER_OK and fulfilled result status. Files are temporary fixture inputs, not published outputs. Do not delegate, publish artifacts, use connected apps or request reviews.'
                    proof['takeover_key'] = str(uuid4())
                    save()
                    task = call('POST', '/api/v1/messages', {'bot_id': 'researcher', 'conversation_id': proof['conversation_id'], 'prompt': prompt}, proof['takeover_key'])
                    proof['takeover_task'] = task['id']
                    save()
                def foreground_started():
                    try:
                        return native.request('researcher', 'POST', '/workspace/export', json={'path': proof.get('foreground_prefix', proof['prefix']) + '/started'})
                    except HTTPException as error:
                        if error.status_code in (404, 422):
                            return None
                        raise
                until(foreground_started)
                start = time.monotonic()
                state = call('POST', '/api/v1/bots/researcher/computer/control', {'action': 'takeover'})
                delay = time.monotonic() - start
                proof['takeover_wait_seconds'] = round(delay, 3)
                save()
                try:
                    assert delay > 3 and state['input_ready'] and state['mine'], delay
                finally:
                    if state.get('mine'):
                        call('POST', '/api/v1/bots/researcher/computer/control', {'action': 'handback'})
                task = until(lambda: settled(proof['takeover_task']))
                assert task['status'] == 'completed' and 'TOOL_GATE_TAKEOVER_OK' in (task['output'] or ''), (task['status'], task['error'])
                counter = native.request('researcher', 'POST', '/workspace/export', json={'path': proof.get('foreground_prefix', proof['prefix']) + '/count'})
                assert base64.b64decode(counter['content_base64']).decode() == '1'
                proof['foreground_effect_count'] = 1
                proof['handback_resumed'] = True
                proof['complete'] = True
                save()
                print('Real parallel calls, foreground takeover and handback passed.', flush=True)
            if mode == 'cleanup':
                assert proof.get('complete'), 'Verify the recorded acceptance before cleanup'
                for key in ('parallel_task', 'takeover_task'):
                    task = call('GET', '/api/v1/tasks/' + proof[key])
                    assert task['status'] in TERMINAL and all(e['status'] in TERMINAL for e in task['executions'])
                response = client.delete('/api/v1/groups/' + proof['conversation_id'])
                response.raise_for_status()
                proof['fixture_archived'] = True
                save()
                print('Archived only the completed tool-gate acceptance group.', flush=True)
    finally:
        db.pool.close()


if __name__ == '__main__':
    main()
