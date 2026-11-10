"""Real-model Continue check: work paused by its budget and continued is resumed, not repeated.

Scout appends a marker line once, then sleeps. Mid-sleep the task's budget is used up, so it pauses;
the owner presses Continue, and the finished task must have written the marker exactly once. Local
fixtures only. Run on the control network with Scout's workspace mounted read-only:

  docker run --rm --network hermbot_control --env-file .env -v "$PWD:/app" -w /app \\
    -v hermbot_researcher-workspace:/bot-workspace:ro hermbot-tools:phase1 \\
    python -m tests.continue_guard_live_acceptance
"""
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

from backend import db
from backend.service import RESUME_RULE
from backend.settings import settings

WORKSPACE = Path('/bot-workspace')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'uncertain'}


def main():
    identity = uuid4().hex[:8]
    proof = {'identity': identity, 'real_model': True}
    out = Path('/app/test-results/continue-guard-live-' + identity + '.json')
    out.parent.mkdir(exist_ok=True)
    db.pool.open(wait=True)
    with httpx.Client(base_url='http://api:8000', headers={'Authorization': 'Bearer ' + settings.owner_token}, timeout=120) as client:
        def call(method, path, body=None, expect_ok=True):
            response = client.request(method, path, json=body, headers={'Idempotency-Key': str(uuid4())} if method in ('POST', 'PUT') else {})
            if expect_ok:
                response.raise_for_status()
            return response

        def task_state(task_id):
            return call('GET', '/api/v1/tasks/' + task_id).json()

        def until(predicate, seconds=420):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                result = predicate()
                if result:
                    return result
                time.sleep(2)
            raise AssertionError('Acceptance state has not arrived')

        def markers():
            path = WORKSPACE / ('continue-check-' + identity) / 'log.txt'
            try:
                return path.read_text().count('MARKER-' + identity)
            except FileNotFoundError:
                return 0

        def save():
            out.write_text(json.dumps(proof, indent=2, default=str) + '\n')

        state = call('GET', '/api/v1/state').json()
        # A message whose interpretation failed waits for the owner's Retry and never dispatches.
        assert not [t for t in state['tasks'] if t['bot_id'] == 'researcher' and t['status'] not in TERMINAL and t.get('intake_status') != 'failed'], 'Scout is busy'
        group = call('POST', '/api/v1/groups', {'title': 'Continue check ' + identity, 'bot_ids': ['researcher', 'writer']}).json()
        folder = '/workspace/continue-check-' + identity
        prompt = ('Continue check: a local test with no external effects. Use the terminal tool only, one command per call. '
                  f'Step 1, run exactly: mkdir -p {folder} && echo MARKER-{identity} >> {folder}/log.txt '
                  'Step 2, run exactly: sleep 60 '
                  f'Step 3, run exactly: cat {folder}/log.txt '
                  'Then reply with CONTINUE_CHECK_OK and how many marker lines step 3 printed. '
                  'Do not delegate, publish artifacts, use connected apps or request reviews.')
        task = call('POST', '/api/v1/messages', {'bot_id': 'researcher', 'conversation_id': group['id'], 'prompt': prompt}).json()
        proof.update(conversation_id=group['id'], task_id=task['id'])
        save()
        until(lambda: markers() >= 1)  # Step 1 is done; the bot is now in its sleep.
        with db.connection() as conn:  # Use up this task's budget, so it pauses mid-sleep.
            root = conn.execute('SELECT root_id FROM tasks WHERE id=%s', (task['id'],)).fetchone()['root_id']
            conn.execute('UPDATE tasks SET active_budget_seconds=1 WHERE id=%s', (root,))
            item = conn.execute('SELECT work_item_id FROM tasks WHERE id=%s', (task['id'],)).fetchone()['work_item_id']
            if item:
                conn.execute('UPDATE work_items SET active_budget_seconds=1 WHERE id=%s', (item,))
        paused = until(lambda: (lambda t: t if t['status'] == 'paused_budget' else None)(task_state(task['id'])))
        proof['markers_when_paused'] = markers()
        proof['paused_status'] = paused['status']
        save()
        continued = call('POST', f"/api/v1/tasks/{task['id']}/continue", expect_ok=False)
        if continued.status_code == 409 and 'Reconcile' in continued.text:
            # The pause cut off a run that had used a native tool: its external result is uncertain until the owner confirms it.
            with db.connection() as conn:
                uncertain = conn.execute("SELECT o.id,o.revision FROM work_outcomes o JOIN tasks t ON t.work_item_id=o.work_item_id "
                                         "WHERE t.root_id=%s AND o.status='uncertain' AND o.kind='native_effect'", (root,)).fetchall()
            for outcome in uncertain:
                call('POST', f"/api/v1/outcomes/{outcome['id']}/reconcile",
                     {'result': 'fulfilled', 'evidence': f'Marker present {markers()} time(s) in the log file', 'expected_revision': outcome['revision']})
            proof['reconciled_outcomes'] = len(uncertain)
            continued = call('POST', f"/api/v1/tasks/{task['id']}/continue", expect_ok=False)
        proof['continue_status'] = continued.status_code
        save()
        assert continued.status_code == 200, continued.text
        final = until(lambda: (lambda t: t if t['status'] in TERMINAL else None)(task_state(task['id'])))
        with db.connection() as conn:
            inputs = [row['input'] for row in conn.execute('SELECT input FROM attempts WHERE task_id=%s ORDER BY number', (task['id'],)).fetchall()]
        proof.update(task_status=final['status'], output=(final.get('output') or '')[-200:], attempts=len(inputs), markers_at_end=markers(),
                     resumed_run_starts_with_resume_rule=bool(inputs) and inputs[-1].startswith(RESUME_RULE),
                     resumed_run_has_continue_guard='never repeat a fulfilled or uncertain external effect' in (inputs[-1] if inputs else ''))
        save()
        assert final['status'] == 'completed', (final['status'], final.get('error'))
        assert proof['markers_at_end'] == 1, 'The marker was written again after Continue'
        assert proof['resumed_run_starts_with_resume_rule'] and proof['resumed_run_has_continue_guard']
        call('DELETE', '/api/v1/groups/' + group['id'])
        proof['complete'] = True
        save()
    print(json.dumps(proof, indent=2, default=str))


if __name__ == '__main__':
    main()
