"""Hosted search result shapes must remain decodable before cache commit."""
import copy
import json

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import Events, MODEL, ProtocolError, Registry, encode_sse
from test_gateway import fake_upstream


BAD_ACTIONS = [[], {}, {"type": None}, {"type": 17},
    {"type": "search", "query": 17}, {"type": "search", "queries": "fixture"},
    {"type": "search", "queries": ["valid", 17]}, {"type": "open_page", "url": 17},
    {"type": "find_in_page", "url": False}, {"type": "find_in_page", "pattern": []}]


def search(**fields):
    return {"id": "w", "type": "openrouter:web_search", "status": "completed", **fields}


@pytest.mark.parametrize("action", BAD_ACTIONS)
@pytest.mark.parametrize("event_kind", ["response.output_item.done", "response.completed"])
def test_bad_known_search_action_never_commits(action, event_kind):
    item = search(action=action)
    events = Events(Registry())
    event = ({"type": event_kind, "response": {"id": "r", "status": "completed", "output": [item]}}
             if event_kind == "response.completed" else {"type": event_kind, "item": item})
    with pytest.raises(ProtocolError):
        events.transform(event)
    assert not events.terminal


@pytest.mark.parametrize("fields", [{}, {"action": None}, {"action": {"type": "search"}},
    {"action": {"type": "search", "query": None, "queries": None}},
    {"action": {"type": "search", "query": "", "queries": ["fixture", ""], "extra": {"keep": True}}},
    {"action": {"type": "open_page", "url": None, "query": 17}},
    {"action": {"type": "open_page", "url": "https://example.test"}},
    {"action": {"type": "find_in_page", "url": None, "pattern": None}},
    {"action": {"type": "find_in_page", "url": "", "pattern": "fixture"}},
    {"action": {"type": "future_action", "query": 17, "url": {"opaque": True}, "extra": [1, None]}}])
def test_optional_unknown_and_extension_search_fields_survive(fields):
    item = search(**fields)
    frozen = copy.deepcopy(item)
    events = Events(Registry())
    out = events.transform({"type": "response.completed", "response": {"id": "r", "status": "completed", "output": [item]}})
    assert events.terminal
    assert out[0]["item"] == {**item, "type": "web_search_call", "id": "ws_w"}
    assert item == frozen


@pytest.mark.parametrize("bad", [search(status=17, action={"type": "search", "query": "fixture"}),
                                  search(action={"type": "search", "query": 17})])
def test_failed_final_cannot_replace_sqlite_preview_evidence(tmp_path, bad):
    store = Store(tmp_path / "search.sqlite")
    try:
        registry = Registry(store)
        with pytest.raises(ProtocolError):
            registry.output(bad)
        assert store.get("ws_w") is None
        preview = search(status="in_progress", action={"type": "search", "query": "preview"})
        registry.output(preview, final=False)
        assert store.get("ws_w") == preview
        with pytest.raises(ProtocolError):
            registry.output(bad)
        assert store.get("ws_w") == preview
        complete = search(action={"type": "search", "query": "complete"})
        registry.output(complete)
        assert store.get("ws_w") == complete
    finally:
        store.close()


def test_partial_search_action_can_recover_before_final_commit():
    events = Events(Registry())
    events.transform({"type": "response.output_item.added", "item": search(status="in_progress", action={})})
    item = search(action={"type": "search", "query": "fixture"})
    result = events.transform({"type": "response.output_item.done", "item": item})
    assert result[-1]["item"]["action"] == item["action"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("action", [{"type": "search", "query": 17}, {"type": "open_page", "url": 17}])
async def test_bad_search_action_fails_real_loopback_without_cache(fake_upstream, tmp_path, stream, action):
    item = search(action=action)
    async def provider(request):
        terminal = {"id": "r", "status": "completed", "output": [item]}
        if not stream:
            return web.json_response(terminal)
        return web.Response(body=encode_sse({"type": "response.output_item.done", "item": item}) +
            encode_sse({"type": "response.completed", "response": terminal}), content_type="text/event-stream")
    fake_upstream.append(provider)
    store = Store(tmp_path / "search.sqlite")
    try:
        async with Gateway("SYNTHETIC", "LOCAL", store, {}) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", json={"model": MODEL, "stream": stream},
                    headers={"Authorization": "Bearer LOCAL"}) as response:
                if stream:
                    frames = [json.loads(line[6:]) for line in (await response.text()).splitlines() if line.startswith("data: ")]
                    assert frames[-1]["type"] == "response.failed"
                    assert not any(frame["type"] in {"response.output_item.done", "response.completed"} for frame in frames)
                else:
                    assert response.status == 502
        assert store.get("ws_w") is None
        assert store.db.execute("SELECT status FROM receipts").fetchall() == [("adapter_error",)]
    finally:
        store.close()
