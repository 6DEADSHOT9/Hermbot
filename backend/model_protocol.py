"""Subscription wire contract. No credentials, database access or tool execution."""
import json

MAX_BODY = 16 * 1024 * 1024
MAX_EVENT = 2 * 1024 * 1024
PROTOCOL = 2


BROKERED_AUTH = ('chatgpt_oauth', 'codex_oauth')
TOKEN_REFRESH_ERRORS = frozenset({'invalid_grant', 'invalid_refresh_token', 'refresh_token_expired',
                                  'refresh_token_invalidated', 'refresh_token_reused', 'token_expired'})
REAUTH_ERRORS = TOKEN_REFRESH_ERRORS | {'sign_in_required', 'refresh_uncertain'}
CODEX_URL = 'https://chatgpt.com/backend-api/codex'


def responses_request(body, auth_kind='chatgpt_oauth'):
    if not isinstance(body, dict) or not isinstance(body.get('model'), str) or not body['model']:
        raise ValueError('Choose a model')
    if not isinstance(body.get('input'), list):
        raise ValueError('Responses requires complete input history')
    forbidden = {'background', 'conversation', 'previous_response_id', 'multi_agent', 'prompt',
                 'moderation', 'user', 'safety_identifier', 'metadata', 'truncation'}
    if forbidden.intersection(body):
        raise ValueError('Unsupported subscription request feature')
    allowed = {'model', 'instructions', 'input', 'tools', 'tool_choice', 'parallel_tool_calls',
               'reasoning', 'text', 'include', 'service_tier', 'prompt_cache_key', 'store', 'stream',
               'max_output_tokens', 'temperature', 'top_p', 'top_logprobs', 'max_tool_calls', 'prompt_cache_retention'}
    if set(body) - allowed:
        raise ValueError('Unsupported subscription request field')
    result = {k: v for k, v in body.items() if k not in {
        'max_output_tokens', 'temperature', 'top_p', 'top_logprobs', 'max_tool_calls', 'prompt_cache_retention'}}
    if body.get('store', False) is not False:
        raise ValueError('Subscription requests cannot store remote conversation state')
    if any(isinstance(item, dict) and item.get('role') == 'system' for item in body['input']):
        raise ValueError('Use developer messages or instructions')
    tools = result.get('tools', [])
    if not isinstance(tools, list):
        raise ValueError('Invalid tools')
    if any(t.get('type') not in ('function', 'custom', 'namespace', 'web_search') for t in tools if isinstance(t, dict)) or any(not isinstance(t, dict) for t in tools):
        raise ValueError('This hosted tool is unavailable with ChatGPT plan usage')
    flat = [t for t in tools if t.get('type') in ('function', 'custom')]
    if flat and auth_kind != 'codex_oauth':
        # The direct plan-use route requires local function/custom definitions in a namespace.
        result['tools'] = [t for t in tools if t not in flat] + [{'type': 'namespace', 'name': 'hermbot', 'description': 'Client-executed tools', 'tools': flat}]
    result.update(store=False, stream=True)
    if auth_kind == 'codex_oauth':
        result.setdefault('instructions', '')
        if any(t.get('type') == 'namespace' for t in tools):
            raise ValueError('Namespaced tool definitions are not supported by this Codex adapter')
    if len(json.dumps(result).encode()) > MAX_BODY:
        raise ValueError('Model request exceeds the size limit')
    return result


def chat_to_responses(body, auth_kind='chatgpt_oauth'):
    if set(body) - {'model', 'messages', 'tools', 'response_format', 'reasoning_effort', 'max_tokens', 'max_completion_tokens', 'temperature'}:
        raise ValueError('Unsupported completion option')
    items = []
    for message in body['messages']:
        if message.get('_response_items'):
            items.extend(message['_response_items'])
            continue
        role = message['role']
        if role == 'tool':
            items.append({'type': 'function_call_output', 'call_id': message['tool_call_id'], 'output': message['content']})
            continue
        content = message.get('content')
        if isinstance(content, list):
            content = [{'type': 'input_text', 'text': p['text']} if p['type'] == 'text' else
                       {'type': 'input_image', 'image_url': p['image_url']['url']} for p in content]
        if content:
            items.append({'role': 'developer' if role == 'system' else role, 'content': content})
        for call in message.get('tool_calls', []):
            items.append({'type': 'function_call', 'call_id': call['id'], **call['function']})
    result = {'model': body['model'], 'input': items}
    if body.get('tools'):
        result['tools'] = [{'type': 'function', **t['function']} for t in body['tools']]
    fmt = body.get('response_format')
    if fmt:
        result['text'] = {'format': {'type': 'json_schema', **fmt['json_schema']} if fmt['type'] == 'json_schema' else fmt}
    if body.get('reasoning_effort'):
        result['reasoning'] = {'effort': body['reasoning_effort']}
    return responses_request(result, auth_kind)


def completed_message(response):
    if response.get('status') != 'completed':
        raise ValueError('Model response did not complete')
    output = response.get('output', [])
    content, calls, refusal = [], [], None
    for item in output:
        if item.get('type') == 'function_call':
            calls.append({'id': item['call_id'], 'type': 'function', 'function': {'name': item['name'], 'arguments': item['arguments']}})
        for part in item.get('content', []):
            if part.get('type') == 'output_text':
                content.append(part['text'])
            elif part.get('type') == 'refusal':
                refusal = part.get('refusal', 'Model declined the request')
    # Responses reasoning/tool history is replayed only on this transport, never sent to Chat Completions.
    replay = [{k: v for k, v in item.items() if k not in ('id', 'status')} for item in output]
    return {'role': 'assistant', 'content': ''.join(content), 'tool_calls': calls, 'refusal': refusal,
            '_response_items': replay}


async def sse_events(response):
    data, size = [], 0
    buffered = b''
    async for chunk in response.aiter_bytes(chunk_size=4096):
        buffered += chunk
        while b'\n' in buffered:
            raw_line, buffered = buffered.split(b'\n', 1)
            size += len(raw_line)
            if size > MAX_EVENT:
                raise ValueError('Model event exceeds the size limit')
            line = raw_line.rstrip(b'\r').decode('utf-8')
            if not line:
                if data:
                    raw = '\n'.join(data)
                    if raw != '[DONE]':
                        event = json.loads(raw)
                        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                            raise ValueError('Invalid model event')
                        yield event
                data, size = [], 0
            elif line.startswith('data:'):
                data.append(line[5:].lstrip())
        if len(buffered) + size > MAX_EVENT:
            raise ValueError('Model event exceeds the size limit')
    if data or buffered:
        raise ValueError('Model stream ended in an incomplete event')
