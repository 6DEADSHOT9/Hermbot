"""A commit-ordered presentation index over existing conversation records."""
import hashlib
import json

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from . import owner_requests


TASK_FIELDS = ('id', 'root_id', 'parent_id', 'bot_id', 'conversation_id', 'owner_message_id',
               'reply_to_message_id', 'execution_kind', 'message_intent', 'intake_status',
               'orchestration_version', 'work_item_id', 'status', 'error', 'created_at',
               'deadline', 'attempts', 'stop_kind', 'stop_reason', 'context', 'input',
               'input_hash', 'depth', 'grant_id', 'output')


def public_task(task):
    return {key: task.get(key) for key in TASK_FIELDS}


def response(conn, attempt_id):
    attempt = conn.execute('SELECT a.*,t.conversation_id,t.reply_to_message_id,t.owner_message_id,t.parent_id,t.execution_kind FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE a.id=%s', (attempt_id,)).fetchone()
    if not attempt:
        return None
    message = conn.execute('SELECT * FROM messages WHERE source_id=%s', (f'runtime-result:{attempt_id}',)).fetchone()
    preview = conn.execute('SELECT * FROM timeline_previews WHERE attempt_id=%s', (attempt_id,)).fetchone()
    return response_entry(attempt, message, preview)


def response_entry(attempt, message, preview):
    if not message and not preview:
        return None
    return {'id': f"response:{attempt['id']}", 'thread_id': attempt['reply_to_message_id'],
            'occurred_at': preview['created_at'] if preview else message['created_at'],
            'message': message, 'text': message['content'] if message else preview['content'],
            'task_id': attempt['task_id'], 'attempt_id': attempt['id'], 'author': attempt['bot_id'],
            'status': attempt['status'], 'cause_message_id': attempt['owner_message_id'],
            'origin': 'coordination' if attempt['execution_kind'] == 'peer_message' else 'delegated' if attempt['parent_id'] else 'response', 'tasks': []}


