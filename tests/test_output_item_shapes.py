"""Output shape contracts shared by the fixed CLI and App native consumers.

These are local protocol fixtures, not claims about cloud response behavior.
"""
import copy

import pytest

from codex_deepseek.protocol import Events, ProtocolError, Registry


def message(**fields):
    return {"id": "msg", "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Fixture answer"}], **fields}


def reasoning(**fields):
    return {"id": "rs", "type": "reasoning", "summary": [], **fields}


def malformed_items():
    missing_summary = reasoning()
    del missing_summary["summary"]
    return [
        message(content=[{"type": "output_text", "text": "answer", "annotations": {}}]),
        message(content=[{"type": "output_text", "text": "answer", "logprobs": "opaque"}]),
        message(phase="future_phase"),
        message(phase={"invalid": "object"}),
        message(phase=[]),
        message(status=7),
        message(encrypted_content=False),
        message(internal_chat_message_metadata_passthrough=[]),
        message(internal_chat_message_metadata_passthrough={"turn_id": 7}),
        message(internal_chat_message_metadata_passthrough={"create_time": True}),
        missing_summary,
        reasoning(summary=None),
        reasoning(summary={}),
        reasoning(summary=[{"type": "other", "text": "thought"}]),
        reasoning(summary=[{"type": {}, "text": "thought"}]),
        reasoning(summary=[{"type": "summary_text", "text": 7}]),
        reasoning(content={}),
        reasoning(content=[{"type": "other", "text": "thought"}]),
        reasoning(content=[{"type": "reasoning_text", "text": None}]),
        reasoning(encrypted_content=[]),
    ]


@pytest.mark.parametrize("item", malformed_items())
@pytest.mark.parametrize("event_kind", ["response.output_item.done", "response.completed"])
def test_native_undecodable_output_never_becomes_a_completed_item(item, event_kind):
    events = Events(Registry())
    event = ({"type": event_kind, "response": {"id": "r", "status": "completed", "output": [item]}}
             if event_kind == "response.completed" else {"type": event_kind, "item": item, "output_index": 0})
    frozen = copy.deepcopy(event)
    with pytest.raises(ProtocolError):
        events.transform(event)
    assert not events.terminal and events.status != "completed"
    assert event == frozen


@pytest.mark.parametrize("item", [
    message(),
    message(status=None, encrypted_content=None, phase=None),
    message(phase="commentary"), message(phase="partial_answer"), message(phase="final_answer"),
    message(content=[{"type": "output_text", "text": "", "annotations": None, "logprobs": []}]),
    message(content=[{"type": "output_text", "text": "answer", "annotations": [
        {"type": "future_annotation", "extension": {"opaque": [1, None]}}, "opaque", 7, None],
        "logprobs": [{"future": True}], "future_content_field": {"keep": True}}],
        future_output_field={"keep": True}),
    reasoning(),
    reasoning(content=None, encrypted_content=None),
    reasoning(summary=[{"type": "summary_text", "text": "", "future": True}], content=[
        {"type": "reasoning_text", "text": "thought", "future": True}, {"type": "text", "text": "tail"}]),
    message(internal_chat_message_metadata_passthrough={"turn_id": None, "create_time": 1.25,
        "content_item_kinds": "native_default_on_error", "cell_id": {"native_skips_deserializing": True},
        "future_metadata": {"keep": True}}),
])
def test_native_optional_and_opaque_extension_fields_are_preserved(item):
    frozen = copy.deepcopy(item)
    events = Events(Registry())
    result = events.transform({"type": "response.completed", "response": {
        "id": "r", "status": "completed", "output": [item]}})
    assert events.terminal and events.status == "completed"
    assert result[0]["item"] == item
    assert result[-1]["response"]["output"] == [item]
    assert item == frozen


@pytest.mark.parametrize("preview,complete", [
    ({"id": "rs", "type": "reasoning"}, reasoning(content=[{"type": "reasoning_text", "text": "thought"}])),
    (message(content=[]), message()),
    (message(content=[{"type": "output_text", "text": "", "annotations": {}}]), message()),
])
def test_partial_added_item_can_recover_before_authoritative_completion(preview, complete):
    events = Events(Registry())
    initial = events.transform({"type": "response.output_item.added", "item": preview})
    assert initial[0]["item"] == preview and not events.terminal
    events.transform({"type": "response.output_item.done", "item": complete})
    result = events.transform({"type": "response.completed", "response": {
        "id": "r", "status": "completed", "output": [complete]}})
    assert events.terminal and result[-1]["response"]["output"] == [complete]
