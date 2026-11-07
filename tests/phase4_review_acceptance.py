"""Prepare real private-provider reviews; only the human owner approves external effects."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from backend import connector_catalog, db, service
from .protocol_acceptance import client, state, wait

CASES = {'gmail': ('google', 'gmail.send'), 'calendar': ('google', 'calendar.create'), 'drive': ('google', 'drive.create'), 'slack': ('slack', 'slack.post'), 'github': ('github', 'github.create_issue')}


def request(method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def recover_manifest(conn, manifest):
    task = conn.execute('SELECT id,bot_id,conversation_id,input FROM tasks WHERE request_key=%s', (manifest['submission_key'],)).fetchone()
    assert task, 'No task found for this submission key; inspect the workspace before creating more work'
    assert task['bot_id'] == manifest['bot_id'] and str(task['conversation_id']) == manifest['conversation_id'] and task['input'] == manifest['prompt'], 'The submission key does not identify this exact test'
    return {**manifest, 'task_id': str(task['id'])}


def plan(case, options, marker):
    provider, action = CASES[case]
    text = f'Hermbot Phase 4 acceptance {marker}. Owner-reviewed test content.'
    if case == 'gmail':
        arguments = {'to': options.recipient, 'subject': text, 'body': text}
    elif case == 'calendar':
        assert options.calendar_id, 'Choose a dedicated test calendar with --calendar-id'
        start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
        arguments = {'calendar_id': options.calendar_id, 'summary': text, 'start': start.isoformat(), 'end': (start + timedelta(minutes=15)).isoformat(), 'description': text, 'attendees': []}
    elif case == 'drive':
        assert options.folder_id, 'Choose a test folder with --folder-id'
        arguments = {'folder_id': options.folder_id, 'name': text, 'content': text, 'document': True}
    elif case == 'slack':
        assert options.channel, 'Choose a test channel ID with --channel'
        arguments = {'channel': options.channel, 'text': text}
    else:
        assert options.repo, 'Choose a test owner/repository with --repo'
        arguments = {'repo': options.repo, 'title': text, 'body': text}
    arguments, _, resource = connector_catalog.validate({'provider': provider}, action, arguments)
    return provider, action, arguments, resource


def ready_connection(data, bot, provider, action, resource):
    matches = [c for c in data['connections'] if c['provider'] == provider and c['status'] == 'connected' and bot in c['bot_ids'] and action in c['actions']]
    assert len(matches) == 1, f'Connect exactly one {provider} account with {action} for {bot}; no task submitted'
    connection = matches[0]
    allowed = connection['resources'].get(action)
    assert allowed is None or resource in allowed, 'The selected test resource is outside connection access; no task submitted'
    now = datetime.now(timezone.utc)
    assert not any(p['connection_id'] == connection['id'] and p['bot_id'] == bot and p['action'] == action and p['resource'] == resource and not p['revoked_at'] and datetime.fromisoformat(p['expires_at']) > now and p['used'] < p['max_uses'] for p in data['permissions']), 'Revoke the matching standing permission first: this acceptance must wait for an owner review'
    return connection


def verify_operation(task, operations, approvals, manifest):
    assert task['status'] == 'completed', 'Native continuation must finish before verification'
    assert task['bot_id'] == manifest['bot_id'] and task['conversation_id'] == manifest['conversation_id']
    assert len(task['executions']) >= 2 and all(a['status'] == 'completed' for a in task['executions']), 'Both initial proposal and receipt continuation must settle'
    assert operations and all(o['status'] in ('superseded', 'denied', 'succeeded') for o in operations), 'A failed, unfinished or unknown provider outcome does not count as acceptance'
    final = [o for o in operations if o['status'] != 'superseded']
    assert len(final) == 1, 'The test must have exactly one final external operation'
    op = final[0]
    assert op['connection_id'] == manifest['connection_id'] and op['action'] == manifest['action'] and op['bot_id'] == manifest['bot_id']
    approval = next(a for a in approvals if a['id'] == op['approval_id'])
    assert approval['status'] == 'decided' and approval['kind'] == 'connector'
    assert approval['payload_hash'] == op['payload_hash'] == service.digest(approval['payload']['action'])
    assert approval['payload']['action']['arguments'] == op['arguments']
    if op['status'] == 'denied':
        assert approval['decision'] == 'deny' and not op['result'] and not op['lease_until']
        assert 'PHASE4_REVIEW_DENIED' in task['output']
        return {'outcome': 'denied', 'external_effect': False, 'operation_id': op['id']}
    assert approval['decision'] == 'once' and not op['permission_id'] and op['result'] and not op['error']
    receipt = op['result']
    if manifest['provider'] == 'slack':
        assert receipt.get('ok') is True and receipt.get('ts') and receipt.get('channel') == op['arguments']['channel']
    elif manifest['provider'] == 'github':
        assert receipt.get('number') and receipt.get('html_url')
    else:
        assert receipt.get('id'), 'Google must return a provider object/message ID'
    assert 'PHASE4_REVIEW_RECEIPT' in task['output']
    return {'outcome': 'succeeded', 'external_effect': True, 'operation_id': op['id'], 'owner_edited': op['arguments'] != manifest['arguments'], 'superseded_operations': len(operations) - 1}


def verify_prepared_review(conn, manifest):
    """A generic owner review is not a frozen connector authorization."""
    rows = conn.execute('SELECT o.*,a.kind,a.payload,a.payload_hash AS approval_hash FROM connector_operations o JOIN approvals a ON a.id=o.approval_id WHERE o.task_id=%s ORDER BY o.created_at', (manifest['task_id'],)).fetchall()
    matching = [r for r in rows if str(r['connection_id']) == manifest['connection_id'] and r['bot_id'] == manifest['bot_id'] and r['action'] == manifest['action'] and r['arguments'] == manifest['arguments']]
    assert matching, 'The native task did not create the exact connector authorization; a generic review cannot pass preparation'
    for row in matching:
        frozen = {'connection_id': str(row['connection_id']), 'connection_revision': row['connection_revision'], 'action': row['action'], 'arguments': row['arguments']}
        if row.get('outcome_id'):
            frozen.update(outcome_id=str(row['outcome_id']), outcome_revision=row['outcome_revision'])
        assert row['kind'] == 'connector' and row['payload']['action'] == frozen
        assert row['payload_hash'] == row['approval_hash'] == service.digest(frozen)
    return [{'operation_id': str(r['id']), 'approval_id': str(r['approval_id'])} for r in matching]


def main():
    parser = argparse.ArgumentParser(description='Prepare one private-provider proposal for human review, or verify its durable receipt. Never auto-approves or repeats an effect.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', choices=CASES)
    mode.add_argument('--verify', type=Path, help='Manifest from a previous prepare; records evidence without submitting work')
    mode.add_argument('--recover', type=Path, help='Find the original task after a lost submission response; never resubmits it')
    parser.add_argument('--bot', help='Defaults to Scout for Google, Quill for Slack/GitHub')
    parser.add_argument('--recipient', default='dhruv@turboml.com')
    parser.add_argument('--calendar-id')
    parser.add_argument('--folder-id')
    parser.add_argument('--channel')
    parser.add_argument('--repo')
    args = parser.parse_args()
    snapshot = state()
    assert snapshot['provider_configured'] and not snapshot['protocol_fixture'], 'Use the real native/model stack'
    if args.recover:
        db.pool.open(wait=True)
        with db.connection() as conn:
            manifest = recover_manifest(conn, json.loads(args.recover.read_text()))
        args.recover.write_text(json.dumps(manifest, indent=2))
        print(json.dumps({'task_id': manifest['task_id'], 'resubmitted': False, 'manifest': str(args.recover)}), flush=True)
        return
    if args.verify:
        manifest = json.loads(args.verify.read_text())
        task = request('GET', '/api/v1/tasks/' + manifest['task_id'])
        activity = request('GET', '/api/v1/connections')
        ops = [o for o in activity['operations'] if o['task_id'] == task['id']]
        proof = verify_operation(task, ops, snapshot['approvals'], manifest)
        if proof['external_effect']:
            db.pool.open(wait=True)
            with db.connection() as conn:
                reports = conn.execute("SELECT id FROM artifacts WHERE task_id=%s AND source='bot'", (task['id'],)).fetchall()
            assert reports, 'Successful receipt continuation must publish its durable evidence report'
            result = next(op['result'] for op in ops if op['id'] == proof['operation_id'])
            provider_id = str(result['number'] if manifest['provider'] == 'github' else result['ts'] if manifest['provider'] == 'slack' else result['id'])
            texts = []
            for artifact in reports:
                response = client.get('/api/v1/artifacts/' + str(artifact['id']))
                response.raise_for_status()
                texts.append(response.text)
            assert any(proof['operation_id'] in text and provider_id in text for text in texts), 'Evidence report must identify the actual operation and provider object'
            proof['report_artifact_ids'] = [str(a['id']) for a in reports]
        proof.update(real_native_model=True, real_private_provider=True, task_id=task['id'], provider=manifest['provider'], action=manifest['action'], full_phase4_exit_gate=False)
        destination = args.verify.with_name(args.verify.stem + '-verified.json')
        destination.write_text(json.dumps(proof, indent=2))
        print(json.dumps(proof), flush=True)
        return
    marker = str(uuid4())
    provider, action, arguments, resource = plan(args.prepare, args, marker)
    bot = args.bot or ('researcher' if provider == 'google' else 'writer')
    connection = ready_connection(request('GET', '/api/v1/connections'), bot, provider, action, resource)
    selected = next(b for b in snapshot['bots'] if b['id'] == bot)
    assert selected['readiness'].get('compatible') and selected['model_name'] != 'hermbot-protocol-fixture'
    group = request('POST', '/api/v1/groups', json={'title': 'Phase 4 reviewed ' + args.prepare + ' ' + marker, 'bot_ids': [bot, 'reviewer' if bot != 'reviewer' else 'researcher']})
    manifest = {'case': args.prepare, 'marker': marker, 'provider': provider, 'action': action, 'arguments': arguments, 'bot_id': bot, 'connection_id': connection['id'], 'conversation_id': group['id'], 'submission_key': 'phase4-review:' + marker}
    path = Path('/app/test-results/phase4-reviewed-' + args.prepare + '-' + marker + '.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2))
    outcome = {'key': 'delivery', 'title': 'Reviewed Phase 4 ' + args.prepare, 'kind': 'connector_effect', 'target': {'connection_id': connection['id'], 'action': action, 'resource': resource}}
    review = {'kind': 'connector', 'summary': 'Review Phase 4 acceptance ' + marker, 'payload': {'connection_id': connection['id'], 'action': action, 'arguments': arguments, 'outcome_id': '<replace with returned delivery outcome id>'}}
    prompt = f'This is an owner-selected Phase 4 review acceptance. First call hermbot_prepare with outcomes [{json.dumps(outcome)}]. Then use hermbot_review exactly ONCE with these tool arguments: {json.dumps(review)}. Replace the placeholder with the returned delivery outcome id INSIDE payload; outcome_id is not a top-level review tool argument. End your turn after proposing; the HUMAN OWNER decides. Do not send through browser/terminal or substitute a tool, inherit permissions, create another proposal or repeat an uncertain effect. If resumed after denial, finish with PHASE4_REVIEW_DENIED. If resumed with a successful definitive connector receipt, publish a concise local evidence artifact including that operation ID and provider object ID, then finish with PHASE4_REVIEW_RECEIPT. Do not claim provider success without the actual receipt.'
    manifest['prompt'] = prompt
    path.write_text(json.dumps(manifest, indent=2))
    response = request('POST', '/api/v1/tasks', headers={'Idempotency-Key': manifest['submission_key']}, json={'bot_id': bot, 'conversation_id': group['id'], 'prompt': prompt})
    manifest['task_id'] = response['id']
    path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'task_id': response['id'], 'manifest': str(path), 'status': 'submitted; waiting for native connector draft'}), flush=True)
    wait(response['id'], statuses=('waiting_approval', 'completed'), timeout=360)
    db.pool.open(wait=True)
    with db.connection() as conn:
        manifest['prepared_connector_reviews'] = verify_prepared_review(conn, manifest)
    path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'task_id': response['id'], 'manifest': str(path), 'next': 'Open this group in the app and inspect/edit/deny/approve its exact connector review. Then use --verify with this manifest. Do not re-run --prepare to recover a lost response.'}), flush=True)


if __name__ == '__main__':
    main()
