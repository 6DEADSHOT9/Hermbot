"""Fixed provider endpoints and bounded event normalization for Phase 5.2."""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re

from fastapi import HTTPException

from . import service
from .connector_catalog import path
from .connector_http import ProviderFailure, request
from .trigger_models import EventData


class IdentityMismatch(ValueError):
    pass


def time(value, default=None):
    if not value:
        return default or service.now()
    if isinstance(value, (int, float)) or re.fullmatch(r'\d+(?:\.\d+)?', str(value)):
        return datetime.fromtimestamp(float(value), UTC)
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Provider timestamp has no timezone')
    return result


def github_version(item):
    fields = {key: item.get(key) for key in ('id', 'updated_at', 'title', 'body', 'state', 'draft', 'merged', 'merge_commit_sha')}
    fields['head_sha'] = (item.get('head') or {}).get('sha')
    fields['labels'] = sorted(label['name'] for label in item.get('labels', []) if isinstance(label, dict) and isinstance(label.get('name'), str))
    return service.digest(fields)


def calendar_version(item):
    return item.get('etag') or service.digest({key: item.get(key) for key in ('id', 'status', 'updated', 'recurringEventId', 'originalStartTime')})


def drive_state(item):
    value = item.get('file') or {}
    if not value.get('modifiedTime'):
        return None
    fields = {key: value.get(key) for key in ('name', 'mimeType', 'modifiedTime', 'trashed')}
    fields['parents'] = sorted(value.get('parents', []))
    fields['removed'] = bool(item.get('removed'))
    return service.digest(fields)


def github_event(src, kind, item, event_type, *, number=None):
    number = number or item.get('number')
    if not number:
        match = re.search(r'/issues/(\d+)$', item.get('issue_url', ''))
        number = int(match[1]) if match else None
    if not item.get('id') or not number:
        raise ValueError('GitHub resource identity missing')
    version = github_version(item)
    resource = f"{src['resource']}#{number}" + (f"/comment/{item['id']}" if kind == 'comment' else '')
    text = str(item.get('title') or '') + '\n' + str(item.get('body') or '')
    return EventData(key=f"github:{kind}:{item['id']}:{version}", event_type=event_type,
        resource=resource, resource_version=version, actor=str((item.get('user') or {}).get('id', '')),
        occurred_at=time(item.get('updated_at') or item.get('created_at')), text=text[:24000],
        summary=f"{src['resource']} #{number} · {str(item.get('title') or 'New comment')[:200]}",
        labels=[v['name'][:500] for v in item.get('labels', []) if isinstance(v, dict) and isinstance(v.get('name'), str)][:100],
        is_bot=(item.get('user') or {}).get('type') == 'Bot',
        metadata={'object_id': str(item['id']), 'object_kind': kind, 'number': number,
                  'url': str(item.get('html_url', ''))[:1000], 'head_sha': str((item.get('head') or {}).get('sha', ''))[:100],
                  'text_truncated': len(text) > 24000})


