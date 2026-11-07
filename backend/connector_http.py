"""Pinned-address, bounded connector transport. No redirects, proxy inheritance or secret error bodies."""

import http.client
import json as json_module
import socket
import ssl
import threading
import time
from urllib.parse import urlencode

from fastapi import HTTPException

MAX_BYTES = 2_000_000
MAX_SECONDS = 20
SLACK_RECOVERY = {
    'not_in_channel': 'Slack returned not_in_channel: invite the connected app to this channel in Slack, then retry the read or submit a new reviewed change.',
    'channel_not_found': 'Slack cannot access this channel: verify its ID and workspace, and add the connected app to private channels.',
    'missing_scope': 'Slack returned missing_scope: this action needs an additional OAuth permission. Update the app scopes and reinstall the app if this action is needed. Other permitted actions remain available.',
    'not_allowed_token_type': 'Slack rejected this token type: use a bot token for channel actions and a user token for message search.',
    'account_inactive': 'Slack returned account_inactive: the bot/account or workspace is inactive. Reinstall or reauthorize the app in an active workspace and replace its token.',
    'token_revoked': 'Slack returned token_revoked: this token authorization was revoked. Reauthorize the app and replace this token.',
    'token_expired': 'Slack returned token_expired: this token expired. Refresh or replace this token.',
    'invalid_auth': 'Slack returned invalid_auth: this credential could not be validated. Check the token, workspace and account access before reconnecting.',
    'is_archived': 'Slack rejected the change because the channel is archived. Choose an active channel.',
    'ratelimited': 'Slack rate-limited this request. Wait before trying again; a rejected change requires a new review.',
}


def sse_response(response, identity):
    """Stop at the matching RPC receipt; an SSE stream need not end after a response."""
    chunks, event, size = [], [], 0
    while True:
        line = response.readline(MAX_BYTES - size + 1)
        if not line:
            break
        size += len(line)
        if size > MAX_BYTES:
            raise ValueError('SSE size limit')
        chunks.append(line)
        if line.strip() == b'':
            data = b'\n'.join(item[5:].lstrip() for item in event if item.startswith(b'data:'))
            event = []
            if data:
                payload = json_module.loads(data)
                if isinstance(payload, dict) and payload.get('jsonrpc') == '2.0' and payload.get('id') == identity:
                    return b''.join(chunks)
        else:
            event.append(line.rstrip(b'\r\n'))
    return b''.join(chunks)


class ProviderFailure(Exception):
    def __init__(self, message, *, uncertain=False, auth=False, status=None, retry_after=None):
        super().__init__(message)
        self.uncertain = uncertain
        self.auth = auth
        self.status = status
        self.retry_after = retry_after


