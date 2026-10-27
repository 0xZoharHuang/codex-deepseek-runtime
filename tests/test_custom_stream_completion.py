"""Custom input finalization must preserve every delta without executing tools."""
import copy
import json
import random

import pytest

from codex_deepseek.protocol import Events, MODEL, ProtocolError, prepare


@pytest.mark.parametrize("source", ["", "SAFE", 'const x = "quote\\" slash\\\\ 中文 🦉";\ntext(x);\r\n'])
@pytest.mark.parametrize("ascii_only", [False, True])
def test_output_done_without_arguments_done_emits_every_custom_input_delta(source, ascii_only):
    raw = json.dumps({"input": source}, ensure_ascii=ascii_only)
    for split in range(len(raw) + 1):
        _, registry = prepare({"model": MODEL, "input": [], "tools": [{"type": "custom", "name": "exec"}]})
        events = Events(registry)
        item = {"type": "function_call", "id": "item", "name": "exec", "call_id": "call", "arguments": ""}
        output = events.transform({"type": "response.output_item.added", "output_index": 0, "item": item})
        # Include no deltas and every possible trailing escape/Unicode boundary.
        for char in raw[:split]:
            output += events.transform({"type": "response.function_call_arguments.delta",
                                        "item_id": "item", "output_index": 0, "delta": char})
        done = {**item, "arguments": raw}
        output += events.transform({"type": "response.output_item.done", "output_index": 0, "item": done})
        assert "".join(x["delta"] for x in output if x["type"] == "response.custom_tool_call_input.delta") == source
        assert [x["input"] for x in output if x["type"] == "response.custom_tool_call_input.done"] == [source]
        assert output[-1]["item"]["input"] == source
        assert events.transform({"type": "response.output_item.done", "output_index": 0, "item": done}) == []
        output += events.transform({"type": "response.completed",
                                    "response": {"id": "response", "status": "completed", "output": [done]}})
        assert output[-1]["response"]["output"][0]["input"] == source
        assert [x["sequence_number"] for x in output] == list(range(len(output)))


def test_parallel_custom_and_function_finalizers_keep_sources_and_call_ids():
    rng = random.Random(188061)
    definitions = [
        {"type": "namespace", "name": ns, "tools": [{"type": "custom", "name": "exec"}]}
        for ns in ["alpha", "beta"]
    ] + [{"type": "function", "name": name, "parameters": {"type": "object"}} for name in ["read", "lookup"]]
    for repetition in range(80):
        request, registry = prepare({"model": MODEL, "input": [], "tools": definitions})
        events = Events(registry)
        items, pending, sources, output = [], [], {}, []
        for index, tool in enumerate(request["tools"]):
            source = 'text("parallel ' + str(index) + ' 中文 🦉");\n'
            raw = json.dumps({"input": source} if index < 2 else {"index": index}, ensure_ascii=bool(index % 2))
            item = {"id": "item-" + str(index), "type": "function_call", "name": tool["name"],
                    "call_id": "call-" + str(index), "arguments": ""}
            output += events.transform({"type": "response.output_item.added", "output_index": index, "item": item})
            items.append({**item, "arguments": raw})
            sources[index] = source
            # Final snapshots supply an arbitrary omitted tail, including zero prior deltas.
            pending.append(list(raw[:rng.randrange(len(raw) + 1)]))
        while any(pending):
            index = rng.choice([i for i, chunks in enumerate(pending) if chunks])
            output += events.transform({"type": "response.function_call_arguments.delta",
                                        "output_index": index, "item_id": items[index]["id"],
                                        "delta": pending[index].pop(0)})
        for index in rng.sample(range(4), 4):
            if (index + repetition) % 2:
                output += events.transform({"type": "response.function_call_arguments.done",
                                            "output_index": index, "item_id": items[index]["id"],
                                            "arguments": items[index]["arguments"]})
            done = {"type": "response.output_item.done", "output_index": index, "item": items[index]}
            output += events.transform(done)
            assert events.transform(copy.deepcopy(done)) == []
        output += events.transform({"type": "response.completed",
                                    "response": {"id": "parallel", "status": "completed", "output": items}})
        assert len([x for x in output if x["type"] == "response.output_item.done"]) == 4
        for index, item in enumerate(output[-1]["response"]["output"]):
            assert item["call_id"] == items[index]["call_id"]
            if index < 2:
                assert item["input"] == sources[index] and item["namespace"] == ["alpha", "beta"][index]
                assert "".join(x["delta"] for x in output if x["type"] == "response.custom_tool_call_input.delta"
                               and x["item_id"] == items[index]["id"]) == sources[index]
                assert [x["input"] for x in output if x["type"] == "response.custom_tool_call_input.done"
                        and x["item_id"] == items[index]["id"]] == [sources[index]]
            else:
                assert item["arguments"] == items[index]["arguments"]
        assert [x["sequence_number"] for x in output] == list(range(len(output)))


