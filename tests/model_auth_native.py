"""Run only in a disposable --network none Hermes container, without owner volumes."""
# ruff: noqa: E402
# Hermes must read the disposable home/config before its modules are imported.
import importlib.util
import inspect
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

os.environ['HERMES_HOME'] = '/tmp/hermbot-subscription-native'
Path(os.environ['HERMES_HOME']).mkdir(mode=0o700, exist_ok=True)
os.environ['MODEL_API_KEY'] = 'hermbot-disposable-inference-key'

spec = importlib.util.spec_from_file_location('model_protocol', '/tmp/model_protocol.py')
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)
requests = []
auxiliary = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path != '/v1/responses':
            auxiliary.append({'path': self.path, 'model': body.get('model'), 'keys': sorted(body)})
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Unsupported fixture route"}}')
            return
        normalized = protocol.responses_request(body, os.environ.get('FIXTURE_AUTH_KIND', 'chatgpt_oauth'))
        requests.append(normalized)
        index = len(requests)
        if index == 1:
            output = [{'type': 'function_call', 'id': 'fc_fixture', 'call_id': 'call_fixture', 'name': 'terminal',
                       'arguments': json.dumps({'command': 'printf hermbot-native-fixture', 'timeout': 5}), 'status': 'completed'}]
        else:
            if index == 2:
                assert any(item.get('type') == 'function_call_output' and item['call_id'] == 'call_fixture' and 'hermbot-native-fixture' in str(item['output']) for item in normalized['input'])
            output = [{'type': 'message', 'id': 'msg_fixture', 'role': 'assistant', 'status': 'completed',
                       'content': [{'type': 'output_text', 'text': 'Native subscription fixture complete', 'annotations': []}]}]
        response = {'id': 'resp_fixture_' + str(index), 'object': 'response', 'created_at': 1, 'model': body['model'],
                    'status': 'completed', 'output': output, 'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        events = [{'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}}]
        if index == 1:
            events.extend([{'type': 'response.output_item.added', 'output_index': 0, 'item': {**output[0], 'arguments': '', 'status': 'in_progress'}},
                           {'type': 'response.function_call_arguments.delta', 'output_index': 0, 'item_id': 'fc_fixture', 'delta': output[0]['arguments']},
                           {'type': 'response.function_call_arguments.done', 'output_index': 0, 'item_id': 'fc_fixture', 'arguments': output[0]['arguments']}])
        else:
            events.extend([{'type': 'response.output_item.added', 'output_index': 0, 'item': {**output[0], 'content': [], 'status': 'in_progress'}},
                           {'type': 'response.output_text.delta', 'output_index': 0, 'content_index': 0, 'item_id': 'msg_fixture', 'delta': output[0]['content'][0]['text']}])
        events.extend([{'type': 'response.output_item.done', 'output_index': 0, 'item': output[0]}, {'type': 'response.completed', 'response': response}])
        for event in events:
            self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
        self.wfile.flush()
        self.close_connection = True


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}/v1'
config = {'model': {'provider': 'custom:hermbot-chatgpt', 'default': 'gpt-6.1-sol', 'base_url': base},
          'providers': {'hermbot-chatgpt': {'base_url': base, 'api_mode': 'codex_responses', 'key_env': 'MODEL_API_KEY'}},
          'terminal': {'backend': 'local', 'cwd': '/tmp'}}
config['auxiliary'] = {task: {'provider': 'custom:hermbot-chatgpt', 'model': 'gpt-6.1-sol', 'base_url': base, 'api_mode': 'codex_responses', 'api_key': '', 'fallback_chain': []}
                       for task in ('compression', 'vision', 'title_generation')}
import yaml
Path(os.environ['HERMES_HOME'], 'config.yaml').write_text(yaml.safe_dump(config))
from hermes_cli.runtime_provider import resolve_runtime_provider
runtime = resolve_runtime_provider(requested='custom:hermbot-chatgpt', target_model='gpt-6.1-sol')
assert runtime['api_mode'] == 'codex_responses', runtime['api_mode']
assert runtime['base_url'] == base
# Exercise the native gateway selection path that direct AIAgent tests bypassed.
from gateway.platforms.api_server import APIServerAdapter, _ProviderAuthResolutionError
adapter = object.__new__(APIServerAdapter)
adapter._last_resolved_model = {}
adapter._model_name = 'gpt-6.1-sol'
kwargs = {key: runtime[key] for key in ('provider', 'api_key', 'api_mode', 'base_url')}
# Loopback endpoints get a native no-key bypass; reproduce the deployed service URL.
config['model']['base_url'] = 'http://api:8000/internal/inference/v1'
Path(os.environ['HERMES_HOME'], 'config.yaml').write_text(yaml.safe_dump(config))
try:
    adapter._select_agent_runtime(dict(kwargs), 'gpt-6.1-sol', requested_model='gpt-6.1-sol',
        requested_provider='custom', route=None, session_model=None, confirmed_runtime_lock=False,
        gateway_session_key=None, session_id='negative-fixture')
except _ProviderAuthResolutionError:
    pass
else:
    raise AssertionError('The old generic override must reproduce the missing-credentials error')
config['model']['base_url'] = base
Path(os.environ['HERMES_HOME'], 'config.yaml').write_text(yaml.safe_dump(config))
selected = dict(kwargs)
model, *_ = adapter._select_agent_runtime(selected, 'gpt-6.1-sol', requested_model='gpt-6.1-sol',
    requested_provider=None, route=None, session_model=None, confirmed_runtime_lock=False,
    gateway_session_key=None, session_id='positive-fixture')
assert selected == kwargs and model == 'gpt-6.1-sol'
from run_agent import AIAgent
agent = AIAgent(base_url=base, api_key=runtime['api_key'], provider=runtime['provider'], api_mode=runtime['api_mode'],
                requested_provider='custom:hermbot-chatgpt', model='gpt-6.1-sol', enabled_toolsets=['terminal'],
                max_iterations=3, skip_context_files=True, skip_memory=True, skip_background_review=True,
                save_trajectories=False, quiet_mode=True, cwd='/tmp')
try:
    result = agent.run_conversation('Run the local fixture command, then report its result.')
    if inspect.isawaitable(result):
        import asyncio
        result = asyncio.run(result)
    assert len(requests) == 2, len(requests)
    assert 'Native subscription fixture complete' in str(result)
    from agent.auxiliary_client import call_llm
    for task in ('compression', 'vision', 'title_generation'):
        content = 'Describe this local fixture.'
        if task == 'vision':
            content = [{'type': 'text', 'text': content}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1kAAAAASUVORK5CYII='}}]
        value = call_llm(task=task, messages=[{'role': 'user', 'content': content}], main_runtime=runtime, timeout=20)
        assert 'Native subscription fixture complete' in str(value)
    assert len(requests) == 5, len(requests)
    assert any(part.get('type') == 'input_image' and part.get('image_url', '').startswith('data:image/png;base64,') for item in requests[3]['input'] for part in item.get('content', []) if isinstance(part, dict))
    assert all(item['path'] == '/api/show' for item in auxiliary)
    print(json.dumps({'named_provider': True, 'gateway_provider_selection': True, 'old_override_reproduced': True, 'tool_round_trip': True, 'history_replayed': True, 'requests': len(requests), 'auxiliary_tasks': ['compression', 'vision', 'title_generation'], 'metadata_probes': len(auxiliary), 'network': 'none', 'owner_volumes': False}))
finally:
    server.shutdown()
