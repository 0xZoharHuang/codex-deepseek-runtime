"""Opt-in parsed upstream-event observation; never a socket-byte capture."""
import json
import os
from pathlib import Path

import aiohttp
from aiohttp import web
import pytest

import codex_deepseek.gateway as module
from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import MODEL, encode_sse
from test_gateway import fake_upstream

KEY = "UPSTREAM-TRACE-KEY"
HEADERS = {"Authorization": "Bearer LOCAL"}
IDENTIFIER = "gen-upstream-trace-fixture"


def completed():
    return {"type": "response.completed", "response": {
        "id": IDENTIFIER, "status": "completed", "model": MODEL, "output": []}}


async def exercise(fake_upstream, tmp_path, monkeypatch, wire, *, enabled):
    monkeypatch.delenv("CODEX_DEEPSEEK_TRACE_DIR", raising=False)
    async def upstream(request):
        await request.json()
        return web.Response(body=wire, content_type="text/event-stream")
    fake_upstream.append(upstream)
    trace = tmp_path / "trace"
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway(KEY, "LOCAL", store, {},
            trace_source=lambda: trace if enabled else None,
            native_http_binary=os.environ.get("CODEX_DEEPSEEK_TEST_HTTP_BINARY")) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses",
                headers=HEADERS, json={"model": MODEL, "stream": True, "input": "non-sensitive fixture"}) as reply:
                text = await reply.text()
        row = store.db.execute("SELECT id,status FROM receipts").fetchone()
        events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
        return trace, row, events
    finally:
        store.close()


async def test_observation_precedes_identity_rejection_and_redacts_known_key(fake_upstream, tmp_path, monkeypatch):
    original = {"type": "response.created", "response": {
        "id": IDENTIFIER, "model": "unknown", "status": "in_progress", "error": {"code": "server_error"}},
        "diagnostic": KEY}
    trace, row, events = await exercise(fake_upstream, tmp_path, monkeypatch, encode_sse(original), enabled=True)
    assert row[1] == "adapter_error" and events[-1]["type"] == "response.failed"
    assert not any(event["type"] == "response.completed" for event in events)
    raw = (trace / (row[0] + "-upstream-events.jsonl")).read_bytes()
    captured = [json.loads(line) for line in raw.splitlines()]
    assert captured == [{**original, "diagnostic": "[REDACTED]"}]
    assert KEY.encode() not in raw


async def test_observation_is_absent_without_explicit_trace(fake_upstream, tmp_path, monkeypatch):
    trace, row, events = await exercise(fake_upstream, tmp_path, monkeypatch, encode_sse(completed()), enabled=False)
    assert row[1] == "completed" and events[-1]["type"] == "response.completed"
    assert not trace.exists() and not list(tmp_path.rglob("*-upstream-events.jsonl"))


async def test_receipt_trace_has_one_mib_ceiling_and_explicit_truncation(fake_upstream, tmp_path, monkeypatch):
    large = {"type": "response.created", "response": {"id": IDENTIFIER, "model": MODEL},
             "padding": "x" * (1024 * 1024 + 1024)}
    trace, row, events = await exercise(fake_upstream, tmp_path, monkeypatch,
                                      encode_sse(large) + encode_sse(completed()), enabled=True)
    assert row[1] == "completed" and events[-1]["type"] == "response.completed"
    raw = (trace / (row[0] + "-upstream-events.jsonl")).read_bytes()
    assert len(raw) <= 1024 * 1024
    captured = [json.loads(line) for line in raw.splitlines()]
    assert len(captured) == 1 and captured[0]["_codex_trace"]["truncated"] is True
    assert captured[0]["response_summary"]["id"] == IDENTIFIER
    assert captured[0]["response_summary"]["model"] == MODEL


async def test_event_trace_storage_failure_does_not_change_forward_outcome(fake_upstream, tmp_path, monkeypatch, caplog):
    original = module.private_write
    def unavailable(path, data):
        if path.name.endswith("-upstream-events.jsonl"):
            raise OSError("owned fixture event-trace storage failure")
        return original(path, data)
    monkeypatch.setattr(module, "private_write", unavailable)
    trace, row, events = await exercise(fake_upstream, tmp_path, monkeypatch, encode_sse(completed()), enabled=True)
    assert row[1] == "completed" and events[-1]["type"] == "response.completed"
    assert "Upstream event trace incomplete (OSError)" in caplog.text
    assert not list(trace.glob("*-upstream-events.jsonl"))