@pytest.mark.parametrize("finish_event", ["response.function_call_arguments.done", "response.output_item.done"])
@pytest.mark.parametrize("raw,prefix", [
    ('{"input":null}', ""),
    ('{"input":"SAFE","extra":true}', ""),
    ('{"input":"SAFE","input":"OTHER"}', ""),
    ('{"input":"\\ud800"}', ""),
    ('{"input":"OTHER"}', '{"input":"SAFE'),
])
def test_bad_custom_finalizer_cannot_emit_native_completion(finish_event, raw, prefix):
    _, registry = prepare({"model": MODEL, "input": [], "tools": [{"type": "custom", "name": "exec"}]})
    events = Events(registry)
    item = {"id": "bad", "type": "function_call", "name": "exec", "call_id": "call", "arguments": ""}
    observed = events.transform({"type": "response.output_item.added", "output_index": 0, "item": item})
    if prefix:
        observed += events.transform({"type": "response.function_call_arguments.delta",
                                      "item_id": "bad", "output_index": 0, "delta": prefix})
    finalizer = ({"type": finish_event, "item": {**item, "arguments": raw}, "output_index": 0}
                 if finish_event == "response.output_item.done" else
                 {"type": finish_event, "item_id": "bad", "arguments": raw, "output_index": 0})
    with pytest.raises(ProtocolError):
        observed += events.transform(finalizer)
    assert not any(x["type"] in {"response.custom_tool_call_input.done", "response.output_item.done"}
                   for x in observed)


@pytest.mark.parametrize("prior", ["none", "added", "partial", "arguments_done"])
def test_completed_snapshot_finalizes_missing_custom_events_in_order(prior):
    source = 'const x = "中文 🦉";\ntext(x);\r\n'
    raw = json.dumps({"input": source})
    _, registry = prepare({"model": MODEL, "input": [], "tools": [{"type": "custom", "name": "exec"}]})
    events = Events(registry)
    item = {"id": "snapshot", "type": "function_call", "name": "exec", "call_id": "call", "arguments": ""}
    output = []
    if prior != "none":
        output += events.transform({"type": "response.output_item.added", "output_index": 0, "item": item})
    if prior in {"partial", "arguments_done"}:
        output += events.transform({"type": "response.function_call_arguments.delta",
                                    "item_id": "snapshot", "output_index": 0, "delta": raw[:19]})
    if prior == "arguments_done":
        output += events.transform({"type": "response.function_call_arguments.done",
                                    "item_id": "snapshot", "output_index": 0, "arguments": raw})
    output += events.transform({"type": "response.completed", "response": {
        "id": "response", "status": "completed", "output": [{**item, "arguments": raw}]}})
    assert "".join(x["delta"] for x in output if x["type"] == "response.custom_tool_call_input.delta") == source
    kinds = [x["type"] for x in output]
    assert kinds.count("response.custom_tool_call_input.done") == 1
    assert kinds.count("response.output_item.done") == 1
    assert kinds.index("response.custom_tool_call_input.done") < kinds.index("response.output_item.done") < len(kinds) - 1
    assert output[-1]["response"]["output"][0]["input"] == source
    assert [x["sequence_number"] for x in output] == list(range(len(output)))


@pytest.mark.parametrize("terminal", ["failed", "incomplete"])
def test_noncompleted_custom_snapshot_never_synthesizes_input_or_item_completion(terminal):
    _, registry = prepare({"model": MODEL, "input": [], "tools": [{"type": "custom", "name": "exec"}]})
    events = Events(registry)
    output = events.transform({"type": "response." + terminal, "response": {
        "id": "response", "status": terminal, "output": [
            {"id": "custom", "type": "function_call", "name": "exec", "call_id": "call",
             "arguments": '{"input":"text(1);"}'}]}})
    assert [x["type"] for x in output] == ["response." + terminal]


@pytest.mark.parametrize("bad", ["undeclared_tool", "duplicate_id", "invalid_custom_grammar"])
def test_entire_snapshot_is_validated_before_synthesizing_any_custom_completion(bad):
    _, registry = prepare({"model": MODEL, "input": [], "tools": [
        {"type": "custom", "name": "exec", "format": {"type": "grammar", "syntax": "regex", "definition": "SAFE"}}]})
    events = Events(registry)
    first = {"id": "first", "type": "function_call", "name": "exec", "call_id": "one", "arguments": '{"input":"SAFE"}'}
    second = {**first, "id": "second", "call_id": "two"}
    if bad == "undeclared_tool":
        second["name"] = "unadvertised"
    elif bad == "duplicate_id":
        second = copy.deepcopy(first)
    else:
        second["arguments"] = '{"input":"UNSAFE"}'
    observed = []
    with pytest.raises(ProtocolError):
        observed += events.transform({"type": "response.completed", "response": {
            "id": "response", "status": "completed", "output": [first, second]}})
    assert observed == []
