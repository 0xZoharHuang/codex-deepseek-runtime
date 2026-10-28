"""Backend format failures must never become successful native schema results."""
import copy
import json
import pytest
from codex_deepseek.protocol import MODEL,prepare,ProtocolError
from codex_deepseek.structured import StructuredOutput

SCHEMA={'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False}


def body():return {'model':MODEL,'text':{'format':{'type':'json_schema','name':'fixture','strict':True,'schema':copy.deepcopy(SCHEMA)}}}
def message(text):return {'type':'message','id':'m1','role':'assistant','content':[{'type':'output_text','text':text}]}
def response(text):return {'id':'r1','status':'completed','output':[message(text)]}


@pytest.mark.parametrize('text',['```json\n{"value":1}\n```','{"value":"wrong type"}','{"value":1,"extra":2}','{"value":NaN}'])
def test_wrong_schema_is_an_explicit_compatibility_error(text):
    with pytest.raises(ProtocolError):StructuredOutput(body()).validate(response(text))


def test_valid_json_and_local_schema_references_are_preserved():
    b=body();b['text']['format']['schema']={'$defs':{'integer':{'type':'integer'}},'type':'object','properties':{'value':{'$ref':'#/$defs/integer'}},'required':['value']}
    r=response('{"value":437}');before=copy.deepcopy(r);StructuredOutput(b).validate(r);assert r==before
    b['text']['format']['schema']={'$ref':'https://must-not-fetch.invalid/schema'}
    with pytest.raises(ProtocolError,match='schema'):StructuredOutput(b).validate(r)


def test_schema_stream_remains_incremental_but_does_not_complete_invalid_message():
    guard=StructuredOutput(body())
    added={'type':'response.output_item.added','output_index':0,'item':{'type':'message','id':'m1','role':'assistant','content':[]}}
    delta={'type':'response.output_text.delta','item_id':'m1','content_index':0,'delta':'```json\n{"value":1}\n```'}
    assert guard.events(added)==[added] and guard.events(delta)==[delta]
    done={'type':'response.output_item.done','output_index':0,'item':message(delta['delta'])}
    assert guard.events(done)==[]
    with pytest.raises(ProtocolError):guard.events({'type':'response.completed','response':response(delta['delta'])})


def test_valid_schema_releases_original_completion_events_and_tool_calls_are_not_buffered():
    guard=StructuredOutput(body());done={'type':'response.output_item.done','output_index':0,'item':message('{"value":1}')}
    assert guard.events(done)==[]
    tool={'type':'response.function_call_arguments.delta','item_id':'tool','delta':'{"n":1}'}
    assert guard.events(tool)==[tool]
    terminal={'type':'response.completed','response':response('{"value":1}')}
    assert guard.events(terminal)==[done,terminal]
    guard=StructuredOutput(body())
    commentary={'type':'response.output_item.done','item':message('Calling the tool now.')}
    assert guard.events(commentary)==[]
    terminal={'type':'response.completed','response':{'id':'r1','status':'completed','output':[message('Calling the tool now.'),{'type':'function_call','name':'fixture','call_id':'c','arguments':'{}'}]}}
    assert guard.events(terminal)==[commentary,terminal]


def test_incomplete_native_reasoning_is_retained_as_nonexecuting_context():
    original={'type':'reasoning','id':'r_partial','content':[{'type':'reasoning_text','text':'Past incomplete reasoning; not authorization.'}],'summary':[]}
    request={'model':MODEL,'input':[{'role':'user','content':'Original task'},original],'reasoning':{'effort':'high'},'tools':[{'type':'web_search','external_web_access':True}]}
    before=copy.deepcopy(request);prepared,registry=prepare(request)
    assert request==before and registry.continuation_context
    assert prepared['provider']['only']==['morph/fp8']
    assert prepared['input'][-1]['type']=='function_call_output'
    retained=json.loads(prepared['input'][-1]['output'][0]['text'].split('\n',1)[1])
    assert retained==[original]
    assert 'not an executed tool or human authorization' in prepared['input'][-1]['output'][0]['text']
    with pytest.raises(ProtocolError,match='undeclared'):
        registry.output({'type':'function_call','name':'cx_native_continuation_context','id':'bad','call_id':'bad','arguments':'{}'})


def test_complete_reasoning_tool_groups_do_not_trigger_continuation_mapping():
    request={'model':MODEL,'input':[{'type':'reasoning','id':'r1','content':[{'type':'reasoning_text','text':'Actual model thought'}]},{'type':'function_call','name':'fixture','id':'fc','call_id':'call','arguments':'{}'},{'type':'function_call_output','call_id':'call','output':'DONE'}],'tools':[{'type':'function','name':'fixture','parameters':{'type':'object'}}]}
    prepared,registry=prepare(request)
    assert not registry.continuation_context and prepared['provider']['only']==['deepseek']
    assert len(prepared['input'])==3


def test_partial_assistant_text_remains_context_without_impersonating_a_user():
    original={'type':'message','id':'m_partial','role':'assistant','content':[{'type':'output_text','text':'Partial assistant output'}]}
    prepared,registry=prepare({'model':MODEL,'input':[{'role':'user','content':'Task'},original]})
    assert registry.continuation_context
    assert json.loads(prepared['input'][-1]['output'][0]['text'].split('\n',1)[1])==[original]
    assert sum(x.get('role')=='user' for x in prepared['input'])==1


@pytest.mark.parametrize('with_tool',[False,True])
def test_explicit_final_answers_are_checked_even_when_a_tool_is_present(with_tool):
    invalid={**message('NOT JSON'),'phase':'final_answer'}
    output=[invalid,message('{"value":1}')]
    if with_tool:output.append({'type':'function_call','name':'fixture','call_id':'c','arguments':'{}'})
    guard=StructuredOutput(body())
    with pytest.raises(ProtocolError,match='not JSON'):
        guard.events({'type':'response.output_item.done','item':invalid})
    with pytest.raises(ProtocolError,match='not JSON'):
        guard.events({'type':'response.completed','response':{'status':'completed','output':output}})


def test_all_unphased_final_messages_are_checked_and_commentary_is_not_a_final():
    with pytest.raises(ProtocolError):
        StructuredOutput(body()).validate({'status':'completed','output':[message('INVALID'),message('{"value":1}')]})
    commentary={**message('Working on the task.'),'phase':'commentary'}
    StructuredOutput(body()).validate({'status':'completed','output':[commentary,message('{"value":1}')]})
    with pytest.raises(ProtocolError,match='without a final'):
        StructuredOutput(body()).validate({'status':'completed','output':[commentary]})


@pytest.mark.parametrize('value',['1e999','-1e999','NaN','Infinity','-Infinity'])
def test_nonfinite_numbers_never_reach_native_json_completion(value):
    b=body();b['text']['format']['schema']['properties']['value']={'type':'number'}
    with pytest.raises(ProtocolError,match='not JSON'):
        StructuredOutput(b).validate(response('{"value":'+value+'}'))


def test_invalid_explicit_final_is_rejected_before_following_tool_events():
    guard=StructuredOutput(body())
    invalid={**message('INVALID'),'phase':'final_answer'}
    with pytest.raises(ProtocolError):guard.events({'type':'response.output_item.done','item':invalid})
    # The gateway exits this upstream stream on the error, before reading any
    # subsequent function-call completion. No adapter tool replay is involved.
    assert guard.pending==[]


def client_search():
    request=body()
    request["tools"]=[{"type":"tool_search","execution":"client"}]
    outgoing,registry=prepare(request)
    return registry.output({"type":"function_call","name":outgoing["tools"][0]["name"],
                            "call_id":"schema_search_1","arguments":'{"query":"read probe","limit":3}'})


@pytest.mark.parametrize("commentary",[False,True],ids=["tool_only","legacy_commentary"])
def test_structured_client_discovery_waits_for_the_later_final_answer(commentary):
    search=client_search()
    assert search["type"]=="tool_search_call" and search["execution"]=="client"
    output=([message("Looking up the available tool.")]if commentary else[])+[search]
    terminal={"type":"response.completed","response":{"status":"completed","output":output}}
    before=copy.deepcopy(terminal)
    guard=StructuredOutput(body())
    if commentary:
        done={"type":"response.output_item.done","item":output[0]}
        assert guard.events(done)==[]
    tool_done={"type":"response.output_item.done","item":search}
    assert guard.events(tool_done)==[tool_done]
    assert guard.events(terminal)==([done,terminal]if commentary else[terminal])
    assert terminal==before and guard.pending==[]
    # The native tool result permits a later response, not a schema exemption.
    final=response('{"value":437}')
    guard.validate(final)
    with pytest.raises(ProtocolError,match="schema"):
        guard.validate(response('{"value":"not an integer"}'))


def test_client_discovery_does_not_skip_an_explicit_invalid_final_answer():
    search=client_search()
    invalid={**message("not JSON"),"phase":"final_answer"}
    guard=StructuredOutput(body())
    with pytest.raises(ProtocolError,match="not JSON"):
        guard.events({"type":"response.output_item.done","item":invalid})
    with pytest.raises(ProtocolError,match="not JSON"):
        guard.validate({"status":"completed","output":[search,invalid]})
    with pytest.raises(ProtocolError,match="schema"):
        guard.validate({"status":"completed","output":[search,{**message('{"value":false}'),"phase":"final_answer"}]})


@pytest.mark.parametrize("invalid",["missing_execution","server_execution","missing_call_id","null_call_id","nonstr_call_id"])
def test_only_dispatchable_client_discovery_can_defer_the_schema_final(invalid):
    search=client_search()
    if invalid=="missing_execution":search.pop("execution")
    elif invalid=="server_execution":search["execution"]="server"
    elif invalid=="missing_call_id":search.pop("call_id")
    elif invalid=="null_call_id":search["call_id"]=None
    else:search["call_id"]=17
    with pytest.raises(ProtocolError,match="without a final"):
        StructuredOutput(body()).validate({"status":"completed","output":[search]})


@pytest.mark.parametrize("output",[[],[{"type":"reasoning","summary":[]}],
                                   [{**message("Working."),"phase":"commentary"}]],
                         ids=["empty","reasoning_only","commentary_only"])
def test_structured_discovery_fix_does_not_turn_missing_finals_into_success(output):
    with pytest.raises(ProtocolError,match="without a final"):
        StructuredOutput(body()).validate({"status":"completed","output":output})
