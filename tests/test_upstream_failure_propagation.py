"""Upstream failures stay failures; synthetic loopback, never paid model evidence."""
import copy
import hashlib
import json
import os
from types import SimpleNamespace

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import MODEL, encode_sse
from codex_deepseek.image_transport import ImageTransport, TransportPolicy
from test_gateway import fake_upstream
from test_image_transport import FakeFiles, part

ERROR = {"code": "image_file_too_large", "message": "original-upstream-failure"}
RESPONSE_ID = "gen-offline-failure"
KEY = "SYNTHETIC-FAILURE-KEY"
HEADERS = {"Authorization": "Bearer LOCAL"}


def failure(**changes):
    return {"id": RESPONSE_ID, "status": "failed", "model": "unknown", "provider": "unknown",
            "output": [], "error": dict(ERROR), **changes}


async def exercise(fake_upstream, tmp_path, *, mode="failed_first", with_images=False, value=None):
    value = value or failure()
    created = {"type": "response.created", "response": {"id": RESPONSE_ID, "model": MODEL}}
    terminal = {"type": "response.failed", "response": value}
    if mode == "error":
        terminal = {"type": "error", "code": ERROR["code"], "message": ERROR["message"], "param": None}
    elif mode == "unknown_completed":
        terminal = {"type": "response.completed", "response": failure(status="completed", error=None)}
    calls = []
    async def provider(request):
        calls.append(await request.json())
        if mode == "nonstream":
            return web.json_response(value)
        wire = (encode_sse(created) if mode == "after_created" else b"") + encode_sse(terminal)
        return web.Response(body=wire, content_type="text/event-stream")
    fake_upstream.append(provider)
    store = Store(tmp_path / "adapter.sqlite")
    backend = FakeFiles()
    transport = ImageTransport(tmp_path / "images", lambda: KEY, backend, TransportPolicy(1))
    image_records = []
    image_stub = SimpleNamespace(transport=lambda _: transport,
                                 record=lambda ref, **kw: image_records.append((ref, kw)))
    try:
        async with Gateway(KEY, "LOCAL", store, {},
                           native_http_binary=os.environ.get("CODEX_DEEPSEEK_TEST_HTTP_BINARY")) as gateway, aiohttp.ClientSession() as client:
            if with_images:
                gateway.images = image_stub
            body = {"model": MODEL, "stream": mode != "nonstream",
                    "input": [{"type": "message", "role": "user", "content": [part()]}] if with_images else "synthetic"}
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", headers=HEADERS, json=body) as reply:
                http, text = reply.status, await reply.text()
        row = store.db.execute("SELECT status,response_id,detail FROM receipts").fetchone()
        attested = store.db.execute("SELECT COUNT(*) FROM adapter_metadata WHERE key LIKE 'native-complete-v1:%'").fetchone()[0]
        return {"http": http, "text": text, "events": [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: ")],
                "row": row, "attested": attested, "calls": calls, "transport": transport, "backend": backend,
                "image_records": image_records, "upstream_event": terminal}
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["nonstream", "failed_first", "after_created"])
@pytest.mark.parametrize("with_images", [False, True])
async def test_valid_failed_unknown_identity_preserves_error_and_id(fake_upstream, tmp_path, mode, with_images):
    result = await exercise(fake_upstream, tmp_path, mode=mode, with_images=with_images)
    final = json.loads(result["text"]) if mode == "nonstream" else result["events"][-1]["response"]
    assert result["http"] == 200 and final["status"] == "failed"
    assert final["error"] == ERROR and final["model"] == final["provider"] == "unknown"
    assert result["row"][:2] == ("failed", RESPONSE_ID) and result["attested"] == 0
    assert len(result["calls"]) == 1
    detail = json.loads(result["row"][2])
    assert detail["event_type"] == "response.failed" and detail["error_code"] == ERROR["code"]
    assert detail["hash_basis"] == "canonical_json_utf8" and len(detail["event_json_sha256"]) == 64
    assert ERROR["message"] not in result["row"][2]
    if with_images:
        assert (await result["transport"].diagnostics())["active_transport_pins"] == 0
        assert (await result["transport"].cleanup(all_unpinned=True))["all_owned_files_deleted"]


