"""Google, Slack and GitHub adapters using fixed API roots and explicit action payloads."""

import base64
from email.message import EmailMessage
from email.utils import formatdate
import json

from .connector_catalog import path
from .connector_http import request, redact


def execute(connection, credential, action, args, operation_id):
    token = credential['access_token']
    write = action in {
        'gmail.send', 'calendar.create', 'calendar.update', 'calendar.delete', 'drive.create', 'drive.upload',
        'slack.post', 'github.create_issue', 'github.comment', 'github.update_issue',
        'github.create_pull', 'github.update_file',
    }

    def call(method, url, **kwargs):
        headers = {'X-GitHub-Api-Version': '2022-11-28', 'Accept': 'application/vnd.github+json'} if connection['provider'] == 'github' else {}
        return request(method, url, token=token, headers=headers, mutation=write, **kwargs)

    if action.startswith('gmail.'):
        root = 'https://gmail.googleapis.com/gmail/v1/users/me'
        if action == 'gmail.search':
            result = call('GET', root + '/messages', params={'q': args['query'], 'maxResults': args['limit']})
        elif action == 'gmail.read':
            result = call('GET', root + '/messages/' + path(args['message_id']), params={'format': 'full'})
        else:
            message = EmailMessage()
            account = connection.get('account') or request('GET', root + '/profile', token=token)['emailAddress']
            message['From'], message['Date'] = account, formatdate(usegmt=True)
            message['To'], message['Subject'] = args['to'], args['subject']
            # One stable Message-ID helps an owner reconcile ambiguous sends; never auto-replay.
            message['Message-ID'] = f'<hermbot-{operation_id}@local.invalid>'
            if args['in_reply_to']:
                message['In-Reply-To'] = args['in_reply_to']
                message['References'] = args['in_reply_to']
            message.set_content(args['body'])
            body = {'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()}
            if args['thread_id']:
                body['threadId'] = args['thread_id']
            result = call('POST', root + '/messages/send', json=body)
    elif action.startswith('calendar.'):
        root = 'https://www.googleapis.com/calendar/v3'
        if action == 'calendar.list':
            result = call('GET', root + '/users/me/calendarList', params={'maxResults': 100})
        elif action == 'calendar.availability':
            result = call('POST', root + '/freeBusy', json={'timeMin': args['start'], 'timeMax': args['end'], 'items': [{'id': args['calendar_id']}]})
        else:
            endpoint = root + '/calendars/' + path(args['calendar_id']) + '/events'
            if action == 'calendar.events':
                result = call('GET', endpoint, params={'timeMin': args['start'], 'timeMax': args['end'], 'maxResults': args['limit'], 'singleEvents': 'true'})
            elif action == 'calendar.delete':
                result = call('DELETE', endpoint + '/' + path(args['event_id']), params={'sendUpdates': 'all'})
            else:
                body = {'summary': args['summary'], 'description': args['description'], 'start': {'dateTime': args['start']}, 'end': {'dateTime': args['end']}}
                if action == 'calendar.create':
                    body.update(id='h' + str(operation_id).replace('-', ''), attendees=[{'email': email} for email in args['attendees']])
                result = call('POST' if action == 'calendar.create' else 'PATCH', endpoint + (('/' + path(args['event_id'])) if action == 'calendar.update' else ''), params={'sendUpdates': 'all'}, json=body)
    elif action.startswith('drive.'):
        root = 'https://www.googleapis.com/drive/v3/files'
        if action == 'drive.search':
            result = call('GET', root, params={'q': args['query'], 'pageSize': args['limit'], 'fields': 'files(id,name,mimeType,size,webViewLink),nextPageToken'})
        elif action in ('drive.read', 'drive.import'):
            metadata = call('GET', root + '/' + path(args['file_id']), params={'fields': 'id,name,mimeType,size,webViewLink'})
            mime = metadata['mimeType']
            if mime.startswith('application/vnd.google-apps.'):
                export_type = 'text/csv' if mime.endswith('spreadsheet') else 'text/plain'
                content, _ = call('GET', root + '/' + path(args['file_id']) + '/export', params={'mimeType': export_type}, raw=True)
                name = metadata['name'] + ('.csv' if export_type == 'text/csv' else '.txt')
                mime = export_type
            else:
                content, _ = call('GET', root + '/' + path(args['file_id']), params={'alt': 'media'}, raw=True)
                name = metadata['name']
            result = {'metadata': metadata, 'name': name, 'content_type': mime, 'content_base64': base64.b64encode(content).decode()}
            if action == 'drive.read' and mime.startswith('text/'):
                result = {'metadata': metadata, 'content': content.decode('utf-8', errors='replace')}
        else:
            boundary = 'hermbot_' + str(operation_id).replace('-', '')
            metadata = {'name': args['name'], 'parents': [args['folder_id']], 'appProperties': {'hermbot_operation': str(operation_id)}}
            if args.get('document'):
                metadata['mimeType'] = 'application/vnd.google-apps.document'
            if action == 'drive.upload':
                content = args['content_bytes']
                content_type = args['content_type']
            else:
                content = args['content'].encode()
                content_type = 'text/plain; charset=UTF-8'
            body = f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n--{boundary}\r\nContent-Type: {content_type}\r\n\r\n'.encode() + content + f'\r\n--{boundary}--\r\n'.encode()
            result = request('POST', 'https://www.googleapis.com/upload/drive/v3/files', token=token, mutation=True, headers={'Content-Type': 'multipart/related; boundary=' + boundary}, params={'uploadType': 'multipart', 'fields': 'id,name,mimeType,webViewLink,version,modifiedTime,parents,trashed'}, content=body)
    elif action.startswith('slack.'):
        root = 'https://slack.com/api/'
        if action == 'slack.search':
            result = call('GET', root + 'search.messages', params={'query': args['query'], 'count': args['limit']})
        elif action == 'slack.channels':
            result = call('GET', root + 'conversations.list', params={'limit': args['limit'], 'exclude_archived': 'true'})
        elif action == 'slack.members':
            result = call('GET', root + 'users.list', params={'limit': args['limit'], **({'cursor': args['cursor']} if args['cursor'] else {})})
            if args['query']:
                query = args['query'].casefold()
                result['members'] = [user for user in result.get('members', []) if query in json.dumps({'name': user.get('name'), 'real_name': user.get('real_name'), 'profile': user.get('profile')}).casefold()]
        elif action == 'slack.history':
            result = call('GET', root + 'conversations.history', params={'channel': args['channel'], 'limit': args['limit']})
        else:
            body = {'channel': args['channel'], 'text': args['text'], 'client_msg_id': str(operation_id), 'unfurl_links': False, 'unfurl_media': False}
            if args['thread_ts']:
                body['thread_ts'] = args['thread_ts']
            result = call('POST', root + 'chat.postMessage', json=body)
    else:
        root = 'https://api.github.com'
        endpoint = root + '/repos/' + '/'.join(path(v) for v in args.get('repo', '').split('/'))
        if action == 'github.repositories':
            result = call('GET', root + '/user/repos', params={'per_page': args['limit']})
        elif action == 'github.repository':
            result = call('GET', endpoint)
        elif action in ('github.issues', 'github.pulls'):
            result = call('GET', endpoint + ('/issues' if action.endswith('issues') else '/pulls'), params={'per_page': args['limit']})
        elif action in ('github.issue', 'github.pull'):
            result = call('GET', endpoint + ('/issues/' if action.endswith('issue') else '/pulls/') + str(args['number']))
        elif action == 'github.file':
            result = call('GET', endpoint + '/contents/' + '/'.join(path(v) for v in args['path'].split('/')), params={'ref': args['ref']} if args['ref'] else {})
        elif action in ('github.create_issue', 'github.update_issue'):
            body = {k: args[k] for k in ('title', 'body')}
            if action == 'github.update_issue':
                body['state'] = args['state']
            result = call('PATCH' if action == 'github.update_issue' else 'POST', endpoint + '/issues' + ('/' + str(args['number']) if action == 'github.update_issue' else ''), json=body)
        elif action == 'github.comment':
            result = call('POST', endpoint + '/issues/' + str(args['number']) + '/comments', json={'body': args['body']})
        elif action == 'github.create_pull':
            result = call('POST', endpoint + '/pulls', json={k: args[k] for k in ('title', 'body', 'head', 'base')})
        else:
            body = {'message': args['message'], 'content': base64.b64encode(args['content'].encode()).decode(), 'branch': args['branch']}
            if args['sha']:
                body['sha'] = args['sha']
            result = call('PUT', endpoint + '/contents/' + '/'.join(path(v) for v in args['path'].split('/')), json=body)
    return redact(result, credential.values())