def normalize_http(src, headers, payload):
    if not isinstance(payload, dict):
        raise ValueError('Expected a JSON object')
    provider = src['provider']
    if provider == 'slack':
        if payload.get('type') != 'event_callback':
            return None
        if payload.get('team_id') != src['remote_identity'].get('team_id'):
            raise IdentityMismatch('Slack workspace identity does not match')
        app_id = src['remote_identity'].get('app_id')
        if app_id and payload.get('api_app_id') != app_id:
            raise IdentityMismatch('Slack app identity does not match')
        item = payload.get('event') or {}
        if item.get('type') not in ('message', 'app_mention') or item.get('subtype') not in (None, 'bot_message', 'file_share', 'thread_broadcast'):
            return None
        if not all(isinstance(item.get(key), str) and item[key] for key in ('channel', 'ts')) or not payload.get('event_id'):
            raise ValueError('Slack event identity missing')
        event_type = 'slack.app_mention' if item['type'] == 'app_mention' else 'slack.message.created'
        text = str(item.get('text', ''))
        return EventData(key='slack:' + str(payload['event_id']), event_type=event_type,
            resource=item['channel'], resource_version=item['ts'], actor=str(item.get('user') or item.get('bot_id') or ''),
            occurred_at=time(item.get('event_ts') or item['ts']), text=text[:24000], summary=f"Slack message in {item['channel']}",
            is_bot=bool(item.get('bot_id') or item.get('subtype') == 'bot_message'),
            metadata={'message_ts': item['ts'], 'thread_ts': str(item.get('thread_ts', '')),
                      'message_key': f"slack-message:{item['channel']}:{item['ts']}", 'text_truncated': len(text) > 24000})
    if provider == 'github':
        repo = payload.get('repository') or {}
        if str(repo.get('full_name', '')).casefold() != src['resource'].casefold():
            raise IdentityMismatch('GitHub repository identity does not match')
        expected = src['remote_identity'].get('repository_id')
        if expected and str(repo.get('id')) != expected:
            raise IdentityMismatch('GitHub repository identity changed')
        event, action = headers.get('x-github-event'), payload.get('action')
        if event == 'issues' and action in ('opened', 'edited', 'closed', 'reopened', 'labeled', 'unlabeled', 'assigned', 'unassigned'):
            return github_event(src, 'issue', payload['issue'], 'github.issue.opened' if action == 'opened' else 'github.issue.updated')
        if event == 'issue_comment' and action == 'created':
            return github_event(src, 'comment', payload['comment'], 'github.comment.created', number=payload['issue']['number'])
        if event == 'pull_request' and action in ('opened', 'synchronize', 'edited', 'closed', 'reopened', 'ready_for_review', 'converted_to_draft', 'labeled', 'unlabeled'):
            return github_event(src, 'pull', payload['pull_request'], 'github.pull.opened' if action == 'opened' else 'github.pull.updated')
        return None
    if provider == 'webhook':
        custom_type = payload['type']
        if not isinstance(custom_type, str) or not 1 <= len(custom_type) <= 100:
            raise ValueError('Supply a bounded event type')
        return EventData(key='custom:' + headers['webhook-id'], event_type='custom.event',
            resource=payload.get('resource', ''), resource_version=payload.get('version', ''), actor=payload.get('actor', ''),
            occurred_at=time(payload.get('occurred_at')), text=payload.get('text', ''),
            summary=payload.get('summary', custom_type), custom_type=custom_type, metadata={'data': payload.get('data', {})})
    raise ValueError('This source does not receive HTTP events')


def probe(src, credential):
    token = credential.get('access_token')
    provider = src['provider']
    if provider == 'webhook':
        return {}
    if provider == 'slack':
        result = request('POST', 'https://slack.com/api/auth.test', token=token)
        if not result.get('team_id') or not result.get('user_id') or not result.get('bot_id'):
            raise ProviderFailure('Use a Slack bot connection for events; a user search token is not a bot identity', auth=True)
        return {'team_id': result['team_id'], 'bot_user_id': result['user_id'], 'bot_id': result.get('bot_id', ''),
                **({'app_id': src['remote_identity']['app_id']} if src['remote_identity'].get('app_id') else {})}
    if provider == 'github':
        account = request('GET', 'https://api.github.com/user', token=token)
        repo = request('GET', 'https://api.github.com/repos/' + '/'.join(path(p) for p in src['resource'].split('/')), token=token)
        return {'account_id': str(account['id']), 'repository_id': str(repo['id'])}
    if provider == 'google_drive':
        account = request('GET', 'https://www.googleapis.com/drive/v3/about', token=token, params={'fields': 'user(permissionId,emailAddress)'})['user']
        root = request('GET', 'https://www.googleapis.com/drive/v3/files/root', token=token, params={'fields': 'id'})
        return {'account_id': str(account['permissionId']), 'email': str(account.get('emailAddress', '')), 'root_id': root['id']}
    if provider == 'google_calendar':
        result = request('GET', 'https://www.googleapis.com/calendar/v3/calendars/' + path(src['resource']), token=token)
        return {'calendar_id': result['id']}
    result = request('GET', 'https://gmail.googleapis.com/gmail/v1/users/me/profile', token=token)
    return {'email': result['emailAddress']}


@dataclass
class Page:
    items: list = field(default_factory=list)
    cursor: dict | None = None
    progress: dict | None = None
    baseline: bool = False
    complete: bool = True
    poll_after_seconds: int = 0


