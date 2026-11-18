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
            if not state.get('terminal_only'):
                for index,item in enumerate(items):
                    ev.append({'type':'response.output_item.done','output_index':index,'item':item})
            terminal={'id':'resp_first','status':'failed' if state.get('fail') else 'completed','output':items}
            if state.get('fail'):terminal['error']={'code':'fixture_failure','message':'synthetic failure'}
            ev.append({'type':'response.failed' if state.get('fail') else 'response.completed','response':terminal})
            return web.Response(text=''.join('data: '+json.dumps(x)+'\n\n' for x in ev),content_type='text/event-stream')
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


@pytest.mark.parametrize('terminal_only',[False,True])
async def test_four_opaque_items_survive_restart_with_exact_upstream_wire(upstream,tmp_path,terminal_only):
    state,items,requests=upstream;path=tmp_path/'adapter.sqlite'
    state['terminal_only']=terminal_only
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


@pytest.mark.parametrize('variant',['missing','cipher','id','summary','content','unknown_top','unknown_nested','credential','route','model'])
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