def collect(conn, conversation_id):
    tasks = conn.execute('SELECT * FROM tasks WHERE conversation_id=%s', (conversation_id,)).fetchall()
    by_task = {t['id']: t for t in tasks}
    messages = conn.execute('SELECT m.*,(SELECT count(*) FROM messages r WHERE r.reply_to_message_id=m.id AND r.id<>m.id) AS reply_count FROM messages m WHERE m.conversation_id=%s ORDER BY m.id', (conversation_id,)).fetchall()
    by_source = {m['source_id']: m for m in messages if m['source_id']}
    attempts = conn.execute('SELECT a.*,t.reply_to_message_id,t.owner_message_id,t.parent_id,t.execution_kind FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE t.conversation_id=%s', (conversation_id,)).fetchall()
    previews = {p['attempt_id']: p for p in conn.execute('SELECT p.* FROM timeline_previews p JOIN tasks t ON t.id=p.task_id WHERE t.conversation_id=%s', (conversation_id,)).fetchall()}
    responses = {}
    for attempt in attempts:
        entry = response_entry(attempt, by_source.get(f"runtime-result:{attempt['id']}"), previews.get(attempt['id']))
        if entry:
            responses[entry['id']] = entry
    questions = conn.execute('SELECT q.* FROM input_requests q JOIN tasks t ON t.id=q.task_id WHERE t.conversation_id=%s ORDER BY q.created_at,q.id', (conversation_id,)).fetchall()
    approvals = conn.execute('SELECT a.* FROM approvals a JOIN tasks t ON t.id=a.task_id WHERE t.conversation_id=%s ORDER BY a.created_at,a.id', (conversation_id,)).fetchall()
    reactions = conn.execute('SELECT r.* FROM message_reactions r JOIN messages m ON m.id=r.message_id WHERE m.conversation_id=%s', (conversation_id,)).fetchall()
    owner_tasks = {}
    owner_by_task = {}
    for task in tasks:
        owner = task['owner_message_id']
        if owner is None:
            owner = next((m['id'] for m in messages if m['task_id'] == task['id'] and m['role'] == 'user'), None)
        if owner:
            owner_tasks.setdefault(owner, []).append(public_task(task))
            owner_by_task[task['id']] = owner
    # Correct proven old answer escapes in presentation, without rewriting native history.
    answer_threads = {q['answer_message_id']: by_task[q['task_id']]['reply_to_message_id'] for q in questions if q['answer_message_id']}
    result = {}
    for message in messages:
        task = by_task.get(message['task_id'], {})
        message['work_item_id'] = task.get('work_item_id')
        message['message_intent'] = task.get('message_intent')
        message['reactions'] = [{'emoji': r['emoji'], 'author': r['author']} for r in reactions if r['message_id'] == message['id']]
        identity = f"message:{message['id']}"
        streamed = None
        if (message['source_id'] or '').startswith('runtime-result:'):
            attempt_id = message['source_id'].removeprefix('runtime-result:')
            streamed = responses.get(f'response:{attempt_id}')
        if streamed:
            identity = streamed['id']
        thread = answer_threads.get(message['id'], answer_threads.get(owner_by_task.get(message['task_id']), message['reply_to_message_id']))
        # Routine/event system anchors deliberately point at their own root.
        if thread == message['id']:
            thread = None
        entry = streamed or {'id': identity, 'occurred_at': message['created_at'],
                                  'task_id': message['task_id'], 'cause_message_id': task.get('owner_message_id'),
                                  'origin': 'coordination' if task.get('execution_kind') == 'peer_message' else 'delegated' if task.get('parent_id') else 'message'}
        entry.update(message=message, thread_id=thread, tasks=sorted(owner_tasks.get(message['id'], []), key=lambda t: (t['created_at'], str(t['id']))) if message['role'] == 'user' else [])
        entry['cause_message_id'] = owner_by_task.get(message['task_id'])
        result[identity] = entry
    for entry in responses.values():
        if entry['id'] not in result:
            entry['cause_message_id'] = owner_by_task.get(entry['task_id'])
            entry['thread_id'] = answer_threads.get(entry['cause_message_id'], entry['thread_id'])
            result[entry['id']] = entry
    for kind, records in (('question', questions), ('review', approvals)):
        for request in records:
            task = by_task[request['task_id']]
            identity = f"{kind}:{request['id']}"
            result[identity] = {'id': identity, 'thread_id': task['reply_to_message_id'],
                                'occurred_at': request['created_at'], 'task_id': task['id'],
                                'cause_message_id': task['owner_message_id'], 'author': task['bot_id'],
                                kind: request, 'tasks': []}
            if kind == 'question' and request['answer'] is not None and not request['answer_message_id']:
                answer_id = f"answer:{request['id']}"
                result[answer_id] = {'id': answer_id, 'thread_id': task['reply_to_message_id'],
                                     'occurred_at': request['answered_at'] or request['created_at'],
                                     'task_id': task['id'], 'cause_question_id': request['id'],
                                     'legacy_answer': request['answer'], 'tasks': []}
    return result


