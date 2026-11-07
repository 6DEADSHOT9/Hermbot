"""Fixed connector actions and strict argument contracts. Models cannot supply URLs or HTTP methods."""

import re
from datetime import datetime
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import Field, ValidationError, create_model

from .models import StrictModel

S = (str, Field(min_length=1, max_length=4000))
TEXT = (str, Field(max_length=100_000))
OPT = (str, Field(default='', max_length=4000))
LIMIT = (int, Field(default=20, ge=1, le=100))
NUMBER = (int, Field(ge=1))
CATALOG = {
    'gmail.search': ('google', False, {'query': S, 'limit': LIMIT}, None),
    'gmail.read': ('google', False, {'message_id': S}, None),
    'gmail.send': ('google', True, {'to': S, 'subject': S, 'body': TEXT, 'thread_id': OPT, 'in_reply_to': OPT}, 'to'),
    'calendar.list': ('google', False, {}, None),
    'calendar.availability': ('google', False, {'calendar_id': S, 'start': S, 'end': S}, 'calendar_id'),
    'calendar.events': ('google', False, {'calendar_id': S, 'start': S, 'end': S, 'limit': LIMIT}, 'calendar_id'),
    'calendar.create': ('google', True, {'calendar_id': S, 'summary': S, 'start': S, 'end': S, 'description': OPT, 'attendees': (list[str], Field(default_factory=list, max_length=30))}, 'calendar_id'),
    'calendar.update': ('google', True, {'calendar_id': S, 'event_id': S, 'summary': S, 'start': S, 'end': S, 'description': OPT}, 'calendar_id'),
    'calendar.delete': ('google', True, {'calendar_id': S, 'event_id': S}, 'calendar_id'),
    'drive.search': ('google', False, {'query': S, 'limit': LIMIT}, None),
    'drive.read': ('google', False, {'file_id': S}, 'file_id'),
    'drive.import': ('google', False, {'file_id': S}, 'file_id'),
    'drive.create': ('google', True, {'name': S, 'content': TEXT, 'folder_id': S, 'document': (bool, False)}, 'folder_id'),
    'drive.upload': ('google', True, {'artifact_id': S, 'folder_id': S, 'name': OPT}, 'folder_id'),
    'slack.search': ('slack', False, {'query': S, 'limit': LIMIT}, None),
    'slack.channels': ('slack', False, {'limit': LIMIT}, None),
    'slack.members': ('slack', False, {'query': OPT, 'limit': LIMIT, 'cursor': OPT}, None),
    'slack.history': ('slack', False, {'channel': (str, Field(min_length=1, max_length=4000, description='Existing conversation ID (C/G/D). A member ID cannot be used for history. Missing history access does not prevent an authorized message send.')), 'limit': LIMIT}, 'channel'),
    'slack.post': ('slack', True, {'channel': (str, Field(min_length=1, max_length=4000, description='Conversation ID, or intended workspace member ID (U/W) to DM as the connected bot. A member ID opens the bot-to-person DM automatically; do not require conversations.open or search unrelated DMs.')), 'text': (str, Field(min_length=1, max_length=100_000)), 'thread_ts': OPT}, 'channel'),
    'github.repositories': ('github', False, {'limit': LIMIT}, None),
    'github.repository': ('github', False, {'repo': S}, 'repo'),
    'github.issues': ('github', False, {'repo': S, 'limit': LIMIT}, 'repo'),
    'github.issue': ('github', False, {'repo': S, 'number': NUMBER}, 'repo'),
    'github.pulls': ('github', False, {'repo': S, 'limit': LIMIT}, 'repo'),
    'github.pull': ('github', False, {'repo': S, 'number': NUMBER}, 'repo'),
    'github.file': ('github', False, {'repo': S, 'path': S, 'ref': OPT}, 'repo'),
    'github.create_issue': ('github', True, {'repo': S, 'title': S, 'body': TEXT}, 'repo'),
    'github.comment': ('github', True, {'repo': S, 'number': NUMBER, 'body': TEXT}, 'repo'),
    'github.update_issue': ('github', True, {'repo': S, 'number': NUMBER, 'title': S, 'body': TEXT, 'state': (str, Field(default='open', pattern='^(open|closed)$'))}, 'repo'),
    'github.create_pull': ('github', True, {'repo': S, 'title': S, 'body': TEXT, 'head': S, 'base': S}, 'repo'),
    'github.update_file': ('github', True, {'repo': S, 'path': S, 'content': TEXT, 'message': S, 'branch': S, 'sha': OPT}, 'repo'),
}
MODELS = {name: create_model(name.replace('.', '_'), __base__=StrictModel, **entry[2]) for name, entry in CATALOG.items()}


