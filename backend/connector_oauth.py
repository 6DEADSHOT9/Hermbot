"""Owner-initiated OAuth with one-use state, browser binding, PKCE and encrypted refresh tokens."""

import base64
from datetime import timedelta
import hashlib
import json
import os
import secrets
from urllib.parse import urlencode

from fastapi import HTTPException

from . import auth, service
from .connector_http import ProviderFailure, request
from .secrets import encrypt, decrypt, credential_hash

OAUTH = {
    'google': ('https://accounts.google.com/o/oauth2/v2/auth', 'https://oauth2.googleapis.com/token', ' '.join([
        'https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/drive.readonly',
        'https://www.googleapis.com/auth/drive.file',
    ])),
    'slack': ('https://slack.com/oauth/v2/authorize', 'https://slack.com/api/oauth.v2.access', 'channels:read,channels:history,groups:read,groups:history,chat:write,users:read'),
    'github': ('https://github.com/login/oauth/authorize', 'https://github.com/login/oauth/access_token', 'repo read:user'),
}


def callback_url(provider):
    return os.environ.get('PUBLIC_URL', 'http://localhost:8780').rstrip('/') + '/api/v1/connections/oauth/' + provider + '/callback'


def app(conn, provider):
    value = conn.execute('SELECT * FROM connector_apps WHERE provider=%s', (provider,)).fetchone()
    if not value:
        raise HTTPException(409, 'Configure the provider OAuth application first')
    return value


