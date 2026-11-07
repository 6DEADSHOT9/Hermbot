"""Real model/native execution beyond five minutes, with an archived test conversation."""
import json
from pathlib import Path
import re
import time
from uuid import uuid4

from .protocol_acceptance import client, state


def verify(task_id, *, restarted=False):
    response = client.get('/api/v1/tasks/' + task_id)
    response.raise_for_status()
    detail = response.json()
    assert detail['status'] == 'completed'
    assert detail['attempts'] == len(detail['executions']) == 1
    assert detail['execution_budget']['used_seconds'] > 300
    response = client.get('/api/v1/conversations/' + detail['conversation_id'] + '/runtime-history/writer')
    response.raise_for_status()
    messages = response.json()['data']
    calls = [c for m in messages for c in (m.get('tool_calls') or []) if c['function']['name'] == 'terminal']
    assert len(calls) == 1, 'The native session must contain exactly one terminal invocation'
    assert 'sleep(320)' in str(calls[0]['function']['arguments'])
    receipt = next(m for m in messages if m.get('role') == 'tool' and m.get('tool_call_id') == calls[0]['id'])
    result = json.loads(receipt['content'])
    assert result['exit_code'] == 0 and not result.get('error')
    elapsed = float(re.search(r'CONTINUOUS_EXECUTION_OK seconds=([0-9.]+)', result['output']).group(1))
    assert elapsed >= 315
    # The event transport may lose a completion on reconnect. Durable native history
    # and the application artifact receipt remain authoritative; never rerun to verify.
    from backend import db, outcomes
    db.pool.open(wait=True)
    try:
        with db.connection() as conn:
            values = outcomes.rows(conn, detail['work_item_id'])
            assert len(values) == 1 and values[0]['status'] == 'fulfilled' and values[0]['evidence_strength'] == 'artifact_exists'
            assert not conn.execute('SELECT 1 FROM connector_operations WHERE task_id=%s', (task_id,)).fetchone()
    finally:
        db.pool.close()
    assert detail['children'] == []
    assert not any(e['kind'] == 'task.stop_requested' for e in detail['events'])
    proof = {'complete': True, 'live_model': True, 'local_native_command_seconds': elapsed, 'task_id': task_id,
             'attempt_id': detail['executions'][0]['id'], 'runtime_run_id': detail['executions'][0]['runtime_run_id'], 'attempts': 1,
             'active_seconds': detail['execution_budget']['used_seconds'], 'completed_required_results': 1, 'harness_modified': False,
             'worker_restart_verified': restarted, 'evidence_source': 'native_session_tool_receipt_and_published_artifact', 'external_changes': False}
    Path('/app/test-results/continuous-execution-live.json').write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof), flush=True)


def main():
    before = state()
    assert before['provider_configured'] and not before['protocol_fixture']
    assert not any(t['bot_id'] == 'writer' and t['status'] not in ('completed', 'failed', 'cancelled', 'interrupted') for t in before['tasks']), 'Quill must be idle for this local proof'
    response = client.post('/api/v1/groups', json={'title': 'Execution check · continuous local command', 'bot_ids': ['writer', 'researcher']})
    response.raise_for_status()
    group = response.json()['id']
    task_id = None
    try:
        command = "python -u -c 'import time; start=time.monotonic(); time.sleep(320); print(\"CONTINUOUS_EXECUTION_OK seconds=\"+str(round(time.monotonic()-start, 2)))'"
        prompt = f"""Authorized local continuous execution acceptance check. Use only local tools;
do not use browser, connected applications, delegation, owner questions or review.
Prepare terminal and hermbot_publish with hermbot_prepare, no required skills, and
register one required artifact outcome: key proof, title Continuous execution proof, kind artifact.
Execute this exact command once with the native terminal tool: {command}
Keep it in the foreground (background false), with timeout at least 400 seconds.
Wait for its actual output; do not finish or replace the execution while it sleeps.
Then publish continuous-execution-proof.txt containing the actual output, using
hermbot_publish. Call hermbot_finish with the artifact outcome ID/revision and
artifact ID as evidence. Do not fabricate a duration or claim success before the command finishes."""
        response = client.post('/api/v1/tasks', json={'bot_id': 'writer', 'conversation_id': group, 'prompt': prompt}, headers={'Idempotency-Key': str(uuid4())})
        response.raise_for_status()
        task_id = response.json()['id']
        print(json.dumps({'task_id': task_id, 'status': 'submitted', 'local_command_seconds': 320}), flush=True)
        deadline = time.monotonic() + 600
        last_print = 0
        while time.monotonic() < deadline:
            response = client.get('/api/v1/tasks/' + task_id)
            response.raise_for_status()
            detail = response.json()
            if time.monotonic() - last_print >= 30:
                print(json.dumps({'status': detail['status'], 'attempts': detail['attempts'], 'active_seconds': detail['execution_budget']['used_seconds']}), flush=True)
                last_print = time.monotonic()
            if detail['status'] == 'completed':
                break
            assert detail['status'] not in ('failed', 'cancelled', 'uncertain', 'interrupted', 'paused_budget', 'waiting_input', 'waiting_approval'), 'Local proof did not remain in continuous execution: ' + detail['status']
            time.sleep(2)
        else:
            raise AssertionError('Local continuous execution proof did not finish within ten minutes')
        verify(task_id)
    finally:
        if task_id:
            response = client.get('/api/v1/tasks/' + task_id)
            if response.status_code == 200 and response.json()['status'] not in ('completed', 'failed', 'cancelled', 'interrupted'):
                client.post('/api/v1/tasks/' + task_id + '/stop').raise_for_status()
                end = time.monotonic() + 90
                while time.monotonic() < end:
                    status = client.get('/api/v1/tasks/' + task_id).json()['status']
                    if status in ('completed', 'failed', 'cancelled', 'interrupted'):
                        break
                    time.sleep(2)
        client.delete('/api/v1/groups/' + group).raise_for_status()
        print('Temporary execution-check group removed from the workspace.', flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-existing')
    parser.add_argument('--worker-restart-verified', action='store_true')
    args = parser.parse_args()
    if args.verify_existing:
        verify(args.verify_existing, restarted=args.worker_restart_verified)
    else:
        main()
