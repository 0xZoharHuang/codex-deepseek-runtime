"""Synthetic end-to-end opaque replay checks, all traffic stays on loopback."""
import copy
import json
from contextlib import asynccontextmanager

import aiohttp
from aiohttp import web
import pytest

import codex_deepseek.gateway as module
from codex_deepseek.gateway import Gateway
from codex_deepseek.protocol import MODEL
from codex_deepseek.state import Store


@pytest.fixture
async def upstream(monkeypatch):
    requests=[]
    state={'mode':'produce'}
    items=[{'id':'rs_synthetic_'+str(i),'type':'reasoning','summary':[],
            'encrypted_content':'SYNTHETIC_OPAQUE_'+str(i),
            'status':'completed','format':'unknown','synthetic_extension':{'index':i}}
           for i in range(4)]
    items[0]['content']=None
    items[1]['content']=[]
    items[2]['content']=[{'type':'text','text':'SYNTHETIC_TEXT_ONLY'}]
    items[3]['content']=[{'type':'reasoning_text','text':'SYNTHETIC_REASONING_TEXT'}]
    async def endpoint(request):
        payload=await request.json();requests.append(payload)
        if state['mode']=='produce':
            ev=[{'type':'response.created','response':{'id':'resp_first','status':'in_progress','output':[]}}]
            if state.get('tool_first'):
                ev.append({'type':'response.output_item.done','output_index':0,'item':{
                    'id':'fc_prior','type':'function_call','call_id':'call_prior','name':'synthetic_tool','arguments':'{}'}})
            if not state.get('terminal_only'):
                for index,item in enumerate(items):
                    ev.append({'type':'response.output_item.done','output_index':index,'item':item})
            terminal={'id':'resp_first','status':'failed' if state.get('fail') else 'completed','output':items}
            if state.get('fail'):terminal['error']={'code':'fixture_failure','message':'synthetic failure'}
            if not payload.get('stream'):
                return web.json_response(terminal)
            ev.append({'type':'response.failed' if state.get('fail') else 'response.completed','response':terminal})
            if state.get('late'):
                ev.append({'type':'response.output_text.delta','item_id':'late','delta':'contradiction'})
            text=''.join('data: '+json.dumps(x)+'\n\n' for x in ev)
            if state.get('truncated'):
                body=text.encode();response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Content-Length':str(len(body)+10)})
                await response.prepare(request);await response.write(body);request.transport.close()
                return response
            return web.Response(text=text,content_type='text/event-stream')
        terminal={'id':'resp_second','status':'completed','output':[]}
        return web.Response(text='data: '+json.dumps({'type':'response.completed','response':terminal})+'\n\n',content_type='text/event-stream')
    app=web.Application();app.router.add_post('/responses',endpoint)
    runner=web.AppRunner(app,handler_cancellation=True);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
    monkeypatch.setattr(module,'UPSTREAM','http://127.0.0.1:'+str(runner.addresses[0][1])+'/responses')
    yield state,items,requests
    await runner.cleanup()


def original():
    return {'model':MODEL,'stream':True,'input':[{'role':'user','content':'synthetic fixture'}]}


def continuation(items):
    # Native serde can omit provider-only output fields while retaining opaque bytes.
    history=[{'type':'reasoning','id':x['id'],'summary':[],
              'encrypted_content':x['encrypted_content']} for x in items]
    history[0]['content']=None
    history[3]['content']=copy.deepcopy(items[3]['content'])
    return {'model':MODEL,'stream':True,'input':[{'role':'user','content':'first'},*history,{'role':'user','content':'continue'}]}


@asynccontextmanager
async def running(path,key='SYNTHETIC_KEY',**kw):
    store=Store(path)
    try:
        async with Gateway(key,'LOCAL',store,{},**kw) as gateway,aiohttp.ClientSession() as client:
            async def post(body):
                async with client.post('http://127.0.0.1:'+str(gateway.port)+'/v1/responses',json=body,
                                       headers={'Authorization':'Bearer LOCAL'}) as r:
                    return r.status,await r.text()
            yield post,store
    finally:store.close()


async def test_four_opaque_items_survive_restart_with_exact_upstream_wire(upstream,tmp_path):
    state,items,requests=upstream;path=tmp_path/'adapter.sqlite'
    async with running(path) as (post,store):
        status,text=await post(original())
        assert status==200 and 'response.completed' in text and 'response.failed' not in text
        for item in items:assert item['encrypted_content'] in text
        assert store.db.execute("select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'").fetchone()[0]==4
    state['mode']='replay'
    async with running(path) as (post,store):
        status,text=await post(continuation(items))
        assert status==200 and 'response.completed' in text and 'response.failed' not in text
        assert len(requests)==2
        replay=[x for x in requests[-1]['input'] if x.get('type')=='reasoning']
        assert replay==items
        assert requests[-1]['provider']['only']==['deepseek']
        assert store.db.execute('select status from receipts order by started').fetchall()==[('completed',),('completed',)]


async def test_verified_opaque_history_with_hosted_search_keeps_official_route(upstream,tmp_path):
    state,items,requests=upstream;path=tmp_path/'adapter.sqlite'
    async with running(path) as (post,_):
        status,text=await post(original());assert status==200 and 'response.completed' in text
    state['mode']='replay';body=continuation(items);body['tools']=[{'type':'web_search'}]
    async with running(path) as (post,_):
        status,text=await post(body)
        assert status==200 and 'response.completed' in text
    assert requests[-1]['provider']['only']==['deepseek']
    assert [x for x in requests[-1]['input'] if x.get('type')=='reasoning']==items


