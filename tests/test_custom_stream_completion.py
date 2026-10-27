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
    if finish_event == "response.function_call_arguments.done":
        observed += events.transform(finalizer)
        assert not any(x["type"] in {"response.custom_tool_call_input.done", "response.output_item.done"}
                       for x in observed)
        finalizer = {"type": "response.output_item.done", "output_index": 0, "item": {**item, "arguments": raw}}
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


@pytest.mark.parametrize("kind", ["custom", "function"])
@pytest.mark.parametrize("preview", ["delta", "arguments_done", "both"])
@pytest.mark.parametrize("finalizer", ["item_done", "terminal_snapshot"])
def test_native_completed_item_is_authority_before_dispatch(kind, preview, finalizer):
    """Native router builds its payload from the completed item, never preview."""
    definition = {"type": kind, "name": "tool"}
    if kind == "function":
        definition["parameters"] = {"type": "object"}
    _, registry = prepare({"model": MODEL, "input": [], "tools": [definition]})
    events = Events(registry)
    old = json.dumps({"input": 'text("preview");'} if kind == "custom" else {"path": "preview"})
    final = json.dumps({"input": 'text("final 中文 🦉");\n'} if kind == "custom" else {"path": "final"})
    item = {"id": "authority", "type": "function_call", "name": "tool", "call_id": "call", "arguments": ""}
    observed = events.transform({"type": "response.output_item.added", "output_index": 0, "item": item})
    if preview in {"delta", "both"}:
        observed += events.transform({"type": "response.function_call_arguments.delta",
                                      "item_id": "authority", "output_index": 0, "delta": old})
    if preview in {"arguments_done", "both"}:
        observed += events.transform({"type": "response.function_call_arguments.done",
                                      "item_id": "authority", "output_index": 0, "arguments": old})
    assert not any(x["type"] in {"response.custom_tool_call_input.done", "response.output_item.done"} for x in observed)
    done = {**item, "arguments": final}
    if finalizer == "item_done":
        observed += events.transform({"type": "response.output_item.done", "output_index": 0, "item": done})
    else:
        observed += events.transform({"type": "response.completed", "response": {
            "id": "response", "status": "completed", "output": [done]}})
    native_calls = [x["item"] for x in observed if x["type"] == "response.output_item.done"]
    assert len(native_calls) == 1 and native_calls[0]["call_id"] == "call"
    if kind == "custom":
        assert native_calls[0]["input"] == json.loads(final)["input"]
        assert [x["input"] for x in observed if x["type"] == "response.custom_tool_call_input.done"] == [json.loads(final)["input"]]
        # There is no append-only correction event; do not fabricate old+new input.
        deltas = "".join(x["delta"] for x in observed if x["type"] == "response.custom_tool_call_input.delta")
        assert deltas == json.loads(old)["input"]
    else:
        assert native_calls[0]["arguments"] == final
    assert events.argument_diagnostic_count and events.argument_diagnostics
    assert all(x["reason"] == "producer_append_constraint_violation" for x in events.argument_diagnostics)


@pytest.mark.parametrize("preview", ['{"input":null}', '{"input":"\\ud800"}', '{"input":"UNSAFE"}', '{"wrong":"field"}'])
def test_invalid_custom_preview_can_be_replaced_by_valid_final_without_early_completion(preview):
    _, registry = prepare({"model": MODEL, "input": [], "tools": [
        {"type": "custom", "name": "exec", "format": {"type": "grammar", "syntax": "regex", "definition": "SAFE"}}]})
    events = Events(registry)
    item = {"id": "recover", "type": "function_call", "name": "exec", "call_id": "call", "arguments": ""}
    events.transform({"type": "response.output_item.added", "item": item})
    assert events.transform({"type": "response.function_call_arguments.done", "item_id": "recover", "arguments": preview}) == []
    output = events.transform({"type": "response.output_item.done", "item": {**item, "arguments": '{"input":"SAFE"}'}})
    assert output[-1]["item"]["input"] == "SAFE"
    assert [x["input"] for x in output if x["type"] == "response.custom_tool_call_input.done"] == ["SAFE"]
    assert any(x["reason"] == "invalid_uncommitted_custom_preview" for x in events.argument_diagnostics)


@pytest.mark.parametrize("final", ['{"input":null}', '{"input":"SAFE","extra":1}',
                                   '{"input":"SAFE","input":"SAFE"}', '{"input":"\\ud800"}',
                                   '{"input":"UNSAFE"}', '{"input":'])
def test_final_custom_envelope_unicode_and_grammar_remain_strict(final):
    _, registry = prepare({"model": MODEL, "input": [], "tools": [
        {"type": "custom", "name": "exec", "format": {"type": "grammar", "syntax": "regex", "definition": "SAFE"}}]})
    events = Events(registry)
    item = {"id": "strict", "type": "function_call", "name": "exec", "call_id": "call", "arguments": ""}
    events.transform({"type": "response.output_item.added", "item": item})
    events.transform({"type": "response.function_call_arguments.done", "item_id": "strict", "arguments": '{"input":"SAFE"}'})
    with pytest.raises(ProtocolError):
        events.transform({"type": "response.output_item.done", "item": {**item, "arguments": final}})
    assert "strict" not in events.custom_done