def request(method, url, *, token=None, headers=None, mutation=False, raw=False, params=None, json=None, data=None, content=None, response_metadata=False, accepted_statuses=(), max_seconds=None):
    from .library import public_address
    max_seconds = MAX_SECONDS if max_seconds is None else max_seconds
    deadline = time.monotonic() + max_seconds
    try:
        parsed, address, port = public_address(url)
    except HTTPException:
        raise ProviderFailure('Provider endpoint must resolve to a public address')
    if time.monotonic() >= deadline:
        raise ProviderFailure('Provider endpoint lookup exceeded its time limit')
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity', 'User-Agent': 'Hermbot/0.4.0', **(headers or {})}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if json is not None:
        content = json_module.dumps(json).encode()
        headers['Content-Type'] = 'application/json'
    elif data is not None:
        content = urlencode(data).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    target = parsed.path or '/'
    if params:
        target += '?' + urlencode(params)
    conn = http.client.HTTPConnection(parsed.hostname, port, timeout=12)
    timer, sent = None, False
    expired = threading.Event()
    try:
        conn.sock = socket.create_connection((address, port), timeout=min(4,max(.001,deadline-time.monotonic())))
        if parsed.scheme == 'https':
            conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=parsed.hostname)
        # The pinned socket retains create_connection's short connect timeout unless reset.
        # Response reads use the absolute request budget, interrupted by the timer below.
        remaining = deadline-time.monotonic()
        if remaining<=0:
            raise ProviderFailure('Provider connection exceeded its time limit')
        conn.sock.settimeout(remaining)

        def interrupt(sock=conn.sock):
            expired.set()
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(remaining, interrupt)
        timer.daemon = True
        timer.start()
        sent = True
        conn.request(method, target, body=content, headers=headers)
        response = conn.getresponse()
        metadata = {key.lower(): value for key, value in response.getheaders() if key.lower() in ('etag', 'link', 'retry-after', 'x-poll-interval', 'x-ratelimit-reset', 'x-ratelimit-remaining', 'date')}
        try:
            retry_after = min(86400, max(1, int(metadata.get('retry-after', '0')))) if metadata.get('retry-after') else None
        except ValueError:
            retry_after = 60
        if raw and isinstance(json, dict) and json.get('id') is not None and 'text/event-stream' in response.getheader('Content-Type', ''):
            body = sse_response(response, json['id'])
        else:
            body = response.read(MAX_BYTES + 1)
        if expired.is_set() or len(body) > MAX_BYTES:
            raise ProviderFailure('Provider response exceeded its size/time limit', uncertain=mutation)
        if response.status in (401, 403) and response.status not in accepted_statuses:
            limited = response.status == 403 and (metadata.get('x-ratelimit-remaining') == '0' or retry_after is not None)
            raise ProviderFailure('Provider rate limit reached; collection will retry' if limited else 'Connection credentials or provider scopes were rejected; reconnect or adjust access', auth=not limited, status=response.status, retry_after=retry_after or (60 if limited else None))
        if not 200 <= response.status < 300 and response.status not in accepted_statuses:
            raise ProviderFailure(f'Provider returned HTTP {response.status}; inspect the connection and action', uncertain=mutation and response.status >= 500, status=response.status, retry_after=retry_after)
        if response.getheader('Content-Encoding', 'identity') not in ('identity', ''):
            raise ProviderFailure('Provider returned an unsupported compressed response', uncertain=mutation)
        if raw:
            metadata = {key.lower(): value for key, value in response.getheaders()}
            return (body, metadata, response.status) if response_metadata else (body, metadata)
        result = json_module.loads(body) if body else {}
        if response.status in accepted_statuses:
            return (result, metadata, response.status) if response_metadata else result
        if isinstance(result, dict) and (result.get('ok') is False or result.get('error')):
            code = str(result.get('error', 'rejected'))
            message = SLACK_RECOVERY.get(code) if parsed.hostname == 'slack.com' else None
            auth = code in ('invalid_auth', 'token_revoked', 'token_expired', 'invalid_grant', 'missing_scope', 'not_allowed_token_type', 'account_inactive')
            if parsed.hostname == 'slack.com' and code in ('missing_scope', 'not_allowed_token_type'):
                # A rejected action does not invalidate credentials for other Slack actions.
                auth = False
            # Fixed templates explain known recovery steps without exposing arbitrary provider bodies.
            raise ProviderFailure(message or 'Provider rejected the request; verify scopes, resources and credentials', auth=auth, uncertain=mutation and code in ('internal_error', 'fatal_error'), status=response.status, retry_after=retry_after or (60 if code == 'ratelimited' else None))
        return (result, metadata, response.status) if response_metadata else result
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        raise ProviderFailure('Provider delivery or response is unavailable; reconcile before repeating a change' if sent else 'Could not connect to the provider; no action was sent', uncertain=mutation and sent)
    finally:
        if timer:
            timer.cancel()
        conn.close()


def redact(value, secrets):
    encoded = json_module.dumps(value, ensure_ascii=False, default=str)
    for secret in sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True):
        encoded = encoded.replace(secret, '[credential omitted]')
    return json_module.loads(encoded)
