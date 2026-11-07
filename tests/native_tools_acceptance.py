"""Real-model native tools with existing connections, without external account changes."""
import argparse
from datetime import datetime
import fcntl
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

from backend.settings import settings

PROOF = Path('/app/test-results/native-tools-acceptance.json')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'uncertain'}


def native_result(content):
    # Hermes wraps external browser data in its native untrusted-result marker.
    # Decode the JSON payload without discarding or relaxing error assertions.
    if content.startswith('<untrusted_tool_result '):
        start = content.index('\n{') + 1
        value, end = json.JSONDecoder().raw_decode(content[start:])
        assert content[start + end:].strip() == '</untrusted_tool_result>'
        return value
    return json.loads(content)


def main():
    with PROOF.with_suffix('.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('An acceptance runner already owns this manifest; wait for it to settle.')
        run()


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['start', 'continue', 'retry-helper', 'retry-native', 'cleanup', 'verify'])
    mode = parser.parse_args().mode
    client = httpx.Client(base_url='http://api:8000',
                         headers={'Authorization': 'Bearer ' + settings.owner_token}, timeout=90)

    def call(method, path, body=None, key=None):
        response = client.request(method, path, json=body,
                                  headers={'Idempotency-Key': key or str(uuid4())} if method in ('POST', 'PUT', 'DELETE') else {})
        response.raise_for_status()
        return response.json()

    def save():
        PROOF.write_text(json.dumps(proof, indent=2))

    if mode == 'start':
        assert not PROOF.exists(), 'Continue the recorded check; never resubmit a lost task.'
        state = call('GET', '/api/v1/state')
        assert state['provider_configured'] and not state['protocol_fixture']
        assert all(t['status'] in TERMINAL for t in state['tasks'])
        assert all(b['readiness'].get('compatible') for b in state['bots'])
        connections = call('GET', '/api/v1/connections')['connections']
        assert any(c['status'] == 'connected' and 'researcher' in c['bot_ids'] for c in connections)
        assert any(c['status'] == 'connected' and 'writer' in c['bot_ids'] for c in connections)
        proof = {'complete': False, 'real_native_model': True, 'external_changes': False,
                 'identity': uuid4().hex[:8], 'tasks': [],
                 'original_groups': [c['id'] for c in state['conversations'] if c['kind'] == 'group'],
                 'original_bots': {b['id']: {key: b[key] for key in ('enabled_tools', 'enabled_skills', 'memory_mb', 'cpus')} for b in state['bots']},
                 'original_connections': [{key: c[key] for key in ('id', 'status', 'revision', 'bot_ids', 'actions', 'resources')} for c in connections]}
        save()
        group = call('POST', '/api/v1/groups', {'title': 'Native tools acceptance ' + proof['identity'],
                                              'bot_ids': ['researcher', 'writer', 'reviewer']})
        proof['conversation_id'] = group['id']
        save()
    else:
        proof = json.loads(PROOF.read_text())

    if mode == 'retry-helper':
        case = next(c for c in proof['tasks'] if c['case'] == 'connected-helper')
        old = call('GET', '/api/v1/tasks/' + case['task_id'])
        assert old['status'] in {'failed', 'cancelled', 'interrupted'}
        assert all(e['status'] in TERMINAL for e in old['executions'])
        assert not case.get('previous_task_ids'), 'A second recovery needs explicit inspection of its recorded failure.'
        case.setdefault('retry_key', str(uuid4()))
        case['retry_prompt'] = old['input'] + '\nThis is a fresh local verification after the staging fix. Create one new helper in this task; do not reuse earlier assignment or artifact IDs.'
        proof.setdefault('review_failures', []).append({'task_id': old['id'], 'status': old['status'],
                                     'execution_statuses': [e['status'] for e in old['executions']],
                                     'cause': 'Readonly code directory and Docker archive admission prevented attachment staging; the execution budget expired before native continuation.'})
        save()
        task = call('POST', '/api/v1/tasks/' + old['id'] + '/retry', {'prompt': case['retry_prompt']}, case['retry_key'])
        case.update(previous_task_ids=[old['id']], task_id=task['id'], status=task['status'])
        save()
        mode = 'continue'

    if mode == 'retry-native':
        case = next(c for c in proof['tasks'] if c['case'] == 'connected-native-tools')
        old = call('GET', '/api/v1/tasks/' + case['task_id'])
        assert old['status'] in TERMINAL and all(e['status'] in TERMINAL for e in old['executions'])
        assert not case.get('previous_task_ids'), 'Inspect the recorded failure before another recovery.'
        case.setdefault('retry_key', str(uuid4()))
        case['retry_prompt'] = old['input'] + '\nThis is a fresh local verification after runtime configuration fixes. Use the actual results of native tools; report a blocker honestly rather than claiming success.'
        proof.setdefault('review_failures', []).append({'task_id': old['id'], 'status': old['status'],
            'cause': 'The model finished honestly with a partial report: inherited write roots excluded /workspace, and headed browser lacked an automatically started desktop. Receipt verification rejected the result.'})
        save()
        task = call('POST', '/api/v1/tasks', {'bot_id': case['bot_id'], 'conversation_id': proof['conversation_id'],
                                            'prompt': case['retry_prompt']}, case['retry_key'])
        case.update(previous_task_ids=[old['id']], task_id=task['id'], status=task['status'])
        save()
        mode = 'continue'

    if mode == 'cleanup':
        for case in proof['tasks']:
            task = call('GET', '/api/v1/tasks/' + case['task_id'])
            assert task['status'] in TERMINAL and all(e['status'] not in ('running', 'submitting', 'external') for e in task['executions'])
        call('DELETE', '/api/v1/groups/' + proof['conversation_id'])
        proof['fixture_archived'] = True
        save()
        print('Native tools acceptance group archived; existing groups, connections and bot settings preserved.', flush=True)
        return

    if mode != 'verify':
        identity = proof['identity']
        cases = [
            ('connected-native-tools', 'researcher',
             f'Verify your enabled native tools alongside your existing app connections. Prepare normally. '
             f'Use native write_file to create /workspace/native-tools-{identity}.txt containing initial. '
             'Use native patch to replace initial with updated. Use native terminal to read that file and '
             'run Python computing 7*8; verify updated and 56 in actual output. Use native browser_navigate '
             'to visit https://example.com and verify Example Domain from its actual result. '
             'Publish a concise verification report using hermbot_publish and finish. '
             'Do not use connected-app actions, log into websites, install packages or change other files.', False),
            ('connected-helper', 'reviewer',
             'Verify collaboration preserves native terminal access. Prepare normally, run native terminal '
             'to compute 11+13, then explicitly delegate to Quill (writer) to prepare and verify 11+13 '
             'using native terminal in its own runtime, publish the actual result and finish. End your turn '
             'while waiting. When resumed, prepare again and use native terminal to verify 24 once more; '
             'read the child report and publish a combined report. Do not use connected-app actions, '
             'change bot settings or create reviews.', True),
        ]
        for name, bot, prompt, managed in cases:
            case = next((c for c in proof['tasks'] if c['case'] == name), None)
            if not case:
                case = {'case': name, 'bot_id': bot, 'submission_key': str(uuid4())}
                proof['tasks'].append(case)
                save()
                body = {'bot_id': bot, 'conversation_id': proof['conversation_id'], 'prompt': prompt}
                if managed:
                    body['managed_bot_ids'] = ['researcher', 'writer']
                task = call('POST', '/api/v1/tasks', body, case['submission_key'])
                case['task_id'] = task['id']
                save()
            assert case.get('task_id'), 'Submission receipt lost; look up its original key instead of resubmitting.'
            # A native attempt may time out and the authorized manager may recover
            # a local helper. Continue observing the same recorded root; never resubmit.
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                task = call('GET', '/api/v1/tasks/' + case['task_id'])
                if task['status'] in TERMINAL:
                    case['status'] = task['status']
                    save()
                    print(name + ': ' + task['status'], flush=True)
                    break
                time.sleep(1)
            assert task['status'] == 'completed', 'Inspect the saved task and receipts; no substitute success is accepted.'

    history = {bot: call('GET', '/api/v1/conversations/' + proof['conversation_id'] + '/runtime-history/' + bot)['data']
               for bot in ('researcher', 'writer', 'reviewer')}
    for case in proof['tasks']:
        task = call('GET', '/api/v1/tasks/' + case['task_id'])
        assert task['status'] == 'completed' and all(e['status'] == 'completed' for e in task['executions'])
        assert task['execution_plan']['ready']
    # A shared conversation retains failed attempts. Verify only messages after
    # the current case was submitted, so historical calls cannot prove recovery.
    for bot in history:
        root = next(c for c in proof['tasks'] if c['case'] == ('connected-native-tools' if bot == 'researcher' else 'connected-helper'))
        created = datetime.fromisoformat(call('GET', '/api/v1/tasks/' + root['task_id'])['created_at']).timestamp()
        history[bot] = [m for m in history[bot] if float(m['timestamp']) >= created]
    researcher_calls = [c for m in history['researcher'] for c in m.get('tool_calls') or []]
    receipts = {m['tool_call_id']: m for m in history['researcher'] if m.get('role') == 'tool' and m.get('tool_call_id')}
    verified = []
    for name in ('write_file', 'patch', 'terminal', 'browser_navigate'):
        calls = [c for c in researcher_calls if c['function']['name'] == name]
        assert calls and all(c['id'] in receipts for c in calls)
        values = [native_result(receipts[c['id']]['content']) for c in calls]
        assert all(not v.get('error') for v in values), f'{name} returned a native error'
        if name == 'write_file':
            assert any(v.get('bytes_written', 0) > 0 for v in values)
        if name == 'patch':
            assert any(v.get('success') is True for v in values)
        if name == 'terminal':
            outputs = '\n'.join(v.get('output', '') for v in values if v.get('exit_code') == 0)
            assert 'updated' in outputs and '56' in outputs
        if name == 'browser_navigate':
            assert any(v.get('success') is True and v.get('title') == 'Example Domain' for v in values)
        verified.append(name)
    helper = next(c for c in proof['tasks'] if c['case'] == 'connected-helper')
    task = call('GET', '/api/v1/tasks/' + helper['task_id'])
    assert task['handoffs'] == 1
    for bot in ('writer', 'reviewer'):
        calls = [c for m in history[bot] for c in m.get('tool_calls') or [] if c['function']['name'] == 'terminal']
        receipts = {m['tool_call_id']: m for m in history[bot] if m.get('role') == 'tool' and m.get('tool_call_id')}
        assert calls and any('24' in str(receipts[c['id']]['content']) for c in calls if c['id'] in receipts)
    parent_calls = [c for m in history['reviewer'] for c in m.get('tool_calls') or []]
    delegate_index = next(i for i, c in enumerate(parent_calls) if c['function']['name'] == 'hermbot_delegate')
    assert any(c['function']['name'] == 'terminal' for c in parent_calls[:delegate_index])
    assert any(c['function']['name'] == 'terminal' for c in parent_calls[delegate_index + 1:])
    assert len(task['executions']) >= 2
    assert not any(c['function']['name'] in ('hermbot_connector', 'hermbot_review')
                   for messages in history.values() for m in messages for c in m.get('tool_calls') or [])
    current = call('GET', '/api/v1/state')
    assert proof['original_bots'] == {b['id']: {key: b[key] for key in ('enabled_tools', 'enabled_skills', 'memory_mb', 'cpus')} for b in current['bots']}
    current_connections = call('GET', '/api/v1/connections')['connections']
    assert proof['original_connections'] == [{key: c[key] for key in ('id', 'status', 'revision', 'bot_ids', 'actions', 'resources')} for c in current_connections]
    assert set(proof['original_groups']) <= {c['id'] for c in current['conversations']}
    proof.update(complete=True, verified_native_tools=verified, connected_helper_terminal_verified=True,
                 parent_terminal_after_handoff_verified=True, owner_configuration_preserved=True)
    save()
    print('Real native tools, public browsing and connected-helper continuation verified.', flush=True)


if __name__ == '__main__':
    main()
