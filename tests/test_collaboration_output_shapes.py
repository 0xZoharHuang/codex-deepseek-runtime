"""Explicit encrypted collaboration metadata cannot become a plaintext marker."""
import copy
import json
import pytest
from codex_deepseek.protocol import Events, MODEL, ProtocolError, prepare


def fixture(name):
    body={"model":MODEL,"input":[],"tools":[{"type":"namespace","name":"collaboration","tools":[{"type":"function","name":name}]}]}
    payload,registry=prepare(body)
    return registry,{"type":"function_call","id":"fc","call_id":"c","name":payload["tools"][0]["name"],
        "arguments":json.dumps({"message":"Plain fixture message 中文"},ensure_ascii=False)}


@pytest.mark.parametrize("name",["spawn_agent","send_message","followup_task"])
@pytest.mark.parametrize("value",[["PRIVATE-CIPHER-PUBLIC-FIXTURE"],[7],"PRIVATE-CIPHER-PUBLIC-FIXTURE",{}])
@pytest.mark.parametrize("kind",["response.output_item.done","response.completed"])
def test_explicit_encrypted_collaboration_metadata_never_commits(name,value,kind):
    registry,item=fixture(name);item["encrypted_function_args"]=value
    frozen=copy.deepcopy(item);events=Events(registry)
    event=({"type":kind,"response":{"id":"r","status":"completed","output":[item]}}
           if kind=="response.completed" else {"type":kind,"item":item})
    with pytest.raises(ProtocolError) as error:events.transform(event)
    assert "PRIVATE-CIPHER-PUBLIC-FIXTURE" not in str(error.value)
    assert not events.terminal and not events.done and item==frozen


@pytest.mark.parametrize("name",["spawn_agent","send_message","followup_task"])
@pytest.mark.parametrize("value",["missing",None,[]])
def test_plaintext_collaboration_marker_preserves_optional_contract(name,value):
    registry,item=fixture(name)
    if value!="missing":item["encrypted_function_args"]=value
    frozen=copy.deepcopy(item);events=Events(registry)
    events.transform({"type":"response.output_item.added","item":{**item,"encrypted_function_args":["preview"]}})
    result=events.transform({"type":"response.completed","response":{"id":"r","status":"completed","output":[item]}})
    native=result[0]["item"]
    assert native["encrypted_function_args"]==[] and native["arguments"]==item["arguments"]
    assert native["namespace"]=="collaboration" and native["name"]==name
    assert item==frozen and events.terminal