def begin(conn, connection, response, authorization, cookie):
    provider = connection['provider']
    if provider not in OAUTH:
        raise HTTPException(422, 'This connection uses a server token instead of OAuth')
    if provider == 'slack' and not callback_url(provider).startswith('https://'):
        raise HTTPException(409, 'Slack OAuth requires HTTPS. For this local workspace, enter a bot token and an optional user search token instead')
    application = app(conn, provider)
    state, binding, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    bearer = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    owner_hash = credential_hash(cookie or bearer)
    conn.execute('INSERT INTO connector_oauth_states(state_hash,binding_hash,owner_hash,connection_id,connection_revision,app_revision,verifier_encrypted,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                 (credential_hash(state), credential_hash(binding), owner_hash, connection['id'], connection['revision'], application['revision'], encrypt(verifier), service.now() + timedelta(minutes=10)))
    params = {'client_id': application['client_id'], 'redirect_uri': callback_url(provider), 'scope': OAUTH[provider][2], 'state': state}
    if provider != 'slack':
        params.update(response_type='code', code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='), code_challenge_method='S256')
    if provider == 'google':
        params.update(access_type='offline', prompt='consent', include_granted_scopes='true')
    if provider == 'slack':
        params['user_scope'] = 'search:read'
    response.set_cookie('hermbot_oauth_binding', binding, httponly=True, secure=callback_url(provider).startswith('https:'), samesite='lax', max_age=600, path='/api/v1/connections/oauth/')
    return {'authorization_url': OAUTH[provider][0] + '?' + urlencode(params)}


def tokens(provider, result, previous=None):
    previous = previous or {}
    if not isinstance(result, dict):
        raise ProviderFailure('Provider did not return a usable token response', auth=True)
    access = result.get('access_token')
    if not isinstance(access, str) or not access:
        raise ProviderFailure('Provider did not return a usable access token')
    value = {'access_token': access, 'refresh_token': result.get('refresh_token') or previous.get('refresh_token'), 'scope': result.get('scope', previous.get('scope', '')), 'oauth': True}
    if result.get('expires_in'):
        value['expires_at'] = (service.now() + timedelta(seconds=int(result['expires_in']))).isoformat()
    if provider == 'slack' and result.get('authed_user', {}).get('access_token'):
        value['search_token'] = result['authed_user']['access_token']
        value['search_refresh_token'] = result['authed_user'].get('refresh_token')
        if result['authed_user'].get('expires_in'):
            value['search_expires_at'] = (service.now() + timedelta(seconds=int(result['authed_user']['expires_in']))).isoformat()
    for key in ('search_token', 'search_refresh_token', 'search_expires_at'):
        if key not in value and previous.get(key):
            value[key] = previous[key]
    return value


def finish(conn, provider, state, binding, code, error):
    row = conn.execute('SELECT * FROM connector_oauth_states WHERE state_hash=%s FOR UPDATE', (credential_hash(state),)).fetchone()
    if not row or row['consumed'] or row['expires_at'] <= service.now() or not secrets.compare_digest(row['binding_hash'], credential_hash(binding or '')):
        raise HTTPException(400, 'OAuth state is invalid, expired, consumed or belongs to another browser')
    owner_valid = secrets.compare_digest(row['owner_hash'], credential_hash(auth.settings.owner_token)) or conn.execute('SELECT 1 FROM owner_sessions WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>now()', (row['owner_hash'],)).fetchone()
    if not owner_valid:
        raise HTTPException(401, 'The initiating owner session expired or was revoked')
    connection = conn.execute('SELECT * FROM connections WHERE id=%s FOR UPDATE', (row['connection_id'],)).fetchone()
    application = app(conn, provider)
    if connection['provider'] != provider or connection['revision'] != row['connection_revision'] or application['revision'] != row['app_revision']:
        raise HTTPException(409, 'Connection or OAuth configuration changed; start a new authorization')
    conn.execute('UPDATE connector_oauth_states SET consumed=true WHERE state_hash=%s', (row['state_hash'],))
    if error or not code:
        return {'connected': False, 'message': 'Authorization was declined. Start a new connection attempt when ready.'}
    data = {'client_id': application['client_id'], 'client_secret': decrypt(application['secret_encrypted']), 'code': code, 'redirect_uri': callback_url(provider)}
    if provider != 'slack':
        data.update(grant_type='authorization_code', code_verifier=decrypt(row['verifier_encrypted']))
    try:
        result = request('POST', OAUTH[provider][1], data=data)
        credential = tokens(provider, result)
        if provider == 'google':
            account = request('GET', 'https://gmail.googleapis.com/gmail/v1/users/me/profile', token=credential['access_token'])['emailAddress']
        elif provider == 'github':
            account = request('GET', 'https://api.github.com/user', token=credential['access_token'])['login']
        else:
            account = result.get('team', {}).get('name') or 'Slack workspace'
        conn.execute("UPDATE connections SET secret_encrypted=%s,account=%s,status='connected',error=NULL,revision=revision+1,updated_at=now() WHERE id=%s", (encrypt(json.dumps(credential)), str(account)[:200], connection['id']))
        from . import triggers
        triggers.connection_changed(conn, connection['id'])
    except (ProviderFailure, KeyError, ValueError, TypeError, AttributeError):
        conn.execute("UPDATE connections SET status='needs_reconnect',error='Authorization failed; start a new connection attempt' WHERE id=%s", (connection['id'],))
        return {'connected': False, 'message': 'Authorization could not be completed. Reconnect from Connections.'}
    return {'connected': True, 'message': 'Connection saved. Return to Hermbot and open Connections.'}


def credential(conn, connection, *, search=False):
    from datetime import datetime
    value = json.loads(decrypt(connection['secret_encrypted']))
    key = 'search_token' if search and connection['provider'] == 'slack' and value.get('search_token') else 'access_token'
    expiry = 'search_expires_at' if key == 'search_token' else 'expires_at'
    if value.get(expiry) and datetime.fromisoformat(value[expiry]) <= service.now() + timedelta(seconds=60):
        refresh_key = 'search_refresh_token' if key == 'search_token' else 'refresh_token'
        if not value.get(refresh_key):
            raise ProviderFailure('This connection expired; reconnect it', auth=True)
        try:
            application = app(conn, connection['provider'])
            data = {'client_id': application['client_id'], 'client_secret': decrypt(application['secret_encrypted']), 'refresh_token': value[refresh_key], 'grant_type': 'refresh_token'}
            result = request('POST', OAUTH[connection['provider']][1], data=data)
            refreshed = tokens(connection['provider'], result, value)
        except HTTPException:
            raise ProviderFailure('OAuth application is missing; configure it and reconnect', auth=True)
        except ProviderFailure as error:
            if error.auth or error.status in (400, 401, 403):
                raise ProviderFailure('Provider rejected token refresh; reconnect this account', auth=True)
            raise
        if key == 'search_token':
            value.update(search_token=refreshed['access_token'], search_refresh_token=refreshed['refresh_token'], search_expires_at=refreshed.get('expires_at'))
        else:
            value = refreshed
        conn.execute('UPDATE connections SET secret_encrypted=%s,updated_at=now() WHERE id=%s', (encrypt(json.dumps(value)), connection['id']))
    return {**value, 'access_token': value[key]}


def revoke_remote(conn, connection):
    """Best effort only: local revocation commits even if a provider cannot be reached."""
    if not connection['secret_encrypted']:
        return False
    value = json.loads(decrypt(connection['secret_encrypted']))
    try:
        if connection['provider'] == 'google':
            request('POST', 'https://oauth2.googleapis.com/revoke', data={'token': value.get('refresh_token') or value['access_token']}, mutation=True)
        elif connection['provider'] == 'slack':
            for token in {value['access_token'], value.get('search_token')} - {None}:
                result = request('POST', 'https://slack.com/api/auth.revoke', token=token, mutation=True)
                if not result.get('revoked'):
                    return False
        elif connection['provider'] == 'github' and value.get('oauth'):
            application = app(conn, 'github')
            from .connector_catalog import path
            basic = base64.b64encode((application['client_id'] + ':' + decrypt(application['secret_encrypted'])).encode()).decode()
            request('DELETE', 'https://api.github.com/applications/' + path(application['client_id']) + '/token', headers={'Authorization': 'Basic ' + basic, 'Accept': 'application/vnd.github+json'}, json={'access_token': value['access_token']}, mutation=True)
        else:
            return False
        return True
    except (ProviderFailure, HTTPException):
        return False