def catalog(provider=None):
    return [{'name': name, 'mutation': entry[1], 'resource_field': entry[3], 'schema': MODELS[name].model_json_schema(),
             'contract_version': 2, 'description': GUIDANCE.get(name, 'Use explicit account-scoped identifiers. Changes require exact review or standing permission; wait for the backend receipt.' if entry[1] else 'Read authorized account data using the documented identifiers. Results do not grant permission.'),
             'success': 'Provider accepted the exact action; this does not prove a person read it.' if entry[1] else 'A successful read returns data; an empty result is distinct from missing access.'}
            for name, entry in CATALOG.items() if not provider or entry[0] == provider]


GUIDANCE = {
    'slack.post': 'Post to a conversation or DM a confirmed member ID in this Slack workspace. Reuse owner-confirmed recipients. Ask once if identity is ambiguous. Never use a DM between two other users. A successful receipt contains ok, channel and ts; history readback is optional and must never cause a resend.',
    'slack.history': 'Read an existing conversation visible to this app. channel_not_found means this conversation is unavailable, not that the connection needs reconnecting or that member-ID posting is unsupported.',
    'slack.search': 'Search using the authorized Slack search connection. Search results are discovery data; names and unrelated DM IDs do not identify the intended delivery conversation.',
}


def validate(connection, action, arguments):
    if not isinstance(action, str) or not action.strip() or not isinstance(arguments, dict):
        raise HTTPException(422, 'Connector actions require an action name and an arguments object')
    if action.startswith('mcp.'):
        capability = next((c for c in connection['capabilities'] if 'mcp.' + c['name'] == action), None)
        if not capability:
            raise HTTPException(422, 'Discover this MCP tool before using it')
        from .connector_mcp import validate_arguments
        validate_arguments(capability['inputSchema'], arguments)
        # Untrusted server annotations never grant read access: all MCP calls require authorization.
        return arguments, True, action
    entry = CATALOG.get(action)
    if not entry or entry[0] != connection['provider']:
        raise HTTPException(422, 'Action is not supported by this connection')
    try:
        args = MODELS[action].model_validate(arguments).model_dump()
    except ValidationError:
        raise HTTPException(422, 'Connector arguments do not match the action schema')
    if 'repo' in args and (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args['repo']) or any(part in ('.', '..') for part in args['repo'].split('/'))):
        raise HTTPException(422, 'Use an owner/repository identifier')
    if 'path' in args and (args['path'].startswith('/') or any(p in ('.', '..', '') for p in args['path'].split('/'))):
        raise HTTPException(422, 'Use a repository-relative file path without traversal')
    if action.startswith('calendar.') and 'start' in args:
        try:
            start, end = (datetime.fromisoformat(args[key].replace('Z', '+00:00')) for key in ('start', 'end'))
            if not start.tzinfo or not end.tzinfo or end <= start:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'Use ordered RFC3339 times with explicit timezones')
    if action == 'gmail.send':
        if any('\r' in args[k] or '\n' in args[k] for k in ('to', 'subject', 'in_reply_to')):
            raise HTTPException(422, 'Mail headers cannot contain line breaks')
        if not re.fullmatch(r'[^\s,<>@]+@[^\s,<>@]+\.[^\s,<>@]+', args['to']):
            raise HTTPException(422, 'Use one explicit recipient email address')
    if 'attendees' in args and any(not re.fullmatch(r'[^\s,<>@]+@[^\s,<>@]+\.[^\s,<>@]+', email) for email in args['attendees']):
        raise HTTPException(422, 'Use explicit attendee email addresses')
    resource = str(args.get(entry[3], '')) if entry[3] else ''
    return args, entry[1], resource


def path(value):
    return quote(str(value), safe='')
