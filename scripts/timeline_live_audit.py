"""Read-only retained-history coverage check through the owner timeline API."""
import json
import os
import urllib.request

from backend import db, timeline


def page(conversation, thread, before=None):
    params = []
    if thread is not None:
        params.append(f'thread={thread}')
    if before is not None:
        params.append(f'before={before}')
    suffix = '?' + '&'.join(params) if params else ''
    request = urllib.request.Request(f'http://127.0.0.1:8000/api/v1/conversations/{conversation}/timeline{suffix}', headers={'Authorization': 'Bearer ' + os.environ['OWNER_TOKEN']})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


db.pool.open(wait=True)
try:
    with db.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conversations = conn.execute('SELECT id FROM conversations ORDER BY id').fetchall()
        expected = {chat['id']: timeline.collect(conn, chat['id']) for chat in conversations}
        message_count = conn.execute('SELECT count(*) AS n FROM messages').fetchone()['n']
        assert conn.execute('SELECT count(*) AS n FROM timeline_dirty').fetchone()['n'] == 0
    covered = set()
    report = {'conversations': len(conversations), 'thread_scopes': 0, 'pages': 0, 'messages': message_count, 'covered_messages': 0, 'failures': []}
    for chat in conversations:
        sources = expected[chat['id']]
        threads = {entry['thread_id'] for entry in sources.values() if entry['thread_id'] is not None}
        for thread in [None, *sorted(threads)]:
            report['thread_scopes'] += thread is not None
            want = {entry['id'] for entry in sources.values() if entry['thread_id'] == thread or (entry.get('message') and entry['message']['id'] == thread)}
            found = set()
            cursor = None
            while True:
                result = page(chat['id'], thread, cursor)
                report['pages'] += 1
                rows = result['entries']
                assert not any(row.get('deleted') for row in rows)
                found.update(row['id'] for row in rows)
                covered.update(row['message']['id'] for row in rows if row.get('message'))
                if not result['has_earlier']:
                    break
                assert rows and (cursor is None or rows[0]['position'] < cursor)
                cursor = rows[0]['position']
            if found != want:
                report['failures'].append({'conversation': str(chat['id']), 'thread': thread, 'missing': sorted(want - found), 'unexpected': sorted(found - want)})
    report['covered_messages'] = len(covered)
    assert not report['failures'], report['failures']
    assert len(covered) == message_count
    print(json.dumps(report, indent=2))
finally:
    db.pool.close()
