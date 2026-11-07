"""Prepare live Phase 5.2 listeners and human-reviewed canaries; never auto-approve."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from backend import connector_catalog, connector_oauth, db, trigger_worker, triggers
from backend.connector_http import request
from backend.secrets import decrypt
from backend.trigger_providers import Page, resource_state
from psycopg.types.json import Jsonb
from scripts.event_trigger_acceptance import api
from tests.phase4_review_acceptance import verify_prepared_review


PROOF = Path('/app/test-results/phase52-provider-events.json')
CASES = {'google_drive': ('google', 'researcher', ['drive.file.changed'], 'drive.create'),
         'google_calendar': ('google', 'researcher', ['calendar.event.created', 'calendar.event.updated'], 'calendar.create'),
         'gmail': ('google', 'researcher', ['gmail.message.added'], 'gmail.send'),
         'github': ('github', 'writer', ['github.issue.opened', 'github.issue.updated'], 'github.create_issue'),
         'slack': ('slack', 'writer', ['slack.message.created', 'slack.app_mention'], 'slack.post')}


def save(proof):
    PROOF.write_text(json.dumps(proof, indent=2, default=str))


def review_manifest(provider, entry):
    arguments, _, _ = connector_catalog.validate({'provider': CASES[provider][0]}, entry['action'], entry['arguments'])
    return {**entry, 'task_id': entry['draft_task_id'], 'arguments': arguments}


def owner_change(provider, events, canary_resource):
    return bool(events) and all('Owner changed' in e.get('text', '') and
        (e['resource'] != canary_resource if provider == 'gmail' else e['resource'] == canary_resource)
        for e in events)


def approved_operation(conn, provider, entry):
    rows = conn.execute('SELECT o.*,a.decision,a.payload_hash AS approval_hash FROM connector_operations o JOIN approvals a ON a.id=o.approval_id WHERE o.task_id=%s', (entry.get('draft_task_id'),)).fetchall()
    if not rows or any(o['status'] != 'succeeded' for o in rows):
        return None
    assert len(rows) == 1, 'The canary must not have been repeated'
    operation = rows[0]
    assert operation['decision'] == 'once' and operation['payload_hash'] == operation['approval_hash']
    verify_prepared_review(conn, review_manifest(provider, entry))
    task = conn.execute('SELECT status FROM tasks WHERE id=%s', (entry['draft_task_id'],)).fetchone()
    return operation if task['status'] == 'completed' else None


def reconcile_creation(proof):
    """Verify the retained pre-correction canary using the collector's one-time read check."""
    entry = proof['drive_correlation']
    with db.connection() as conn:
        src = triggers.source(conn, entry['source_id'])
        operation = approved_operation(conn, 'google_drive', entry)
        assert operation, 'Wait for the exact approved write and receipt continuation'
    credential = trigger_worker.credentials(src)
    file = request('GET', 'https://www.googleapis.com/drive/v3/files/' + operation['result']['id'], token=credential['access_token'],
        params={'fields': 'id,name,mimeType,parents,version,modifiedTime,trashed,appProperties'})
    item = {'fileId': file['id'], 'file': file}
    trigger_worker.check_drive_creations(src, credential, Page(items=[item]))
    assert item.get('_hermbot_creation_operation_id') == str(operation['id']), 'The observed initial document must still match the exact approved text, name and folder'
    resource, version, metadata = resource_state(src, item)
    with db.connection() as conn:
        current = triggers.source(conn, src['id'], lock=True)
        assert current['generation'] == src['generation'] and current['revision'] == src['revision']
        triggers.source_connection(conn, current)
        known = conn.execute('SELECT * FROM trigger_resource_versions WHERE source_id=%s AND resource=%s FOR UPDATE', (src['id'], resource)).fetchone()
        assert known and known['version'] == version and known['metadata']['file_state'] == metadata['file_state']
        events = conn.execute('SELECT e.id,e.payload_encrypted,r.task_id FROM trigger_events e LEFT JOIN trigger_matches m ON m.event_id=e.id LEFT JOIN trigger_runs r ON r.id=m.run_id WHERE e.source_id=%s AND e.resource=%s', (src['id'], resource)).fetchall()
        events = [e for e in events if e['payload_encrypted'] and json.loads(decrypt(e['payload_encrypted']))['metadata'].get('file_state') == metadata['file_state']]
        assert events and all(not e['task_id'] for e in events), 'Never rewrite already admitted work'
        conn.execute('UPDATE trigger_resource_versions SET metadata=metadata||%s WHERE source_id=%s AND resource=%s AND version=%s', (Jsonb(metadata), src['id'], resource, version))
        db.event(conn, 'trigger.creation_verified', source_id='phase52-drive-creation:' + str(operation['id']), data={
            'source_id': str(src['id']), 'operation_id': str(operation['id']), 'resource': resource, 'version': version,
            'file_state': metadata['file_state'], 'matched': 'Approved initial text, name and folder; retained pending event has no task'})
    proof['drive_creation_reconciliation'] = {'operation_id': str(operation['id']), 'resource': resource, 'version': version,
        'checked_at': datetime.now(timezone.utc).isoformat(), 'method': 'Read-only current metadata/export through the actual collector check; ordinary admission reclassifies the retained pending event'}
    save(proof)
    print(json.dumps(proof['drive_creation_reconciliation']))