def fingerprint(entry):
    return hashlib.sha256(json.dumps(jsonable_encoder(entry), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def publish(conn):
    # Trigger staging participates in savepoints; publication is the final lock phase.
    if not conn.execute("SELECT to_regclass('timeline_dirty') AS table_name").fetchone()['table_name']:
        return
    dirty = conn.execute('SELECT conversation_id,source FROM timeline_dirty WHERE transaction_id=txid_current_if_assigned() ORDER BY conversation_id,source').fetchall()
    conversations = sorted({row['conversation_id'] for row in dirty}, key=str)
    for cid in conversations:
        conn.execute('INSERT INTO timeline_clocks(conversation_id) VALUES(%s) ON CONFLICT DO NOTHING', (cid,))
        conn.execute('SELECT version FROM timeline_clocks WHERE conversation_id=%s FOR UPDATE', (cid,))
    for cid in conversations:
        full = any(row['source'] == '*' for row in dirty if row['conversation_id'] == cid)
        if full:
            candidates = collect(conn, cid)
        else:
            candidates = {}
            for row in dirty:
                if row['conversation_id'] == cid:
                    entry = response(conn, row['source'])
                    if entry:
                        candidates[entry['id']] = entry
        existing = {row['id']: row for row in conn.execute('SELECT * FROM timeline_entries WHERE conversation_id=%s', (cid,)).fetchall()}
        version = conn.execute('SELECT version FROM timeline_clocks WHERE conversation_id=%s', (cid,)).fetchone()['version']
        ordered = sorted(candidates.values(), key=lambda e: (e['occurred_at'], e.get('message', {}).get('id', 0) if e.get('message') else 0, e['id']))
        for entry in ordered:
            previous = existing.get(entry['id'])
            signature = fingerprint(entry)
            if previous and not previous['deleted'] and previous['signature'] == signature:
                continue
            version += 1
            conn.execute('''INSERT INTO timeline_entries(conversation_id,id,position,revision,thread_id,occurred_at,signature)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(conversation_id,id) DO UPDATE SET
                revision=excluded.revision,thread_id=excluded.thread_id,signature=excluded.signature,deleted=false''',
                (cid, entry['id'], previous['position'] if previous else version, version, entry['thread_id'], entry['occurred_at'], signature))
        if full:
            for identity, previous in existing.items():
                if identity not in candidates and not previous['deleted']:
                    version += 1
                    conn.execute('UPDATE timeline_entries SET deleted=true,revision=%s WHERE conversation_id=%s AND id=%s', (version, cid, identity))
        conn.execute('UPDATE timeline_clocks SET version=%s WHERE conversation_id=%s', (version, cid))
    if dirty:
        conn.execute('DELETE FROM timeline_dirty WHERE transaction_id=txid_current()', ())


def read(conn, conversation_id, *, before=None, after=None, thread=None, limit=100):
    if before is not None and after is not None:
        raise HTTPException(422, 'Choose history pagination or change catchup')
    if before is not None and before < 1 or after is not None and after < 0:
        raise HTTPException(422, 'Invalid timeline cursor')
    if not conn.execute('SELECT 1 FROM conversations WHERE id=%s', (conversation_id,)).fetchone():
        raise HTTPException(404, 'Conversation not found')
    if thread is not None and not conn.execute('SELECT 1 FROM messages WHERE id=%s AND conversation_id=%s AND (reply_to_message_id IS NULL OR reply_to_message_id=id)', (thread, conversation_id)).fetchone():
        raise HTTPException(404, 'Thread root not found in this conversation')
    sources = collect(conn, conversation_id)
    actions = {}
    for source in sources.values():
        for kind in ('question', 'review'):
            if source.get(kind):
                actions[source['id']] = owner_requests.valid(conn, source[kind], conn.execute('SELECT * FROM tasks WHERE id=%s', (source['task_id'],)).fetchone(), review=kind == 'review')
    root_entry = next((e['id'] for e in sources.values() if e.get('message') and e['message']['id'] == thread), None) if thread else None
    clock = conn.execute('SELECT version FROM timeline_clocks WHERE conversation_id=%s', (conversation_id,)).fetchone()
    watermark = clock['version'] if clock else 0
    if after is not None and after > watermark:
        raise HTTPException(409, 'Timeline cursor is ahead of this conversation; reload it')
    if after is not None:
        # Include changes outside this thread as tombstones, so a moved entry is removed.
        rows = conn.execute('SELECT * FROM timeline_entries WHERE conversation_id=%s AND revision>%s ORDER BY revision LIMIT %s', (conversation_id, after, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        cursor = rows[-1]['revision'] if more else watermark
    else:
        rows = conn.execute('''SELECT * FROM timeline_entries WHERE conversation_id=%s AND NOT deleted
            AND (%s::bigint IS NULL OR position<%s) AND (thread_id IS NOT DISTINCT FROM %s::bigint OR id=%s)
            ORDER BY position DESC LIMIT %s''', (conversation_id, before, before, thread, root_entry, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        cursor = watermark
    entries = []
    for row in rows:
        source = sources.get(row['id'])
        in_scope = row['thread_id'] == thread or row['id'] == root_entry
        if row['deleted'] or not source or not in_scope:
            entries.append({'id': row['id'], 'position': row['position'], 'revision': row['revision'], 'deleted': True})
            continue
        entry = {**source, 'position': row['position'], 'revision': row['revision'], 'occurred_at': row['occurred_at']}
        if row['id'] in actions:
            entry['actionable'] = actions[row['id']]
        entries.append(entry)
    return {'entries': entries, 'cursor': cursor, 'actions': actions, 'has_more': more if after is not None else False,
            'has_earlier': more if after is None else False,
            'artifacts': conn.execute('SELECT a.id,a.task_id,a.bot_id,a.name,a.content_type,a.sha256,a.size,a.created_at FROM artifacts a LEFT JOIN tasks t ON t.id=a.task_id WHERE COALESCE(a.conversation_id,t.conversation_id)=%s ORDER BY a.created_at DESC', (conversation_id,)).fetchall(),
            'attachments': conn.execute('SELECT id,name,content_type,size,sha256 FROM attachments WHERE conversation_id=%s ORDER BY created_at', (conversation_id,)).fetchall()}
