"""Focused offline counterexamples for Files detail, role and byte-budget boundaries."""
import base64
import copy
import json

import pytest

from codex_deepseek.image_transport import ImageTransport, PayloadBudgetExceeded, TransportPolicy, encode_body
from codex_deepseek.protocol import MODEL, ProtocolError, Registry, prepare
from test_image_transport import FakeFiles, png, part


def request(items, tools=None):
    return {"model": MODEL, "input": items, "tools": tools or [],
            "reasoning": {"effort": "none"}, "stream": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["function", "custom"])
async def test_low_is_not_a_file_reference_even_when_same_bytes_are_uploaded_for_tool_high(tmp_path, kind):
    data = png()
    call = {"type": "custom_tool_call" if kind == "custom" else "function_call", "call_id": "native-exact-call",
            "name": "read_image", "namespace": "functions"}
    definition = {"type": kind, "name": "read_image", "description": "Synthetic image tool"}
    if kind == "function":
        call["arguments"] = "{}"
        definition["parameters"] = {"type": "object"}
    else:
        call["input"] = "read"
        definition["format"] = {"type": "text"}
    items = [
        {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "before user image"}, part(data, "low"),
            {"type": "input_text", "text": "after user image"}]},
        call,
        {"type": "custom_tool_call_output" if kind == "custom" else "function_call_output", "call_id": "native-exact-call", "output": [
            {"type": "input_text", "text": "before tool image"}, part(data, "high"),
            {"type": "input_text", "text": "between tool images"}, part(data, "low"),
            {"type": "input_text", "text": "after tool images"}]},
    ]
    native = request(items, [{"type": "namespace", "name": "functions", "tools": [definition]}])
    native_before = copy.deepcopy(native)
    payload, _ = prepare(native)
    prepared_before = copy.deepcopy(payload)
    backend = FakeFiles()
    tr = ImageTransport(tmp_path / "cache", lambda: "synthetic-key", backend,
                        TransportPolicy(1, max_file_references=1))
    async with tr.prepare(payload) as result:
        wire = json.loads(result.body)
        assert native == native_before and payload == prepared_before
        assert wire["provider"] == {"only": ["deepseek"], "allow_fallbacks": False, "require_parameters": True}
        assert wire["input"][0]["role"] == "user"
        assert wire["input"][0]["content"] == prepared_before["input"][0]["content"]
        output = wire["input"][2]
        assert output["type"] == "function_call_output"
        assert output["call_id"] == wire["input"][1]["call_id"] == "native-exact-call"
        assert output["output"][0] == prepared_before["input"][2]["output"][0]
        assert output["output"][2:] == prepared_before["input"][2]["output"][2:]
        assert output["output"][1]["detail"] == "high" and "file_id" in output["output"][1]
        assert result.converted_occurrences == 1 and result.image_occurrences == 3
        assert result.receipt()["remaining_inline_images"] == 2
        assert len(backend.uploaded) == 1 and backend.uploaded[0][1] == data
        restored = copy.deepcopy(wire)
        image = restored["input"][2]["output"][1]
        image.pop("file_id")
        image["image_url"] = "data:image/png;base64," + base64.b64encode(data).decode()
        assert restored == prepared_before


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "developer"])
async def test_supported_image_message_keeps_native_role_and_content_order_through_wire(tmp_path, role):
    original = request([{"type": "message", "role": role, "content": [
        {"type": "input_text", "text": "first"}, part(detail="high"),
        {"type": "input_text", "text": "last"}]}])
    before = copy.deepcopy(original)
    payload, _ = prepare(original)
    assert payload["input"][0]["role"] == role
    assert payload["input"][0]["content"] == original["input"][0]["content"]
    backend = FakeFiles()
    tr = ImageTransport(tmp_path / "cache", lambda: "synthetic-key", backend, TransportPolicy(1))
    async with tr.prepare(payload) as result:
        message = json.loads(result.body)["input"][0]
        assert message["role"] == role and len(message["content"]) == 3
        assert message["content"][0] == before["input"][0]["content"][0]
        assert message["content"][2] == before["input"][0]["content"][2]
        assert message["content"][1]["detail"] == "high" and "file_id" in message["content"][1]
    assert original == before


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["system", "assistant"])
async def test_forbidden_image_role_fails_before_files_or_credential_callback(tmp_path, role):
    backend = FakeFiles()
    credentials = []
    def credential():
        credentials.append(True)
        return "synthetic-key"
    tr = ImageTransport(tmp_path / "cache", credential, backend, TransportPolicy(1))
    body = request([{"type": "message", "role": role, "content": [part(detail="high")]}])
    with pytest.raises(ProtocolError):
        payload, _ = prepare(body)
        async with tr.prepare(payload):
            pass
    assert not backend.uploaded and not credentials


def test_provider_assistant_input_image_is_explicit_protocol_failure():
    registry = Registry()
    with pytest.raises(ProtocolError):
        registry.output({"type": "message", "id": "provider-message", "role": "assistant",
                         "content": [part(detail="high")]})


@pytest.mark.asyncio
async def test_wire_budget_invariant_covers_both_sides_of_activation_boundary(tmp_path):
    with pytest.raises(ValueError, match="at least as large"):
        TransportPolicy(10000, wire_budget_bytes=1000)
    backend = FakeFiles()
    credentials = []
    def credential():
        credentials.append(True)
        return "synthetic-key"
    tr = ImageTransport(tmp_path / "cache", credential, backend,
                        TransportPolicy(1000, wire_budget_bytes=1000))
    small = request([{"role": "user", "content": [{"type": "input_text", "text": "x" * 500}]}])
    assert len(encode_body(small)) < 1000
    async with tr.prepare(small) as result:
        assert result.body == encode_body(small)
        assert result.receipt()["within_wire_budget"] is True
    large = request([{"role": "user", "content": [{"type": "input_text", "text": "x" * 1500}]}])
    assert len(encode_body(large)) > 1000
    with pytest.raises(PayloadBudgetExceeded):
        async with tr.prepare(large):
            pass
    assert not backend.uploaded and not credentials
