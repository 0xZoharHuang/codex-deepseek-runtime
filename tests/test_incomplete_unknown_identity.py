"""Unknown identity on error-only incomplete responses; real loopback, no model."""
import asyncio
import copy
import json

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import MODEL, encode_sse
from test_gateway import fake_upstream


def incomplete(model="unknown", reason="max_output_tokens"):
    response = {"id": "gen-incomplete-identity", "status": "incomplete", "model": model,
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                "output": [{"type": "function_call", "id": "fc-partial", "call_id": "call-partial",
                            "name": "ping", "arguments": '{"partial":', "status": "incomplete"}]}
    if reason is not None:
        response["incomplete_details"] = {"reason": reason}
    return response


async def exchange(fake_upstream, tmp_path, terminal, *, stream, prefix=False,
                   initial_model=MODEL, event_type=None):
    calls = []
    dispatched = asyncio.Event()
    effect = tmp_path / "effect.txt"
    prior = {"type": "function_call", "id": "fc-committed", "call_id": "call-committed",
             "name": "ping", "arguments": "{}", "status": "completed"}
    response = copy.deepcopy(terminal)
    if prefix:
        response["output"].insert(0, prior)

    async def provider(request):
        calls.append(await request.json())
        if not stream:
            return web.json_response(response)
        reply = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await reply.prepare(request)
        await reply.write(encode_sse({"type": "response.created", "response": {
            "id": response["id"], "status": "in_progress", "model": initial_model}}))
        if prefix:
            await reply.write(encode_sse({"type": "response.output_item.done", "output_index": 0,
                                          "item": prior}))
            await asyncio.wait_for(dispatched.wait(), 5)
        await reply.write(encode_sse({"type": event_type or "response." + response["status"],
                                     "response": response}))
        await reply.write_eof()
        return reply

    fake_upstream.append(provider)
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SYNTHETIC", "LOCAL", store, {}) as gateway:
            async with aiohttp.ClientSession(trust_env=False) as client:
                body = {"model": MODEL, "stream": stream, "input": [], "tools": [
                    {"type": "function", "name": "ping", "parameters": {"type": "object"}}]}
                async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses",
                                       headers={"Authorization": "Bearer LOCAL"}, json=body) as reply:
                    status = reply.status
                    if stream:
                        events = []
                        async for raw in reply.content:
                            if not raw.startswith(b"data: "):
                                continue
                            event = json.loads(raw[6:])
                            events.append(event)
                            if event["type"] == "response.output_item.done":
                                assert event["item"]["call_id"] == "call-committed"
                                with effect.open("a") as out:
                                    out.write("call-committed\n")
                                dispatched.set()
                        result = None
                    else:
                        result = await reply.json()
                        events = []
        receipt = store.db.execute("SELECT status,upstream_status FROM receipts").fetchone()
        attestations = store.db.execute(
            "SELECT count(*) FROM adapter_metadata WHERE key LIKE 'native-complete-v1:%'").fetchone()[0]
        return {"status": status, "events": events, "result": result, "receipt": receipt,
                "calls": calls, "effect": effect.read_text() if effect.exists() else "",
                "attestations": attestations, "expected": response}
    finally:
        store.close()


def assert_incomplete(observed, *, stream):
    assert observed["status"] == 200 and observed["receipt"] == ("incomplete", 200)
    assert len(observed["calls"]) == 1 and observed["attestations"] == 0
    if stream:
        events = observed["events"]
        assert events[-1]["type"] == "response.incomplete"
        assert not any(e["type"] in {"response.completed", "response.failed"} for e in events)
        result = events[-1]["response"]
    else:
        result = observed["result"]
    assert result == observed["expected"]
    assert result["output"][-1]["arguments"] == '{"partial":'


def assert_rejected(observed, *, stream):
    assert observed["receipt"] == ("adapter_error", 200)
    assert len(observed["calls"]) == 1 and observed["attestations"] == 0
    if stream:
        assert observed["events"][-1]["type"] == "response.failed"
        assert not any(e["type"] in {"response.completed", "response.incomplete",
                                     "response.output_item.done"} for e in observed["events"])
    else:
        assert observed["status"] == 502
    assert observed["effect"] == ""


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("reason", ["max_output_tokens", "content_filter", "future_error_reason", None],
                         ids=["tokens", "filter", "future-error", "missing-reason"])