def setup():
    assert not PROOF.exists(), 'Resume the saved manifest; do not create duplicate fixtures'
    data = api('/api/v1/connections')['connections']
    connections = {provider: next(c for c in data if c['provider'] == provider and c['status'] == 'connected')
                   for provider in ('google', 'github', 'slack')}
    with db.connection() as conn:
        account = conn.execute('SELECT * FROM connections WHERE id=%s FOR UPDATE', (connections['slack']['id'],)).fetchone()
        token = connector_oauth.credential(conn, account)['access_token']
    channels = request('GET', 'https://slack.com/api/conversations.list', token=token, params={'limit': 200, 'exclude_archived': 'true'})
    channel = next(c for c in channels['channels'] if c['name'] == 'test')
    group = api('/api/v1/groups', {'title': 'Phase 5.2 provider event acceptance', 'bot_ids': ['researcher', 'writer']})
    proof = {'marker': 'phase52-' + uuid4().hex[:10], 'created_at': datetime.now(timezone.utc).isoformat(),
             'conversation_id': group['id'], 'slack_channel': channel['id'], 'cases': {}}
    save(proof)
    for provider, (account, bot, kinds, action) in CASES.items():
        src = api('/api/v1/event-sources', {'name': 'Phase 5.2 ' + provider + ' acceptance', 'provider': provider,
            'connection_id': connections[account]['id'], 'transport': 'socket' if provider == 'slack' else 'poll',
            'resource': 'primary' if provider == 'google_calendar' else 'dhruvturboml/hermbot-phase4-test' if provider == 'github' else ''})
        proof['cases'][provider] = {'source_id': src['id'], 'connection_id': connections[account]['id'], 'bot_id': bot, 'action': action, 'event_types': kinds}
        save(proof)
    print(json.dumps({'conversation_id': group['id'], 'marker': proof['marker'], 'sources_prepared': list(proof['cases'])}))


def listen(proof):
    sources = {s['id']: s for s in api('/api/v1/event-sources')['data']}
    for provider, entry in proof['cases'].items():
        src = sources[entry['source_id']]
        if entry.get('trigger_id') or src['status'] not in ('ready', 'listening'):
            continue
        filters = [{'field': 'text', 'operator': 'contains', 'value': proof['marker']}]
        if provider == 'slack':
            filters.append({'field': 'resource', 'value': proof['slack_channel']})
        trigger = api('/api/v1/event-triggers', {'name': 'Observe ' + provider + ' ' + proof['marker'],
            'source_id': src['id'], 'bot_id': entry['bot_id'], 'conversation_id': proof['conversation_id'],
            'instructions': 'Report the supplied event facts from handoff_context.event_trigger. Include provider, event_type, resource, resource_version and summary verbatim, plus ' + proof['marker'] + '. Fulfill the required report. Do not make external changes, browse or start other work.',
            'requirements': [{'key': 'report', 'title': 'Report the actual provider event identity and version', 'kind': 'report'}],
            'event_types': entry['event_types'], 'filters': filters, 'active_budget_seconds': 300, 'max_runs_per_day': 3,
            'start_window_minutes': 1440, 'max_pending': 5, 'enabled': True})
        entry['trigger_id'] = trigger['id']
        save(proof)
    print(json.dumps({p: {'source_status': sources[v['source_id']]['status'], 'trigger_id': v.get('trigger_id')} for p,v in proof['cases'].items()}))


