"""Regression for skipped Proactor cleanup, without changing the shared stdlib."""
import asyncio
import ctypes
import hashlib
import inspect
import logging
import os
import socket
import sys
from types import SimpleNamespace

from aiohttp import web
import pytest

from codex_deepseek import windows_asyncio as compat


class Socket:
    def __init__(self, *, shutdown_error=None, close_error=None, fileno=10):
        self.shutdown_error = shutdown_error
        self.close_error = close_error
        self.fd = fileno
        self.shutdown_calls = 0
        self.close_calls = 0

    def fileno(self):
        return self.fd

    def shutdown(self, how):
        assert how == socket.SHUT_RDWR
        self.shutdown_calls += 1
        if self.shutdown_error:
            raise self.shutdown_error

    def close(self):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error
        self.fd = -1


def fixture_transport(sock=None, protocol_error=None):
    protocol_calls = []
    detached = []
    def lost(exc):
        protocol_calls.append(exc)
        if protocol_error:
            raise protocol_error
    transport = SimpleNamespace(
        _called_connection_lost=False,
        _protocol=SimpleNamespace(connection_lost=lost),
        _sock=sock if sock is not None else Socket(),
        _server=SimpleNamespace(_detach=detached.append),
    )
    return transport, protocol_calls, detached


def reset_error():
    return ctypes.WinError(10054) if os.name == "nt" else ConnectionResetError(10054, "fixture reset")


def assert_clean(transport, calls, detached, original_exc):
    assert calls == [original_exc]
    assert detached == [transport]
    assert transport._sock is None
    assert transport._server is None
    assert transport._called_connection_lost is True


def test_shutdown_error_closes_detaches_and_preserves_original_error():
    error = reset_error()
    sock = Socket(shutdown_error=error)
    transport, calls, detached = fixture_transport(sock)
    with pytest.raises(ConnectionResetError) as caught:
        compat._call_connection_lost(transport, None)
    assert caught.value is error
    assert sock.shutdown_calls == sock.close_calls == 1
    assert_clean(transport, calls, detached, None)


@pytest.mark.parametrize("fd", [10, -1])
def test_normal_close_and_repeat_are_idempotent(fd):
    sock = Socket(fileno=fd)
    transport, calls, detached = fixture_transport(sock)
    cause = RuntimeError("original connection loss")
    compat._call_connection_lost(transport, cause)
    compat._call_connection_lost(transport, cause)
    assert sock.shutdown_calls == (0 if fd == -1 else 1)
    assert sock.close_calls == 1
    assert_clean(transport, calls, detached, cause)


def test_socket_without_shutdown_still_closes():
    closes = []
    transport, calls, detached = fixture_transport(SimpleNamespace(close=lambda: closes.append(True)))
    compat._call_connection_lost(transport, None)
    assert closes == [True]
    assert_clean(transport, calls, detached, None)


def test_protocol_error_still_cleans_and_propagates():
    error = RuntimeError("protocol failed")
    transport, calls, detached = fixture_transport(protocol_error=error)
    with pytest.raises(RuntimeError) as caught:
        compat._call_connection_lost(transport, None)
    assert caught.value is error
    assert_clean(transport, calls, detached, None)


def test_shutdown_error_keeps_protocol_error_context():
    protocol_error = RuntimeError("protocol failed")
    shutdown_error = reset_error()
    transport, calls, detached = fixture_transport(Socket(shutdown_error=shutdown_error), protocol_error)
    with pytest.raises(ConnectionResetError) as caught:
        compat._call_connection_lost(transport, None)
    assert caught.value is shutdown_error
    assert caught.value.__context__ is protocol_error
    assert_clean(transport, calls, detached, None)


def test_close_failure_is_not_falsely_marked_closed_or_detached():
    error = OSError("close failed")
    sock = Socket(close_error=error)
    transport, calls, detached = fixture_transport(sock)
    with pytest.raises(OSError) as caught:
        compat._call_connection_lost(transport, None)
    assert caught.value is error
    assert sock.close_calls == 1
    assert transport._sock is sock
    assert transport._server is not None
    assert not transport._called_connection_lost
    assert detached == [] and calls == [None]