async def test_unknown_error_incomplete_keeps_original_diagnostics_without_tool_commit(
        fake_upstream, tmp_path, stream, reason):
    observed = await exchange(fake_upstream, tmp_path, incomplete(reason=reason), stream=stream)
    assert_incomplete(observed, stream=stream)
    assert observed["effect"] == ""
    assert not any(e["type"] == "response.output_item.done" for e in observed["events"])


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("model", [MODEL, None], ids=["matching", "missing"])
@pytest.mark.parametrize("reason", ["max_output_tokens", "interrupted"])
async def test_existing_matching_or_missing_model_behavior_is_unchanged(
        fake_upstream, tmp_path, stream, model, reason):
    observed = await exchange(fake_upstream, tmp_path, incomplete(model, reason), stream=stream)
    assert_incomplete(observed, stream=stream)


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("model", ["other/model", MODEL + "-wrong", 17, ""],
                         ids=["different-model", "different-version", "wrong-type", "empty"])
async def test_explicit_model_conflict_or_malformed_identity_still_fails(
        fake_upstream, tmp_path, stream, model):
    assert_rejected(await exchange(fake_upstream, tmp_path, incomplete(model), stream=stream), stream=stream)


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
async def test_unknown_interrupted_is_not_an_error_only_exception(fake_upstream, tmp_path, stream):
    assert_rejected(await exchange(fake_upstream, tmp_path, incomplete(reason="interrupted"),
                                   stream=stream), stream=stream)


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
async def test_unknown_does_not_mask_explicit_provider_conflict(fake_upstream, tmp_path, stream):
    response = incomplete()
    response["provider"] = "Morph"
    assert_rejected(await exchange(fake_upstream, tmp_path, response, stream=stream), stream=stream)


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
async def test_unknown_completed_identity_still_fails(fake_upstream, tmp_path, stream):
    response = {"id": "gen-incomplete-identity", "model": "unknown", "status": "completed",
                "output": [{"type": "message", "id": "msg-one", "role": "assistant",
                            "status": "completed", "content": [
                                {"type": "output_text", "text": "safe", "annotations": []}]}]}
    assert_rejected(await exchange(fake_upstream, tmp_path, response, stream=stream), stream=stream)


async def test_unknown_created_identity_still_fails(fake_upstream, tmp_path):
    observed = await exchange(fake_upstream, tmp_path, incomplete(MODEL), stream=True,
                              initial_model="unknown")
    assert_rejected(observed, stream=True)
    assert not any(e["type"] == "response.created" for e in observed["events"])


@pytest.mark.parametrize("reason", ["max_output_tokens", "content_filter"])
async def test_incomplete_never_replays_a_tool_already_delivered(
        fake_upstream, tmp_path, reason):
    observed = await exchange(fake_upstream, tmp_path, incomplete(reason=reason),
                              stream=True, prefix=True)
    assert_incomplete(observed, stream=True)
    assert observed["effect"] == "call-committed\n"
    assert sum(e["type"] == "response.output_item.done" for e in observed["events"]) == 1


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
async def test_unknown_cannot_bypass_incomplete_terminal_shape(fake_upstream, tmp_path, stream):
    response = incomplete()
    response["output"] = {}
    assert_rejected(await exchange(fake_upstream, tmp_path, response, stream=stream), stream=stream)


async def test_unknown_cannot_bypass_event_terminal_status_consistency(fake_upstream, tmp_path):
    response = incomplete()
    response["status"] = "completed"
    observed = await exchange(fake_upstream, tmp_path, response, stream=True,
                              event_type="response.incomplete")
    assert_rejected(observed, stream=True)


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("reason", ["max_output_tokens", "content_filter"])
async def test_unknown_error_incomplete_releases_only_proved_image_consumption(
        fake_upstream, tmp_path, stream, reason):
    from test_image_terminal_identity import ID, envelope, run_case
    response = envelope("incomplete")
    response["model"] = "unknown"
    response["incomplete_details"] = {"reason": reason}
    response["usage"] = {"input_tokens": 1, "output_tokens": 1}
    result = await run_case(fake_upstream, tmp_path, response, stream=stream)
    assert result["receipt_status"] == "incomplete" and result["response_id"] == ID
    if stream:
        values = [json.loads(line[6:]) for line in result["text"].splitlines()
                  if line.startswith("data: ")]
        assert values[-1]["type"] == "response.incomplete"
        assert not any(v["type"] in {"response.completed", "response.output_item.done"} for v in values)
        observed = values[-1]["response"]
    else:
        observed = json.loads(result["text"])
    assert observed == response and result["attested"] == 0
    diag = await result["transport"].diagnostics()
    assert diag["active_transport_pins"] == 0 and not diag["unknown_upstream_calls"]
    assert result["image"]["consumption_status"] == "provider_terminal"
    assert result["image"]["provider_terminal"] == "incomplete"
    assert (await result["transport"].cleanup(all_unpinned=True))["all_owned_files_deleted"]
    assert len(result["deleted"]) == 1 and result["calls"] == 1