@pytest.mark.parametrize("kind", ["custom", "function"])
def test_final_authority_does_not_allow_changing_already_completed_call(kind):
    definition = {"type": kind, "name": "tool"}
    if kind == "function":
        definition["parameters"] = {"type": "object"}
    _, registry = prepare({"model": MODEL, "input": [], "tools": [definition]})
    events = Events(registry)
    first = {"id": "committed", "type": "function_call", "name": "tool", "call_id": "call",
             "arguments": json.dumps({"input": "first"} if kind == "custom" else {"value": "first"})}
    done = {"type": "response.output_item.done", "item": first}
    observed = events.transform(done)
    assert len([x for x in observed if x["type"] == "response.output_item.done"]) == 1
    assert events.transform(copy.deepcopy(done)) == []
    with pytest.raises(ProtocolError):
        events.transform({"type": "response.output_item.done", "item": {
            **first, "arguments": json.dumps({"input": "changed"} if kind == "custom" else {"value": "changed"})}})
    with pytest.raises(ProtocolError):
        events.transform({"type": "response.completed", "response": {
            "id": "response", "status": "completed", "output": [{**first, "arguments": "{}"}]}})


def test_revised_interleaved_calls_commit_only_exact_final_payloads():
    definitions = [{"type": "custom", "name": "exec"}, {"type": "function", "name": "read", "parameters": {"type": "object"}}]
    _, registry = prepare({"model": MODEL, "input": [], "tools": definitions})
    events = Events(registry)
    items, output = [], []
    for index in range(4):
        item = {"id": "i" + str(index), "type": "function_call", "name": "exec" if index % 2 == 0 else "read",
                "call_id": "c" + str(index), "arguments": ""}
        output += events.transform({"type": "response.output_item.added", "output_index": index, "item": item})
        old = json.dumps({"input": "preview-" + str(index)} if index % 2 == 0 else {"value": "preview-" + str(index)})
        output += events.transform({"type": "response.function_call_arguments.done",
                                    "output_index": index, "item_id": item["id"], "arguments": old})
        items.append({**item, "arguments": json.dumps({"input": "final-" + str(index)} if index % 2 == 0 else {"value": "final-" + str(index)})})
    assert not any(x["type"] in {"response.custom_tool_call_input.done", "response.output_item.done"} for x in output)
    output += events.transform({"type": "response.completed", "response": {"id": "r", "status": "completed", "output": items}})
    calls = [x["item"] for x in output if x["type"] == "response.output_item.done"]
    assert [x["call_id"] for x in calls] == ["c0", "c1", "c2", "c3"]
    assert [x["input"] for x in calls if x["type"] == "custom_tool_call"] == ["final-0", "final-2"]
    assert [json.loads(x["arguments"]) for x in calls if x["type"] == "function_call"] == [{"value": "final-1"}, {"value": "final-3"}]


def test_argument_revision_diagnostics_are_bounded_hash_only_and_observable(caplog):
    import hashlib
    _, registry = prepare({"model": MODEL, "input": [], "tools": [{"type": "custom", "name": "exec"}]})
    events = Events(registry)
    secret = "PRIVATE_CODE_SENTINEL_DO_NOT_LOG"
    for index in range(40):
        item = {"id": "raw-item-id-" + str(index), "type": "function_call", "name": "exec",
                "call_id": "raw-call-id-" + str(index), "arguments": ""}
        events.transform({"type": "response.output_item.added", "item": item})
        events.transform({"type": "response.function_call_arguments.done", "item_id": item["id"],
                          "arguments": json.dumps({"input": secret + "-preview"})})
        events.transform({"type": "response.output_item.done", "item": {**item, "arguments": json.dumps({"input": secret + "-final"})}})
    assert len(events.argument_diagnostics) == 16 and events.argument_diagnostics_truncated
    assert events.argument_diagnostic_count >= 40
    warnings = [r for r in caplog.records if r.name == "codex_deepseek.protocol"]
    assert len(warnings) == 16
    evidence = json.dumps(events.argument_diagnostics) + caplog.text
    assert all(value not in evidence for value in [secret, "raw-item-id-", "raw-call-id-"])
    assert hashlib.sha256(b"raw-call-id-0").hexdigest() in evidence
    summary = events.diagnostic_summary()
    assert set(summary) == {"total_count", "truncated", "records"}
    assert summary["total_count"] == events.argument_diagnostic_count
    assert summary["truncated"] is True and len(summary["records"]) == 16
    assert all(set(record) == {"reason", "item_sha256", "call_sha256",
                               "preview_arguments_sha256", "candidate_arguments_sha256", "hash_encoding"}
               for record in summary["records"])
    summary["records"][0]["reason"] = "MUTATED_COPY"
    summary["records"].clear()
    assert len(events.diagnostic_summary()["records"]) == 16
    assert events.diagnostic_summary()["records"][0]["reason"] == "producer_append_constraint_violation"


