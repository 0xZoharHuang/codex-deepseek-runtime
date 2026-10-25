"""Real loopback HTTP identity boundaries; no model or native tool execution."""
import asyncio
import json

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek.gateway import Gateway, Store, sse_events
from codex_deepseek.protocol import MODEL, encode_sse
from test_gateway import fake_upstream

HEADERS = {"Authorization": "Bearer LOCAL"}
CASES = [
    ("deepseek", "DeepSeek", {}),
    ("morph/fp8", "Morph", {"text": {"format": {
        "type": "json_schema", "name": "result", "strict": True,
        "schema": {"type": "object", "properties": {}, "additionalProperties": False}}}}),
    ("primeintellect", "PrimeIntellect", {"parallel_tool_calls": False,
        "tools": [{"type": "function", "name": "ping", "parameters": {"type": "object"}}]}),
]

def terminal(**fields):
    return {"id": "gen-loopback", "status": "completed", "output": [
        {"type": "message", "id": "msg-one", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "{}"}]}], **fields}

def events_from(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]

def no_attestations(store):
    return store.db.execute("SELECT count(*) FROM adapter_metadata WHERE key LIKE 'native-complete-v1:%'").fetchone()[0] == 0

async def exercise(fake_upstream, tmp_path, observations, mode, body=None):
    calls = []
    async def provider(request):
        calls.append(await request.json())
        result = terminal(**observations)
        if mode == "nonstream":
            return web.json_response(result)
        created = {"type": "response.created", "response": {
            "id": "gen-loopback", "status": "in_progress", "output": []}}
        if mode == "early":
            created["response"].update(observations)
            result = terminal()
        return web.Response(body=encode_sse(created) + encode_sse({"type": "response.completed", "response": result}),
                            content_type="text/event-stream")
    fake_upstream.append(provider)
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SYNTHETIC", "LOCAL", store, {}) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", headers=HEADERS,
                                   json={"model": MODEL, "stream": mode != "nonstream", **(body or {})}) as reply:
                status, text = reply.status, await reply.text()
        receipt = store.db.execute("SELECT status,provider,upstream_status,detail FROM receipts").fetchone()
        return status, text, calls, receipt, no_attestations(store)
    finally:
        store.close()

@pytest.mark.parametrize("route,display,body", CASES)
@pytest.mark.parametrize("slug", [False, True])
@pytest.mark.parametrize("mode", ["nonstream", "late"])
async def test_matching_exact_provider_aliases(fake_upstream, tmp_path, route, display, body, slug, mode):
    status, text, calls, receipt, _ = await exercise(fake_upstream, tmp_path,
        {"model": MODEL, "provider": route if slug else display}, mode, body)
    assert status == 200 and receipt[:3] == ("completed", route, 200)
    assert [x["provider"]["only"] for x in calls] == [[route]]
    if mode == "nonstream":
        result = json.loads(text)
    else:
        assert events_from(text)[-1]["type"] == "response.completed"
        result = events_from(text)[-1]["response"]
    assert result["provider"] == (route if slug else display)

@pytest.mark.parametrize("model", ["deepseek-v4.1-flash-1017", "deepseek/deepseek-v4.1-flash-20261017", "deepseek-v4.1-flash-20261017"])
async def test_exact_model_aliases_are_not_silently_rewritten(fake_upstream, tmp_path, model):
    status, text, calls, receipt, _ = await exercise(fake_upstream, tmp_path, {"model": model}, "nonstream")
    assert status == 200 and json.loads(text)["model"] == model
    assert receipt[0] == "completed" and len(calls) == 1

@pytest.mark.parametrize("route,display,body", CASES)
@pytest.mark.parametrize("mode", ["nonstream", "early", "late"])
async def test_wrong_route_provider_is_not_completed(fake_upstream, tmp_path, route, display, body, mode):
    wrong = "Morph" if route == "deepseek" else "DeepSeek"
    status, text, calls, receipt, no_attested = await exercise(fake_upstream, tmp_path, {"model": MODEL, "provider": wrong}, mode, body)
    assert [x["provider"]["only"] for x in calls] == [[route]]
    assert receipt[0:3] == ("adapter_error", route, 200) and "provider" in receipt[3]
    assert no_attested
    if mode == "nonstream":
        assert status == 502 and "provider" in json.loads(text)["error"]["message"]
    else:
        events = events_from(text)
        assert events[-1]["type"] == "response.failed"
        assert not any(x["type"] in {"response.completed", "response.output_item.done"} for x in events)
        if mode == "early": assert not any(x["type"] == "response.created" for x in events)

