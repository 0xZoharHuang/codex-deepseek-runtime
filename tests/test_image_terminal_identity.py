"""Synthetic terminal/identity lifecycle regression; no paid provider or App GUI evidence."""
import json
import pytest
from aiohttp import web
from codex_deepseek.protocol import MODEL, encode_sse
ID = "gen-terminal-identity-offline"

import os
from types import SimpleNamespace
import aiohttp
from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.image_transport import ImageTransport, TransportPolicy
from test_gateway import fake_upstream
from test_image_transport import FakeFiles, part
KEY = "SYNTHETIC-TERMINAL-IDENTITY-KEY"
HEADERS = {"Authorization": "Bearer LOCAL"}


async def run_case(fake_upstream, tmp_path, value, *, stream, damage=None, header=None, header_only=False):
    frames = frames_for(value, damage)
    if header_only:
        frames = [{"type": "error", "code": "synthetic_header_only", "message": "No response terminal or ID"}]
        value = {"error": {"code": "synthetic_header_only", "message": "No response terminal or ID"}}
    headers = {} if header is None else {"X-Generation-Id": header}
    calls = []
    async def upstream(request):
        calls.append(await request.json())
        if not stream: return web.json_response(value, headers=headers)
        return web.Response(body=b"".join(encode_sse(e) for e in frames), content_type="text/event-stream", headers=headers)
    fake_upstream.append(upstream)
    backend = FakeFiles()
    transport = ImageTransport(tmp_path / "files", lambda: KEY, backend, TransportPolicy(1))
    records = []
    image_stub = SimpleNamespace(transport=lambda _: transport, record=lambda ref, **updates: records.append(updates))
    store = Store(tmp_path / "adapter.sqlite")
    try:
        async with Gateway(KEY, "LOCAL", store, {}, native_http_binary=os.environ.get("CODEX_DEEPSEEK_TEST_HTTP_BINARY")) as gateway, aiohttp.ClientSession() as client:
            gateway.images = image_stub
            body = {"model": MODEL, "stream": stream, "input": [{"role": "user", "content": [part()]}],
                    "tools": [{"type": "function", "name": "terminal_only", "parameters": {"type": "object"}}]}
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", headers=HEADERS, json=body) as reply:
                text = await reply.text()
        row = store.db.execute("SELECT status,response_id FROM receipts").fetchone()
        image = {}
        for updates in records: image.update(updates)
        return {"receipt_status": row[0], "response_id": row[1], "text": text, "image": image,
                "attested": store.db.execute("SELECT COUNT(*) FROM adapter_metadata WHERE key LIKE 'native-complete-v1:%'").fetchone()[0],
                "transport": transport, "deleted": backend.deleted, "calls": len(calls)}
    finally:
        store.close()



def envelope(status="completed", identity="model"):
    value = {"id": ID, "status": status, "model": MODEL, "output": [], "error": None}
    if identity == "model": value["model"] = "unknown"
    else: value["provider"] = "unrequested-provider"
    if status == "completed":
        value["output"] = [{"type": "function_call", "id": "fc-terminal-only", "call_id": "call-terminal-only",
                            "name": "terminal_only", "arguments": "{}", "status": "completed"}]
    return value


def frames_for(value, damage=None):
    created = {"type": "response.created", "response": {"id": ID, "model": MODEL, "status": "in_progress"}}
    terminal = {"type": "response." + value["status"], "response": value}
    if damage == "created_unknown": created["response"]["model"] = "unknown"
    elif damage == "flat_error": terminal = {"type": "error", "code": "synthetic_error", "message": "failure without a terminal response"}
    elif damage == "missing_id": value.pop("id")
    elif damage == "changed_id": value["id"] = "gen-unrelated-offline"
    elif damage == "credential_id": value["id"] = "gen-" + KEY
    elif damage == "model_shape": value["model"] = 7
    elif damage == "output_shape": value["output"] = None
    elif damage == "status_mismatch": terminal["type"] = "response.incomplete"
    return [created, terminal]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", ["completed", "incomplete"])
