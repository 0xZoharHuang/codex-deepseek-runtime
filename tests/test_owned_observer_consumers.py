"""Consumer lifecycle checks using psutil's real children algorithm and owned OS probes."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest
import codex_deepseek.processes as process_ids
import test_native_payload_retry as payload
import verify_mixed_stream_faults as mixed
from ci import qualify_native as qualify
from test_qualification_scope import _observed_process_graph


def graph_case(monkeypatch, mode):
    q, graph, callbacks, signals, recursive, root = _observed_process_graph(
        monkeypatch, foreign=mode == 'foreign')
    if mode == 'parent-reuse':
        callbacks[('children', 200)] = lambda: graph[200].update(birth='foreign-70', ctime=70, ppid=999)
    elif mode == 'child-reuse':
        callbacks[('cmdline', 200)] = lambda: graph[200].update(birth='foreign-80', ctime=80, ppid=999)
        graph[300]['alive'] = False
    elif mode == 'denied-edge':
        original = psutil.Process.ppid
        def denied(self):
            if self.pid == 200:
                raise psutil.AccessDenied(200)
            return original(self)
        monkeypatch.setattr(psutil.Process, 'ppid', denied)
    elif mode == 'natural-exit':
        graph[300]['alive'] = False
        def gone():
            graph[200]['alive'] = False
            raise psutil.NoSuchProcess(200)
        callbacks[('ppid', 200)] = gone
    elif mode == 'owner-missing':
        monkeypatch.setattr(process_ids, 'identity', lambda pid: None)
    return q, graph, callbacks, signals, recursive, root


def async_clock(q, **extra):
    async def sleep(seconds):
        q.time.sleep(seconds)
        await asyncio.sleep(0)
    return SimpleNamespace(Event=asyncio.Event, create_task=asyncio.create_task,
                           to_thread=asyncio.to_thread, sleep=sleep,
                           wait_for=asyncio.wait_for, subprocess=asyncio.subprocess,
                           TimeoutError=asyncio.TimeoutError, **extra)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['foreign', 'owned-survivor', 'parent-reuse', 'child-reuse', 'denied-edge', 'natural-exit'])
async def test_mixed_cleanup_never_promotes_psutil_snapshot_candidates(monkeypatch, tmp_path, mode):
    q, graph, callbacks, signals, recursive, root = graph_case(monkeypatch, mode)
    monkeypatch.setattr(mixed, 'os', SimpleNamespace(getpid=lambda: 100))
    monkeypatch.setattr(mixed, 'asyncio', async_clock(q))
    monkeypatch.setattr(mixed, 'time', SimpleNamespace(monotonic=q.time.monotonic, time=q.time.monotonic))
    for name in ('identity', 'matches', 'terminate'):
        monkeypatch.setattr(mixed.base, name, getattr(process_ids, name))
    monkeypatch.setattr(mixed.base, 'private_directory', lambda path: path.mkdir(parents=True, exist_ok=True))
    class StopBeforeNative:
        def __init__(self, root, kind): self.requests = []
        async def start(self):
            await asyncio.sleep(0)
            raise RuntimeError('controlled stop before native or network')
    monkeypatch.setattr(mixed, 'MixedUpstream', StopBeforeNative)
    executable = tmp_path / 'synthetic-binary'; executable.write_bytes(b'no execution')
    report = await mixed.case(tmp_path / 'case', executable, executable, executable, executable, 'eof')
    assert report['passed'] is False and 'controlled stop' in report['error']
    assert not any(recursive) and all(pid != 100 for pid, birth in signals)
    assert all(row['pid'] != 100 for row in report['owned_processes'])
    if mode == 'owned-survivor':
        assert {pid for pid, birth in signals} == {200, 300}
        assert 'Owned subprocesses survived normal cleanup' in report['cleanup_errors']
    else:
        assert signals == []
    if mode == 'foreign':
        assert graph[200]['alive'] and graph[300]['alive']
        assert report['owned_processes'] == [] and report['rejected_processes']
    if mode in {'parent-reuse', 'child-reuse', 'denied-edge'}:
        assert report['unknown_live']
        assert 'Process ownership observation remained unresolved' in report['cleanup_errors']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['foreign', 'owned-survivor', 'parent-reuse', 'child-reuse', 'denied-edge', 'natural-exit', 'owner-missing'])
async def test_payload_consumer_uses_proven_edges_and_keeps_cleanup_failures(monkeypatch, tmp_path, mode):
    q, graph, callbacks, signals, recursive, root = graph_case(monkeypatch, mode)
    main_actions = []
    class Process:
        pid = 100
        returncode = None
        async def communicate(self, input=None):
            await asyncio.sleep(0)
            self.returncode = 0; graph[100]['alive'] = False
            return b'FIXTURE_OK', b''
        def terminate(self):
            main_actions.append('terminate'); self.returncode = 1; graph[100]['alive'] = False
        def kill(self):
            main_actions.append('kill'); self.returncode = 1; graph[100]['alive'] = False
        async def wait(self): return self.returncode
    process = Process()
    async def create(*args, **kwargs): return process
    monkeypatch.setattr(payload, 'asyncio', async_clock(q, create_subprocess_exec=create))
    monkeypatch.setattr(payload, 'time', SimpleNamespace(monotonic=q.time.monotonic))
    for name in ('identity', 'matches', 'terminate'):
        monkeypatch.setattr(payload, name, getattr(process_ids, name))
    monkeypatch.setattr(payload, 'binary', lambda: tmp_path / 'synthetic.exe')
    monkeypatch.setattr(payload, 'environment', lambda *args: {})
    saved = []; monkeypatch.setattr(payload, 'evidence', lambda path, name, record: saved.append(record.copy()))
    record = await payload.invoke(tmp_path / 'home', 1, tmp_path / 'work')
    assert len(saved) == 1 and not any(recursive) and main_actions == []
    assert all(row['pid'] != 100 for row in record['children'])
    if mode == 'owned-survivor':
        assert {pid for pid, birth in signals} == {200, 300}
        assert record['remaining_before_cleanup'] and record['forced_owned_cleanup']
        assert record['remaining_after_cleanup'] == []
    else:
        assert signals == []
    if mode == 'foreign':
        assert graph[200]['alive'] and graph[300]['alive']
        assert record['children'] == [] and record['rejected_processes']
    if mode in {'foreign', 'natural-exit'}:
        payload.capacity_natural_exit_assertions(record)
    else:
        with pytest.raises(AssertionError): payload.capacity_natural_exit_assertions(record)
    if mode in {'parent-reuse', 'child-reuse', 'denied-edge'}:
        assert record['unknown_live']
    if mode == 'owner-missing':
        assert record['observation_errors'][0]['reason'] == 'created_owner_identity_missing'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'cancellation'])
async def test_payload_keeps_main_handle_cleanup_and_failure_lifecycle(monkeypatch, tmp_path, failure):
    q, graph, callbacks, signals, recursive, root = graph_case(monkeypatch, 'foreign')
    actions = []
    class Process:
        pid = 100
        returncode = None
        async def communicate(self, input=None):
            await asyncio.sleep(0)
            raise asyncio.TimeoutError() if failure == 'timeout' else asyncio.CancelledError()
        def terminate(self):
            actions.append('terminate'); self.returncode = 1; graph[100]['alive'] = False
        def kill(self):
            actions.append('kill'); self.returncode = 1; graph[100]['alive'] = False
        async def wait(self): return self.returncode
    async def create(*args, **kwargs): return Process()
    monkeypatch.setattr(payload, 'asyncio', async_clock(q, create_subprocess_exec=create))
    monkeypatch.setattr(payload, 'time', SimpleNamespace(monotonic=q.time.monotonic))
    for name in ('identity', 'matches', 'terminate'):
        monkeypatch.setattr(payload, name, getattr(process_ids, name))
    monkeypatch.setattr(payload, 'binary', lambda: tmp_path / 'synthetic.exe')
    monkeypatch.setattr(payload, 'environment', lambda *args: {})
    saved = []; monkeypatch.setattr(payload, 'evidence', lambda path, name, record: saved.append(record.copy()))
    with pytest.raises(asyncio.TimeoutError if failure == 'timeout' else asyncio.CancelledError):
        await payload.invoke(tmp_path / 'home', 1, tmp_path / 'work')
    assert len(saved) == 1 and actions == ['terminate'] and signals == []
    assert saved[0]['natural_main_exit'] is False
    assert saved[0]['main_timed_out'] is (failure == 'timeout')
    assert saved[0]['forced_owned_cleanup'][0]['role'] == 'main'
    assert graph[200]['alive'] and graph[300]['alive']


def test_real_owned_child_cleanup_keeps_unrelated_sibling_alive(tmp_path):
    """Actual local process handles; no native CLI, credentials, model or sandbox."""
    executable = sys._base_executable
    child = 'import time;time.sleep(30)'
    pid_file = tmp_path / 'owned-child.pid'
    parent_code = ('import subprocess,sys,time,pathlib;'
                   'p=subprocess.Popen([sys.executable,"-I","-c",'+repr(child)+']);'
                   'pathlib.Path('+repr(str(pid_file))+').write_text(str(p.pid));time.sleep(30)')
    parent = subprocess.Popen([executable, '-I', '-c', parent_code])
    sibling = subprocess.Popen([executable, '-I', '-c', child])
    family = {}; uncertain = {}; rejected = {}; errors = {}; owned = None
    try:
        birth = process_ids.identity(parent.pid); assert birth
        family[(parent.pid, birth)] = {'pid': parent.pid, 'birth': birth, 'ownership': 'created_Popen_handle'}
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            qualify._observe_process_family(family, uncertain, rejected, errors)
            if pid_file.exists():
                child_pid = int(pid_file.read_text())
                owned = next((x for x in family.values() if x['pid'] == child_pid), None)
                if owned: break
            time.sleep(.02)
        assert owned and owned['parent_pid'] == parent.pid and owned['parent_birth'] == birth
        assert sibling.pid not in {row['pid'] for row in family.values()} and sibling.poll() is None
        process_ids.terminate(owned['pid'], owned['birth'], timeout=2)
        assert not process_ids.matches(owned['pid'], owned['birth']) and sibling.poll() is None
        assert not uncertain and not errors
    finally:
        if owned and process_ids.matches(owned['pid'], owned['birth']):
            process_ids.terminate(owned['pid'], owned['birth'], timeout=2)
        for process in (parent, sibling):
            if process.poll() is None: process.terminate()
            process.wait(timeout=5)

@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'cancellation'])
async def test_actual_payload_main_retirement_preserves_live_unrelated_process(monkeypatch, tmp_path, failure):
    """Run the real controller with local OS fixtures; only the 120 s wait is shortened."""
    executable = sys._base_executable
    sibling = subprocess.Popen([executable, '-I', '-c', 'import time;time.sleep(30)'])
    created = asyncio.Event(); owned = []; saved = []
    async def create(*args, **kwargs):
        process = await asyncio.create_subprocess_exec(
            executable, '-I', '-c', 'import time;time.sleep(30)',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        owned.append(process); created.set(); return process
    async def wait_for(awaitable, timeout):
        return await asyncio.wait_for(awaitable, .2 if failure == 'timeout' and timeout == 120 else timeout)
    api = SimpleNamespace(Event=asyncio.Event, create_task=asyncio.create_task,
                          to_thread=asyncio.to_thread, sleep=asyncio.sleep,
                          wait_for=wait_for, subprocess=asyncio.subprocess,
                          TimeoutError=asyncio.TimeoutError, create_subprocess_exec=create)
    monkeypatch.setattr(payload, 'asyncio', api)
    monkeypatch.setattr(payload, 'binary', lambda: Path(executable))
    monkeypatch.setattr(payload, 'environment', lambda *args: {})
    monkeypatch.setattr(payload, 'evidence', lambda path, name, record: saved.append(record.copy()))
    task = asyncio.create_task(payload.invoke(tmp_path / 'home', 1, tmp_path / 'work'))
    try:
        await asyncio.wait_for(created.wait(), 5)
        if failure == 'cancellation':
            await asyncio.sleep(.1); task.cancel()
        with pytest.raises(asyncio.TimeoutError if failure == 'timeout' else asyncio.CancelledError):
            await task
        assert owned[0].returncode is not None and sibling.poll() is None
        record = saved[0]
        assert record['natural_main_exit'] is False
        assert record['main_timed_out'] is (failure == 'timeout')
        assert record['forced_owned_cleanup'] and all(row['role'] == 'main' for row in record['forced_owned_cleanup'])
        assert record['remaining_after_cleanup'] == [] and record['unknown_live'] == []
        assert record['observation_errors'] == [] and record['cleanup_errors'] == []
        assert sibling.pid not in {row['pid'] for row in record['children']}
    finally:
        if not task.done(): task.cancel(); await asyncio.gather(task, return_exceptions=True)
        for process in owned:
            if process.returncode is None: process.terminate(); await process.wait()
        if sibling.poll() is None: sibling.terminate()
        sibling.wait(timeout=5)