def fetch_page(src, credential, cursor, progress):
    token = credential['access_token']
    provider = src['provider']
    if provider == 'google_drive':
        root = 'https://www.googleapis.com/drive/v3/changes'
        if cursor is None:
            result = request('GET', root + '/startPageToken', token=token, params={'supportsAllDrives': 'true'})
            return Page(cursor={'page_token': result['startPageToken']}, baseline=True)
        # At most ten initial text exports keep a page within the collector lease.
        result = request('GET', root, token=token, params={'pageToken': cursor['page_token'], 'pageSize': 10,
            'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true',
            'fields': 'nextPageToken,newStartPageToken,changes(fileId,removed,time,file(id,name,mimeType,parents,version,modifiedTime,trashed,appProperties))'})
        next_token = result.get('nextPageToken') or result.get('newStartPageToken')
        if not next_token:
            raise ValueError('Drive change cursor missing')
        return Page(items=result.get('changes', []), cursor={'page_token': next_token}, complete=not bool(result.get('nextPageToken')))
    if provider == 'google_calendar':
        state = progress or {'sync_token': (cursor or {}).get('sync_token'), 'baseline': cursor is None}
        params = {'maxResults': 100, 'singleEvents': 'false', 'showDeleted': 'true'}
        if state.get('sync_token'):
            params['syncToken'] = state['sync_token']
        if state.get('page_token'):
            params['pageToken'] = state['page_token']
        result, _, status = request('GET', 'https://www.googleapis.com/calendar/v3/calendars/' + path(src['resource']) + '/events',
            token=token, params=params, response_metadata=True, accepted_statuses=(410,))
        if status == 410:
            raise ProviderFailure('Calendar history expired or access changed; explicitly rebuild the listening baseline', status=410)
        if result.get('nextPageToken'):
            return Page(items=result.get('items', []), cursor=cursor, progress={**state, 'page_token': result['nextPageToken']}, baseline=state['baseline'], complete=False)
        if not result.get('nextSyncToken'):
            raise ValueError('Calendar synchronization cursor missing')
        return Page(items=result.get('items', []), cursor={'sync_token': result['nextSyncToken']}, baseline=state['baseline'])
    if provider == 'gmail':
        root = 'https://gmail.googleapis.com/gmail/v1/users/me'
        if cursor is None:
            result = request('GET', root + '/profile', token=token)
            return Page(cursor={'history_id': str(result['historyId'])}, baseline=True)
        state = progress or {'history_id': cursor['history_id']}
        params = {'startHistoryId': state['history_id'], 'historyTypes': 'messageAdded', 'maxResults': 50}
        if state.get('page_token'):
            params['pageToken'] = state['page_token']
        result, _, status = request('GET', root + '/history', token=token, params=params, response_metadata=True, accepted_statuses=(404,))
        if status == 404:
            raise ProviderFailure('Gmail history is unavailable; explicitly rebuild the listening baseline', status=404)
        messages = {}
        for record in result.get('history', []):
            for entry in record.get('messagesAdded', []):
                messages[entry['message']['id']] = entry['message']
        # Bounded enrichment is its own page; do not advance history past messages not yet recorded.
        pending = list(messages.values())
        offset = int(state.get('offset', 0))
        items = []
        for entry in pending[offset:offset + 10]:
            message, _, message_status = request('GET', root + '/messages/' + path(entry['id']), token=token,
                params={'format': 'metadata'}, response_metadata=True, accepted_statuses=(404,))
            if message_status != 404:
                items.append({**message, 'history_labels': entry.get('labelIds', message.get('labelIds', []))})
            else:
                items.append({'id': entry['id'], 'history_labels': entry.get('labelIds', []), 'unavailable': True})
        if len(pending) > offset + 10:
            return Page(items=items, cursor=cursor, progress={**state, 'offset': offset + 10}, complete=False)
        if result.get('nextPageToken'):
            return Page(items=items, cursor=cursor, progress={'history_id': state['history_id'], 'page_token': result['nextPageToken']}, complete=False)
        return Page(items=items, cursor={'history_id': str(result['historyId'])})
    if provider == 'github':
        if cursor is None:
            return Page(cursor={'since': service.now().isoformat()}, baseline=True)
        root = 'https://api.github.com/repos/' + '/'.join(path(p) for p in src['resource'].split('/'))
        if progress:
            state = progress
        else:
            etags, unchanged, interval = {}, 0, 0
            for kind, endpoint in (('issues', '/issues'), ('comments', '/issues/comments')):
                previous = cursor.get('head_etags', {}).get(kind)
                _, metadata, status = request('GET', root + endpoint, token=token,
                    params={'sort': 'updated', 'direction': 'desc', 'per_page': 1, **({'state': 'all'} if kind == 'issues' else {})},
                    headers={'X-GitHub-Api-Version': '2022-11-28', **({'If-None-Match': previous} if previous else {})},
                    response_metadata=True, accepted_statuses=(304,))
                unchanged += status == 304
                if metadata.get('etag'):
                    etags[kind] = metadata['etag']
                try:
                    interval = max(interval, min(3600, int(metadata.get('x-poll-interval', '0'))))
                except ValueError:
                    pass
            if unchanged == 2:
                return Page(cursor=cursor, poll_after_seconds=interval)
            state = {'since': cursor['since'], 'until': service.now().isoformat(), 'phase': 'issues', 'page': 1, 'head_etags': etags}
        since = time(state['since']) - timedelta(seconds=120)
        endpoint = '/issues' if state['phase'] == 'issues' else '/issues/comments'
        params = {'since': since.isoformat(), 'sort': 'updated', 'direction': 'asc', 'per_page': 10, 'page': state['page']}
        if state['phase'] == 'issues':
            params['state'] = 'all'
        response, metadata, status = request('GET', root + endpoint, token=token, params=params,
            headers={'X-GitHub-Api-Version': '2022-11-28'}, response_metadata=True)
        items = []
        if status != 304:
            if not isinstance(response, list):
                raise ValueError('GitHub list response missing')
            for item in response:
                kind = 'comment' if state['phase'] == 'comments' else 'pull' if 'pull_request' in item else 'issue'
                if kind == 'pull':
                    item = request('GET', root + '/pulls/' + str(item['number']), token=token)
                items.append({'kind': kind, 'item': item})
        has_next = 'rel="next"' in metadata.get('link', '')
        if has_next:
            return Page(items=items, cursor=cursor, progress={**state, 'page': state['page'] + 1}, complete=False)
        if state['phase'] == 'issues':
            return Page(items=items, cursor=cursor, progress={**state, 'phase': 'comments', 'page': 1}, complete=False)
        # The cycle's start is the high-water mark; later updates remain eligible on the next poll.
        return Page(items=items, cursor={'since': state['until'], 'head_etags': state['head_etags']})
    raise ValueError('This source is not polled')