@pytest.mark.parametrize("identity", ["model", "provider"])
async def test_verified_terminal_ends_image_consumption_even_when_success_identity_is_rejected(fake_upstream, tmp_path, stream, status, identity):
    result = await run_case(fake_upstream, tmp_path, envelope(status, identity), stream=stream)
    assert result["receipt_status"] == "adapter_error" and result["response_id"] == ID
    assert result["attested"] == 0 and "response.completed" not in result["text"]
    assert "response.incomplete" not in result["text"] and "response.output_item" not in result["text"]
    diag = await result["transport"].diagnostics()
    assert diag["active_transport_pins"] == 0 and not diag["unknown_upstream_calls"]
    assert result["image"]["consumption_status"] == "provider_terminal"
    assert result["image"]["provider_terminal"] == status and result["image"]["provider_response_id"] == ID
    assert (await result["transport"].cleanup(all_unpinned=True))["all_owned_files_deleted"]
    assert len(result["deleted"]) == 1 and result["calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["created_unknown", "flat_error", "missing_id", "changed_id", "credential_id", "model_shape", "output_shape", "status_mismatch"])
async def test_unproved_or_unsafe_response_still_keeps_image_consumption_unknown(fake_upstream, tmp_path, damage):
    result = await run_case(fake_upstream, tmp_path, envelope(), stream=True, damage=damage)
    diag = await result["transport"].diagnostics()
    assert diag["active_transport_pins"] == 1 and len(diag["unknown_upstream_calls"]) == 1
    assert not result["deleted"] and result["image"].get("consumption_status") != "provider_terminal"
    assert result["attested"] == 0 and KEY not in result["text"]
    assert not any(marker in result["text"] for marker in ("response.completed", "response.incomplete", "response.output_item"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_safe_generation_header_is_candidate_only_without_response_identity(fake_upstream, tmp_path, stream):
    identifier = "gen-header-candidate-only"
    result = await run_case(fake_upstream, tmp_path, envelope(), stream=stream, header=identifier, header_only=True)
    assert result["image"].get("generation_header_candidate") == {
        "id": identifier, "provenance": "http_response_header:X-Generation-Id"}
    assert result["response_id"] is None and identifier not in result["text"]
    assert result["image"].get("provider_response_id") is None
    assert result["image"].get("consumption_status") != "provider_terminal"
    diag = await result["transport"].diagnostics()
    assert diag["active_transport_pins"] == 1 and len(diag["unknown_upstream_calls"]) == 1
    assert not result["deleted"] and result["attested"] == 0
    for line in result["text"].splitlines():
        if line.startswith("data: "):
            assert "id" not in json.loads(line[6:]).get("response", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["", "or_file_not_a_generation", "gen-bad_id", "gen-" + "x" * 125, "gen-" + KEY])
async def test_unsafe_generation_header_is_not_persisted_or_promoted(fake_upstream, tmp_path, header):
    result = await run_case(fake_upstream, tmp_path, envelope(), stream=True, header=header, header_only=True)
    assert "generation_header_candidate" not in result["image"]
    assert result["response_id"] is None and KEY not in result["text"]
    assert result["image"].get("provider_response_id") is None
    diag = await result["transport"].diagnostics()
    assert diag["active_transport_pins"] == 1 and len(diag["unknown_upstream_calls"]) == 1
    assert not result["deleted"] and result["attested"] == 0


def test_header_candidate_is_persisted_without_becoming_response_identity(tmp_path):
    from codex_deepseek.image_provider import ImageProvider
    provider = ImageProvider(tmp_path / "private", None, lambda: KEY, {})
    candidate = {"id": "gen-header-candidate-only", "provenance": "http_response_header:X-Generation-Id"}
    provider.record("synthetic-header-only", generation_header_candidate=candidate)
    value = provider.read_receipt(provider.receipts, "synthetic-header-only")
    assert value["response_id"] is None
    image = value["adaptations"]["image_transport"]
    assert image == {"generation_header_candidate": candidate}
    assert "provider_response_id" not in image and "provider_terminal" not in image
