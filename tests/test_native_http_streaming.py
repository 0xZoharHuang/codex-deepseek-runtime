"""Actual local IPC sockets: large bodies, exact encoding and stream ownership."""
import asyncio
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path

import aiohttp
from aiohttp import web
import pytest

import codex_deepseek.native_http as module


@pytest.mark.parametrize("kind", ["raw", "json"])
@pytest.mark.parametrize("outcome", [
    "success", "http_error", "consumer_error", "cancel_headers",
    "cancel_upload", "disconnect_upload",
])
async def test_large_ipc_body_stream_owns_exact_wire_and_closes(monkeypatch, kind, outcome):
    streams = []
    class TrackedStream(BytesIO):
        def __init__(self, data):
            super().__init__(data)
            self.final_position = None
            streams.append(self)
        def close(self):
            if not self.closed:
                self.final_position = self.tell()
            super().close()
    monkeypatch.setattr(module, "BytesIO", TrackedStream)
    raw = (b"\x00\xff \r\n" * 6801831)[:34009154]
    assert len(raw) == 34009154
    payload = {"text": "x" * 34000000, "unicode": "中文", "number": 1.0}
    url = "https://synthetic.invalid/responses"
    envelope = {"api_key": "SYNTHETIC_ONLY", "url": url, "method": "POST",
                "payload": payload, "policy": "default"}
    expected = raw if kind == "raw" else json.dumps(envelope).encode("utf-8")
    expected_digest = hashlib.sha256(expected).hexdigest()
    reached = asyncio.Event()
    exited = asyncio.Event()
    resume_reading = asyncio.Event()
    calls = []
    async def endpoint(request):
        row = {"headers": dict(request.headers), "path": request.path}
        calls.append(row)
        try:
            if outcome in {"cancel_upload", "disconnect_upload"}:
                row["prefix"] = await request.content.readexactly(65536)
                reached.set()
                if outcome == "disconnect_upload":
                    request.transport.abort()
                    return web.Response()
                # A paused receive buffer cannot observe the peer's EOF. Resume
                # reading only after the caller has cancelled its partial upload.
                await resume_reading.wait()
                try:
                    while await request.content.read(65536):
                        pass
                except ConnectionError:
                    row["disconnected"] = True
                    return web.Response()
                raise AssertionError("Cancelled partial upload unexpectedly completed")
            row["body"] = await request.read()
            reached.set()
            if outcome == "cancel_headers":
                await asyncio.Future()
            return web.Response(status=500 if outcome == "http_error" else 200,
                                body=b"synthetic-result", headers={"X-Fixture": "unchanged"})
        except asyncio.CancelledError:
            row["disconnected"] = True
            raise
        finally:
            exited.set()
    app = web.Application(client_max_size=40*1024*1024)
    app.router.add_post("/forward-raw" if kind == "raw" else "/forward", endpoint)
    runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=.2)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    native = module.NativeTransport(url)
    native.port = runner.addresses[0][1]
    native.raw_protocol_version = 1
    native.client = aiohttp.ClientSession(trust_env=False)
    async def ready():
        pass
    monkeypatch.setattr(native, "start", ready)
    async def call():
        context = (native.request_raw("POST", url, api_key="SYNTHETIC_ONLY", body=raw,
                                      content_type="application/json")
                   if kind == "raw" else
                   native.request("POST", url, api_key="SYNTHETIC_ONLY", payload=payload))
        async with context as response:
            assert len(streams) == 1 and not streams[0].closed
            assert response.status == (500 if outcome == "http_error" else 200)
            assert response.headers["X-Fixture"] == "unchanged"
            assert await response.read() == b"synthetic-result"
            if outcome == "consumer_error":
                raise ValueError("owned caller failure")
    task = asyncio.create_task(call())
    try:
        if outcome.startswith("cancel_"):
            await asyncio.wait_for(reached.wait(), 10)
            if outcome == "cancel_upload":
                assert 0 < streams[0].tell() < len(expected), "Cancellation must interrupt an incomplete upload"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            resume_reading.set()
            await asyncio.wait_for(exited.wait(), 5)
            assert calls[0]["disconnected"] is True
        elif outcome == "disconnect_upload":
            with pytest.raises(aiohttp.ClientConnectionError):
                await asyncio.wait_for(task, 10)
        elif outcome == "consumer_error":
            with pytest.raises(ValueError, match="owned caller failure"):
                await asyncio.wait_for(task, 10)
        else:
            await asyncio.wait_for(task, 10)
        assert len(streams) == 1 and streams[0].closed
        assert len(calls) == 1, "A failed local delivery must not replay the request"
        row = calls[0]
        assert row["headers"]["Authorization"] == "Bearer " + native.token
        assert row["headers"]["Content-Length"] == str(len(expected))
        assert "Transfer-Encoding" not in row["headers"]
        assert row["headers"]["Content-Type"] == ("application/octet-stream" if kind == "raw" else "application/json")
        if kind == "raw":
            assert json.loads(row["headers"]["X-CDS-Transport-Metadata"]) == {
                "api_key": "SYNTHETIC_ONLY", "url": url, "method": "POST",
                "policy": "default", "content_type": "application/json"}
        if outcome in {"cancel_upload", "disconnect_upload"}:
            assert row["prefix"] == expected[:65536]
            assert streams[0].final_position < len(expected)
        else:
            assert row["body"] == expected
            assert hashlib.sha256(row["body"]).hexdigest() == expected_digest
        assert hashlib.sha256(raw if kind == "raw" else json.dumps(envelope).encode("utf-8")).hexdigest() == expected_digest
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await native.close()
        await runner.cleanup()


async def test_large_json_real_native_helper_preserves_default_encoding(monkeypatch):
    """The complete normal JSON route reaches an owned upstream, not only IPC."""
    from test_network import clear_proxies
    clear_proxies(monkeypatch)
    monkeypatch.delenv("CODEX_CA_CERTIFICATE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    payload = {"text": "x" * 34000000, "unicode": "中文", "number": 1.0}
    observed = []
    async def endpoint(request):
        raw = await request.read()
        observed.append((dict(request.headers), raw))
        assert request.headers["Authorization"] == "Bearer SYNTHETIC_ONLY"
        return web.json_response({"ok": True})
    app = web.Application(client_max_size=40*1024*1024)
    app.router.add_post("/responses", endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}/responses"
    helper = Path(os.environ["CODEX_DEEPSEEK_TEST_HTTP_BINARY"])
    assert helper.is_file()
    native = module.NativeTransport(url, binary=helper)
    try:
        async with native.request("POST", url, api_key="SYNTHETIC_ONLY", payload=payload) as response:
            assert response.status == 200 and await response.json() == {"ok": True}
        assert len(observed) == 1
        headers, body = observed[0]
        assert headers["Content-Length"] == str(len(body))
        assert json.loads(body) == payload
    finally:
        await native.close()
        await runner.cleanup()