def normalize_poll(src, item, known):
    provider = src['provider']
    if provider == 'github':
        kind, value = item['kind'], item['item']
        if kind == 'comment':
            if time(value['created_at']) < src['initialized_at'] or known:
                return None
            event_type = 'github.comment.created'
        else:
            fresh = not known and time(value['created_at']) >= src['initialized_at']
            event_type = 'github.' + kind + ('.opened' if fresh else '.updated')
        return github_event(src, kind, value, event_type)
    if provider == 'google_drive':
        value = item.get('file') or {}
        state = drive_state(item)
        if state and known and state == known['metadata'].get('file_state'):
            return None  # Drive versions also advance for invisible server-side changes.
        resource = item['fileId']
        removed = bool(item.get('removed') or value.get('trashed'))
        version = str(value.get('version') or item.get('time') or '')
        if not version:
            raise ValueError('Drive resource version missing')
        parents = value.get('parents', [])
        if src['remote_identity'].get('root_id') in parents:
            parents = [*parents, 'root']
        return EventData(key='drive:' + service.digest({'id': resource, 'version': version, 'removed': removed}),
            event_type='drive.file.removed' if removed else 'drive.file.changed', resource=resource, resource_version=version,
            occurred_at=time(item.get('time') or value.get('modifiedTime')), summary=('Removed: ' if removed else 'Changed: ') + str(value.get('name') or resource)[:450],
            text=str(value.get('name', ''))[:1000], parent_ids=parents[:10], mime_type=value.get('mimeType', ''),
            metadata={'content_included': False, 'removed_or_inaccessible': bool(item.get('removed')), 'file_state': state,
                      'creation_operation_id': item.get('_hermbot_creation_operation_id')})
    if provider == 'google_calendar':
        event_type = 'calendar.event.cancelled' if item.get('status') == 'cancelled' else 'calendar.event.updated' if known else 'calendar.event.created'
        version = calendar_version(item)
        text = json.dumps({key: item.get(key) for key in ('summary', 'description', 'location', 'start', 'end', 'recurrence', 'recurringEventId', 'originalStartTime')}, ensure_ascii=False)
        return EventData(key='calendar:' + service.digest({'id': item['id'], 'etag': version}), event_type=event_type,
            resource=item['id'], resource_version=version, occurred_at=time(item.get('updated')),
            actor=str((item.get('creator') or {}).get('email', ''))[:300], summary=str(item.get('summary') or 'Calendar cancellation')[:500], text=text[:24000],
            metadata={'calendar_id': src['resource'], 'recurring_event_id': item.get('recurringEventId'), 'text_truncated': len(text) > 24000})
    headers = {str(h.get('name', '')).casefold(): str(h.get('value', '')) for h in item.get('payload', {}).get('headers', [])}
    return EventData(key='gmail:added:' + item['id'], event_type='gmail.message.added', resource=item['id'],
        resource_version=item['id'], occurred_at=service.now(), actor=headers.get('from', '')[:300],
        summary='Email unavailable before collection' if item.get('unavailable') else headers.get('subject', 'New email')[:500] or 'New email',
        text=(headers.get('subject', '') + '\n' + headers.get('from', '') + '\n' + str(item.get('snippet', '')))[:24000],
        labels=item.get('history_labels', item.get('labelIds', []))[:100],
        metadata={'thread_id': item.get('threadId'), 'message_id': headers.get('message-id', '')[:500], 'body_included': False,
                  'unavailable': bool(item.get('unavailable')), 'event_time_is_collection_time': True})


