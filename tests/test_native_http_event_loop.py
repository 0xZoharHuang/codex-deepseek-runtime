"""Owned local regression: slow metadata persistence must not stall the event loop."""
import asyncio
import json
import socket
import threading
import time

from aiohttp import web
import pytest

from codex_deepseek.native_http import NativeTransport
from codex_deepseek import service


async def test_slow_native_metadata_does_not_block_other_loopback_requests(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    seen = {}
    original = service.atomic_json

    def slow_atomic(path, value):
        entered.set()
        if not release.wait(3):
            raise TimeoutError('fixture did not release the owned metadata write')
        original(path, value)

    monkeypatch.setattr(service, 'atomic_json', slow_atomic)
    app = web.Application()
    async def health(request):
        return web.Response(text='READY')
    app.router.add_get('/health', health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 0).start()
    port = runner.addresses[0][1]
    transport = NativeTransport('http://127.0.0.1:1', metadata_path=tmp_path/'native-http.json')

    def client():
        try:
            if not entered.wait(8):
                seen['error'] = 'metadata write was not reached'
                return
            with socket.create_connection(('127.0.0.1', port), timeout=1) as peer:
                peer.settimeout(1)
                peer.sendall(b'GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                seen['response'] = peer.recv(4096)
        except OSError as exc:
            seen['error'] = type(exc).__name__
        finally:
            release.set()

    worker = threading.Thread(target=client, daemon=True)
    worker.start()
    try:
        await transport.start()
        assert b'200 OK' in seen.get('response', b''), seen
        value = json.loads((tmp_path/'native-http.json').read_text())
        assert value['pid'] == transport.process.pid and value['port'] == transport.port
    finally:
        release.set()
        await asyncio.to_thread(worker.join, 4)
        await transport.close()
        await runner.cleanup()
    assert not worker.is_alive()


async def test_cancel_waits_for_owned_metadata_write_before_stopping_helper(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original = service.atomic_json

    def slow_atomic(path, value):
        entered.set()
        if not release.wait(3):
            raise TimeoutError('fixture did not release the owned metadata write')
        original(path, value)

    monkeypatch.setattr(service, 'atomic_json', slow_atomic)
    transport = NativeTransport('http://127.0.0.1:1', metadata_path=tmp_path/'native-http.json')
    guard = threading.Timer(3, release.set)
    guard.start()
    task = asyncio.create_task(transport.start())
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
        assert entered.is_set() and not release.is_set()
        task.cancel()
        await asyncio.sleep(.05)
        assert not task.done(), 'cancellation must not abandon the outstanding metadata write'
        assert transport.process is not None and transport.process.returncode is None
        task.cancel()
        await asyncio.sleep(.05)
        assert not task.done(), 'a repeated cancellation must not abandon the write either'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert transport.process.returncode is not None
        assert (tmp_path/'native-http.json').is_file()
    finally:
        release.set()
        guard.cancel()
        if not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
        await transport.close()


async def test_metadata_write_failure_does_not_leave_native_helper_running(tmp_path, monkeypatch):
    def failed_atomic(path, value):
        raise OSError('synthetic persistence failure')

    monkeypatch.setattr(service, 'atomic_json', failed_atomic)
    transport = NativeTransport('http://127.0.0.1:1', metadata_path=tmp_path/'native-http.json')
    try:
        with pytest.raises(OSError, match='synthetic persistence failure'):
            await transport.start()
        assert transport.process is not None and transport.process.returncode is not None
        assert not (tmp_path/'native-http.json').exists()
    finally:
        await transport.close()


async def test_close_waits_for_inflight_metadata_before_retiring_helper(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original = service.atomic_json

    def slow_atomic(path, value):
        entered.set()
        if not release.wait(3):
            raise TimeoutError('fixture did not release the owned metadata write')
        original(path, value)

    monkeypatch.setattr(service, 'atomic_json', slow_atomic)
    transport = NativeTransport('http://127.0.0.1:1', metadata_path=tmp_path/'native-http.json')
    starter = asyncio.create_task(transport.start())
    closer = None
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
        closer = asyncio.create_task(transport.close())
        await asyncio.sleep(.05)
        assert not closer.done(), 'close must wait for the outstanding metadata write'
        assert transport.process.returncode is None
        release.set()
        await asyncio.gather(starter, closer)
        assert transport.process.returncode is not None
        assert json.loads((tmp_path/'native-http.json').read_text())['pid'] == transport.process.pid
        with pytest.raises(RuntimeError, match='closed'):
            await transport.start()
    finally:
        release.set()
        await asyncio.gather(starter, *( [closer] if closer else []), return_exceptions=True)
        await transport.close()


async def test_concurrent_helpers_do_not_race_on_the_same_metadata_path(tmp_path, monkeypatch):
    original = service.atomic_json
    guard = threading.Lock()
    count = {'active': 0, 'maximum': 0}

    def observed_atomic(path, value):
        with guard:
            count['active'] += 1
            count['maximum'] = max(count['maximum'], count['active'])
        try:
            time.sleep(.1)
            original(path, value)
        finally:
            with guard:
                count['active'] -= 1

    monkeypatch.setattr(service, 'atomic_json', observed_atomic)
    path = tmp_path/'native-http.json'
    first = NativeTransport('http://127.0.0.1:1', metadata_path=path)
    second = NativeTransport('http://127.0.0.1:1', metadata_path=path)
    try:
        await asyncio.gather(first.start(), second.start())
        assert count['maximum'] == 1
        value = json.loads(path.read_text())
        assert value['pid'] in {first.process.pid, second.process.pid}
    finally:
        await asyncio.gather(first.close(), second.close())


async def test_different_metadata_paths_do_not_share_a_blocking_write_lock(tmp_path, monkeypatch):
    original = service.atomic_json
    both_started = threading.Barrier(2, timeout=3)

    def concurrent_atomic(path, value):
        both_started.wait()
        original(path, value)

    monkeypatch.setattr(service, 'atomic_json', concurrent_atomic)
    first = NativeTransport('http://127.0.0.1:1', metadata_path=tmp_path/'first.json')
    second = NativeTransport('http://127.0.0.1:1', metadata_path=tmp_path/'second.json')
    try:
        await asyncio.gather(first.start(), second.start())
        assert json.loads((tmp_path/'first.json').read_text())['pid'] == first.process.pid
        assert json.loads((tmp_path/'second.json').read_text())['pid'] == second.process.pid
    finally:
        await asyncio.gather(first.close(), second.close())
