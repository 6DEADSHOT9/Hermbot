"""Account-scoped references resolved by application code, never by fuzzy UUID repair."""
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb


def fail(code, message, recovery, *, status=422):
    raise HTTPException(status, {'code': code, 'message': message, 'recovery': recovery})


def resolve_connection(conn, task, args):
    from .connections import authorize
    if args.get('connection_id'):
        connection = conn.execute('SELECT * FROM connections WHERE id=%s', (args['connection_id'],)).fetchone()
        if not connection:
            fail('invalid_connection_reference', 'The connection reference is unknown.', 'Discover available connections and use their name or returned reference.')
        authorize(connection, task['bot_id'], args['action'])
        return connection
    available = conn.execute("SELECT * FROM connections WHERE bot_ids ? %s AND status='connected' AND actions ? %s", (task['bot_id'], args['action'])).fetchall()
    name = args.get('connection', '').strip()
    if name:
        available = [c for c in available if c['name'].casefold() == name.casefold()]
    if not available:
        fail('connection_access_missing', 'No authorized connection matches this operation.', 'Inspect available actions and account access; use a capable authorized teammate or connect the required account.', status=403)
    if len(available) != 1:
        fail('ambiguous_connection', 'Several authorized accounts match this operation.', 'Select an exact connection name from discovery; clarify the account with the owner when necessary.')
    return available[0]


def remember(conn, task, connection, action, result):
    entries = []
    if action == 'slack.members':
        for user in result.get('members', []):
            profile = user.get('profile', {})
            labels = list(dict.fromkeys(label for label in (profile.get('display_name'), profile.get('real_name'), user.get('real_name'), user.get('name'), profile.get('email')) if isinstance(label, str) and label.strip()))
            entries.append(('channel', user.get('id'), labels))
    elif action == 'slack.channels':
        entries = [('channel', c.get('id'), [c.get('name', '')]) for c in result.get('channels', [])]
    elif action == 'drive.search':
        entries = [('file_id', f.get('id'), [f.get('name', '')]) for f in result.get('files', [])]
    elif action == 'calendar.list':
        entries = [('calendar_id', c.get('id'), [c.get('summary', '')]) for c in result.get('items', [])]
    elif action == 'github.repositories':
        entries = [('repo', r.get('full_name'), [r.get('full_name', '')]) for r in result if isinstance(r, dict)]
    references = []
    for kind, resource, labels in entries:
        if not resource or not labels:
            continue
        identity = uuid4()
        labels = [label.strip() for label in labels if label.strip()]
        conn.execute('INSERT INTO connector_references(id,connection_id,connection_revision,work_item_id,task_id,kind,resource,label,data) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                     (identity, connection['id'], connection['revision'], task['work_item_id'], task['id'], kind, resource, labels[0], Jsonb({'labels': labels})))
        references.append({'reference': 'ref:' + str(identity), 'kind': kind, 'label': labels[0], 'labels': labels})
    return references


def resolve_arguments(conn, task, connection, arguments):
    result = dict(arguments)
    for field, value in result.items():
        if not isinstance(value, str) or not value.startswith('ref:'):
            continue
        try:
            identity = UUID(value[4:])
        except ValueError:
            fail('invalid_resource_reference', 'This resource reference is malformed.', 'Rediscover the resource and use a returned reference.')
        reference = conn.execute("SELECT * FROM connector_references WHERE id=%s AND connection_id=%s AND connection_revision=%s AND ((work_item_id=%s AND work_item_id IS NOT NULL) OR task_id=%s) AND created_at>now()-interval '24 hours'",
                     (identity, connection['id'], connection['revision'], task['work_item_id'], task['id'])).fetchone()
        if not reference or reference['kind'] != field:
            fail('stale_resource_reference', 'This reference is not valid for the account, work, or argument.', 'Rediscover this resource in the selected account.')
        result[field] = reference['resource']
    return result


def requested_authorization(conn, task, outcome_id, connection, action, resource, *, authorization_id=None):
    if task.get('orchestration_version') != 2 or action.startswith('mcp.'):
        return None
    mode = conn.execute('SELECT external_action_mode FROM workspace_settings').fetchone()['external_action_mode']
    if mode != 'requested':
        return None
    scopes = conn.execute('SELECT a.* FROM work_authorizations a JOIN work_outcomes o ON o.id=a.outcome_id AND o.revision=a.outcome_revision WHERE a.work_item_id=%s AND a.outcome_id=%s AND a.action=%s AND a.revoked_at IS NULL',
                          (task['work_item_id'], outcome_id, action)).fetchall()
    for scope in scopes:
        if authorization_id and scope['id'] != authorization_id:
            continue
        if scope['resource'] and scope['resource'] == resource:
            return scope
        if not scope['resource_label']:
            continue
        rows = conn.execute("SELECT resource,data FROM connector_references WHERE connection_id=%s AND connection_revision=%s AND work_item_id=%s AND created_at>now()-interval '24 hours'", (connection['id'], connection['revision'], task['work_item_id'])).fetchall()
        matching = {r['resource'] for r in rows if scope['resource_label'].casefold() in {label.casefold() for label in r['data'].get('labels', [])}}
        if matching == {resource}:
            return scope
    return None