@pytest.fixture
def original_target(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows stdlib Proactor is not available/imported on Unix")
    from asyncio import proactor_events
    target = proactor_events._ProactorBasePipeTransport
    current = target._call_connection_lost
    # Earlier full-suite tests may already have constructed NativeTransport.
    # Recover only the real method saved at the exact-source installation.
    if current is compat._call_connection_lost and compat._installed_target is target:
        original = compat._original_method
        assert original is not None
    else:
        original = current
    monkeypatch.setattr(target, "_call_connection_lost", original)
    monkeypatch.setattr(compat, "_WINDOWS", True)
    monkeypatch.setattr(compat, "_installed_target", None)
    monkeypatch.setattr(compat, "_original_method", None)
    monkeypatch.setattr(compat, "_warned", False)
    return target, original


def require_reviewed(original):
    reviewed = (
        inspect.isfunction(original)
        and hashlib.sha256(inspect.getsource(original).encode()).hexdigest() in compat._REVIEWED_SOURCES
    )
    if not reviewed:
        if sys.platform == "win32" and sys.version_info[:3] == (3, 13, 3):
            pytest.fail("Packaged Windows CPython3.13.3 must run the reviewed original-method controls")
        pytest.skip("Explicitly unqualified interpreter source; not the reviewed Windows qualification target")


def test_real_stdlib_installer_and_second_install_are_exact_identity(original_target, monkeypatch):
    target, original = original_target
    require_reviewed(original)
    assert compat.install_proactor_cleanup_backport() == "applied"
    assert target._call_connection_lost is compat._call_connection_lost
    monkeypatch.setattr(compat.inspect, "getsource", lambda _: pytest.fail("idempotence must not reinspect"))
    assert compat.install_proactor_cleanup_backport() == "already_applied"
    error = reset_error()
    transport, calls, detached = fixture_transport(Socket(shutdown_error=error))
    with pytest.raises(ConnectionResetError) as caught:
        target._call_connection_lost(transport, None)
    assert caught.value is error
    assert_clean(transport, calls, detached, None)


def test_unknown_wrapper_is_not_overwritten_and_warns_once(original_target, monkeypatch, caplog):
    target, original = original_target
    def wrapper(self, exc):
        return original(self, exc)
    monkeypatch.setattr(target, "_call_connection_lost", wrapper)
    with caplog.at_level(logging.WARNING, logger=compat.__name__):
        assert compat.install_proactor_cleanup_backport() == "not_applied"
        assert compat.install_proactor_cleanup_backport() == "not_applied"
    assert target._call_connection_lost is wrapper
    assert len(caplog.records) == 1
    assert "NOT_APPLIED" in caplog.text and "Original implementation retained" in caplog.text


@pytest.mark.parametrize("unavailable", [False, True])
def test_unknown_or_unavailable_source_remains_original(original_target, monkeypatch, caplog, unavailable):
    target, original = original_target
    def source(_):
        if unavailable:
            raise OSError("source not installed")
        return "unreviewed source"
    monkeypatch.setattr(compat.inspect, "getsource", source)
    with caplog.at_level(logging.WARNING, logger=compat.__name__):
        assert compat.install_proactor_cleanup_backport() == "not_applied"
    assert target._call_connection_lost is original
    assert len(caplog.records) == 1 and "NOT_APPLIED" in caplog.text


def test_unix_is_noop_even_without_proactor_source(monkeypatch):
    monkeypatch.setattr(compat, "_WINDOWS", False)
    monkeypatch.setattr(compat.inspect, "getsource", lambda _: pytest.fail("Unix must not inspect Proactor"))
    assert compat.install_proactor_cleanup_backport() == "not_applicable"


async def test_callback_error_is_still_delivered_to_default_handler(original_target, caplog):
    target, original = original_target
    require_reviewed(original)
    assert compat.install_proactor_cleanup_backport() == "applied"
    error = reset_error()
    transport, calls, detached = fixture_transport(Socket(shutdown_error=error))
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    seen = []
    def observe(loop, context):
        seen.append(context)
        loop.default_exception_handler(context)
    loop.set_exception_handler(observe)
    try:
        with caplog.at_level(logging.ERROR, logger="asyncio"):
            loop.call_soon(target._call_connection_lost, transport, None)
            await asyncio.sleep(0)
        assert len(seen) == 1 and seen[0]["exception"] is error
        assert "Exception in callback" in caplog.text
        assert_clean(transport, calls, detached, None)
    finally:
        loop.set_exception_handler(previous)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows Proactor/native IPC qualification")
async def test_real_native_http_ipc_keeps_proactor_pipes_eof_and_cleanup(original_target):
    from codex_deepseek.native_http import NativeTransport, native_helper
    target, original = original_target
    require_reviewed(original)
    loop = asyncio.get_running_loop()
    assert isinstance(loop, asyncio.ProactorEventLoop)
    calls = []
    async def endpoint(request):
        assert request.headers["Authorization"] == "Bearer FIXTURE_ONLY"
        calls.append(await request.json())
        return web.json_response({"ok": True})
    app = web.Application()
    app.router.add_post("/responses", endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    upstream = f"http://127.0.0.1:{runner.addresses[0][1]}/responses"
    transport = NativeTransport(upstream, binary=native_helper())
    assert target._call_connection_lost is compat._call_connection_lost
    child = None
    try:
        for sequence in range(2):
            async with transport.request("POST", upstream, api_key="FIXTURE_ONLY", payload={"sequence": sequence}) as response:
                assert response.status == 200
                assert await response.json() == {"ok": True}
        child = transport.process
        assert child is not None and child.returncode is None
        assert child.stdin.is_closing(), "Native bootstrap requires closed stdin/EOF after configuration"
        assert calls == [{"sequence": 0}, {"sequence": 1}]
    finally:
        await transport.close()
        await runner.cleanup()
    assert child is not None and child.returncode is not None
    assert runner.server is None


def test_original_stdlib_counterexample_requires_the_backport(original_target):
    target, original = original_target
    require_reviewed(original)
    error = reset_error()
    sock = Socket(shutdown_error=error)
    transport, calls, detached = fixture_transport(sock)
    with pytest.raises(ConnectionResetError) as caught:
        original(transport, None)
    assert caught.value is error
    assert calls == [None] and detached == []
    assert sock.close_calls == 0 and transport._sock is sock
    assert not transport._called_connection_lost


def test_missing_private_target_is_diagnosed_not_assumed_repaired(original_target, monkeypatch, caplog):
    from asyncio import proactor_events
    monkeypatch.delattr(proactor_events, "_ProactorBasePipeTransport")
    with caplog.at_level(logging.WARNING, logger=compat.__name__):
        assert compat.install_proactor_cleanup_backport() == "not_applied"
    assert "proactor_target_unavailable" in caplog.text


def test_invalid_signature_metadata_is_diagnosed_not_raised(original_target, monkeypatch, caplog):
    target, original = original_target
    monkeypatch.setattr(original, "__signature__", "invalid fixture signature", raising=False)
    with caplog.at_level(logging.WARNING, logger=compat.__name__):
        assert compat.install_proactor_cleanup_backport() == "not_applied"
    assert target._call_connection_lost is original
    assert "unrecognized_function_metadata" in caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows Proactor HTTP transport qualification")
@pytest.mark.parametrize("reset", [False, True], ids=["FIN", "RST"])
async def test_actual_aiohttp_connection_reaches_clean_state(original_target, reset):
    import struct
    target, original = original_target
    require_reviewed(original)
    assert compat.install_proactor_cleanup_backport() == "applied"
    accepted = []
    async def endpoint(request):
        accepted.append(request.transport)
        return web.Response(body=b"OK")
    app = web.Application()
    app.router.add_get("/", endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    port = runner.addresses[0][1]
    def client():
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n")
            body = b""
            while b"\r\n\r\n" not in body or not body.split(b"\r\n\r\n", 1)[1].endswith(b"OK"):
                chunk = connection.recv(4096)
                assert chunk, "Server closed before complete HTTP response"
                body += chunk
            assert body.startswith(b"HTTP/1.1 200 ")
            assert body.split(b"\r\n\r\n", 1)[1] == b"OK"
            if reset:
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
    try:
        await asyncio.wait_for(asyncio.to_thread(client), 3)
        async with asyncio.timeout(2):
            while not accepted or not accepted[0]._called_connection_lost or runner.server.connections:
                await asyncio.sleep(.01)
        assert accepted[0]._sock is None and accepted[0]._server is None
        assert not runner.server.connections
    finally:
        await runner.cleanup()
