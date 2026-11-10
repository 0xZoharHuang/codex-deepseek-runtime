"""Regression cases derived from observed native/provider incompatibilities."""

import hashlib
import json

import pytest

from codex_deepseek.gateway import Store
from codex_deepseek.protocol import Events, MODEL, InputDecoder, ProtocolError, Registry, prepare


def request(tools, **more):
    return {"model": MODEL, "input": [], "tools": tools, **more}


def test_schema_format_requirement_preserves_native_tools_history_and_streaming():
    import copy
    from codex_deepseek.protocol import STRUCTURED_FORMAT_INSTRUCTION
    body=request([{'type':'function','name':'lookup','parameters':{'type':'object'}}],
                 instructions='Original native policy.',stream=True,
                 input=[{'role':'user','content':[{'type':'input_text','text':'Read the data, then answer.'}]}])
    plain,_=prepare(body);original=copy.deepcopy(body)
    schema={'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False}
    body['text']={'format':{'type':'json_schema','name':'result','strict':True,'schema':schema}}
    frozen=copy.deepcopy(body);structured,registry=prepare(body)
    assert body==frozen
    assert structured['tools']==plain['tools'] and structured['input']==plain['input']
    assert structured['stream'] is True and structured['text']=={'format':{'type':'json_object'}}
    assert structured['instructions'].startswith(original['instructions']+'\n\n'+STRUCTURED_FORMAT_INSTRUCTION)
    assert json.loads(structured['instructions'].split('JSON Schema:\n',1)[1])==schema
    assert registry.structured_output_mapping['requested_strict'] is True
    assert plain['instructions']==original['instructions']
    again,_=prepare({**body,'instructions':structured['instructions']})
    assert again['instructions']==structured['instructions']


@pytest.mark.parametrize('instructions',[False,0,[],{}])
def test_schema_instruction_hint_does_not_coerce_invalid_instructions(instructions):
    with pytest.raises(ProtocolError,match='instructions must be text'):
        prepare(request([],instructions=instructions,text={'format':{'type':'json_schema','schema':{'type':'object'}}}))


def test_custom_tool_roundtrip_preserves_source_call_pair_and_namespace():
    original = {"type": "namespace", "name": "functions", "tools": [
        {"type": "custom", "name": "exec", "description": "Run JavaScript", "format": {"type": "text"}}]}
    payload, registry = prepare(request([original]))
    wire = payload["tools"][0]["name"]
    source = 'const x = "中文 🦉";\ntext(x);'
    native = registry.output({"type": "function_call", "name": wire, "call_id": "call1",
                              "arguments": json.dumps({"input": source})})
    assert native == {"type": "custom_tool_call", "name": "exec", "namespace": "functions", "call_id": "call1", "input": source}
    outgoing, _ = prepare(request([original], input=[native, {"type": "custom_tool_call_output", "call_id": "call1", "output": "done"}]))
    assert outgoing["input"][0]["name"] == wire
    assert json.loads(outgoing["input"][0]["arguments"])["input"] == source
    assert outgoing["input"][1] == {"type": "function_call_output", "call_id": "call1", "output": "done"}
    assert outgoing["provider"] == {"only": ["deepseek"], "allow_fallbacks": False, "require_parameters": True}


def test_equal_names_in_different_namespaces_remain_distinct_on_resume():
    tools = [{"type": "namespace", "name": namespace, "tools": [{"type": "function", "name": "read", "parameters": {"type": "object"}}]} for namespace in ["one", "two"]]
    first, registry = prepare(request(tools))
    second, _ = prepare(request(list(reversed(tools))))
    assert len({tool["name"] for tool in first["tools"]}) == 2
    assert [tool["name"] for tool in first["tools"]] == list(reversed([tool["name"] for tool in second["tools"]]))
    for namespace, tool in zip(["one", "two"], first["tools"]):
        item = registry.output({"type": "function_call", "name": tool["name"], "arguments": "{}", "call_id": namespace})
        assert item["namespace"] == namespace


@pytest.mark.parametrize("ascii_only", [False, True])
def test_custom_stream_all_split_positions_preserve_unicode_and_escapes(ascii_only):
    source = 'const x="quote\\\" backslash\\ newline\n 中文 🦉";'
    raw = json.dumps({"input": source}, ensure_ascii=ascii_only)
    for split in range(len(raw) + 1):
        decoder = InputDecoder()
        chunks = [decoder.feed(raw[:split]), decoder.feed(raw[split:]), decoder.finish(raw)]
        assert "".join(chunks) == source
    decoder = InputDecoder()
    assert "".join(decoder.feed(char) for char in raw) == source


def test_native_codemode_stream_emits_custom_events_and_matching_final_item():
    outgoing, registry = prepare(request([{"type": "custom", "name": "exec"}]))
    name = outgoing["tools"][0]["name"]
    events = Events(registry)
    item = {"type": "function_call", "id": "fc1", "call_id": "c1", "name": name, "arguments": ""}
    transformed = events.transform({"type": "response.output_item.added", "item": item, "output_index": 0})
    raw = json.dumps({"input": 'text("ok");'})
    for chunk in raw:
        transformed += events.transform({"type": "response.function_call_arguments.delta", "item_id": "fc1", "delta": chunk})
    transformed += events.transform({"type": "response.function_call_arguments.done", "item_id": "fc1", "arguments": raw})
    item["arguments"] = raw
    transformed += events.transform({"type": "response.completed", "response": {"id": "resp1", "status":"completed", "output": [item]}})
    assert "".join(e["delta"] for e in transformed if e["type"] == "response.custom_tool_call_input.delta") == 'text("ok");'
    assert transformed[-1]["response"]["output"][0]["type"] == "custom_tool_call"
    assert [e["sequence_number"] for e in transformed] == list(range(len(transformed)))


def test_discovery_reveals_tools_only_after_native_search_result():
    spec = {"type": "tool_search", "execution": "client", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}
    outgoing, registry = prepare(request([spec]))
    assert len(outgoing["tools"]) == 1
    native = registry.output({"type": "function_call", "name": outgoing["tools"][0]["name"], "call_id": "search1", "arguments": '{"query":"audit"}'})
    assert native["type"] == "tool_search_call" and native["execution"] == "client"
    discovered = {"type": "namespace", "name": "audit", "tools": [{"type": "function", "name": "read_nonce", "parameters": {"type": "object"}}]}
    outgoing, registry = prepare(request([spec], input=[native, {"type": "tool_search_output", "call_id": "search1", "tools": [discovered]}]))
    assert len(outgoing["tools"]) == 2
    assert outgoing["input"][-1]["type"] == "function_call_output"
    assert any(tool.name == "read_nonce" and tool.namespace == "audit" for tool in registry.by_wire.values())


def test_search_evidence_survives_process_restart(tmp_path):
    path = tmp_path / "adapter.sqlite"
    store = Store(path)
    _, registry = prepare(request([]), store)
    raw = {"type": "openrouter:web_search", "id": "st1", "status": "completed", "action": {"type": "search", "query": "fixture", "sources": [{"url": "https://example.com"}]}}
    native = registry.output(raw)
    store.close()
    store = Store(path)
    try:
        outgoing, _ = prepare(request([], input=[native]), store)
        assert outgoing["input"] == [raw]
    finally:
        store.close()


def test_fail_closed_on_unknown_tool_or_cross_model_opaque_state():
    _, registry = prepare(request([]))
    with pytest.raises(ProtocolError, match="undeclared"):
        registry.output({"type": "function_call", "name": "unregistered", "arguments": "{}"})
    with pytest.raises(ProtocolError, match="opaque"):
        prepare(request([], input=[{"type": "compaction", "encrypted_content": "opaque"}]))
    with pytest.raises(ProtocolError, match="hosted"):
        prepare(request([{"type": "image_generation"}]))


def test_no_tool_compaction_does_not_require_parallel_tool_capability():
    outgoing, _ = prepare(request([], parallel_tool_calls=False))
    assert "parallel_tool_calls" not in outgoing
    outgoing, _ = prepare(request([{"type": "function", "name": "write", "parameters": {}}], parallel_tool_calls=False))
    assert outgoing['parallel_tool_calls'] is False
    assert outgoing['provider']['only'] == ['primeintellect']


def test_native_v2_plaintext_marker_and_task_content_are_preserved():
    spec = {"type": "namespace", "name": "collaboration", "tools": [
        {"type": "function", "name": "spawn_agent", "parameters": {"type": "object"}}]}
    payload, registry = prepare(request([spec]))
    call = registry.output({"type": "function_call", "name": payload['tools'][0]['name'],
                            "call_id": "spawn1", "arguments": '{"message":"Compute 19*23"}'})
    assert call['encrypted_function_args'] == []
    message = {"type": "agent_message", "author": "/root", "recipient": "/root/child", "content": [
        {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\nCompute 19*23"}]}
    payload, _ = prepare(request([], input=[message]))
    assert len(payload["input"]) == 1
    item = payload["input"][0]
    # Native AgentMessage has no role and marks incoming context. The bridge
    # supplies a provider user boundary, not the unused assistant fallback.
    assert (item["type"], item["role"]) == ("message", "user")
    assert "phase" not in item
    assert item["content"][0] == {"type": "input_text", "text":
        '{"type":"agent_message","author":"/root","recipient":"/root/child"}'}
    assert item["content"][1:] == message["content"]
    message['content'].append({"type": "encrypted_content", "encrypted_content": "opaque"})
    with pytest.raises(ProtocolError, match="opaque cross-agent"):
        prepare(request([], input=[message]))


def test_schema_routes_explicitly_without_downgrading_constraint():
    original = request([], text={"format":{"type":"json_schema","name":"fixture","strict":True,"schema":{"type":"object","properties":{},"additionalProperties":False}}})
    outgoing, _ = prepare(original)
    assert outgoing['text'] == original['text']
    assert outgoing['provider'] == {'only':['morph/fp8'],'allow_fallbacks':False,'require_parameters':True}


def test_escaped_json_property_name_is_not_mistaken_for_missing_input():
    decoder=InputDecoder()
    raw='{"\\u0069nput":"text(1);"}'
    assert ''.join(decoder.feed(char) for char in raw)=='text(1);'
    assert decoder.finish(raw)==''


def test_nullable_format_and_invalid_wire_objects_are_handled_explicitly():
    outgoing,_=prepare(request([],text=None))
    assert outgoing['provider']['only']==['deepseek']
    with pytest.raises(ProtocolError,match='must be an object'):
        prepare([])
    with pytest.raises(ProtocolError,match='items must be objects'):
        prepare(request([],input=[123]))



def test_native_tool_names_and_unique_aliases_survive_incremental_stream():
    payload, registry = prepare(request([{'type':'custom','name':'exec'}, {'type':'namespace','name':'collaboration','tools':[{'type':'function','name':'spawn_agent','parameters':{}}]}]))
    assert payload['tools'][0]['name']=='exec'
    events=Events(registry)
    assert events.transform({'type':'response.output_item.added','item':{'type':'function_call','id':'fc','call_id':'c','name':'exec','arguments':''}})[0]['item']['type']=='custom_tool_call'
    assert events.transform({'type':'response.function_call_arguments.delta','item_id':'fc','delta':'{"input":"text(1);"}'})[0]['type']=='response.custom_tool_call_input.delta'
    assert registry.output({'type':'function_call','call_id':'c2','name':'collaboration.spawn_agent','arguments':'{}'})['encrypted_function_args']==[]
    assert registry.output({'type':'function_call','call_id':'c3','name':'spawn_agent','arguments':'{}'})['namespace']=='collaboration'
    _, collision=prepare(request([{'type':'namespace','name':ns,'tools':[{'type':'function','name':'read','parameters':{}}]} for ns in ['one','two']]))
    with pytest.raises(ProtocolError,match='ambiguous'):
        collision.output({'type':'function_call','name':'read','arguments':'{}'})


def test_native_developer_policy_keeps_its_original_role():
    items=[{'type':'message','role':'developer','content':[{'type':'input_text','text':'Native runtime tool policy'}]}, {'role':'user','content':'Actual user task'}]
    outgoing,_=prepare(request([],input=items))
    assert outgoing['input'][0]['role']=='developer'
    assert outgoing['input'][0]['content']==items[0]['content']
    assert outgoing['input'][1]==items[1]


def test_native_discovery_unhides_provider_function_without_replacing_current_schema():
    search={'type':'tool_search','execution':'client'}
    current={'type':'function','name':'read_nonce','parameters':{'type':'object','properties':{'fresh':{'type':'boolean'}}},'defer_loading':True}
    stale={**current,'parameters':{'type':'object','properties':{'stale':{'type':'string'}}}}
    history=[{'type':'tool_search_call','call_id':'search1','arguments':{'query':'audit'}}, {'type':'tool_search_output','call_id':'search1','tools':[{'type':'namespace','name':'audit','tools':[stale]}]}]
    output,_=prepare(request([search],input=history))
    found=next(t for t in output['tools'] if 'audit.read_nonce' in t.get('description',''))
    assert 'defer_loading' not in found
    output,_=prepare(request([search,{'type':'namespace','name':'audit','tools':[current]}],input=history))
    found=next(t for t in output['tools'] if 'audit.read_nonce' in t.get('description',''))
    assert 'defer_loading' not in found and found['parameters']==current['parameters']


def test_parallel_tool_history_uses_completed_response_evidence_after_restart(tmp_path):
    path = tmp_path / "adapter.sqlite"
    tools = [{"type": "function", "name": "sample", "parameters": {"type": "object"}}]
    body = request(tools)
    reasoning = {"type": "reasoning", "id": "rs_parallel",
                 "content": [{"type": "reasoning_text", "text": "Use two independent tools."}]}
    first = {"type": "function_call", "name": "sample", "id": "fc_first",
             "call_id": "call_first", "arguments": "{}", "status": "completed"}
    second = {**first, "id": "fc_second", "call_id": "call_second"}
    third = {**first, "id": "fc_third", "call_id": "call_third"}
    first_output = {"type": "function_call_output", "call_id": first["call_id"], "output": "first"}
    second_output = {"type": "function_call_output", "call_id": second["call_id"], "output": "second"}
    third_output = {"type": "function_call_output", "call_id": third["call_id"], "output": "third"}
    policy = {"type": "message", "role": "system", "content": "Keep the current task."}
    store = Store(path)
    try:
        for number, output in enumerate(([reasoning, first, second], [third])):
            receipt = store.start()
            _, registry = prepare(body, store)
            events = Events(registry)
            events.transform({"type": "response.completed", "response": {
                "id": f"resp_{number}", "status": "completed", "output": output}})
            store.finish(receipt, "completed", 200, events)
    finally:
        store.close()

    store = Store(path)
    try:
        history = [reasoning, first, first_output, second, second_output, policy]
        payload, registry = prepare({**body, "input": history}, store)
        assert payload["input"] == [reasoning, first, second, first_output, second_output, policy]
        assert registry.regrouped_tool_history == 1
        assert history == [reasoning, first, first_output, second, second_output, policy]
        for unchanged in (
            [reasoning, first, second, first_output, second_output],
            [reasoning, first, first_output, third, third_output],
            [reasoning, first, first_output, {**second, "id": "different_id"}, second_output],
            [reasoning, first, first_output, {**second, "arguments": '{"changed":true}'}, second_output],
            [reasoning, first, first_output, policy, second, second_output],
            [reasoning, first, first_output, second],
        ):
            payload, registry = prepare({**body, "input": unchanged}, store)
            assert payload["input"] == unchanged
            assert registry.regrouped_tool_history == 0
    finally:
        store.close()


def test_interrupted_reasoning_before_peer_delivery_is_not_an_empty_assistant_turn():
    tools = [{"type": "function", "name": "sample", "parameters": {"type": "object"}}]
    reasoning = {"type": "reasoning", "id": "rs_interrupted",
                 "content": [{"type": "reasoning_text", "text": "Waiting for the peer."}]}
    prior = {"type": "function_call", "id": "fc_prior", "call_id": "call_prior",
             "name": "sample", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": "call_prior", "output": "ready"}
    peer = {"type": "agent_message", "author": "/root/reviewer", "recipient": "/root",
            "content": [{"type": "input_text", "text": "Message Type: FINAL_ANSWER\nDone."}]}
    payload, registry = prepare(request(tools, input=[prior, output, reasoning, peer]))
    items = payload["input"]
    assert items[1:3] == [prior, output]
    assert items[0]["role"] == "assistant"
    assert items[3]["role"] == "user" and "phase" not in items[3]
    assert json.loads(items[0]["content"][0]["text"].split("\n", 1)[1]) == [
        {"input_index": 2, "reasoning_index": 0, "item": reasoning}
    ]
    assert json.loads(items[3]["content"][0]["text"]) == {
        "type": "agent_message", "author": peer["author"], "recipient": peer["recipient"]}
    assert items[3]["content"][1:] == peer["content"]
    assert registry.continuation_context


def test_retained_context_does_not_change_complete_assistant_history_or_invent_a_tool():
    reasoning = {"type": "reasoning", "id": "rs_previous",
                 "content": [{"type": "reasoning_text", "text": "A complete prior turn."}]}
    answer = {"type": "message", "id": "msg_previous", "role": "assistant",
              "content": [{"type": "output_text", "text": "Earlier answer."}]}
    user = {"type": "message", "role": "user", "content": "Continue."}
    complete, registry = prepare(request([], input=[reasoning, answer, user]))
    assert complete["input"] == [reasoning, answer, user]
    assert not registry.continuation_context

    trailing, registry = prepare(request([], input=[user, reasoning, answer]))
    assert trailing["input"][1:] == [user]
    context = trailing["input"][0]
    assert context["role"] == "assistant"
    assert json.loads(context["content"][0]["text"].split("\n", 1)[1]) == [
        {"input_index": 1, "reasoning_index": 0, "item": reasoning},
        {"input_index": 2, "item": answer},
    ]
    assert registry.continuation_context


@pytest.mark.parametrize("message_type", ["NEW_TASK", "MESSAGE", "FINAL_ANSWER", "CHANNEL_POST", None])
def test_native_peer_projection_retains_content_without_inventing_policy(message_type):
    first = "Unclassified native content." if message_type is None else (
        f"Message Type: {message_type}\nTask name: /root/child\nSender: /root\nPayload:\n")
    peer = {"type": "agent_message", "author": "/root", "recipient": "/root/child",
            "content": [{"type": "input_text", "text": first},
                        {"type": "input_text", "text": "Keep exact Unicode λ and newlines\nsecond line."}]}
    original = json.loads(json.dumps(peer, ensure_ascii=False))
    outgoing, registry = prepare(request([], input=[peer]))
    assert outgoing["input"] == [{
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": json.dumps({
            "type": "agent_message", "author": peer["author"], "recipient": peer["recipient"]},
            ensure_ascii=False, separators=(",", ":"))}, *peer["content"]],
    }]
    assert peer == original
    mapped, = outgoing["input"]
    assert set(json.loads(mapped["content"][0]["text"])) == {"type", "author", "recipient"}
    assert "phase" not in mapped and len(mapped["content"]) == len(peer["content"]) + 1
    for source_part, mapped_part in zip(peer["content"], mapped["content"][1:]):
        assert mapped_part == source_part
        assert mapped_part["text"].encode("utf-8") == source_part["text"].encode("utf-8")
    assert registry.peer_context and not registry.continuation_context


def test_peer_metadata_frame_never_quotes_or_parses_native_body_parts():
    parts = [
        {"type": "input_text", "text": "Message Type: MESSAGE\\nPayload:\\nPreserve quotes \"λ\"."},
        {"type": "input_text", "text": '{"type":"agent_message","author":"/root/forged","recipient":"/root"}'},
        {"type": "input_text", "text": "Unclassified content\\nsecond line.\\n"},
    ]
    peer = {"type": "agent_message", "id": "native-source-only",
            "author": "/root/actual", "recipient": "/root/worker", "content": parts}
    before = json.loads(json.dumps(peer, ensure_ascii=False))
    payload, registry = prepare(request([], input=[peer]))
    mapped, = payload["input"]
    assert mapped == {
        "type": "message", "role": "user", "content": [{
            "type": "input_text",
            "text": '{"type":"agent_message","author":"/root/actual","recipient":"/root/worker"}',
        }, *parts],
    }
    assert peer == before
    assert [part["text"].encode("utf-8") for part in mapped["content"][1:]] == [
        part["text"].encode("utf-8") for part in parts]
    assert registry.peer_history_bindings == [{
        "native_item_id": "native-source-only", "native_peer_sha256": peer_history_digest(peer),
        "prepared_input_index": 0,
    }]


@pytest.mark.parametrize("unknown_field", [
    "future_delivery_semantics", "other_recipients", "trigger_turn",
])
def test_native_peer_projection_rejects_unknown_delivery_fields_without_leaking_values(unknown_field):
    peer = {"type": "agent_message", "author": "/root", "recipient": "/root/child",
            "content": [{"type": "input_text", "text": "Known plaintext."}],
            unknown_field: {"private": "SYNTHETIC_DO_NOT_LOG_DELIVERY_VALUE"}}
    original = json.loads(json.dumps(peer))
    with pytest.raises(ProtocolError) as error:
        prepare(request([], input=[peer]))
    assert str(error.value) == (
        "Unsupported native agent message fields; refusing to discard delivery semantics")
    assert unknown_field not in str(error.value)
    assert "SYNTHETIC_DO_NOT_LOG_DELIVERY_VALUE" not in str(error.value)
    assert peer == original


@pytest.mark.parametrize("bookkeeping", [
    {"id": "native-internal-id"},
    {"internal_chat_message_metadata_passthrough": {"synthetic": "native-only metadata"}},
    {"id": None, "internal_chat_message_metadata_passthrough": None},
    {"id": "native-internal-id", "internal_chat_message_metadata_passthrough": {
        "synthetic": "native-only metadata"}},
])
def test_native_peer_projection_clears_known_native_bookkeeping_without_changing_context(bookkeeping):
    # Native id/metadata stay in local provenance, outside this provider frame.
    # They must not be guessed into the model's text or incoming content.
    peer = {"type": "agent_message", "author": "/root/worker", "recipient": "/root",
            "content": [{"type": "input_text", "text": "Message Type: MESSAGE\n"},
                        {"type": "input_text", "text": "Preserve λ and exact\npart order."}]}
    expected, baseline_registry = prepare(request([], input=[peer]))
    original = json.loads(json.dumps({**peer, **bookkeeping}, ensure_ascii=False))
    outgoing, registry = prepare(request([], input=[original]))
    assert outgoing == expected
    assert original == {**peer, **bookkeeping}
    expected_registry = {**vars(baseline_registry), "peer_history_bindings": [{
        "native_item_id": bookkeeping.get("id"),
        "native_peer_sha256": peer_history_digest(peer),
        "prepared_input_index": 0,
    }]}
    assert vars(registry) == expected_registry


def peer_history_digest(peer):
    envelope = {key: peer[key] for key in ("author", "recipient", "content")}
    return hashlib.sha256(json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()


def test_peer_history_binding_tracks_native_identity_after_interrupted_prefix():
    peer = {"type": "agent_message", "id": "native-peer-1",
            "author": "/root/worker", "recipient": "/root",
            "content": [{"type": "input_text", "text": "Message Type: MESSAGE\nPayload:\nλ"}]}
    frame = {"type": "agent_message", "author": peer["author"], "recipient": peer["recipient"]}
    echo = {"type": "message", "role": "user", "content": [{
        "type": "input_text", "text": json.dumps(frame, ensure_ascii=False, separators=(",", ":")),
    }, *peer["content"]]}
    reasoning = {"type": "reasoning", "id": "rs_orphan",
                 "content": [{"type": "reasoning_text", "text": "Interrupted work."}]}
    payload, registry = prepare(request([], input=[echo, reasoning, peer]))
    assert len(payload["input"]) == 3
    # An ordinary user message is identical to the incoming-context codec.
    # Only the true native AgentMessage binds after insert(0) shifts its index.
    assert payload["input"][1] == payload["input"][2] == echo
    assert registry.peer_history_bindings == [{
        "native_item_id": "native-peer-1", "native_peer_sha256": peer_history_digest(peer),
        "prepared_input_index": 2,
    }]
    assert set(registry.peer_history_bindings[0]) == {
        "native_item_id", "native_peer_sha256", "prepared_input_index"}


@pytest.mark.parametrize("echo_role", ["user", "assistant"])
def test_peer_history_bindings_reset_and_never_qualify_an_ordinary_message_echo(echo_role):
    peer = {"type": "agent_message", "id": "native-peer-1",
            "author": "/root", "recipient": "/root/worker",
            "content": [{"type": "input_text", "text": "Message Type: NEW_TASK\nOwn task."}]}
    registry = Registry()
    projected, = registry.history([peer])
    assert registry.peer_history_bindings == [{
        "native_item_id": "native-peer-1", "native_peer_sha256": peer_history_digest(peer),
        "prepared_input_index": 0,
    }]
    echo = json.loads(json.dumps(projected))
    if echo_role == "assistant":
        echo["role"] = "assistant"
        echo["phase"] = "commentary"
        for part in echo["content"]:
            part["type"] = "output_text"
    history = [echo, {"type": "message", "role": "user", "content": "Continue."}]
    assert registry.history(history) == history
    assert registry.peer_history_bindings == []


@pytest.mark.parametrize("native_ids", [(None, None), ("duplicate", "duplicate"),
                                         ("native-first", "native-second")])
def test_peer_history_bindings_keep_missing_or_duplicate_ids_without_guessing(native_ids):
    peer = {"type": "agent_message", "author": "/root", "recipient": "/root/worker",
            "content": [{"type": "input_text", "text": "Message Type: MESSAGE\nSame data."}]}
    peers = [{**peer, **({"id": native_id} if native_id is not None else {})}
             for native_id in native_ids]
    payload, registry = prepare(request([], input=peers))
    assert len(payload["input"]) == 2 and payload["input"][0] == payload["input"][1]
    assert registry.peer_history_bindings == [
        {"native_item_id": native_id, "native_peer_sha256": peer_history_digest(peer),
         "prepared_input_index": index}
        for index, native_id in enumerate(native_ids)
    ]


def test_completed_peer_delivery_does_not_consume_a_later_partial_assistant():
    peer = {"type": "agent_message", "author": "/root/worker", "recipient": "/root",
            "content": [{"type": "input_text", "text": "Message Type: FINAL_ANSWER\nPayload:\nReady."}]}
    partial = {"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": "An unfinished model answer."}]}
    outgoing, registry = prepare(request([], input=[peer, partial]))
    projected, delivery = outgoing["input"]
    assert json.loads(projected["content"][0]["text"].split("\n", 1)[1]) == [
        {"input_index": 1, "item": partial}]
    assert delivery["role"] == "user" and "phase" not in delivery
    assert json.loads(delivery["content"][0]["text"]) == {
        "type": "agent_message", "author": peer["author"], "recipient": peer["recipient"]}
    assert delivery["content"][1:] == peer["content"]
    assert registry.continuation_context


