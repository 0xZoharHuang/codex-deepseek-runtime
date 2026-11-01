"""Selected ordinary provider retains transport, final-schema and billed-identity contracts."""
import asyncio
import copy
import json
from types import SimpleNamespace
from pathlib import Path
import pytest
import aiohttp
from aiohttp import web
from codex_deepseek import gateway as module, cli, provider_config
from codex_deepseek.gateway import Gateway,Store
from codex_deepseek.protocol import MODEL,prepare


@pytest.mark.parametrize('status',[429,502])
async def test_explicit_morph_error_never_retries_or_changes_provider(tmp_path,monkeypatch,status):
    calls=[]
    async def handle(request):
        calls.append(await request.json());return web.json_response({'error':{'message':'kept failure'}},status=status)
    app=web.Application();app.router.add_post('/responses',handle);runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start();monkeypatch.setattr(module,'UPSTREAM',f'http://127.0.0.1:{runner.addresses[0][1]}/responses');store=Store(tmp_path/'adapter.sqlite')
    try:
        async with Gateway('FAKE','LOCAL',store,{},ordinary_default_provider='morph/fp8') as g,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{g.port}/v1/responses',json={'model':MODEL,'provider':{'only':['deepseek'],'allow_fallbacks':True}},headers={'Authorization':'Bearer LOCAL'}) as r:assert r.status==status;await r.read()
            async with client.get(f'http://127.0.0.1:{g.port}/health',headers={'Authorization':'Bearer LOCAL'}) as r:
                h=await r.json();assert h['provider']=='morph/fp8' and h['provider_config_sha256']==provider_config.digest({'schema':1,'ordinary_default_provider':'morph/fp8'})
        assert len(calls)==1 and calls[0]['provider']=={'only':['morph/fp8'],'allow_fallbacks':False,'require_parameters':True}
        assert store.db.execute('select provider,status,upstream_status,route_reason from receipts').fetchone()==('morph/fp8','upstream_error',status,'operator_default/morph/fp8')
    finally:store.close();await runner.cleanup()


@pytest.mark.parametrize('stream',[False,True])
@pytest.mark.parametrize('valid',[False,True])
async def test_morph_auto_schema_uses_existing_full_final_validator(tmp_path,monkeypatch,stream,valid):
    calls=[];final={'nonce':'real'} if valid else {'nonce':42};response={'id':'r1','object':'response','status':'completed','model':MODEL,'provider':'Morph','output':[{'type':'message','id':'m1','role':'assistant','content':[{'type':'output_text','text':json.dumps(final)}]}]}
    async def handle(request):
        calls.append(await request.json())
        if not stream:return web.json_response(response)
        created={**response,'status':'in_progress','output':[]}
        body='event: response.created\ndata: '+json.dumps({'type':'response.created','response':created})+'\n\n'+'event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':response})+'\n\n'
        return web.Response(text=body,content_type='text/event-stream')
    app=web.Application();app.router.add_post('/responses',handle);runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start();monkeypatch.setattr(module,'UPSTREAM',f'http://127.0.0.1:{runner.addresses[0][1]}/responses');store=Store(tmp_path/'adapter.sqlite')
    body={'model':MODEL,'stream':stream,'tools':[{'type':'function','name':'read','parameters':{'type':'object','properties':{}}}],'tool_choice':'auto','text':{'format':{'type':'json_schema','name':'final','strict':True,'schema':{'type':'object','properties':{'nonce':{'type':'string'}},'required':['nonce'],'additionalProperties':False}}}}
    try:
        async with Gateway('FAKE','LOCAL',store,{},ordinary_default_provider='morph/fp8') as g,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{g.port}/v1/responses',json=body,headers={'Authorization':'Bearer LOCAL'}) as r:
                text=await r.text()
                if not stream:assert r.status==(200 if valid else 502)
                else:assert ('response.completed' in text)==valid;assert ('response.failed' in text)!=valid
        assert len(calls)==1 and calls[0]['provider']['only']==['morph/fp8'] and calls[0]['tool_choice']=='auto' and calls[0]['text']['format']=={'type':'text'}
        row=store.db.execute('select provider,status,structured_output_mapping from receipts').fetchone();assert row[0]=='morph/fp8' and row[1]==('completed' if valid else 'adapter_error');mapping=json.loads(row[2]);assert mapping['route_format_strategy']=='nonofficial_route_text' and mapping['upstream_decoder_constraint'] is False
    finally:store.close();await runner.cleanup()