def prepare(proof):
    marker = proof['marker']
    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    arguments = {
        'google_drive': {'name': marker + ' event test', 'content': marker + ' owner-reviewed event test.', 'folder_id': 'root', 'document': True},
        'google_calendar': {'calendar_id': 'primary', 'summary': marker + ' event test', 'description': marker + ' event test; no guests.', 'start': start.isoformat(), 'end': (start+timedelta(minutes=15)).isoformat(), 'attendees': []},
        'gmail': {'to': 'dhruv@turboml.com', 'subject': marker + ' event test', 'body': marker + ' owner-reviewed self-test email.'},
        'github': {'repo': 'dhruvturboml/hermbot-phase4-test', 'title': marker + ' event test', 'body': marker + ' owner-reviewed event test; no production changes.'},
        'slack': {'channel': proof['slack_channel'], 'text': marker + ' owner-reviewed event test in #test.'}}
    for provider, entry in proof['cases'].items():
        if entry.get('draft_task_id') or not entry.get('trigger_id'):
            continue
        entry['arguments'] = entry.get('arguments') or arguments[provider]
        canary = entry['arguments']
        outcome = {'key': 'delivery', 'title': 'Owner-reviewed '+provider+' event canary', 'kind': 'connector_effect',
            'target': {'connection_id': entry['connection_id'], 'action': entry['action'], 'resource': canary.get('repo') or canary.get('calendar_id') or canary.get('folder_id') or canary.get('to') or canary.get('channel')}}
        review = {'kind': 'connector', 'summary': 'Review Phase 5.2 ' + provider + ' canary ' + marker,
            'payload': {'connection_id': entry['connection_id'], 'action': entry['action'], 'arguments': canary, 'outcome_id': '<delivery outcome id returned by preparation>'}}
        prompt = entry.get('prompt') or 'Prepare ONE exact, owner-reviewed Phase 5.2 provider-event canary. First call hermbot_prepare with outcomes '+json.dumps([outcome])+'. Then use hermbot_review exactly once with '+json.dumps(review)+'. Replace the outcome placeholder INSIDE payload. End your turn and wait for the actual human review. Do not execute through native tools or grant yourself authorization. On receipt continuation, call hermbot_prepare again with the same outcome key before finishing; preparation is execution-specific. After the successful provider receipt, fulfill the delivery result, report the operation/provider IDs, and finish. On denial, report denial and finish without repeating the action.'
        entry['prompt'] = prompt
        entry['submission_key'] = 'phase52-provider-draft:' + marker + ':' + provider
        save(proof)
        task = api('/api/v1/tasks', {'bot_id': entry['bot_id'], 'conversation_id': proof['conversation_id'], 'prompt': prompt}, method='POST', key=entry['submission_key'])
        entry['draft_task_id'] = task['id']
        save(proof)
    print('Submitted native canary drafts; only the human owner may approve them.')


def status(proof):
    with db.connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        result = {}
        for provider, entry in proof['cases'].items():
            src = conn.execute('SELECT status,error,initialized_at,checked_at FROM event_sources WHERE id=%s', (entry['source_id'],)).fetchone()
            runs = conn.execute('SELECT status,reason,task_id FROM trigger_runs WHERE trigger_id=%s', (entry.get('trigger_id'),)).fetchall()
            ops = conn.execute('SELECT id,status,approval_id,result,error FROM connector_operations WHERE task_id=%s', (entry.get('draft_task_id'),)).fetchall()
            for op in ops:
                op['result'] = {k: v for k, v in (op['result'] or {}).items() if k in ('id', 'number', 'html_url', 'htmlLink', 'webViewLink', 'version', 'etag', 'threadId', 'channel', 'ts', 'ok')}
            if ops:
                entry['prepared_reviews'] = verify_prepared_review(conn, review_manifest(provider, entry))
            draft = conn.execute('SELECT status,error FROM tasks WHERE id=%s', (entry.get('draft_task_id'),)).fetchone()
            result[provider] = {'source': src, 'runs': runs, 'draft': draft, 'operations': ops}
    proof['latest_status'] = result
    save(proof)
    print(json.dumps(result, default=str))