def test_native_message_roles_order_and_image_content_remain_unchanged():
    # Native ResponseItem::Message retains role and content independently.
    messages = [
        {"type": "message", "role": "system", "content": "Synthetic base policy."},
        {"type": "message", "role": "developer", "content": [
            {"type": "input_text", "text": "Synthetic developer policy."}]},
        {"type": "message", "role": "developer", "content": [
            {"type": "input_text", "text": "The same native authority with an image."},
            {"type": "input_image", "image_url": "https://example.invalid/developer.png", "detail": "original"}]},
        {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "User input λ."},
            {"type": "input_image", "image_url": "https://example.invalid/user.png", "detail": "high"}]},
        {"type": "message", "role": "assistant", "phase": "commentary", "content": [
            {"type": "output_text", "text": "Completed assistant context."}]},
        {"type": "message", "role": "user", "content": "Continue the same task."},
    ]
    original = json.loads(json.dumps(messages, ensure_ascii=False))
    body, registry = prepare(request([], input=messages))
    assert body["input"] == original and messages == original
    assert not registry.continuation_context


@pytest.mark.parametrize("content_form", ["missing", "null", "empty", "present"])
def test_native_reasoning_summary_content_and_signature_remain_distinct(content_form):
    # Native ResponseItem::Reasoning has independent summary and content fields.
    reasoning = {"type": "reasoning", "id": "rs_native_distinct",
                 "summary": [{"type": "summary_text", "text": "A concise native summary."}],
                 "signature": "synthetic-signature", "encrypted_content": None}
    if content_form != "missing":
        reasoning["content"] = {"null": None, "empty": [], "present": [
            {"type": "reasoning_text", "text": "Distinct full reasoning."}]}[content_form]
    call = {"type": "function_call", "name": "probe", "call_id": "call_native", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": "call_native", "output": "synthetic result"}
    user = {"type": "message", "role": "user", "content": "Continue."}
    original = json.loads(json.dumps(reasoning))
    outgoing, _ = prepare(request([], input=[reasoning, call, output, user]))
    assert outgoing["input"][0] == original and reasoning == original


def test_orphan_summary_projection_retains_original_reasoning_fields():
    reasoning = {"type": "reasoning", "id": "rs_summary_only",
                 "summary": [{"type": "summary_text", "text": "A native summary only."}],
                 "signature": "synthetic-signature"}
    user = {"type": "message", "role": "user", "content": "Continue."}
    outgoing, registry = prepare(request([], input=[reasoning, user]))
    retained = json.loads(outgoing["input"][0]["content"][0]["text"].split("\n", 1)[1])
    assert retained == [{"input_index": 0, "reasoning_index": 0, "item": reasoning}]
    assert "content" not in retained[0]["item"] and registry.continuation_context


@pytest.mark.parametrize("role", ["system", "developer", "user", "assistant"])
def test_native_additional_tools_preserve_role_and_complete_declaration_data(role):
    declarations = {"type": "additional_tools", "id": "at_native", "role": role, "tools": [
        {"type": "namespace", "name": "original", "description": "Native tool context.", "tools": [
            {"type": "function", "name": "read", "parameters": {"type": "object", "properties": {}}}]}]}
    original = json.loads(json.dumps(declarations))
    body, registry = prepare(request([], input=[declarations]))
    message, = body["input"]
    assert message["role"] == role
    part, = message["content"]
    assert part["type"] == ("output_text" if role == "assistant" else "input_text")
    assert json.loads(part["text"]) == original and declarations == original
    assert message.get("phase") == ("commentary" if role == "assistant" else None)
    assert not registry.peer_context and not registry.continuation_context


def test_additional_tools_preserve_an_existing_phase():
    declaration = {"type": "additional_tools", "role": "developer", "phase": "commentary", "tools": []}
    outgoing, _ = prepare(request([], input=[declaration]))
    item, = outgoing["input"]
    assert item["phase"] == "commentary" and item["role"] == "developer"
    assert json.loads(item["content"][0]["text"]) == declaration


@pytest.mark.parametrize("role", [None, "tool", [], {}])
def test_additional_tools_reject_unknown_roles_without_inventing_authority(role):
    declaration = {"type": "additional_tools", "role": role, "tools": []}
    with pytest.raises(ProtocolError, match="valid native message role"):
        prepare(request([], input=[declaration]))
