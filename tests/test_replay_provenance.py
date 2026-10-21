"""Incomplete native reasoning/tool history stays intact on a compatible route."""
import copy
from types import SimpleNamespace
from codex_deepseek.protocol import MODEL,Registry,native_history_fingerprint,prepare
from codex_deepseek.state import Store


def history():
    return [{'type':'reasoning','id':'r','content':[{'type':'reasoning_text','text':'Actual model reasoning, preserved verbatim.'}],'summary':[]},
            {'type':'custom_tool_call','id':'c','call_id':'native-call-1','name':'exec','input':'const x = 1;'},
            {'type':'custom_tool_call_output','call_id':'native-call-1','output':'ACTUAL_NATIVE_RESULT'}]


def request():
    return {'model':MODEL,'input':history(),'tools':[{'type':'custom','name':'exec'},{'type':'web_search'}],'reasoning':{'effort':'high'}}


def complete(store,items,status='completed'):
    registry=Registry(store);registry.tools([{'type':'custom','name':'exec'}])
    raw=[]
    for item in items:
        if item['type']=='reasoning':raw.append(copy.deepcopy(item))
        elif item['type']=='custom_tool_call':raw.append({'type':'function_call','id':item['id'],'call_id':item['call_id'],'name':'exec','arguments':__import__('json').dumps({'input':item['input']})})
    events=SimpleNamespace(response_id='response',usage={},terminal=status=='completed',status=status,registry=registry,done={str(i):x for i,x in enumerate(raw)})
    key=store.start(context_key='thread');store.finish(key,status,200,events)


def test_unattested_history_keeps_contents_and_ids_on_compatible_same_model_route(tmp_path):
    store=Store(tmp_path/'state.sqlite');body=request();original=copy.deepcopy(body)
    try:
        payload,_=prepare(body,store)
        assert payload['provider']=={'only':['morph/fp8'],'allow_fallbacks':False,'require_parameters':True}
        assert payload['model']==MODEL and payload['reasoning']=={'effort':'high'}
        assert body==original and payload['input'][0]==original['input'][0]
        assert payload['input'][1]['call_id']=='native-call-1' and payload['input'][1]['id']=='c'
        assert __import__('json').loads(payload['input'][1]['arguments'])=={'input':'const x = 1;'}
        assert payload['input'][2]['output']=='ACTUAL_NATIVE_RESULT'
    finally:store.close()


def test_completed_history_prefers_official_and_one_partial_item_changes_route(tmp_path):
    store=Store(tmp_path/'state.sqlite')
    try:
        body=request();complete(store,body['input']);payload,_=prepare(body,store)
        assert payload['provider']['only']==['deepseek']
        body['input'][0]['content'][0]['text']+=' Retained partial suffix.'
        payload,_=prepare(body,store);assert payload['provider']['only']==['morph/fp8']
    finally:store.close()


def test_failed_or_cancelled_stream_cannot_attest_dispatched_prefix_items(tmp_path):
    store=Store(tmp_path/'state.sqlite')
    try:
        for status in ['adapter_error','cancelled','incomplete']:
            complete(store,history(),status)
            assert not store.has_completed_native_item(native_history_fingerprint(history()[0]))
            assert prepare(request(),store)[0]['provider']['only']==['morph/fp8']
    finally:store.close()


def test_provenance_survives_process_reopen_and_compaction_restores_official_route(tmp_path):
    path=tmp_path/'state.sqlite';store=Store(path);complete(store,history());store.close()
    store=Store(path)
    try:
        assert prepare(request(),store)[0]['provider']['only']==['deepseek']
        body=request();body['input']=[{'type':'message','role':'user','content':'Native compaction summary; no retained model items.'}]
        assert prepare(body,store)[0]['provider']['only']==['deepseek']
    finally:store.close()


def test_nonstream_terminal_validation_attests_the_same_native_semantics(tmp_path):
    from codex_deepseek.protocol import Events
    store=Store(tmp_path/'state.sqlite')
    try:
        registry=Registry(store);registry.tools([{'type':'custom','name':'exec'}]);events=Events(registry)
        response={'id':'response-complete','status':'completed','output':[
            history()[0],{'type':'function_call','id':'c','call_id':'native-call-1','name':'exec','arguments':'{"input":"const x = 1;"}'}]}
        events.transform({'type':'response.completed','response':response})
        receipt=store.start();store.finish(receipt,'completed',200,events)
        assert prepare(request(),store)[0]['provider']['only']==['deepseek']
    finally:store.close()
