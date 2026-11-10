"""Real-model takeover pause: while the owner is in control, a bot's own background work and its active
time stop, and the task finishes after handback. Local fixtures only; nothing leaves the machine.

Run on the control network with Scout's workspace volume mounted read-only, so the counter can be read
without calling the paused bot. The argument is the reply wait to test, 0 or 15 seconds:

  docker run --rm --network hermbot_control --env-file .env -v "$PWD:/app" -w /app \
    -v hermbot_researcher-workspace:/bot-workspace:ro hermbot-tools:phase1 \
    python -m tests.takeover_pause_live_acceptance 0
"""
import json
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

import httpx

from backend.settings import settings

WORKSPACE = Path('/bot-workspace')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'uncertain'}
PAUSE_SECONDS = 20


def main():
    wait = int(sys.argv[1])
    identity = uuid4().hex[:8]
    proof = {'reply_wait_seconds': wait, 'identity': identity, 'real_model': True}
    out = Path('/app/test-results/takeover-pause-live-' + identity + '.json')
    out.parent.mkdir(exist_ok=True)
    with httpx.Client(base_url='http://api:8000', headers={'Authorization': 'Bearer ' + settings.owner_token}, timeout=150) as client:
        def call(method, path, body=None):
            response = client.request(method, path, json=body, headers={'Idempotency-Key': str(uuid4())} if method in ('POST', 'PUT') else {})
            response.raise_for_status()
            return response.json()

        def until(predicate, seconds=300):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                result = predicate()
                if result:
                    return result
                time.sleep(1)
            raise AssertionError('Acceptance state has not arrived')

        def counter():
            path = WORKSPACE / ('pause-acceptance-' + identity) / 'counter'
            try:
                return int(path.read_text().strip())
            except (FileNotFoundError, ValueError):
                return 0

        def executions(task_id):
            task = call('GET', '/api/v1/tasks/' + task_id)
            return task, sum(float(row['active_seconds']) for row in task['executions'])

        def save():
            out.write_text(json.dumps(proof, indent=2) + '\n')

        state = call('GET', '/api/v1/state')
        scout = next(bot for bot in state['bots'] if bot['id'] == 'researcher')
        assert scout['readiness'].get('compatible'), 'Scout is not ready'
        assert not [t for t in state['tasks'] if t['bot_id'] == 'researcher' and t['status'] not in TERMINAL], 'Scout is busy'
        call('PUT', '/api/v1/settings/advanced', {'takeover_unpausable': 'ask', 'takeover_reply_wait_seconds': wait})
        group = call('POST', '/api/v1/groups', {'title': 'Takeover pause acceptance ' + identity, 'bot_ids': ['researcher', 'writer']})
        folder = '/workspace/pause-acceptance-' + identity
        loop = f"i=0; while [ $i -lt 400 ]; do i=$((i+1)); echo $i > {folder}/counter; sleep 1; done"
        prompt = (
            'Takeover pause acceptance: a local test with no external effects. Use the terminal tool only, one command per call. '
            f"Step 1, run exactly: mkdir -p {folder} && setsid nohup sh -c '{loop}' >/dev/null 2>&1 < /dev/null & "
            'Step 2, run exactly: sleep 20 '
            f'Step 3, run exactly: cat {folder}/counter '
            'Then reply with PAUSE_ACCEPTANCE_OK and the number step 3 printed. If the owner takes control of your computer, end your turn '
            'and wait for handback; afterwards continue from where you stopped and never repeat step 1 or step 2. '
            'Do not delegate, publish artifacts, use connected apps or request reviews.')
        task = call('POST', '/api/v1/messages', {'bot_id': 'researcher', 'conversation_id': group['id'], 'prompt': prompt})
        proof.update(conversation_id=group['id'], task_id=task['id'])
        save()
        until(lambda: counter() >= 3)  # The counter runs in the background and the bot is in its 20-second sleep.

        phases, done = [], threading.Event()

        def watch():  # What the pane would show while the takeover runs.
            while not done.is_set():
                try:
                    phase = (call('GET', '/api/v1/bots/researcher/computer').get('takeover') or {}).get('phase')
                    if phase and (not phases or phases[-1] != phase):
                        phases.append(phase)
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
        watcher = threading.Thread(target=watch)
        watcher.start()
        started = time.monotonic()
        try:
            status = call('POST', '/api/v1/bots/researcher/computer/control', {'action': 'takeover'})
        finally:
            done.set()
            watcher.join()
        proof['takeover_seconds'] = round(time.monotonic() - started, 1)
        proof['phases_seen'] = phases
        assert status['mine'] and status['private_session'] and status['takeover'] == {'phase': 'paused'}, status
        if wait:
            assert 'finishing' in phases, phases
        paused_task, paused_active = executions(task['id'])
        proof['task_status_while_paused'] = paused_task['status']
        frozen_from = counter()
        time.sleep(PAUSE_SECONDS)
        frozen_to = counter()
        held_task, held_active = executions(task['id'])
        proof['counter_while_paused'] = [frozen_from, frozen_to]
        proof['active_seconds_while_paused'] = [round(paused_active, 3), round(held_active, 3)]
        proof['budget_used_while_paused'] = [paused_task['execution_budget']['used_seconds'], held_task['execution_budget']['used_seconds']]
        assert frozen_to == frozen_from, 'The background counter kept running while paused'
        assert held_active == paused_active and proof['budget_used_while_paused'][0] == proof['budget_used_while_paused'][1]
        proof['status_while_paused'] = call('GET', '/api/v1/bots/researcher/computer')['takeover']
        proof['thumbnail_while_paused'] = call('GET', '/api/v1/bots/researcher/computer/thumbnail')
        save()

        handed_back = time.monotonic()
        back = call('POST', '/api/v1/bots/researcher/computer/control', {'action': 'handback'})
        proof['handback_seconds'] = round(time.monotonic() - handed_back, 1)
        assert back['lease']['holder'] == 'agent' and back['takeover'] is None
        time.sleep(4)
        proof['counter_after_handback'] = counter()
        assert proof['counter_after_handback'] > frozen_to, 'The counter did not resume'
        final = until(lambda: (lambda t: t if t['status'] in TERMINAL else None)(call('GET', '/api/v1/tasks/' + task['id'])))
        finished = time.monotonic()
        _, final_active = executions(task['id'])
        proof.update(task_status=final['status'], output=(final.get('output') or '')[-300:], attempts=len(final['executions']),
                     active_seconds_after_handback=round(final_active - held_active, 1), seconds_from_handback_to_finish=round(finished - handed_back, 1))
        save()
        assert final['status'] == 'completed' and 'PAUSE_ACCEPTANCE_OK' in (final.get('output') or ''), (final['status'], final.get('error'))
        assert final_active - held_active <= finished - handed_back + 2, 'The pause was charged as active time'
        call('DELETE', '/api/v1/groups/' + group['id'])
        proof['complete'] = True
        save()
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
