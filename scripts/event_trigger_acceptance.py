"""Local Phase 5.2 proof. Run stages in a private, networked app/tools container."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
from urllib import request
from uuid import uuid4

from backend import db
from backend.secrets import decrypt


PROOF = Path('/app/test-results/phase52-live.json')
BASE = 'http://api:8000'


def api(path, body=None, method=None, *, key=None):
    headers = {'Authorization': 'Bearer ' + os.environ['OWNER_TOKEN'], 'Content-Type': 'application/json',
               'Idempotency-Key': key or str(uuid4())}
    with request.urlopen(request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                        headers=headers, method=method), timeout=30) as response:
        return json.load(response)


def save(proof):
    PROOF.write_text(json.dumps(proof, indent=2, default=str))


def setup():
    assert not PROOF.exists(), 'Inspect or clean the previous fixture before creating another'
    group = api('/api/v1/groups', {'title': 'Phase 5.2 local event acceptance', 'bot_ids': ['researcher', 'reviewer']})
    proof = {'started_at': datetime.now(timezone.utc).isoformat(), 'conversation_id': group['id']}
    save(proof)
    src = api('/api/v1/event-sources', {'name': 'Phase 5.2 local signed event', 'provider': 'webhook', 'transport': 'http'})
    proof['source_id'] = src['id']
    save(proof)
    api(f"/api/v1/event-sources/{src['id']}/secrets", {'expected_revision': src['revision'],
        'signing_secret': 'whsec_' + base64.b64encode(secrets.token_bytes(32)).decode()})
    trigger = api('/api/v1/event-triggers', {'name': 'Phase 5.2 arithmetic event proof', 'bot_id': 'researcher',
        'conversation_id': group['id'], 'source_id': src['id'], 'event_types': ['custom.event'],
        'filters': [{'field': 'custom_type', 'value': 'phase52.acceptance'}],
        'instructions': 'For each matching event, calculate 173 + 268 using local arithmetic and publish the answer in this conversation as the required report. Use hermbot.complete_outcome for the report. Do not browse or make any external changes.',
        'requirements': [{'key': 'report', 'title': 'Report the arithmetic result 441 in this conversation', 'kind': 'report'}],
        'active_budget_seconds': 300, 'max_runs_per_day': 2, 'enabled': True})
    proof['trigger_id'] = trigger['id']
    proof['delivery_id'] = 'phase52-' + str(uuid4())
    save(proof)
    print('Created a local-only event acceptance fixture; no external change requested.')


def deliver(proof):
    with db.connection() as conn:
        row = conn.execute('SELECT secret_encrypted FROM event_sources WHERE id=%s', (proof['source_id'],)).fetchone()
    secret = json.loads(decrypt(row['secret_encrypted']))['signing_secret']
    stamp = str(int(datetime.now(timezone.utc).timestamp()))
    raw = json.dumps({'type': 'phase52.acceptance', 'resource': 'local-arithmetic', 'text': 'Local verification only.'}).encode()
    digest = hmac.new(base64.b64decode(secret[6:]), proof['delivery_id'].encode()+b'.'+stamp.encode()+b'.'+raw, hashlib.sha256).digest()
    headers = {'Content-Type': 'application/json', 'webhook-id': proof['delivery_id'], 'webhook-timestamp': stamp,
               'webhook-signature': 'v1,' + base64.b64encode(digest).decode()}
    with request.urlopen(request.Request(BASE + f"/hooks/webhook/{proof['source_id']}", data=raw, headers=headers), timeout=20) as response:
        return json.load(response)['disposition']


def pending(proof):
    with ThreadPoolExecutor(max_workers=2) as pool:
        dispositions = list(pool.map(lambda _: deliver(proof), range(2)))
    assert sorted(dispositions) == ['duplicate', 'recorded'], dispositions
    runs = api(f"/api/v1/event-triggers/{proof['trigger_id']}/runs")['data']
    assert len(runs) == 1 and runs[0]['status'] == 'pending' and runs[0]['task_id'] is None
    proof.update(before_restart={'occurrences': 1, 'tasks': 0, 'concurrent_delivery_dispositions': dispositions})
    save(proof)
    print('Two concurrent deliveries committed one pending occurrence and no task while the event worker was stopped.')


def verify(proof):
    runs = api(f"/api/v1/event-triggers/{proof['trigger_id']}/runs")['data']
    assert len(runs) == 1, 'Expected exactly one occurrence'
    run = runs[0]
    print(json.dumps({k: run[k] for k in ('status', 'display_status', 'execution_status', 'reason', 'event_count')}))
    if run['display_status'] != 'completed':
        return
    assert '441' in (run['output'] or ''), 'Arithmetic report missing'
    assert deliver(proof) == 'duplicate'
    with db.connection() as conn:
        operations = conn.execute('SELECT count(*) AS n FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.conversation_id=%s', (proof['conversation_id'],)).fetchone()['n']
        outcomes = conn.execute('SELECT o.key,o.status FROM work_outcomes o JOIN tasks t ON t.work_item_id=o.work_item_id WHERE t.id=%s', (run['task_id'],)).fetchall()
        tasks = conn.execute('SELECT count(*) AS n FROM tasks WHERE trigger_run_id=%s', (run['id'],)).fetchone()['n']
    assert operations == 0 and tasks == 1 and outcomes and all(o['status'] == 'fulfilled' for o in outcomes)
    proof.update(after_restart={'completed_at': datetime.now(timezone.utc).isoformat(), 'run_id': run['id'],
        'task_id': run['task_id'], 'occurrences': 1, 'tasks': tasks, 'event_count': run['event_count'],
        'result': run['output'], 'outcomes': outcomes, 'connector_operations': operations, 'post_completion_redelivery': 'duplicate'})
    save(proof)


def cleanup(proof):
    assert proof.get('after_restart'), 'Complete acceptance before archiving the fixture'
    trigger = next(r for r in api('/api/v1/event-triggers')['data'] if r['id'] == proof['trigger_id'])
    if trigger['state'] != 'archived':
        api(f"/api/v1/event-triggers/{trigger['id']}/archive", {'expected_revision': trigger['revision']})
    src = next(r for r in api('/api/v1/event-sources')['data'] if r['id'] == proof['source_id'])
    if src['status'] != 'archived':
        api(f"/api/v1/event-sources/{src['id']}/archive", {'expected_revision': src['revision']})
    api(f"/api/v1/groups/{proof['conversation_id']}", method='DELETE')
    proof['fixtures_archived'] = True
    save(proof)
    print('Archived the fixture group, trigger and source; preserved receipts and results.')


def provider_setup(proof):
    assert not proof.get('provider_sources'), 'Provider fixtures already exist'
    connections = api('/api/v1/connections')['connections']
    sources = []
    for provider, action, resource in [('google_drive', 'drive.read', ''), ('google_calendar', 'calendar.events', 'primary'),
                                        ('gmail', 'gmail.read', ''), ('github', 'github.issue', 'dhruvturboml/hermbot-phase4-test')]:
        account = next((c for c in connections if c['provider'] == ('github' if provider == 'github' else 'google')
                        and c['status'] == 'connected' and action in c['actions']), None)
        if not account:
            sources.append({'provider': provider, 'status': 'unconfigured'})
            continue
        src = api('/api/v1/event-sources', {'name': 'Phase 5.2 baseline ' + provider, 'provider': provider,
            'connection_id': account['id'], 'transport': 'poll', 'resource': resource})
        sources.append({'id': src['id'], 'provider': provider})
        proof['provider_sources'] = sources
        save(proof)
    print('Prepared read-only provider baselines; no provider mutation or task requested.')


def provider_verify(proof):
    sources = {s['id']: s for s in api('/api/v1/event-sources')['data']}
    for entry in proof.get('provider_sources', []):
        if 'id' in entry:
            src = sources[entry['id']]
            entry.update(status=src['status'], initialized=bool(src['initialized_at']), error=src['error'])
    save(proof)
    print(json.dumps(proof.get('provider_sources', [])))


def provider_cleanup(proof):
    for entry in proof.get('provider_sources', []):
        if 'id' in entry:
            src = next(s for s in api('/api/v1/event-sources')['data'] if s['id'] == entry['id'])
            if src['status'] != 'archived':
                api(f"/api/v1/event-sources/{src['id']}/archive", {'expected_revision': src['revision']})
    proof['provider_fixtures_archived'] = True
    save(proof)


def browser_cleanup(proof):
    # Browser previews used synthetic saved instructions; remove only those empty, archived fixtures.
    with db.connection() as conn:
        rows = conn.execute("SELECT d.id,d.source_id,v.definition FROM event_trigger_definitions d JOIN event_trigger_revisions v ON v.trigger_id=d.id AND v.revision=d.revision JOIN event_sources s ON s.id=d.source_id WHERE d.state='archived' AND s.status='archived' AND s.provider='webhook' AND s.name=v.definition->>'name'").fetchall()
        count = 0
        for row in rows:
            if not re.fullmatch(r'Phase 5\.2 browser \d{13}', row['definition']['name']):
                continue
            assert row['definition']['instructions'] == 'Write a report about the supplied event. Do not change any external application.'
            assert not conn.execute('SELECT 1 FROM trigger_runs WHERE trigger_id=%s', (row['id'],)).fetchone()
            assert not conn.execute('SELECT 1 FROM trigger_matches WHERE trigger_id=%s', (row['id'],)).fetchone()
            assert not conn.execute('SELECT 1 FROM trigger_deliveries WHERE source_id=%s', (row['source_id'],)).fetchone()
            messages = [r['source_message_id'] for r in conn.execute('DELETE FROM event_trigger_revisions WHERE trigger_id=%s RETURNING source_message_id', (row['id'],)).fetchall()]
            conn.execute('DELETE FROM event_trigger_definitions WHERE id=%s', (row['id'],))
            conn.execute('DELETE FROM messages WHERE id=ANY(%s)', (messages,))
            conn.execute('DELETE FROM event_sources WHERE id=%s', (row['source_id'],))
            count += 1
        sources = conn.execute("SELECT id,name FROM event_sources s WHERE provider='webhook' AND status='archived' AND NOT EXISTS(SELECT 1 FROM event_trigger_definitions d WHERE d.source_id=s.id) AND NOT EXISTS(SELECT 1 FROM trigger_deliveries r WHERE r.source_id=s.id)").fetchall()
        for src in sources:
            if re.fullmatch(r'Phase 5\.2 browser \d{13}', src['name']):
                conn.execute('DELETE FROM event_sources WHERE id=%s', (src['id'],))
    proof['empty_browser_fixtures_removed'] = count
    save(proof)
    print(f'Removed {count} empty archived browser fixtures and their synthetic saved-instruction messages.')


def audit(proof):
    assert proof['fixtures_archived'] and proof['provider_fixtures_archived']
    with db.connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        trigger = conn.execute('SELECT state FROM event_trigger_definitions WHERE id=%s', (proof['trigger_id'],)).fetchone()
        source = conn.execute('SELECT status,secret_encrypted FROM event_sources WHERE id=%s', (proof['source_id'],)).fetchone()
        group = conn.execute('SELECT archived_at FROM conversations WHERE id=%s', (proof['conversation_id'],)).fetchone()
        leftovers = conn.execute("SELECT count(*) AS n FROM event_sources WHERE name LIKE 'Phase 5.2 browser %%'").fetchone()['n']
        active = conn.execute("SELECT count(*) AS n FROM tasks WHERE conversation_id=%s AND status NOT IN ('completed','cancelled','failed','interrupted','uncertain')", (proof['conversation_id'],)).fetchone()['n']
    assert trigger['state'] == 'archived' and source['status'] == 'archived' and source['secret_encrypted'] is None
    assert group['archived_at'] and leftovers == 0 and active == 0
    runs = api(f"/api/v1/event-triggers/{proof['trigger_id']}/runs")['data']
    assert len(runs) == 1 and runs[0]['display_status'] == 'completed' and '441' in runs[0]['output']
    assert proof['after_restart']['connector_operations'] == 0
    assert all(p['status'] == 'ready' and p['initialized'] for p in proof['provider_sources'])
    proof['final_audit'] = {'checked_at': datetime.now(timezone.utc).isoformat(), 'history_preserved': True,
        'source_credential_revoked': True, 'active_fixture_tasks': active, 'browser_fixture_sources_remaining': leftovers}
    save(proof)
    print(json.dumps(proof['final_audit']))


if __name__ == '__main__':
    stage = sys.argv[1]
    db.pool.open(wait=True)
    try:
        if stage == 'setup':
            setup()
        else:
            {'pending': pending, 'verify': verify, 'cleanup': cleanup, 'provider-setup': provider_setup,
             'provider-verify': provider_verify, 'provider-cleanup': provider_cleanup,
             'browser-cleanup': browser_cleanup, 'audit': audit}[stage](json.loads(PROOF.read_text()))
    finally:
        db.pool.close()
