"""Downstream delivery loss is distinct from upstream/protocol failure."""
import asyncio
from contextlib import asynccontextmanager
import json

import aiohttp
from aiohttp import web
import pytest

import codex_deepseek.gateway as module
from codex_deepseek.gateway import Gateway
from codex_deepseek.protocol import MODEL, encode_sse
from codex_deepseek.state import Store
from test_gateway import fake_upstream


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("boundary", ["midstream", "terminal_write", "terminal_eof"])
async def test_real_client_close_then_server_write_records_delivery_loss(
    fake_upstream, tmp_path, monkeypatch, boundary
):
    """Disable handler cancellation only in this fixture to select the write-error race.

    The production cancellation path remains tested in test_gateway. Here the
    actual localhost socket closes, then the real pinned aiohttp writer runs.
    """
    original_response = web.StreamResponse
    continue_upstream = asyncio.Event()
    continue_eof = asyncio.Event()
    terminal_sent = asyncio.Event()
    handler_finished = asyncio.Event()
    upstream_closed = asyncio.Event()
    local_requests = []
    write_errors = []
    calls = []

    class ObservedResponse(original_response):
        async def prepare(self, request):
            local_requests.append(request)
            return await super().prepare(request)

        async def write(self, data):
            try:
                await super().write(data)
            except ConnectionError as exc:
                write_errors.append(("write", type(exc).__name__, str(exc)))
                raise
            if boundary == "terminal_eof" and b"event: response.completed\n" in data:
                terminal_sent.set()
                await continue_eof.wait()

        async def write_eof(self, *args, **kwargs):
            try:
                return await super().write_eof(*args, **kwargs)
            except ConnectionError as exc:
                write_errors.append(("write_eof", type(exc).__name__, str(exc)))
                raise

    class ObservedGateway(Gateway):
        async def responses(self, request):
            try:
                return await super().responses(request)
            finally:
                handler_finished.set()

    async def provider(request):
        calls.append(await request.json())
        response = original_response(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(encode_sse({
            "type": "response.created",
            "response": {"id": "resp_disconnect", "status": "in_progress", "output": []},
        }))
        try:
            await continue_upstream.wait()
            if boundary == "midstream":
                event = {"type": "response.in_progress", "response": {
                    "id": "resp_disconnect", "status": "in_progress", "output": []}}
            else:
                event = {"type": "response.completed", "response": {
                    "id": "resp_disconnect", "status": "completed", "output": [{
                        "id": "tool_one", "type": "function_call", "name": "fixture",
                        "call_id": "call_one", "arguments": "{}",
                    }]}}
            await response.write(encode_sse(event))
            # The gateway must close this provider connection on delivery loss,
            # including after a terminal event; no second request is needed.
            await asyncio.Event().wait()
        finally:
            upstream_closed.set()

    fake_upstream.append(provider)
    monkeypatch.setattr(module.web, "StreamResponse", ObservedResponse)
    store = Store(tmp_path / "state.sqlite")
    trace = tmp_path / "trace"
    try:
        async with ObservedGateway("SECRET", "LOCAL", store, {}, trace_source=lambda: trace) as gateway:
            assert gateway.runner.server.handler_cancellation is True
            gateway.runner.server.handler_cancellation = False
            async with aiohttp.ClientSession(trust_env=False) as client:
                response = await client.post(
                    f"http://127.0.0.1:{gateway.port}/v1/responses",
                    json={"model": MODEL, "stream": True, "tools": [{
                        "type": "function", "name": "fixture", "parameters": {"type": "object"}}]},
                    headers={"Authorization": "Bearer LOCAL"},
                )
                await asyncio.wait_for(response.content.readuntil(b"\n\n"), 5)
                if boundary == "terminal_eof":
                    continue_upstream.set()
                    await asyncio.wait_for(terminal_sent.wait(), 5)
                response.close()
                await until(lambda: bool(local_requests) and (
                    local_requests[0].transport is None
                    or local_requests[0].transport.is_closing()
                ))
                continue_upstream.set()
                continue_eof.set()
                await asyncio.wait_for(handler_finished.wait(), 5)
                await asyncio.wait_for(upstream_closed.wait(), 5)

            key, status, upstream, response_id, detail = store.db.execute(
                "SELECT id,status,upstream_status,response_id,detail FROM receipts"
            ).fetchone()
            assert len(calls) == 1
            assert write_errors and write_errors[0][1:] == (
                "ClientConnectionResetError", "Cannot write to closing transport")
            assert upstream == 200 and response_id == "resp_disconnect"
            observation = json.loads((trace / (key + "-delivery.json")).read_text())
            assert observation == {
                "connection_status": "disconnected",
                "delivery_status": "unknown",
                "operation": "write_eof" if boundary == "terminal_eof" else "write",
                "error_type": "ClientConnectionResetError",
                "upstream_terminal": None if boundary == "midstream" else "completed",
            }
            fingerprints = store.db.execute(
                "SELECT key FROM adapter_metadata WHERE key LIKE 'native-complete-v1:%'"
            ).fetchall()
            if boundary == "midstream":
                assert status == "cancelled"
                assert "Downstream response disconnected during write" in detail
                assert "delivery outcome unknown" in detail
                assert fingerprints == []
            else:
                # A completed receipt is the already-known upstream result,
                # not proof that the native client received or executed it.
                assert status == "completed" and detail is None
                assert len(fingerprints) == 1
    finally:
        continue_upstream.set()
        continue_eof.set()
        store.close()


class Request:
    headers = {"Authorization": "Bearer LOCAL"}

    async def json(self):
        return {"model": MODEL, "stream": True}


class LocalUpstream:
    status = 200
    headers = {}

    def __init__(self, fault=None):
        self.content = self
        self.fault = fault

    async def iter_any(self):
        yield encode_sse({"type": "response.created", "response": {
            "id": "resp_boundary", "status": "in_progress", "output": []}})
        if isinstance(self.fault, bytes):
            yield self.fault
        elif self.fault is not None:
            raise self.fault
        yield encode_sse({"type": "response.completed", "response": {
            "id": "resp_boundary", "status": "completed", "output": []}})


class LocalTransport:
    def __init__(self, fault=None):
        self.calls = 0
        self.closed = False
        self.fault = fault

    @asynccontextmanager
    async def post(self, *args, **kwargs):
        self.calls += 1
        try:
            yield LocalUpstream(self.fault)
        finally:
            self.closed = True


class MemoryResponse:
    def __init__(self, *args, **kwargs):
        self.prepared = False
        self.output = []

    async def prepare(self, request):
        self.prepared = True

    async def write(self, data):
        self.output.append(data)

    async def write_eof(self):
        pass


@pytest.mark.parametrize("operation", ["prepare", "write", "write_eof"])
@pytest.mark.parametrize("exception", [
    ConnectionResetError("reset"), BrokenPipeError("pipe"), ConnectionError("Connection lost"),
])
async def test_only_explicit_downstream_io_connection_errors_are_cancelled(
    tmp_path, monkeypatch, operation, exception
):
    class DisconnectResponse(MemoryResponse):
        pass

    async def fail(self, *args):
        raise exception

    setattr(DisconnectResponse, operation, fail)
    monkeypatch.setattr(module.web, "StreamResponse", DisconnectResponse)
    store = Store(tmp_path / "state.sqlite")
    trace = tmp_path / "trace"
    gateway = Gateway("SECRET", "LOCAL", store, {}, trace_source=lambda: trace)
    gateway.client = LocalTransport()
    try:
        await gateway.responses(Request())
        key, status, detail = store.db.execute("SELECT id,status,detail FROM receipts").fetchone()
        assert gateway.client.calls == 1 and gateway.client.closed
        assert status == ("completed" if operation == "write_eof" else "cancelled")
        observation = json.loads((trace / (key + "-delivery.json")).read_text())
        assert observation["operation"] == operation
        assert observation["error_type"] == type(exception).__name__
        assert observation["delivery_status"] == "unknown"
    finally:
        store.close()


@pytest.mark.parametrize("fault", [
    ConnectionResetError("upstream reset"),
    aiohttp.ClientConnectionResetError("Cannot write to closing transport"),
    module.ProtocolError("invalid upstream protocol"),
    b"data: {invalid JSON\n\n",
])
async def test_upstream_error_never_becomes_downstream_cancellation(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(module.web, "StreamResponse", MemoryResponse)
    store = Store(tmp_path / "state.sqlite")
    trace = tmp_path / "trace"
    gateway = Gateway("SECRET", "LOCAL", store, {}, trace_source=lambda: trace)
    gateway.client = LocalTransport(fault)
    try:
        response = await gateway.responses(Request())
        assert gateway.client.calls == 1 and gateway.client.closed
        status, detail = store.db.execute("SELECT status,detail FROM receipts").fetchone()
        assert status == "adapter_error"
        if isinstance(fault, bytes):
            assert "Expecting property name" in detail
        else:
            assert detail == str(fault)
        assert b"response.failed" in b"".join(response.output)
        assert not list(trace.glob("*-delivery.json"))
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["prepare", "write"])
@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, ValueError])
async def test_writer_programming_errors_remain_adapter_errors(tmp_path, monkeypatch, operation, error_type):
    class BuggyResponse(MemoryResponse):
        pass

    async def fail(self, *args):
        if not getattr(self, "failed_once", False):
            self.failed_once = True
            raise error_type("synthetic writer programming error")
        return await getattr(super(BuggyResponse, self), operation)(*args)

    setattr(BuggyResponse, operation, fail)
    monkeypatch.setattr(module.web, "StreamResponse", BuggyResponse)
    store = Store(tmp_path / "state.sqlite")
    trace = tmp_path / "trace"
    gateway = Gateway("SECRET", "LOCAL", store, {}, trace_source=lambda: trace)
    gateway.client = LocalTransport()
    try:
        await gateway.responses(Request())
        status, detail = store.db.execute("SELECT status,detail FROM receipts").fetchone()
        assert status == "adapter_error" and detail == "synthetic writer programming error"
        assert not list(trace.glob("*-delivery.json"))
    finally:
        store.close()