@pytest.mark.parametrize("with_images", [False, True])
async def test_standard_error_becomes_native_failure_without_inventing_id_or_settling_unknown(fake_upstream, tmp_path, with_images):
    result = await exercise(fake_upstream, tmp_path, mode="error", with_images=with_images)
    assert [e["type"] for e in result["events"]] == ["response.failed"]
    final = result["events"][0]["response"]
    assert final["status"] == "failed" and "id" not in final and final["error"]["code"] == ERROR["code"]
    assert final["error"]["message"] == ERROR["message"]
    assert result["row"][:2] == ("failed", None) and result["attested"] == 0 and len(result["calls"]) == 1
    assert json.loads(result["row"][2])["event_type"] == "error"
    if with_images:
        status = await result["transport"].diagnostics()
        assert status["active_transport_pins"] == len(status["unknown_upstream_calls"]) == 1
        assert not result["backend"].deleted


async def test_explicit_unknown_success_still_rejected_but_response_id_is_recorded(fake_upstream, tmp_path):
    result = await exercise(fake_upstream, tmp_path, mode="unknown_completed")
    assert result["row"][:2] == ("adapter_error", RESPONSE_ID) and result["attested"] == 0
    assert not any(e["type"] == "response.completed" for e in result["events"])


@pytest.mark.parametrize("bad", [{"id": 3}, {"status": "completed"}, {"output": None}])
async def test_malformed_failed_is_not_a_verified_terminal(fake_upstream, tmp_path, bad):
    result = await exercise(fake_upstream, tmp_path, value=failure(**bad), with_images=True)
    assert result["row"][0] == "adapter_error" and result["attested"] == 0
    assert (await result["transport"].diagnostics())["active_transport_pins"] == 1
    diagnostic = json.loads(result["row"][2])["provider_failure"]
    assert diagnostic["event_type"] == "response.failed" and diagnostic["error_code"] == ERROR["code"]


async def test_failed_message_redacts_credential_without_persisting_message(fake_upstream, tmp_path):
    result = await exercise(fake_upstream, tmp_path, value=failure(error={"code": "server_error", "message": "bad " + KEY}))
    assert KEY not in result["text"] and "[REDACTED]" in result["text"]
    assert KEY not in result["row"][2] and "bad " not in result["row"][2]


def test_failed_snapshot_keeps_original_error_and_never_reemits_completed_tool():
    from codex_deepseek.protocol import Events, Registry
    registry = Registry()
    registry.tools([{"type": "function", "name": "ping", "parameters": {"type": "object"}}])
    events = Events(registry)
    events.transform({"type": "response.created", "response": {"id": RESPONSE_ID}})
    tool = {"type": "function_call", "id": "fc-done", "call_id": "call-done",
            "name": "ping", "arguments": "{}", "status": "completed"}
    first = events.transform({"type": "response.output_item.done", "output_index": 0, "item": tool})
    assert sum(item["type"] == "response.output_item.done" for item in first) == 1
    # The native failed-response parser consumes error, not output snapshots.
    partial = failure(output=[{"type": "function_call", "id": "fc-partial", "call_id": "call-partial",
                               "name": "ping", "arguments": "{", "status": "incomplete"}])
    failed = events.transform({"type": "response.failed", "response": partial})
    assert [item["type"] for item in failed] == ["response.failed"]
    assert failed[0]["response"] == partial and events.status == "failed" and events.terminal


def test_standard_error_uses_only_the_already_observed_response_id():
    from codex_deepseek.protocol import Events, Registry
    events = Events(Registry())
    events.transform({"type": "response.created", "response": {"id": RESPONSE_ID}})
    output = events.transform({"type": "error", "code": ERROR["code"], "message": ERROR["message"], "param": None})
    assert output[0]["response"]["id"] == RESPONSE_ID and events.status == "failed"


def test_failure_diagnostic_hashes_original_event_without_persisting_message():
    from codex_deepseek.protocol import provider_failure_diagnostic
    event = {"type": "response.failed", "response": failure()}
    detail = provider_failure_diagnostic(event)
    expected = hashlib.sha256(json.dumps(event, ensure_ascii=False, allow_nan=False,
                                        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert detail == {"event_type": "response.failed", "error_code": ERROR["code"],
                      "event_json_sha256": expected, "hash_basis": "canonical_json_utf8"}
    assert ERROR["message"] not in json.dumps(detail)
