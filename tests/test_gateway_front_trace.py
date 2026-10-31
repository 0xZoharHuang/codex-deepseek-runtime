"""Actual aiohttp front requests; no model, credentials or external network."""
import asyncio
import json
import logging
import time

import aiohttp
from aiohttp import web
import pytest

import codex_deepseek.gateway as module
from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import MODEL, encode_sse
from test_gateway import fake_upstream


FIELDS = {"version", "completed_at", "duration_seconds", "method", "path", "upgrade_websocket", "status"}


def records(trace):
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in trace.glob("front-http-*.json")]


def terminal():
    return {"id": "gen-front-trace", "status": "completed", "output": [
        {"type": "message", "id": "msg-front-trace", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "SENSITIVE_RESPONSE"}]}]}


def provider(stream):
    async def complete(request):
        body = await request.json()
        assert body["input"] == "SENSITIVE_BODY"
        if not stream:
            return web.json_response(terminal())
        return web.Response(body=encode_sse({"type": "response.created", "response": {
            "id": "gen-front-trace", "status": "in_progress", "output": []}}) +
            encode_sse({"type": "response.completed", "response": terminal()}),
            content_type="text/event-stream")
    return complete


@pytest.mark.parametrize("via_environment", [False, True])
@pytest.mark.parametrize("access_level", [logging.WARNING, logging.INFO])
async def test_router_upgrade_405_is_traced_without_logging_sensitive_data(tmp_path, caplog, monkeypatch, via_environment, access_level):
    trace = tmp_path / "trace"
    if via_environment:
        monkeypatch.setenv("CODEX_DEEPSEEK_TRACE_DIR", str(trace))
    store = Store(tmp_path / "state.sqlite")
    start = time.time()
    try:
        with caplog.at_level(access_level, logger="aiohttp.access"):
            async with Gateway("SECRET", "LOCAL", store, {},
                    trace_source=None if via_environment else lambda: str(trace)) as gateway, aiohttp.ClientSession() as client:
                async with client.get(f"http://127.0.0.1:{gateway.port}/v1/responses?secret=SENSITIVE_QUERY",
                        headers={"Upgrade": "WebSocket", "Connection": "upgrade", "Authorization": "Bearer SENSITIVE_HEADER",
                                 "Referer": "SENSITIVE_REFERRER", "User-Agent": "SENSITIVE_AGENT"}) as response:
                    assert response.status == 405
                    await response.read()
                assert logging.getLogger("aiohttp.access").getEffectiveLevel() == access_level
        [record] = records(trace)
        assert set(record) == FIELDS
        assert record["version"] == 1 and record["method"] == "GET"
        assert record["path"] == "/v1/responses" and record["status"] == 405
        assert record["upgrade_websocket"] is True
        assert start <= record["completed_at"] <= time.time() and record["duration_seconds"] >= 0
        assert "SENSITIVE" not in next(trace.glob("front-http-*.json")).read_text(encoding="utf-8")
        assert "SENSITIVE" not in caplog.text
    finally:
        store.close()


@pytest.mark.parametrize("stream", [False, True])
async def test_post_completion_and_sensitive_data_boundary(fake_upstream, tmp_path, stream):
    fake_upstream.append(provider(stream))
    trace = tmp_path / "trace"
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SECRET", "LOCAL", store, {}, trace_source=lambda: str(trace)) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses?secret=SENSITIVE_QUERY",
                    json={"model": MODEL, "input": "SENSITIVE_BODY", "stream": stream},
                    headers={"Authorization": "Bearer LOCAL", "X-Secret": "SENSITIVE_HEADER"}) as response:
                assert response.status == 200
                text = await response.text()
                assert "SENSITIVE_RESPONSE" in text
                if stream:
                    assert "response.completed" in text and "response.failed" not in text
        [record] = records(trace)
        assert set(record) == FIELDS
        assert (record["method"], record["path"], record["status"], record["upgrade_websocket"]) == ("POST", "/v1/responses", 200, False)
        assert "SENSITIVE" not in next(trace.glob("front-http-*.json")).read_text(encoding="utf-8")
        assert store.db.execute("SELECT status FROM receipts").fetchall() == [("completed",)]
    finally:
        store.close()


async def test_local_auth_rejection_is_traced_without_upstream(tmp_path):
    trace = tmp_path / "trace"
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SECRET", "LOCAL", store, {}, trace_source=lambda: str(trace)) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", json={"model": MODEL}) as response:
                assert response.status == 401
                await response.read()
            assert gateway.client.process is None
        assert [(r["method"], r["status"]) for r in records(trace)] == [("POST", 401)]
    finally:
        store.close()


