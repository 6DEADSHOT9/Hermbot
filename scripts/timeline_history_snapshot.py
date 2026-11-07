"""Read-only timeline cutover evidence: hashes/counts, never message content."""
import hashlib
import json

from backend import db

db.pool.open(wait=True)
try:
    with db.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        report = {}
        for table in ('messages', 'tasks', 'attempts', 'approvals', 'input_requests'):
            rows = conn.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
            for row in rows:
                if table == 'input_requests':
                    row.pop('answer_message_id', None)
            report[table] = {'count': len(rows), 'sha256': hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()}
        print(json.dumps(report))
finally:
    db.pool.close()
