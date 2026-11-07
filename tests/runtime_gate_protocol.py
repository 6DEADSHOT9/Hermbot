"""Isolated pinned-Hermes ordering and real native-lease takeover acceptance; no model or network."""
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from hermbot_runtime_gate import DrainTimeout, admit, drain


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    source = Path('/opt/hermes/agent/tool_executor.py').read_text()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == '_StartOrderGate')
    namespace = {'threading': threading, 'logger': logging.getLogger('protocol')}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'pinned_hermes_start_gate', 'exec'), namespace)
    with tempfile.TemporaryDirectory() as home:
        os.environ['HERMES_HOME'] = home
        sys.path.insert(0, '/opt/hermes')
        from tools.bot_desktop import lease
        plugin = load('protocol_plugin', '/opt/hermbot/plugin/__init__.py')
        plugin.post = lambda *args: {}  # Isolate policy transport; exercise actual admission and ordering.
        adapter = load('protocol_adapter', '/opt/hermbot/runtime_api.py')
        adapter.computer = lambda: {'input_ready': adapter.ready_for_input('owner' * 8)}
        trials = []
        for _ in range(10):
            order_gate = namespace['_StartOrderGate'](0.8)
            entered = threading.Event()
            ordered = []
            def invoke(index):
                def callback(args):
                    if index == 1:
                        entered.set()
                    order_gate.begin_in_order(index, lambda: ordered.append(index), tool_name='read_file')
                    return 'local protocol result'
                return plugin.execution_boundary('read_file', {}, callback, task_id='protocol')
            start = time.monotonic()
            with ThreadPoolExecutor(2) as pool:
                later = pool.submit(invoke, 1)
                assert entered.wait(2)
                earlier = pool.submit(invoke, 0)
                assert earlier.result(timeout=3) == later.result(timeout=3)
            duration = time.monotonic() - start
            assert ordered == [0, 1] and duration < 0.4, (ordered, duration)
            trials.append(round(duration, 4))
        entered = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        viewer = 'owner' * 8
        def callback(index):
            entered[index].set()
            assert release[index].wait(5)
            return 'finished'
        with ThreadPoolExecutor(3) as pool:
            tools = [pool.submit(plugin.execution_boundary, 'terminal', {}, lambda args, i=i: callback(i), task_id='protocol') for i in range(2)]
            try:
                assert all(event.wait(2) for event in entered)
                takeover = pool.submit(adapter.control, adapter.Control(action='takeover', viewer_id=viewer))
                deadline = time.monotonic() + 2
                while not lease.human_holds() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert lease.human_holds() and not adapter.ready_for_input(viewer)
                assert json.loads(plugin.execution_boundary('terminal', {}, lambda args: 'MUST_NOT_EXECUTE', task_id='protocol'))['error'] == 'human_has_control'
                release[0].set()
                tools[0].result(timeout=2)
                assert not takeover.done() and not adapter.ready_for_input(viewer)
                release[1].set()
                assert takeover.result(timeout=2)['input_ready']
                tools[1].result(timeout=2)
            finally:
                for event in release:
                    event.set()
        adapter.control(adapter.Control(action='handback', viewer_id=viewer))
        assert not adapter.ready_for_input(viewer)
        assert plugin.execution_boundary('read_file', {}, lambda args: 'RESUMED', task_id='protocol') == 'RESUMED'
        child_code = 'import sys; from hermbot_runtime_gate import admit;\nwith admit(sys.argv[1],lambda:False):\n print("ADMITTED",flush=True)\n sys.stdin.read()\n'
        child = subprocess.Popen([sys.executable, '-c', child_code, home], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            assert child.stdout.readline().strip() == 'ADMITTED'
            try:
                with drain(home, lambda: True, timeout=0):
                    raise AssertionError('Child admission did not prevent premature takeover')
            except DrainTimeout:
                pass
            child.terminate()
            child.wait(timeout=3)
            with drain(home, lambda: True, timeout=0):
                pass
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdin.close()
            child.stdout.close()
        # Also ensure separate shared holders can coexist across the whole callback lifetime.
        with admit(home, lambda: False), admit(home, lambda: False):
            pass
    proof = {'complete': True, 'network': 'disabled', 'real_model': False,
             'pinned_hermes_gate': True, 'native_desktop_lease': True, 'reverse_entry_trials': trials,
             'all_parallel_calls_drained': True, 'new_admission_rejected': True, 'handback_resumed': True,
             'process_exit_releases_admission': True, 'hermes_executor_sha256': hashlib.sha256(source.encode()).hexdigest()}
    Path('/output/tool-gate-protocol.json').write_text(json.dumps(proof, indent=2) + '\n')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