@pytest.mark.parametrize("kind", ["custom", "function"])
def test_multiple_argument_done_previews_do_not_commit_or_lock_the_call(kind):
    definition = {"type": kind, "name": "tool"}
    if kind == "function":
        definition["parameters"] = {"type": "object"}
    _, registry = prepare({"model": MODEL, "input": [], "tools": [definition]})
    events = Events(registry)
    item = {"id": "several", "type": "function_call", "name": "tool", "call_id": "call", "arguments": ""}
    events.transform({"type": "response.output_item.added", "item": item})
    observed = []
    for value in ["first", "second"]:
        raw = json.dumps({"input": value} if kind == "custom" else {"value": value})
        observed += events.transform({"type": "response.function_call_arguments.done",
                                      "item_id": "several", "arguments": raw})
    assert not any(x["type"] in {"response.custom_tool_call_input.done", "response.output_item.done"} for x in observed)
    final = json.dumps({"input": "final"} if kind == "custom" else {"value": "final"})
    committed = events.transform({"type": "response.output_item.done", "item": {**item, "arguments": final}})
    assert committed[-1]["item"].get("input", committed[-1]["item"].get("arguments")) == ("final" if kind == "custom" else final)


def test_malformed_delta_is_advisory_but_valid_final_is_still_required():
    _, registry = prepare({"model": MODEL, "input": [], "tools": [{"type": "custom", "name": "exec"}]})
    events = Events(registry)
    item = {"id": "bad-preview", "type": "function_call", "name": "exec", "call_id": "call", "arguments": ""}
    events.transform({"type": "response.output_item.added", "item": item})
    output = events.transform({"type": "response.function_call_arguments.delta",
                               "item_id": "bad-preview", "delta": '{"input":"preview'})
    output += events.transform({"type": "response.function_call_arguments.delta",
                                "item_id": "bad-preview", "delta": '\\q'})
    assert not any(x["type"] == "response.output_item.done" for x in output)
    final = events.transform({"type": "response.output_item.done", "item": {**item, "arguments": '{"input":"SAFE"}'}})
    assert final[-1]["item"]["input"] == "SAFE"
    assert "".join(x["delta"] for x in output + final if x["type"] == "response.custom_tool_call_input.delta") == "preview"
    assert any(x["reason"] == "invalid_uncommitted_custom_preview" for x in events.argument_diagnostics)


def test_equivalent_json_wrapper_encoding_and_added_partial_input_are_not_execution_authority():
    _, registry = prepare({"model": MODEL, "input": [], "tools": [{"type": "custom", "name": "exec"}]})
    events = Events(registry)
    item = {"id": "added", "type": "function_call", "name": "exec", "call_id": "call", "arguments": '{"input":"old'}
    added = events.transform({"type": "response.output_item.added", "item": item})
    assert added[-1]["item"]["type"] == "custom_tool_call"
    final = events.transform({"type": "response.output_item.done", "item": {**item, "arguments": json.dumps({"input": "中文 🦉"}, ensure_ascii=False)}})
    assert final[-1]["item"]["input"] == "中文 🦉"

    _, registry = prepare({"model": MODEL, "input": [], "tools": [{"type": "custom", "name": "exec"}]})
    events = Events(registry)
    item = {**item, "arguments": ""}
    events.transform({"type": "response.output_item.added", "item": item})
    events.transform({"type": "response.function_call_arguments.delta",
                      "item_id": "added", "delta": json.dumps({"input": "中文 🦉"}, ensure_ascii=True)})
    final = events.transform({"type": "response.output_item.done", "item": {**item, "arguments": json.dumps({"input": "中文 🦉"}, ensure_ascii=False)}})
    assert final[-1]["item"]["input"] == "中文 🦉"
    assert events.argument_diagnostics


@pytest.mark.parametrize("terminal", ["failed", "incomplete"])
def test_argument_done_preview_followed_by_noncompletion_never_dispatches(terminal):
    _, registry = prepare({"model": MODEL, "input": [], "tools": [{"type": "custom", "name": "exec"}]})
    events = Events(registry)
    item = {"id": "no-commit", "type": "function_call", "name": "exec", "call_id": "call", "arguments": ""}
    output = events.transform({"type": "response.output_item.added", "item": item})
    output += events.transform({"type": "response.function_call_arguments.done",
                                "item_id": "no-commit", "arguments": '{"input":"preview"}'})
    output += events.transform({"type": "response." + terminal, "response": {
        "id": "response", "status": terminal, "output": [{**item, "arguments": '{"input":"valid-final"}'}]}})
    assert not any(x["type"] in {"response.custom_tool_call_input.done", "response.output_item.done"} for x in output)