@pytest.mark.parametrize("observations", [
    {"model": "deepseek/deepseek-v4.1-flash-1018"},
    {"model": "other/model"}, {"provider": "UnconfiguredProvider"},
    {"model": 3}, {"provider": {"name": "DeepSeek"}}, {"model": ""}, {"provider": False},
])
@pytest.mark.parametrize("mode", ["nonstream", "early", "late"])
async def test_explicit_bad_identity_never_attests_success(fake_upstream, tmp_path, observations, mode):
    status, text, calls, receipt, no_attested = await exercise(fake_upstream, tmp_path, observations, mode)
    assert len(calls) == 1 and receipt[0] == "adapter_error" and no_attested
    if mode == "nonstream": assert status == 502
    else:
        events = events_from(text)
        assert events[-1]["type"] == "response.failed"
        assert not any(x["type"] in {"response.completed", "response.output_item.done"} for x in events)

@pytest.mark.parametrize("observations", [{"future_field": {"opaque": True}}, {"model": None, "provider": None}])
@pytest.mark.parametrize("mode", ["nonstream", "late"])
async def test_missing_identity_remains_unknown(fake_upstream, tmp_path, observations, mode):
    status, text, calls, receipt, _ = await exercise(fake_upstream, tmp_path, observations, mode)
    assert status == 200 and receipt[0] == "completed" and len(calls) == 1
    result = json.loads(text) if mode == "nonstream" else events_from(text)[-1]["response"]
    assert result.get("model") is None and result.get("provider") is None
    assert all(result[k] == v for k, v in observations.items())

@pytest.mark.parametrize("field,start,end", [("model", MODEL, "deepseek-v4.1-flash-1017"), ("provider", "DeepSeek", "deepseek")])
async def test_equivalent_exact_aliases_may_change_spelling_within_one_stream(fake_upstream, tmp_path, field, start, end):
    calls = []
    async def provider(request):
        calls.append(await request.json())
        return web.Response(body=encode_sse({"type": "response.created", "response": {"id": "gen-loopback", field: start}})
            + encode_sse({"type": "response.completed", "response": terminal(**{field: end})}), content_type="text/event-stream")
    fake_upstream.append(provider)
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SYNTHETIC", "LOCAL", store, {}) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", headers=HEADERS, json={"model": MODEL, "stream": True}) as reply:
                events = events_from(await reply.text())
        assert events[-1]["type"] == "response.completed"
        assert events[0]["response"][field] == start and events[-1]["response"][field] == end
        assert store.db.execute("SELECT status FROM receipts").fetchone()[0] == "completed"
        assert len(calls) == 1
    finally:
        store.close()

async def test_late_mismatch_keeps_already_delivered_tool_and_effect(fake_upstream, tmp_path):
    calls = []; effect = tmp_path / "downstream-effect.txt"; dispatched = asyncio.Event()
    tool = {"type": "function_call", "id": "fc-one", "call_id": "call-one", "name": "ping", "arguments": "{}", "status": "completed"}
    async def provider(request):
        calls.append(await request.json())
        reply = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await reply.prepare(request)
        await reply.write(encode_sse({"type": "response.created", "response": {"id": "gen-loopback", "model": MODEL}}))
        await reply.write(encode_sse({"type": "response.output_item.done", "output_index": 0, "item": tool}))
        await asyncio.wait_for(dispatched.wait(), 5)
        assert effect.read_text() == "call-one"
        await reply.write(encode_sse({"type": "response.completed", "response": terminal(output=[tool], provider="Morph")}))
        await reply.write_eof()
        return reply
    fake_upstream.append(provider)
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SYNTHETIC", "LOCAL", store, {}) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", headers=HEADERS, json={"model": MODEL, "stream": True,
                "tools": [{"type": "function", "name": "ping", "parameters": {"type": "object"}}]}) as reply:
                events = []
                async for event in sse_events(reply.content):
                    events.append(event)
                    if event["type"] == "response.output_item.done":
                        # A synthetic downstream consumer performs an observable local effect
                        # on real HTTP delivery, before the provider sends the contradiction.
                        assert event["item"]["call_id"] == "call-one"
                        effect.write_text("call-one")
                        dispatched.set()
        assert effect.read_text() == "call-one" and len(calls) == 1
        assert events[-1]["type"] == "response.failed" and not any(x["type"] == "response.completed" for x in events)
        assert sum(x["type"] == "response.output_item.done" for x in events) == 1
        assert store.db.execute("SELECT status,response_id FROM receipts").fetchone() == ("adapter_error", "gen-loopback")
        assert no_attestations(store)
    finally:
        store.close()
