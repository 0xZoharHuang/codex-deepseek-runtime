"""Controlled pure checks for the supplement; these never run native or a paid model."""
import asyncio
import json
from types import SimpleNamespace

import pytest
import verify_mixed_stream_faults as mixed


def body(call="c"):
    return {"input": [
        {"type": "function_call", "call_id": call, "arguments": json.dumps({"marker": call})},
        {"type": "function_call_output", "call_id": call,
         "output": [{"type": "input_text", "text": "APPENDED:" + call}]}
    ]}


@pytest.mark.parametrize("change", ["none", "missing-call", "duplicate-call", "bad-arguments",
                                    "missing-output", "wrong-output", "duplicate-output",
                                    "substring-output", "untyped-output"])
def test_retry_history_requires_original_call_and_exact_real_result(change):
    value = body()
    if change == "missing-call":
        value["input"].pop(0)
    elif change == "duplicate-call":
        value["input"].insert(0, value["input"][0])
    elif change == "bad-arguments":
        value["input"][0]["arguments"] = '{"marker":"other"}'
    elif change == "missing-output":
        value["input"].pop()
    elif change == "wrong-output":
        value["input"][1]["output"][0]["text"] = "APPENDED:other"
    elif change == "duplicate-output":
        value["input"].append(value["input"][1])
    elif change == "substring-output":
        value["input"][1]["output"][0]["text"] = "NOT_APPENDED:c_extra"
    elif change == "untyped-output":
        value["input"][1]["output"] = "APPENDED:c"
    if change == "none":
        assert mixed.retry_history(value, "c")["outputs"]
    else:
        with pytest.raises(AssertionError):
            mixed.retry_history(value, "c")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", mixed.KINDS)
async def test_fault_cannot_precede_observed_fsynced_effect(tmp_path, monkeypatch, kind):
    writes = []
    published = asyncio.Event()
    class Response:
        def __init__(self, **kwargs):
            pass
        async def prepare(self, request):
            pass
        async def write(self, data):
            writes.append(data)
            if b'"id": "mixed_call"' in data:
                published.set()
        async def write_eof(self):
            writes.append(b"EOF")
    monkeypatch.setattr(mixed.web, "StreamResponse", Response)
    value = {"tools": [{"type": "function", "name": "append_once"}], "input": []}
    async def incoming():
        return value
    request = SimpleNamespace(json=incoming, headers={"Authorization": "Bearer " + mixed.base.KEY1})
    upstream = mixed.MixedUpstream(tmp_path, kind)
    task = asyncio.create_task(upstream.handle(request))
    try:
        await asyncio.wait_for(published.wait(), 2)
        await asyncio.sleep(0)
        assert not task.done() and len(writes) == 2
        assert upstream.requests[0]["premise_established"] is False
        assert upstream.requests[0]["fault_emitted"] is False
        # Controlled fixture injection only. Actual native execution is a separate
        # acceptance run and cannot be inferred from this pure unit check.
        (tmp_path / "effects.jsonl").write_text(json.dumps({"marker": upstream.call_id}) + "\n")
        (tmp_path / "append-entered").write_text("fsync-barrier-fixture")
        await asyncio.wait_for(task, 2)
        row = upstream.requests[0]
        assert row["premise_established"] is True and row["fault_emitted"] is True
        assert row["tool_done_sent_at"] <= row["effect_observed_at"] <= row["fault_at"]
        assert row["wire"][1]["event"]["item"]["call_id"] == upstream.call_id
        assert writes[-1] == b"EOF"
        if kind == "undeclared-tool":
            assert len(writes) == 4 and b"nonexistent_mixed_stream_fixture" in writes[2]
        elif kind == "malformed-json":
            assert len(writes) == 4
            with pytest.raises(json.JSONDecodeError):
                json.loads(writes[2].decode().removeprefix("data: "))
        else:
            assert len(writes) == 3
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
@pytest.mark.parametrize("premise", ["absent", "wrong-marker", "duplicate"])
async def test_missing_or_false_effect_premise_never_injects_later_fault(tmp_path, monkeypatch, premise):
    class Response:
        def __init__(self, **kwargs):
            pass
        async def prepare(self, request):
            pass
        async def write(self, data):
            pass
    monkeypatch.setattr(mixed.web, "StreamResponse", Response)
    async def incoming():
        return {"tools": [{"type": "function", "name": "append_once"}]}
    request = SimpleNamespace(json=incoming, headers={"Authorization": "Bearer " + mixed.base.KEY1})
    upstream = mixed.MixedUpstream(tmp_path, "eof")
    if premise == "absent":
        async def no_barrier(predicate, seconds):
            assert not predicate()
            raise TimeoutError("No actual append observed")
        monkeypatch.setattr(mixed.base, "until", no_barrier)
    else:
        (tmp_path / "append-entered").write_text("fixture")
        rows = [{"marker": "wrong"}] if premise == "wrong-marker" else [{"marker": upstream.call_id}] * 2
        (tmp_path / "effects.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises((AssertionError, TimeoutError)):
        await upstream.handle(request)
    assert upstream.requests[0]["fault_emitted"] is False
    assert upstream.requests[0]["premise_established"] is False


def test_unknown_variant_is_rejected_and_existing_gate_set_is_untouched(tmp_path):
    with pytest.raises(ValueError):
        mixed.MixedUpstream(tmp_path, "unknown")
    assert mixed.base.SCENARIOS == ("truncated", "tcp-reset", "auth401", "auth403",
                                     "effect-before", "effect-after", "effect-worker")


@pytest.mark.parametrize("mutation", ["substring-output", "untyped-output", "duplicate-call"])
def test_cold_history_rejects_shapes_accepted_by_legacy_substring_check(tmp_path, monkeypatch, mutation):
    """A cold replay must meet the same exact call/result contract as warm retry."""
    value = body()
    if mutation == "substring-output":
        value["input"][1]["output"][0]["text"] = "NOT_APPENDED:c_extra"
    elif mutation == "untyped-output":
        value["input"][1]["output"] = "APPENDED:c"
    else:
        value["input"].insert(0, value["input"][0])
    upstream = SimpleNamespace(requests=[{"body": value}], call_id="c")
    peer = SimpleNamespace(events=[])
    monkeypatch.setattr(mixed.base, "effects", lambda root: [{"marker": "c"}])
    monkeypatch.setattr(mixed.base, "persisted_items", lambda home, call: [])
    # Preserve the established generic cold helper. This supplement adds the
    # stricter replay contract instead of broadening the generic helper's scope.
    assert mixed.base.cold_evidence(upstream, tmp_path, peer, returned=True)
    with pytest.raises(AssertionError):
        mixed.retry_history(value, "c")