async def test_upstream_rejection_preserves_status_and_trace(fake_upstream, tmp_path):
    async def reject(request):
        return web.json_response({"error": {"message": "synthetic rejection"}}, status=429)
    fake_upstream.append(reject)
    trace = tmp_path / "trace"
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SECRET", "LOCAL", store, {}, trace_source=lambda: str(trace)) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", json={"model": MODEL},
                    headers={"Authorization": "Bearer LOCAL"}) as response:
                assert response.status == 429
                await response.read()
        assert [(r["method"], r["status"]) for r in records(trace)] == [("POST", 429)]
    finally:
        store.close()


@pytest.mark.parametrize("info_enabled", [False, True])
async def test_disabled_trace_keeps_existing_access_log_and_creates_no_file(tmp_path, monkeypatch, caplog, info_enabled):
    monkeypatch.delenv("CODEX_DEEPSEEK_TRACE_DIR", raising=False)
    store = Store(tmp_path / "state.sqlite")
    try:
        with caplog.at_level(logging.INFO if info_enabled else logging.WARNING, logger="aiohttp.access"):
            async with Gateway("SECRET", "LOCAL", store, {}) as gateway, aiohttp.ClientSession() as client:
                async with client.get(f"http://127.0.0.1:{gateway.port}/health", headers={"Authorization": "Bearer LOCAL"}) as response:
                    assert response.status == 200
                    await response.read()
        assert not list(tmp_path.rglob("front-http-*.json"))
        access = [r for r in caplog.records if r.name == "aiohttp.access"]
        assert bool(access) is info_enabled
    finally:
        store.close()


async def test_trace_can_toggle_on_existing_keepalive_with_info_disabled(tmp_path, caplog):
    trace = tmp_path / "trace"
    selected = None
    store = Store(tmp_path / "state.sqlite")
    try:
        with caplog.at_level(logging.WARNING, logger="aiohttp.access"):
            async with Gateway("SECRET", "LOCAL", store, {}, trace_source=lambda: selected) as gateway, aiohttp.ClientSession() as client:
                url = f"http://127.0.0.1:{gateway.port}/health"
                async def get():
                    async with client.get(url, headers={"Authorization": "Bearer LOCAL"}) as response:
                        assert response.status == 200
                        await response.read()
                await get()
                [connection] = gateway.runner.server.connections
                selected = str(trace)
                await get()
                assert gateway.runner.server.connections == [connection]
                assert len(records(trace)) == 1
                selected = None
                await get()
                assert gateway.runner.server.connections == [connection]
                assert len(records(trace)) == 1
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    finally:
        store.close()


async def test_broken_trace_source_warns_without_changing_router_response(tmp_path, caplog):
    def broken():
        raise ValueError("SENSITIVE_CONFIGURATION")
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SECRET", "LOCAL", store, {}, trace_source=broken) as gateway, aiohttp.ClientSession() as client:
            async with client.get(f"http://127.0.0.1:{gateway.port}/v1/responses?secret=SENSITIVE_QUERY") as response:
                assert response.status == 405
                await response.read()
        assert "Front HTTP trace incomplete (ValueError)" in caplog.text
        assert "SENSITIVE" not in caplog.text
        assert not list(tmp_path.rglob("front-http-*.json"))
    finally:
        store.close()


async def test_front_write_failure_does_not_change_completed_sse(fake_upstream, tmp_path, monkeypatch, caplog):
    fake_upstream.append(provider(True))
    trace = tmp_path / "trace"
    original = module.private_write
    def fail_front(path, data):
        if path.name.startswith("front-http-"):
            raise OSError("SENSITIVE_DISK_OR_PATH")
        return original(path, data)
    monkeypatch.setattr(module, "private_write", fail_front)
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SECRET", "LOCAL", store, {}, trace_source=lambda: str(trace)) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses",
                    json={"model": MODEL, "input": "SENSITIVE_BODY", "stream": True},
                    headers={"Authorization": "Bearer LOCAL"}) as response:
                assert response.status == 200
                text = await response.text()
                assert "response.completed" in text and "response.failed" not in text
        assert store.db.execute("SELECT status FROM receipts").fetchall() == [("completed",)]
        assert "Front HTTP trace incomplete (OSError)" in caplog.text
        assert "SENSITIVE" not in caplog.text and records(trace) == []
    finally:
        store.close()


async def test_concurrent_router_and_health_requests_each_have_one_record(tmp_path):
    trace = tmp_path / "trace"
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SECRET", "LOCAL", store, {}, trace_source=lambda: str(trace)) as gateway, aiohttp.ClientSession() as client:
            async def get(path):
                async with client.get(f"http://127.0.0.1:{gateway.port}" + path,
                        headers={"Authorization": "Bearer LOCAL"}) as response:
                    await response.read()
                    return response.status
            statuses = await asyncio.gather(*(get("/health") for _ in range(4)),
                                            *(get("/v1/responses") for _ in range(4)))
            assert statuses == [200] * 4 + [405] * 4
        result = records(trace)
        assert len(result) == 8
        assert sorted(r["status"] for r in result) == [200] * 4 + [405] * 4
    finally:
        store.close()
