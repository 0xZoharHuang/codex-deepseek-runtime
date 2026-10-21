"""Regression cases derived from observed native/provider incompatibilities."""

import json

import pytest

from codex_deepseek.gateway import Store
from codex_deepseek.protocol import Events, MODEL, InputDecoder, ProtocolError, prepare


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
    frozen=copy.deepcopy(body);structured,_=prepare(body)
    assert body==frozen
    assert structured['tools']==plain['tools'] and structured['input']==plain['input']
    assert structured['stream'] is True and structured['text']==body['text']
    assert structured['instructions']==original['instructions']+'\n\n'+STRUCTURED_FORMAT_INSTRUCTION
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
    assert payload['input'][0]['role']=='system' and '/root/child' in payload['input'][0]['content'][0]['text']
    assert payload['input'][1]['type']=='reasoning' and 'not model-generated reasoning' in payload['input'][1]['content'][0]['text']
    assert payload['input'][2]['type']=='function_call'
    assert payload['input'][3]['type']=='function_call_output'
    assert payload['input'][2]['call_id']==payload['input'][3]['call_id']
    assert 'Compute 19*23' in payload['input'][3]['output'][0]['text']
    assert not any(item.get('role')=='user' for item in payload['input'])
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


def test_deepseek_does_not_demote_native_developer_policy_to_user():
    items=[{'type':'message','role':'developer','content':[{'type':'input_text','text':'Native runtime tool policy'}]}, {'role':'user','content':'Actual user task'}]
    outgoing,_=prepare(request([],input=items))
    assert outgoing['input'][0]['role']=='system'
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
