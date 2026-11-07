"""Owner-configured Streamable HTTP MCP, including JSON/SSE responses and conservative mutation policy."""

import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from jsonschema import Draft202012Validator, ValidationError, SchemaError

from .connector_http import ProviderFailure, request, redact

VERSION = '2025-11-25'


def validate_endpoint(endpoint):
    try:
        url = urlsplit(endpoint or '')
        port = url.port or 443
    except ValueError:
        raise HTTPException(422, 'Use a valid public HTTPS MCP endpoint')
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment or url.query:
        raise HTTPException(422, 'Use a public HTTPS MCP endpoint without URL credentials, queries or fragments')
    try:
        addresses = socket.getaddrinfo(url.hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        raise HTTPException(422, 'MCP endpoint could not be resolved')
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise HTTPException(422, 'MCP endpoints must resolve only to public addresses')
    return endpoint


def check_schema(schema):
    # Untrusted schemas must not fetch references or run attacker-controlled regexes.
    try:
        text = json.dumps(schema)
    except (ValueError, RecursionError):
        raise HTTPException(422, 'MCP argument schema is too complex')
    if len(text) > 30_000:
        raise HTTPException(422, 'MCP argument schema is too large')
    stack, count, combinations = [(schema, 0, True)], 0, 1
    while stack:
        value, depth, is_schema = stack.pop()
        count += 1
        if depth > 16 or count > 1500:
            raise HTTPException(422, 'MCP argument schema is too complex')
        if isinstance(value, dict):
            if is_schema and set(value) & {'$ref', '$dynamicRef', 'pattern', 'patternProperties'}:
                raise HTTPException(422, 'MCP schemas cannot contain references or regular expressions')
            for key in ('allOf', 'anyOf', 'oneOf') if is_schema else ():
                if isinstance(value.get(key), list):
                    combinations *= max(1, len(value[key]))
            if combinations > 200:
                raise HTTPException(422, 'MCP argument schema has too many combinations')
            mapping_keywords = {'properties', '$defs', 'dependentSchemas'}
            schema_keywords = {'additionalProperties', 'unevaluatedProperties', 'items', 'contains', 'propertyNames', 'not', 'if', 'then', 'else', 'unevaluatedItems', 'contentSchema', 'allOf', 'anyOf', 'oneOf', 'prefixItems'}
            for key, child in value.items():
                if is_schema and key in mapping_keywords and isinstance(child, dict):
                    stack.extend((item, depth + 1, True) for item in child.values())
                else:
                    stack.append((child, depth + 1, is_schema and key in schema_keywords))
        elif isinstance(value, list):
            stack.extend((child, depth + 1, is_schema) for child in value)
    try:
        Draft202012Validator.check_schema(schema)
    except (SchemaError, RecursionError):
        raise HTTPException(422, 'MCP tool provided an invalid argument schema')


def validate_arguments(schema, arguments):
    check_schema(schema)
    try:
        if len(json.dumps(arguments)) > 100_000:
            raise HTTPException(422, 'MCP arguments are too large')
        Draft202012Validator(schema).validate(arguments)
    except (ValidationError, RecursionError, TypeError, ValueError):
        raise HTTPException(422, 'MCP arguments do not match the discovered tool schema')


class Session:
    def __init__(self, connection, credential):
        self.endpoint = validate_endpoint(connection['endpoint'])
        self.credential = credential
        self.headers = {'Accept': 'application/json, text/event-stream'}
        initialized = self.rpc('initialize', {'protocolVersion': VERSION, 'capabilities': {}, 'clientInfo': {'name': 'hermbot', 'version': '0.4.0'}})
        version = initialized.get('protocolVersion')
        if version not in (VERSION, '2025-06-18', '2025-03-26'):
            raise ProviderFailure('MCP server negotiated an unsupported protocol version')
        self.headers['MCP-Protocol-Version'] = version
        self.rpc('notifications/initialized', None, notification=True)

    def rpc(self, method, params=None, *, mutation=False, notification=False):
        identity = str(uuid4())
        payload = {'jsonrpc': '2.0', 'method': method}
        if not notification:
            payload['id'] = identity
        if params is not None:
            payload['params'] = params
        body, headers = request('POST', self.endpoint, token=self.credential.get('access_token'), headers=self.headers, json=payload, raw=True, mutation=mutation)
        if headers.get('mcp-session-id'):
            session_id = headers['mcp-session-id']
            if not re.fullmatch(r'[\x21-\x7e]{1,300}', session_id):
                raise ProviderFailure('MCP server returned an invalid session identifier', uncertain=mutation)
            self.headers['MCP-Session-Id'] = session_id
        if notification:
            return {}
        try:
            if 'text/event-stream' in headers.get('content-type', ''):
                candidates = []
                for event in body.decode().replace('\r\n', '\n').split('\n\n'):
                    data = '\n'.join(line[5:].lstrip() for line in event.splitlines() if line.startswith('data:'))
                    if data:
                        candidates.append(json.loads(data))
                result = next(item for item in candidates if item.get('id') == identity)
            else:
                result = json.loads(body)
            if result.get('jsonrpc') != '2.0' or result.get('id') != identity:
                raise ValueError()
            if 'error' in result:
                raise ProviderFailure('MCP server rejected the tool request', uncertain=mutation)
            if not isinstance(result['result'], dict):
                raise ValueError()
            return redact(result['result'], self.credential.values())
        except (ValueError, KeyError, StopIteration, TypeError, AttributeError, RecursionError):
            raise ProviderFailure('MCP response was invalid; reconcile an external change before repeating', uncertain=mutation)

    def close(self):
        if self.headers.get('MCP-Session-Id'):
            try:
                request('DELETE', self.endpoint, token=self.credential.get('access_token'), headers=self.headers)
            except ProviderFailure:
                pass


def discover(connection, credential):
    session = Session(connection, credential)
    try:
        tools, cursor = [], None
        for _ in range(10):
            page = session.rpc('tools/list', {'cursor': cursor} if cursor else {})
            page_tools = page.get('tools')
            if not isinstance(page_tools, list) or len(tools) + len(page_tools) > 200:
                raise ProviderFailure('MCP discovery must return a bounded tool list')
            for tool in page_tools:
                if not isinstance(tool, dict) or not isinstance(tool.get('name'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', tool['name']):
                    raise ProviderFailure('MCP server returned an invalid tool name')
                if not isinstance(tool.get('inputSchema'), dict):
                    raise ProviderFailure('MCP tool must include an object argument schema')
                check_schema(tool['inputSchema'])
                if any(previous['name'] == tool['name'] for previous in tools):
                    raise ProviderFailure('MCP server returned duplicate tool names')
                tools.append({'name': tool['name'], 'description': str(tool.get('description', ''))[:2000], 'inputSchema': tool['inputSchema'], 'mutation': True})
            cursor = page.get('nextCursor')
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 2000):
                raise ProviderFailure('MCP discovery returned an invalid pagination cursor')
            if not cursor:
                return tools
        raise ProviderFailure('MCP discovery exceeded its page limit')
    finally:
        session.close()


def execute(connection, credential, action, arguments):
    session = Session(connection, credential)
    try:
        result = session.rpc('tools/call', {'name': action[4:], 'arguments': arguments}, mutation=True)
        if result.get('isError'):
            raise ProviderFailure('MCP tool reported an error; reconcile its possible effects', uncertain=True)
        return result
    finally:
        session.close()
