"""Authoritative native tool completion must not commit an unfinished item."""
import json

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import Events, MODEL, ProtocolError, prepare, encode_sse
from test_gateway import fake_upstream


def fixture(kind):
    spec = ({"type": "tool_search", "execution": "client"} if kind == "search" else
            {"type": kind, "name": "fixture_" + kind})
    body = {"model": MODEL, "input": [], "tools": [spec]}
    payload, registry = prepare(body)
    arguments = ({"input": "fixture-only source"} if kind == "custom" else
                 {"query": "fixture", "limit": 1} if kind == "search" else {})
    item = {"type": "function_call", "id": "fc", "call_id": "call", "name": payload["tools"][0]["name"],
            "arguments": json.dumps(arguments)}
    return body, registry, item


@pytest.mark.parametrize("kind", ["function", "custom", "search"])
@pytest.mark.parametrize("status", ["failed", "incomplete", "in_progress"])
@pytest.mark.parametrize("event_kind", ["response.output_item.done", "response.completed"])
def test_explicit_unfinished_tool_cannot_commit(kind, status, event_kind):
    _, registry, item = fixture(kind)
    item["status"] = status
    event = ({"type": event_kind, "response": {"id": "r", "status": "completed", "output": [item]}}
             if event_kind == "response.completed" else {"type": event_kind, "item": item})
    events = Events(registry)
    with pytest.raises(ProtocolError, match="unfinished tool"):
        events.transform(event)
    assert not events.terminal and not events.done


@pytest.mark.parametrize("kind", ["function", "custom", "search"])
@pytest.mark.parametrize("final_status", ["missing", None, "completed"])
def test_in_progress_argument_preview_and_optional_final_status_remain_valid(kind, final_status):
    _, registry, item = fixture(kind)
    events = Events(registry)
    preview = {**item, "arguments": "", "status": "in_progress"}
    events.transform({"type": "response.output_item.added", "item": preview})
    events.transform({"type": "response.function_call_arguments.delta", "item_id": "fc", "delta": item["arguments"]})
    events.transform({"type": "response.function_call_arguments.done", "item_id": "fc", "arguments": item["arguments"]})
    if final_status != "missing":
        item["status"] = final_status
    result = events.transform({"type": "response.output_item.done", "item": item})
    assert any(event["type"] == "response.output_item.done" for event in result)
    expected = {"function": "function_call", "custom": "custom_tool_call", "search": "tool_search_call"}[kind]
    completed_item = result[-1]["item"]
    assert completed_item["type"] == expected
    assert ("status" in completed_item) == (final_status != "missing")
    if final_status != "missing":
        assert completed_item["status"] == final_status
    terminal = events.transform({"type": "response.completed", "response": {
        "id": "r", "status": "completed", "output": [item]}})
    assert terminal[-1]["type"] == "response.completed" and events.terminal


@pytest.mark.parametrize("kind", ["function", "custom", "search"])
@pytest.mark.parametrize("stream", [False, True])
async def test_explicit_unfinished_tool_fails_over_real_loopback_http(fake_upstream, tmp_path, kind, stream):
    body, _, item = fixture(kind)
    item["status"] = "in_progress"
    calls = []
    async def provider(request):
        calls.append(await request.json())
        terminal = {"id": "r", "status": "completed", "output": [item]}
        if not stream:
            return web.json_response(terminal)
        frames = [
            {"type": "response.created", "response": {"id": "r"}},
            {"type": "response.output_item.added", "item": {**item, "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "item_id": "fc", "delta": item["arguments"]},
            {"type": "response.output_item.done", "item": item},
            {"type": "response.completed", "response": terminal},
        ]
        return web.Response(body=b"".join(encode_sse(event) for event in frames), content_type="text/event-stream")
    fake_upstream.append(provider)
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SYNTHETIC", "LOCAL", store, {}) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", json={**body, "stream": stream},
                                   headers={"Authorization": "Bearer LOCAL"}) as response:
                if stream:
                    frames = [json.loads(line[6:]) for line in (await response.text()).splitlines() if line.startswith("data: ")]
                    assert frames[-1]["type"] == "response.failed"
                    assert not any(frame["type"] in {"response.output_item.done", "response.completed"} for frame in frames)
                else:
                    assert response.status == 502
        assert len(calls) == 1
        assert store.db.execute("SELECT status FROM receipts").fetchall() == [("adapter_error",)]
    finally:
        store.close()
