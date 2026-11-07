"""Concurrent admission, drain ownership and cleanup through the real plugin and adapter."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import sys
import threading
from types import ModuleType, SimpleNamespace

from fastapi import HTTPException
import pytest

import hermbot_runtime_gate as gate

OWNER = 'owner' * 8


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    state = SimpleNamespace(holder='agent', viewer_id=None, epoch=0)
    transitions = []
    def acquire(viewer_id, **kwargs):
        state.holder, state.viewer_id = 'human', viewer_id
        state.epoch += 1
        transitions.append(threading.current_thread().name)
        return SimpleNamespace(**vars(state))
    def release(viewer_id=None):
        if viewer_id is None or state.viewer_id == viewer_id:
            state.holder, state.viewer_id = 'agent', None
            state.epoch += 1
        return SimpleNamespace(**vars(state))
    desktop = ModuleType('tools.bot_desktop')
    desktop.lease = SimpleNamespace(public_view=lambda value: vars(value), human_holds=lambda: state.holder == 'human', get=lambda: SimpleNamespace(**vars(state)),
                                  acquire=acquire, release=release)
    desktop.runtime = SimpleNamespace(status=lambda: SimpleNamespace(as_dict=lambda: {'running': True}))
    monkeypatch.setitem(sys.modules, 'tools', ModuleType('tools'))
    monkeypatch.setitem(sys.modules, 'tools.bot_desktop', desktop)
    plugin = load('gate_plugin', '/app/plugins/hermbot/__init__.py')
    calls = []
    monkeypatch.setattr(plugin, 'post', lambda path, body: calls.append((path, body)) or {})
    adapter = load('gate_adapter', '/app/docker/runtime_api.py')
    monkeypatch.setattr(adapter, 'HOME', tmp_path)
    return SimpleNamespace(plugin=plugin, adapter=adapter, state=state, home=tmp_path, calls=calls, transitions=transitions)


def test_later_worker_does_not_block_earlier_harness_dispatch(runtime):
    """The continuation mimics Hermes's ordering condition; entry is deliberately reversed."""
    later_entered = threading.Event()
    earlier_started = threading.Event()
    dispatches = []
    def continuation(index):
        if index == 1:
            later_entered.set()
            assert earlier_started.wait(2), 'Earlier tool was prevented from reaching the harness'
        else:
            dispatches.append(index)
            earlier_started.set()
            return 'finished'
        dispatches.append(index)
        return 'finished'
    with ThreadPoolExecutor(2) as pool:
        later = pool.submit(runtime.plugin.execution_boundary, 'read_file', {}, lambda args: continuation(1), task_id='same-task')
        assert later_entered.wait(2)
        earlier = pool.submit(runtime.plugin.execution_boundary, 'read_file', {}, lambda args: continuation(0), task_id='same-task')
        assert earlier.result(timeout=3) == later.result(timeout=3) == 'finished'
    assert dispatches == [0, 1]


def test_takeover_drains_every_parallel_callback_and_rejects_new_admission(runtime, monkeypatch):
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    takeover_waiting = threading.Event()
    original_drain = runtime.adapter.drain
    @contextmanager
    def observed_drain(*args, **kwargs):
        takeover_waiting.set()
        with original_drain(*args, **kwargs):
            yield
    monkeypatch.setattr(runtime.adapter, 'drain', observed_drain)
    def tool(index):
        entered[index].set()
        assert release[index].wait(5)
        return f'finished-{index}'
    with ThreadPoolExecutor(3) as pool:
        workers = [pool.submit(runtime.plugin.execution_boundary, 'terminal', {}, lambda args, i=i: tool(i), task_id='same-task') for i in range(2)]
        try:
            assert all(event.wait(2) for event in entered), 'Parallel admission was serialized'
            takeover = pool.submit(runtime.adapter.control, runtime.adapter.Control(action='takeover', viewer_id=OWNER))
            assert takeover_waiting.wait(2)
            assert not runtime.adapter.ready_for_input(OWNER)
            assert not takeover.done()
            result = runtime.plugin.execution_boundary('terminal', {}, lambda args: pytest.fail('New tool executed during takeover'), task_id='same-task')
            assert json.loads(result)['error'] == 'human_has_control'
            release[0].set()
            assert workers[0].result(timeout=2) == 'finished-0'
            assert not takeover.done() and not runtime.adapter.ready_for_input(OWNER)
            release[1].set()
            assert takeover.result(timeout=3)['input_ready']
            assert workers[1].result(timeout=2) == 'finished-1'
        finally:
            for event in release:
                event.set()
    runtime.adapter.control(runtime.adapter.Control(action='handback', viewer_id=OWNER))
    assert not runtime.adapter.ready_for_input(OWNER)
    assert runtime.plugin.execution_boundary('read_file', {}, lambda args: 'resumed', task_id='same-task') == 'resumed'


def test_failure_and_cancellation_release_only_after_callback_returns(runtime):
    class Cancelled(BaseException):
        pass
    for error in [ValueError('tool failed'), OSError('tool I/O failed after execution'), Cancelled()]:
        def callback(args):
            with gate.descriptor(runtime.home) as fd:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise error
        with pytest.raises(type(error)):
            runtime.plugin.execution_boundary('terminal', {}, callback, task_id='same-task')
        with gate.drain(runtime.home, lambda: True, timeout=0):
            pass


