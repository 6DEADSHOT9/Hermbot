"""Exercise the actual bounded HTTP transport against controlled loopback servers."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import urlsplit

import pytest

from backend import connector_http, library


@pytest.fixture
def provider(monkeypatch):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append({'path': self.path, 'authorization': self.headers.get('Authorization'), 'body': self.rfile.read(int(self.headers.get('Content-Length', '0')))})
            route = self.path.split('?')[0]
            if route == '/delayed':
                time.sleep(.09)
            if route == '/sse-open':
                identity = json.loads(seen[-1]['body'])['id']
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                payload = {'jsonrpc': '2.0', 'id': identity, 'result': {'content': [{'text': 'actual receipt'}]}}
                frames = 'data: ' + json.dumps({'jsonrpc': '2.0', 'id': 'other', 'result': {}}) + '\n\ndata: ' + json.dumps(payload) + '\n\n'
                self.wfile.write(frames.encode())
                self.wfile.flush()
                time.sleep(.6)  # Keep the stream open longer than the client's deadline.
                return
            status = {'/redirect': 307, '/reject': 400, '/crash': 503, '/auth': 401}.get(route, 200)
            body = {'/reject': b'PRIVATE_SECRET_CANARY', '/broken': b'not JSON', '/large': b'x' * 500}.get(route, json.dumps({'ok': True}).encode())
            if route == '/provider-error':
                body = seen[-1]['body']
            self.send_response(status)
            if route == '/redirect':
                self.send_header('Location', 'http://127.0.0.1/private')
            if route == '/compressed':
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                if route == '/drip':
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.03)
                else:
                    self.wfile.write(body)
            except OSError:
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Only this test bypasses public DNS admission, then the real transport must use the pinned IP.
    monkeypatch.setattr(library, 'public_address', lambda url: (urlsplit(url), '127.0.0.1', server.server_port))
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    yield seen
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_transport_uses_pinned_address_without_proxy_and_does_not_redirect(provider):
    assert connector_http.request('POST', 'http://unresolvable.invalid/ok', token='private', json={'query': 'safe'}, params={'name': 'a/b'}) == {'ok': True}
    assert provider[0]['path'] == '/ok?name=a%2Fb' and provider[0]['authorization'] == 'Bearer private'
    with pytest.raises(connector_http.ProviderFailure) as rejected:
        connector_http.request('POST', 'http://unresolvable.invalid/redirect', mutation=True)
    assert not rejected.value.uncertain and len(provider) == 2


@pytest.mark.parametrize('route,uncertain,auth', [('reject', False, False), ('crash', True, False), ('auth', False, True), ('broken', True, False), ('compressed', True, False), ('large', True, False)])
def test_transport_classifies_outcomes_without_provider_error_secrets(provider, monkeypatch, route, uncertain, auth):
    monkeypatch.setattr(connector_http, 'MAX_BYTES', 100)
    with pytest.raises(connector_http.ProviderFailure) as error:
        connector_http.request('POST', 'http://unresolvable.invalid/' + route, mutation=True)
    assert error.value.uncertain == uncertain and error.value.auth == auth
    assert 'PRIVATE_SECRET_CANARY' not in str(error.value)


def test_transport_absolute_deadline_stops_slow_drip(provider, monkeypatch):
    monkeypatch.setattr(connector_http, 'MAX_SECONDS', .08)
    start = time.monotonic()
    with pytest.raises(connector_http.ProviderFailure) as error:
        connector_http.request('POST', 'http://unresolvable.invalid/drip', mutation=True)
    assert error.value.uncertain and time.monotonic() - start < .5


def test_transport_rejects_private_network_before_connect(monkeypatch):
    # Use the production public-address admission here, with no bypass.
    with pytest.raises(connector_http.ProviderFailure) as error:
        connector_http.request('POST', 'http://127.0.0.1/private', mutation=True)
    assert not error.value.uncertain


def test_sse_receipt_returns_before_server_closes_stream(provider, monkeypatch):
    monkeypatch.setattr(connector_http, 'MAX_SECONDS', .2)
    start = time.monotonic()
    body, headers = connector_http.request('POST', 'http://unresolvable.invalid/sse-open', raw=True, mutation=True, json={'jsonrpc': '2.0', 'id': 'expected', 'method': 'tools/call'})
    assert time.monotonic() - start < .2 and headers['content-type'] == 'text/event-stream'
    assert b'actual receipt' in body and b'"id": "expected"' in body


def test_response_budget_is_independent_of_connect_timeout(provider, monkeypatch):
    connect = connector_http.socket.create_connection
    monkeypatch.setattr(connector_http.socket, 'create_connection', lambda address, timeout: connect(address, timeout=.02))
    monkeypatch.setattr(connector_http, 'MAX_SECONDS', .3)
    assert connector_http.request('POST', 'http://unresolvable.invalid/delayed') == {'ok': True}


@pytest.mark.parametrize('host,code,auth,expected', [
    ('slack.com', 'not_in_channel', False, 'invite the connected app'),
    ('slack.com', 'missing_scope', False, 'reinstall the app'),
    ('slack.com', 'not_allowed_token_type', False, 'user token'),
    ('slack.com', 'account_inactive', True, 'account_inactive'),
    ('slack.com', 'token_revoked', True, 'token_revoked'),
    ('slack.com', 'token_expired', True, 'token_expired'),
    ('slack.com', 'invalid_auth', True, 'invalid_auth'),
    ('slack.com', 'PRIVATE_SECRET_CANARY', False, 'Provider rejected'),
    ('unresolvable.invalid', 'not_in_channel', False, 'Provider rejected'),
])
def test_known_slack_errors_explain_recovery_without_leaking_provider_content(provider, host, code, auth, expected):
    with pytest.raises(connector_http.ProviderFailure) as failure:
        connector_http.request('POST', 'http://' + host + '/provider-error', json={'ok': False, 'error': code, 'private': 'PRIVATE_SECRET_CANARY'}, mutation=True)
    assert expected in str(failure.value) and 'PRIVATE_SECRET_CANARY' not in str(failure.value)
    assert failure.value.auth == auth and not failure.value.uncertain


def test_push_session_uses_pinned_transport_and_does_not_follow_redirect(provider,monkeypatch):
    from backend.notifications import PinnedSession
    actual=library.public_address
    monkeypatch.setattr(library,'public_address',lambda url:actual(url.replace('https://','http://',1)))
    response=PinnedSession().post('https://push.example.com/redirect',data=b'encrypted',headers={'TTL':'60'})
    assert response.status_code==307
    assert len(provider)==1 and provider[0]['body']==b'encrypted'
    assert provider[0]['authorization'] is None
