"""Rehearse timeline migration against a private database copy; print hashes/counts only."""
import hashlib
import json
from pathlib import Path

from backend import db, timeline


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def snapshot():
    with db.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        result = {}
        for table in ('conversations', 'bots', 'messages', 'tasks', 'attempts', 'approvals', 'input_requests', 'teaching_sessions', 'events'):
            rows = conn.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
            if table == 'input_requests':
                for row in rows:
                    row.pop('answer_message_id', None)
            result[table] = {'count': len(rows), 'sha256': digest(rows)}
        return result


if not db.settings.database_url.endswith('/hermbot_timeline_rehearsal'):
    raise SystemExit('Rehearsal requires the isolated hermbot_timeline_rehearsal database')
db.pool.open(wait=True)
try:
    before = snapshot()
    migration = Path('/app/backend/migrations/023_timeline.sql').read_text()
    with db.connection() as conn:
        conn.execute(migration)
    after = snapshot()
    assert before == after, 'Migration changed existing domain history'
    with db.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        chats = conn.execute('SELECT id FROM conversations ORDER BY id').fetchall()
        report = {'preserved': before, 'conversations': len(chats), 'covered_messages': 0, 'answered_questions': 0, 'legacy_answers': 0, 'peer_outputs': 0, 'corrected_answer_scopes': 0}
        for chat in chats:
            entries = timeline.collect(conn, chat['id'])
            indexed = conn.execute('SELECT * FROM timeline_entries WHERE conversation_id=%s AND NOT deleted ORDER BY position', (chat['id'],)).fetchall()
            assert set(entries) == {e['id'] for e in indexed}
            messages = [entries[e['id']]['message'] for e in indexed if entries[e['id']].get('message')]
            assert [m['id'] for m in messages] == sorted(m['id'] for m in messages)
            report['covered_messages'] += len(messages)
            for entry in entries.values():
                report['answered_questions'] += bool(entry.get('question', {}).get('status') == 'answered')
                report['legacy_answers'] += 'legacy_answer' in entry
                report['peer_outputs'] += entry.get('origin') == 'coordination'
                if entry.get('message'):
                    report['corrected_answer_scopes'] += entry['thread_id'] != entry['message']['reply_to_message_id']
        assert report['covered_messages'] == before['messages']['count']
        index_hash = digest(conn.execute('SELECT * FROM timeline_entries ORDER BY conversation_id,position').fetchall())
    with db.connection() as conn:
        conn.execute(migration)
    with db.connection() as conn:
        assert index_hash == digest(conn.execute('SELECT * FROM timeline_entries ORDER BY conversation_id,position').fetchall())
    report['idempotent'] = True
    print(json.dumps(report, indent=2))
finally:
    db.pool.close()