def verify(proof):
    checks = {}
    with db.connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        for provider, entry in proof['cases'].items():
            if not entry.get('trigger_id'):
                checks[provider] = {'complete': False, 'waiting': 'Source setup'}
                continue
            operation = approved_operation(conn, provider, entry)
            if not operation:
                checks[provider] = {'complete': False, 'waiting': 'Human canary review or provider receipt'}
                continue
            receipt = operation['result']
            resource = ('dhruvturboml/hermbot-phase4-test#' + str(receipt['number'])) if provider == 'github' else receipt.get('channel') if provider == 'slack' else receipt['id']
            own_operation, own_resource = operation, resource
            if provider == 'google_drive' and proof.get('drive_correlation'):
                extra = proof['drive_correlation']
                assert all(extra[k] == entry[k] for k in ('source_id', 'trigger_id', 'connection_id', 'bot_id', 'action'))
                own_operation = approved_operation(conn, provider, extra)
                if not own_operation:
                    checks[provider] = {'complete': False, 'waiting': 'Fresh Drive correlation canary review or receipt continuation'}
                    continue
                own_resource = own_operation['result']['id']
            own = conn.execute("SELECT e.id,e.resource_version,e.metadata,m.run_id,r.status AS run_status,r.task_id FROM trigger_events e LEFT JOIN trigger_matches m ON m.event_id=e.id AND m.trigger_id=%s LEFT JOIN trigger_runs r ON r.id=m.run_id WHERE e.source_id=%s AND e.resource=%s ORDER BY e.received_at", (entry['trigger_id'], entry['source_id'], own_resource)).fetchall()
            if provider == 'slack':
                own = [e for e in own if e['resource_version'] == receipt['ts']]
            suppressed = [e for e in own if (e['metadata'].get('self_generated') or e['run_status'] == 'ignored') and not e['task_id']]
            reports = conn.execute("SELECT r.id,r.task_id,t.status,t.output,t.handoff_context FROM trigger_runs r JOIN tasks t ON t.id=r.task_id WHERE r.trigger_id=%s ORDER BY r.created_at", (entry['trigger_id'],)).fetchall()
            completed = []
            for report in reports:
                if report['status'] != 'completed':
                    continue
                context = report['handoff_context']['event_trigger']
                assert context['source_id'] == entry['source_id'] and context['provider'] == provider
                assert context['events'] and all(e['resource'] in (report['output'] or '') for e in context['events'])
                assert proof['marker'] in report['output']
                assert not conn.execute('SELECT 1 FROM connector_operations WHERE task_id=%s', (report['task_id'],)).fetchone()
                assert not conn.execute("SELECT 1 FROM work_outcomes o JOIN tasks t ON t.work_item_id=o.work_item_id WHERE t.id=%s AND o.required AND o.status<>'fulfilled'", (report['task_id'],)).fetchone()
                if not owner_change(provider, context['events'], resource):
                    continue  # Creation echoes are not evidence of an external owner change.
                completed.append({'run_id': report['id'], 'task_id': report['task_id'], 'event_keys': [e['key'] for e in context['events']]})
            keys = [key for report in completed for key in report['event_keys']]
            assert len(keys) == len(set(keys)), 'One provider event created duplicate completed work'
            checks[provider] = {'complete': bool(suppressed and completed), 'initial_operation_id': operation['id'], 'own_operation_id': own_operation['id'],
                'own_event_ids': [e['id'] for e in suppressed], 'completed_reports': completed,
                'waiting': None if suppressed and completed else 'Provider collection or an owner change outside Hermbot'}
    proof['verification'] = checks
    proof['complete'] = all(check['complete'] for check in checks.values()) and len(checks) == len(CASES)
    save(proof)
    print(json.dumps({'complete': proof['complete'], 'providers': checks}, default=str))


def cleanup(proof):
    verify(proof)
    assert proof['complete'], 'Verify all real provider paths before cleanup'
    with db.connection() as conn:
        assert not conn.execute('SELECT 1 FROM tasks WHERE conversation_id=%s AND status<>ALL(%s)',
            (proof['conversation_id'], ['completed', 'failed', 'cancelled', 'interrupted', 'expired'])).fetchone()
    definitions = {row['id']: row for row in api('/api/v1/event-triggers')['data']}
    sources = {row['id']: row for row in api('/api/v1/event-sources')['data']}
    names = {'slack': 'Slack workspace events', 'google_drive': 'Google Drive changes',
        'google_calendar': 'Primary Google Calendar events', 'gmail': 'Gmail messages',
        'github': 'GitHub dhruvturboml/hermbot-phase4-test'}
    for provider, entry in proof['cases'].items():
        definition = definitions[entry['trigger_id']]
        if definition['state'] != 'archived':
            api('/api/v1/event-triggers/' + definition['id'] + '/archive', {'expected_revision': definition['revision']})
        source = sources[entry['source_id']]
        if source['name'] != names[provider]:
            body = {k: source[k] for k in ('provider', 'connection_id', 'transport', 'resource', 'enabled', 'poll_interval_seconds')}
            api('/api/v1/event-sources/' + source['id'], {**body, 'name': names[provider], 'expected_revision': source['revision']}, method='PUT')
    api('/api/v1/groups/' + proof['conversation_id'], method='DELETE')
    proof['cleanup'] = {'at': datetime.now(timezone.utc).isoformat(), 'group_and_triggers_archived': True,
        'configured_sources_preserved': names, 'external_test_objects_deleted': False}
    save(proof)
    print('Archived acceptance group and triggers; preserved history and privately configured reusable sources.')


if __name__ == '__main__':
    db.pool.open(wait=True)
    try:
        stage = sys.argv[1]
        if stage == 'setup':
            setup()
        else:
            {'listen': listen, 'prepare': prepare, 'status': status, 'verify': verify, 'reconcile_creation': reconcile_creation, 'cleanup': cleanup}[stage](json.loads(PROOF.read_text()))
    finally:
        db.pool.close()