def resource_state(src, item):
    if src['provider'] == 'github':
        return item['kind'] + ':' + str(item['item']['id']), github_version(item['item']), {}
    if src['provider'] == 'google_drive':
        value = item.get('file') or {}
        return item['fileId'], str(value.get('version') or item.get('time') or ''), {
            'parent_ids': value.get('parents', [])[:10], 'file_state': drive_state(item),
            'creation_checked': bool(item.get('_hermbot_creation_checked')),
            'creation_operation_id': item.get('_hermbot_creation_operation_id')}
    if src['provider'] == 'google_calendar':
        return item['id'], calendar_version(item), {}
    return item['id'], item['id'], {}


def verify_signature(src, secret, headers, body):
    import base64
    import hmac
    provider = src['provider']
    if not secret:
        return False
    if provider == 'github':
        expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected.encode(), headers.get('x-hub-signature-256', '').encode())
    try:
        if provider == 'slack':
            timestamp = headers['x-slack-request-timestamp']
            if abs(service.now().timestamp() - int(timestamp)) > 300:
                return False
            expected = 'v0=' + hmac.new(secret.encode(), b'v0:' + timestamp.encode() + b':' + body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected.encode(), headers.get('x-slack-signature', '').encode())
        identity, timestamp = headers['webhook-id'], headers['webhook-timestamp']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identity) or abs(service.now().timestamp() - int(timestamp)) > 300:
            return False
        key = base64.b64decode(secret.removeprefix('whsec_'), validate=True)
        signed = identity.encode() + b'.' + timestamp.encode() + b'.' + body
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        return any(hmac.compare_digest(('v1,' + expected).encode(), signature.encode()) for signature in headers.get('webhook-signature', '').split())
    except (ValueError, KeyError, OverflowError):
        return False


def safe_normalize(src, headers, payload):
    try:
        return normalize_http(src, headers, payload)
    except IdentityMismatch:
        raise HTTPException(403, 'Event account or resource does not match this configured source') from None
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise HTTPException(422, 'Authenticated event does not match the supported provider schema; inspect the delivery and source setup') from None