async def test_nonstream_completion_can_seed_streamed_opaque_continuation(upstream,tmp_path):
    state, items, requests = upstream
    path = tmp_path / 'adapter.sqlite'
    async with running(path) as (post, store):
        status, text = await post({**original(), 'stream': False})
        assert status == 200 and json.loads(text)['output'] == items
        assert store.db.execute(
            "select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'"
        ).fetchone()[0] == len(items)
    state['mode'] = 'replay'
    async with running(path) as (post, _):
        status, text = await post(continuation(items))
        assert status == 200 and 'response.completed' in text and 'response.failed' not in text
    assert [item for item in requests[-1]['input'] if item.get('type') == 'reasoning'] == items


async def test_terminal_only_stream_is_not_authoritative(upstream,tmp_path):
    state,items,_=upstream;state['terminal_only']=True
    async with running(tmp_path/'adapter.sqlite') as (post,store):
        status,text=await post(original())
        assert status==200 and 'response.failed' in text and 'response.completed' not in text
        assert all(x['encrypted_content'] not in text for x in items)
        assert store.db.execute("select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'").fetchone()[0]==0


@pytest.mark.parametrize('fault',['late','truncated'])
async def test_opaque_completion_waits_for_clean_transport_eof(upstream,tmp_path,fault):
    state,items,_=upstream;state[fault]=True
    async with running(tmp_path/'adapter.sqlite') as (post,store):
        status,text=await post(original())
        assert status==200 and 'response.failed' in text and 'response.completed' not in text
        assert all(x['encrypted_content'] not in text for x in items)
        assert store.db.execute("select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'").fetchone()[0]==0
        assert store.db.execute('select status from receipts').fetchone()[0]=='adapter_error'


@pytest.mark.parametrize('error_kind',['protocol','sqlite'])
async def test_evidence_failure_rolls_back_receipt_and_evidence(upstream,tmp_path,monkeypatch,error_kind):
    import codex_deepseek.opaque_replay as replay
    from codex_deepseek.protocol import ProtocolError
    import sqlite3
    def fail_after_partial_insert(db,credential,receipt_id,output):
        db.execute("insert into adapter_metadata values ('opaque-reasoning-v1:partial','synthetic')")
        raise (ProtocolError('synthetic evidence failure') if error_kind=='protocol' else sqlite3.OperationalError('synthetic database failure'))
    monkeypatch.setattr(replay,'remember_completed',fail_after_partial_insert)
    _,items,_=upstream
    async with running(tmp_path/'adapter.sqlite') as (post,store):
        status,text=await post(original())
        assert status==200 and 'response.failed' in text and 'response.completed' not in text
        assert all(x['encrypted_content'] not in text for x in items)
        assert store.db.execute("select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'").fetchone()[0]==0
        assert store.db.execute('select status from receipts').fetchone()[0]=='adapter_error'


@pytest.mark.parametrize('variant',['missing','cipher','id','summary','content','unknown_top','unknown_nested','known_extra_type','credential','route','model'])
async def test_unproven_or_wrong_scope_stops_before_upstream(upstream,tmp_path,variant):
    state,items,requests=upstream;path=tmp_path/'adapter.sqlite'
    if variant!='missing':
        async with running(path) as (post,_):
            status,text=await post(original());assert status==200 and 'response.completed' in text
    state['mode']='replay';body=continuation(items)
    if variant=='cipher':body['input'][1]['encrypted_content']+='X'
    if variant=='id':body['input'][1]['id']+='X'
    if variant=='summary':body['input'][1]['summary']=[{'type':'summary_text','text':'changed'}]
    if variant=='content':body['input'][4]['content'][0]['text']='changed'
    if variant=='unknown_top':body['input'][1]['unknown_field']='not-discardable'
    if variant=='unknown_nested':body['input'][4]['content'][0]['unknown_field']='not-discardable'
    if variant=='known_extra_type':body['input'][1]['synthetic_extension']={'index':False}
    if variant=='model':body['model']='another/model'
    options={'ordinary_default_provider':'morph/fp8'} if variant=='route' else {}
    key='OTHER_SYNTHETIC_KEY' if variant=='credential' else 'SYNTHETIC_KEY'
    before=len(requests)
    async with running(path,key,**options) as (post,_):
        status,text=await post(body)
        assert status in (400,502) and 'response.completed' not in text and 'SYNTHETIC_OPAQUE_' not in text
    assert len(requests)==before


async def test_failed_stream_does_not_publish_opaque_or_make_replayable(upstream,tmp_path):
    state,items,requests=upstream;state['fail']=True;path=tmp_path/'adapter.sqlite'
    async with running(path) as (post,store):
        status,text=await post(original())
        assert status==200 and 'response.failed' in text and 'response.completed' not in text
        assert 'fixture_failure' in text and 'synthetic failure' in text
        assert all(x['encrypted_content'] not in text for x in items)
        assert store.db.execute("select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'").fetchone()[0]==0
        assert store.db.execute('select status from receipts').fetchone()[0]!='completed'
    state.update(mode='replay',fail=False);before=len(requests)
    async with running(path) as (post,_):
        status,text=await post(continuation(items));assert status in (400,502) and 'response.completed' not in text
    assert len(requests)==before


async def test_prior_tool_event_is_not_rolled_back_by_later_opaque_failure(upstream,tmp_path):
    state,items,_=upstream;state.update(fail=True,tool_first=True)
    body=original();body['tools']=[{'type':'function','name':'synthetic_tool','parameters':{'type':'object'}}]
    async with running(tmp_path/'adapter.sqlite') as (post,store):
        status,text=await post(body)
        assert status==200 and 'response.output_item.done' in text and 'call_prior' in text
        assert 'response.failed' in text and 'response.completed' not in text
        assert all(x['encrypted_content'] not in text for x in items)
        assert store.db.execute('select status from receipts').fetchone()[0]=='failed'