@pytest.mark.parametrize('ordinary,actual',[('deepseek','DeepSeek'),('morph/fp8','Morph')])
@pytest.mark.parametrize('bad',[None,'id','model','provider','selected'])
def test_doctor_billed_generation_identity_stays_strict(ordinary,actual,bad):
    generation={'id':'response1','model':MODEL,'provider_name':actual};selected=ordinary
    if bad=='id':generation['id']='other-response'
    elif bad=='model':generation['model']='different-model'
    elif bad=='provider':generation['provider_name']='PrimeIntellect'
    elif bad=='selected':selected='primeintellect'
    assert cli._verified_generation(generation,'response1',selected,ordinary)==(bad is None)


@pytest.mark.parametrize('ordinary,actual',[('deepseek','DeepSeek'),('morph/fp8','Morph')])
async def test_doctor_child_uses_same_home_and_reports_actual_admitted_policy(tmp_path,monkeypatch,ordinary,actual,capsys):
    home=tmp_path/'home';home.mkdir();captured={}
    class Process:
        returncode=0
        async def communicate(self):return (b'{"type":"thread.started","thread_id":"owned-thread"}\n{"type":"item.completed","item":{"type":"agent_message","text":"CONNECTED"}}\n',b'')
    async def launch(*args,**kwargs):captured.update(args=args,env=kwargs['env']);return Process()
    class Transport:
        def __init__(self,*a,**k):pass
        async def close(self):pass
    async def generation(*a,**k):return ({'id':'response1','model':MODEL,'provider_name':actual},{'source':'fake-billed-record-only'})
    class DB:
        def execute(self,*a):return SimpleNamespace(fetchone=lambda:('response1','default-fallback',ordinary,'completed'))
    doc={'schema':1,'ordinary_default_provider':ordinary};gateway=SimpleNamespace(metadata={'ordinary_default_provider':ordinary,'provider_config_sha256':provider_config.digest(doc)})
    monkeypatch.setattr(cli.asyncio,'create_subprocess_exec',launch);monkeypatch.setattr(cli,'NativeTransport',Transport);monkeypatch.setattr(cli,'generation_receipt',generation);monkeypatch.setattr(cli,'data_root',lambda:tmp_path)
    assert await cli.doctor('FAKE',gateway,SimpleNamespace(db=DB()),home,native_binary=tmp_path/'codex.exe')==0
    result=json.loads(capsys.readouterr().out);assert result['provider']==actual and result['ordinary_default_provider']==ordinary and result['provider_config_sha256']==provider_config.digest(doc)
    assert captured['env']['CODEX_DEEPSEEK_HOME']==str(home)


@pytest.mark.parametrize('observed',[{'model':MODEL,'provider':'DeepSeek'},{'model':'different','provider':'Morph'},{'model':MODEL,'provider':'PrimeIntellect'}])
def test_Morph_observed_identity_cannot_be_substituted(observed):
    with pytest.raises(ValueError):module.validate_observed_identity(observed,'morph/fp8')


async def test_cancelled_explicit_route_closes_upstream_without_switch(tmp_path,monkeypatch):
    started=asyncio.Event();closed=asyncio.Event();payloads=[]
    async def handle(request):
        payloads.append(await request.json());r=web.StreamResponse(headers={'Content-Type':'text/event-stream'});await r.prepare(request);await r.write(b'data: {"type":"response.created","response":{"id":"r1"}}\n\n');started.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    app=web.Application();app.router.add_post('/responses',handle);runner=web.AppRunner(app,handler_cancellation=True);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start();monkeypatch.setattr(module,'UPSTREAM',f'http://127.0.0.1:{runner.addresses[0][1]}/responses');store=Store(tmp_path/'adapter.sqlite')
    try:
        async with Gateway('FAKE','LOCAL',store,{},ordinary_default_provider='morph/fp8') as g,aiohttp.ClientSession() as client:
            r=await client.post(f'http://127.0.0.1:{g.port}/v1/responses',json={'model':MODEL,'stream':True},headers={'Authorization':'Bearer LOCAL'});await asyncio.wait_for(started.wait(),3);await r.content.read(10);r.close();await asyncio.wait_for(closed.wait(),3)
        assert len(payloads)==1 and payloads[0]['provider']['only']==['morph/fp8']
    finally:store.close();await runner.cleanup()