def test_takeover_timeout_preserves_pause_without_enabling_input(runtime, monkeypatch):
    original_drain = runtime.adapter.drain
    monkeypatch.setattr(runtime.adapter, 'drain', lambda home, owner: original_drain(home, owner, timeout=0))
    with gate.admit(runtime.home, lambda: False):
        with pytest.raises(HTTPException) as error:
            runtime.adapter.control(runtime.adapter.Control(action='takeover', viewer_id=OWNER))
        assert error.value.status_code == 409
        assert runtime.state.holder == 'human'
        assert not runtime.adapter.ready_for_input(OWNER)
    assert runtime.adapter.control(runtime.adapter.Control(action='takeover', viewer_id=OWNER))['input_ready']


def test_takeover_does_not_publish_stale_owner_epoch(runtime, monkeypatch):
    @contextmanager
    def changed_epoch(home, owns_control):
        runtime.state.epoch += 1  # same viewer, different ownership transition
        with gate.drain(home, owns_control, timeout=0):
            yield
    monkeypatch.setattr(runtime.adapter, 'drain', changed_epoch)
    with pytest.raises(HTTPException) as error:
        runtime.adapter.control(runtime.adapter.Control(action='takeover', viewer_id=OWNER))
    assert error.value.status_code == 409
    assert not runtime.adapter.ready_for_input(OWNER)


def test_admission_rechecks_human_ownership_after_acquisition(runtime, monkeypatch):
    original_flock = gate.fcntl.flock
    def takeover_at_acquisition(fd, operation):
        result = original_flock(fd, operation)
        runtime.state.holder = 'human'
        return result
    monkeypatch.setattr(gate.fcntl, 'flock', takeover_at_acquisition)
    result = runtime.plugin.execution_boundary('read_file', {}, lambda args: pytest.fail('Tool raced takeover'), task_id='same-task')
    assert json.loads(result)['error'] == 'human_has_control'


def test_blocked_admission_is_nonblocking_and_does_not_claim_human_ownership(runtime):
    with gate.descriptor(runtime.home) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        result = runtime.plugin.execution_boundary('read_file', {}, lambda args: pytest.fail('Unadmitted callback ran'), task_id='same-task')
        assert json.loads(result)['error'] == 'tool_admission_unavailable'
        assert not any(path == '/internal/computer-paused' for path, body in runtime.calls)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo'])
def test_lock_path_cannot_follow_links_or_open_special_files(runtime, kind):
    path = runtime.home / 'hermbot-tool-gate.lock'
    target = runtime.home / 'other'
    target.write_text('private')
    if kind == 'symlink':
        path.symlink_to(target)
    elif kind == 'hardlink':
        import os
        os.link(target, path)
    else:
        import os
        os.mkfifo(path)
    with pytest.raises(gate.GateUnavailable):
        with gate.admit(runtime.home, lambda: False):
            pytest.fail('Unsafe admission path was accepted')
    assert target.read_text() == 'private'


def test_each_admission_descriptor_is_private_and_not_inherited(runtime):
    with gate.descriptor(runtime.home) as first, gate.descriptor(runtime.home) as second:
        import os
        assert first != second
        assert not os.get_inheritable(first) and not os.get_inheritable(second)


def test_competing_viewer_abandons_old_drain_without_granting_input(runtime, monkeypatch):
    @contextmanager
    def changed_viewer(home, owns_control):
        runtime.state.viewer_id = 'second-owner'
        with gate.drain(home, owns_control, timeout=0):
            yield
    monkeypatch.setattr(runtime.adapter, 'drain', changed_viewer)
    with pytest.raises(HTTPException):
        runtime.adapter.control(runtime.adapter.Control(action='takeover', viewer_id=OWNER))
    assert not runtime.adapter.ready_for_input(OWNER)
    assert not runtime.adapter.ready_for_input('second-owner')


@pytest.mark.parametrize('alteration', [{}, {'no_agent': False}, {'script': 'different.py'}, {'schedule': {'kind': 'interval', 'minutes': 2}}])
def test_native_routine_wake_accepts_only_fixed_script_and_minute_timer(runtime, monkeypatch, alteration):
    from pathlib import Path
    from urllib import request
    from io import BytesIO
    job = {'id': 'managed-job', 'name': 'Hermbot wake 00000000-0000-0000-0000-000000000001', 'no_agent': True,
           'script': 'hermbot_routine_wakeup.py', 'schedule': {'kind': 'interval', 'minutes': 1}, 'enabled': True, **alteration}
    original_read = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda path, *a, **kw: '# fixed script' if str(path) == '/opt/hermbot/routine_wakeup.py' else original_read(path, *a, **kw))
    monkeypatch.setattr(request, 'urlopen', lambda *a, **kw: BytesIO(json.dumps({'jobs': [job]}).encode()))
    monkeypatch.setenv('API_SERVER_KEY', 'private-native-key')
    monkeypatch.setenv('BOT_ID', 'researcher')
    body = runtime.adapter.RoutineScheduler(generation='00000000-0000-0000-0000-000000000001', token='scoped-wake-token-' * 3, enabled=True)
    if alteration:
        with pytest.raises(HTTPException) as error:
            runtime.adapter.routine_scheduler(body)
        assert error.value.status_code == 409
    else:
        assert runtime.adapter.routine_scheduler(body)['job_id'] == job['id']
        config = json.loads((runtime.home / 'hermbot-routine-wakeup.json').read_text())
        assert config['url'].endswith('/internal/routine-wake/researcher/' + body.generation)
        assert config['token'] == body.token
