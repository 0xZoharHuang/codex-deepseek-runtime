import asyncio,hashlib,json,sqlite3
from contextlib import asynccontextmanager
from email.parser import BytesParser
from email import policy as email_policy
from pathlib import Path
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest
from codex_deepseek.native_http import NativeTransport
from codex_deepseek.image_provider import NativeFilesSession,ImageProvider,load_image_config,maintenance_exit_code,maintenance_command
from codex_deepseek.image_transport import OpenRouterFilesBackend,TransportPolicy,multipart_file,ImageTransportError
from codex_deepseek.gateway import Gateway
from codex_deepseek.state import Store
from codex_deepseek.protocol import MODEL
from test_image_transport import png,payload

KEY='SYNTHETIC-KEY-ONLY'


def config(root,**updates):
    value={'schema_version':1,'enabled':True,'native_outbound_policy':'system','activate_above_bytes':1,'idle_ttl_seconds':0}
    value.update(updates);(root/'image-transport.json').write_text(json.dumps(value),'utf-8');return value


def decode_file(body,content_type):
    doc=BytesParser(policy=email_policy.default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
    item=list(doc.iter_parts());assert len(item)==1
    return item[0].get_payload(decode=True),item[0].get_filename(),item[0].get_content_type()


@pytest.mark.asyncio
async def test_raw_ipc_preserves_exact_body_without_json_or_base64(monkeypatch):
    captured=[]
    async def forward(request):
        assert request.headers['Authorization']=='Bearer '+transport.token
        metadata=json.loads(request.headers['X-CDS-Transport-Metadata']);body=await request.read();captured.append((metadata,body))
        return web.json_response({'received':len(body)})
    app=web.Application();app.router.add_post('/forward-raw',forward)
    transport=NativeTransport('https://openrouter.ai/api/v1/responses')
    async with TestServer(app) as server:
        async def start():
            if transport.client is None:transport.client=aiohttp.ClientSession(trust_env=False)
            transport.port=server.port;transport.raw_protocol_version=1
        monkeypatch.setattr(transport,'start',start)
        body=' { "unicode" : "原图😀", "number" : 1.00 } '.encode()
        async with transport.request_raw('POST',transport.upstream,api_key=KEY,body=body,content_type='application/json',policy='system') as response:assert response.status==200
        await transport.close()
    assert captured[0][1]==body
    assert captured[0][0]=={'api_key':KEY,'url':transport.upstream,'method':'POST','policy':'system','content_type':'application/json'}
    assert 'payload' not in captured[0][0]


@pytest.mark.asyncio
async def test_metadata_budget_rejects_before_start(monkeypatch):
    native=NativeTransport('https://openrouter.ai/api/v1/responses')
    async def forbidden():raise AssertionError('must fail before helper startup')
    monkeypatch.setattr(native,'start',forbidden)
    with pytest.raises(ValueError,match='metadata'):
        async with native.request_raw('GET','https://openrouter.ai/api/v1/files',api_key='x'*9000):pass


class Content:
    def __init__(self,body):self.body=body
    async def read(self,n=-1):
        take=len(self.body) if n<0 else n;value,self.body=self.body[:take],self.body[take:];return value
    async def iter_any(self):
        if self.body:yield await self.read()
class Response:
    def __init__(self,status,body,headers=None):
        self.status=status;self.body=body if isinstance(body,bytes) else json.dumps(body).encode();self.headers=headers or {};self.content=Content(self.body)
    async def read(self):return self.body
    async def json(self):return json.loads(self.body)


class FakeNative:
    def __init__(self,*a,**kw):
        self.files={};self.calls=[];self.deleted=[];self.model_status=200;self.model_body=None;self.model_started=asyncio.Event();self.cancelled=asyncio.Event();self.wait_model=False
    async def close(self):pass
    async def require_raw(self):pass
    @asynccontextmanager
    async def request_raw(self,method,url,*,api_key,body=b'',content_type=None,policy='default'):
        self.calls.append({'method':method,'url':url,'key':api_key,'body':body,'content_type':content_type,'policy':policy})
        if '/files' in url:
            if method=='POST':
                raw,name,mime=decode_file(body,content_type);fid='or_file_'+str(len(self.files)+1).zfill(12)
                item={'id':fid,'filename':name,'mime_type':mime,'size_bytes':len(raw)};self.files[fid]=item
                yield Response(200,item)
            elif method=='GET':
                fid=url.rsplit('/',1)[-1];yield Response(200,self.files[fid]) if fid in self.files else Response(404,{})
            else:
                fid=url.rsplit('/',1)[-1];self.files.pop(fid,None);self.deleted.append(fid);yield Response(200,{'id':fid,'type':'file_deleted'})
            return
        self.model_started.set()
        try:
            if self.wait_model:await asyncio.Future()
            data=self.model_body or {'id':'gen-synthetic','model':MODEL,'status':'completed','output':[]}
            yield Response(self.model_status,data,{'Retry-After':'9'} if self.model_status==429 else {})
        finally:self.cancelled.set()
    @asynccontextmanager
    async def post(self,url,*,json,headers,policy,**kw):
        self.calls.append({'legacy':True,'json':json,'key':headers['Authorization'][7:],'policy':policy})
        yield Response(200,{'id':'gen-synthetic','model':MODEL,'status':'completed','output':[]})


@pytest.mark.asyncio
async def test_native_files_upload_get_delete_preserves_bytes_and_policy():
    native=FakeNative();backend=OpenRouterFilesBackend(NativeFilesSession(native,'system'))
    image=png();meta=await backend.upload(KEY,image,'image/png','cds-original.png')
    assert decode_file(native.calls[0]['body'],native.calls[0]['content_type'])[0]==image
    assert await backend.get(KEY,meta.file_id)==meta
    await backend.delete(KEY,meta.file_id);assert await backend.get(KEY,meta.file_id) is None
    assert [c['method'] for c in native.calls]==['POST','GET','DELETE','GET']
    assert all(c['policy']=='system' and c['key']==KEY for c in native.calls)
    assert native.calls[0]['url']=='https://openrouter.ai/api/v1/files'


def test_native_files_cannot_override_proxy_redirect_or_origin():
    session=NativeFilesSession(FakeNative(),'default')
    for change in [{'proxy':'http://other:8080'},{'allow_redirects':True}]:
        with pytest.raises(ValueError):session.request('GET','https://openrouter.ai/api/v1/files',headers={'Authorization':'Bearer '+KEY},**change)
    with pytest.raises(ValueError):session.request('DELETE','https://other.invalid/api/v1/files/or_file_12345678',headers={'Authorization':'Bearer '+KEY})


def test_config_default_off_strict_explicit_policy(tmp_path):
    assert load_image_config(tmp_path) is None
    config(tmp_path);assert load_image_config(tmp_path)['native_outbound_policy']=='system'
    for value in [{'schema_version':1,'enabled':True,'activate_above_bytes':1}, {'schema_version':1,'enabled':False,'activate_above_bytes':1},{'schema_version':True,'enabled':False},{'schema_version':1,'enabled':True,'native_outbound_policy':[],'activate_above_bytes':1}]:
        (tmp_path/'image-transport.json').write_text(json.dumps(value))
        with pytest.raises(ValueError):load_image_config(tmp_path)


@pytest.mark.parametrize('result,expected',[
 ({'status':'pending'},1),({'status':'cooldown'},1),({'status':'service_error'},1),({'status':'timed_out'},1),
 ({'status':'complete'},0),({'complete':True},0),({'complete':False},1),({},1),({'status':'error','complete':True},1)])
def test_maintenance_requires_explicit_complete(result,expected):assert maintenance_exit_code(result)==expected


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[False,True])
async def test_gateway_default_off_and_bound_credential(tmp_path,monkeypatch,enabled):
    native=FakeNative();monkeypatch.setattr('codex_deepseek.gateway.NativeTransport',lambda *a,**kw:native)
    if enabled:config(tmp_path)
    reads=[]
    def credential():reads.append(True);return KEY if len(reads)==1 else 'ROTATED'
    store=Store(tmp_path/'adapter.sqlite')
    try:
        async with Gateway(credential,'LOCAL',store,{}) as gateway,aiohttp.ClientSession() as client:
            body={'model':MODEL,'input':payload()['input'],'stream':False}
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json=body,headers={'Authorization':'Bearer LOCAL','x-codex-deepseek-network-policy':'default-fallback'}) as response:
                assert response.status==200,await response.text()
            assert len(reads)==1 and all(call['key']==KEY for call in native.calls)
            if enabled:
                assert all(call['policy']=='default-fallback' for call in native.calls)
                model=next(call for call in native.calls if call['url'].endswith('/responses'))
                assert json.loads(model['body'])['input'][0]['content'][1]['file_id']
                assert (await gateway.images.transport().diagnostics(credential=KEY))['active_transport_pins']==0
            else:assert native.calls[0]['legacy'] and not (tmp_path/'image-transport').exists()
    finally:store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[429,500])
async def test_known_rejection_releases_but_unknown_provider_failure_retains(tmp_path,monkeypatch,status):
    native=FakeNative();native.model_status=status;native.model_body={'error':{'message':'synthetic'}}
    monkeypatch.setattr('codex_deepseek.gateway.NativeTransport',lambda *a,**kw:native);config(tmp_path)
    store=Store(tmp_path/'adapter.sqlite')
    try:
        async with Gateway(KEY,'LOCAL',store,{}) as gateway,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json={'model':MODEL,'input':payload()['input']},headers={'Authorization':'Bearer LOCAL'}) as response:
                assert response.status==status
                if status==429:assert response.headers['Retry-After']=='9'
            diag=await gateway.images.transport().diagnostics();assert bool(diag['unknown_upstream_calls'])==(status==500)
            result,code=await maintenance_command(tmp_path,native,lambda:KEY,'cleanup',all_unpinned=True)
            assert code==(1 if status==500 else 0)
            assert bool(native.deleted)==(status==429)
    finally:store.close()


@pytest.mark.asyncio
async def test_cancellation_before_model_headers_retains_consumption_pin(tmp_path,monkeypatch):
    native=FakeNative();native.wait_model=True;monkeypatch.setattr('codex_deepseek.gateway.NativeTransport',lambda *a,**kw:native);config(tmp_path)
    store=Store(tmp_path/'adapter.sqlite')
    try:
        async with Gateway(KEY,'LOCAL',store,{}) as gateway,aiohttp.ClientSession() as client:
            task=asyncio.create_task(client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json={'model':MODEL,'input':payload()['input']},headers={'Authorization':'Bearer LOCAL'}))
            await asyncio.wait_for(native.model_started.wait(),2);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            await asyncio.wait_for(native.cancelled.wait(),2)
            for _ in range(20):
                if store.db.execute('SELECT status FROM receipts').fetchone()[0]=='cancelled':break
                await asyncio.sleep(.01)
            diag=await gateway.images.transport().diagnostics();assert len(diag['unknown_upstream_calls'])==1
            cleanup=await gateway.images.transport().cleanup(all_unpinned=True);assert not cleanup['all_owned_files_deleted'] and not native.deleted
    finally:store.close()


@pytest.mark.asyncio
async def test_old_helper_rejected_before_raw_request_not_json_fallback(monkeypatch):
    from codex_deepseek.native_http import NativeRawUnavailable
    native=NativeTransport('https://openrouter.ai/api/v1/responses')
    async def start():native.raw_protocol_version=None
    monkeypatch.setattr(native,'start',start)
    with pytest.raises(NativeRawUnavailable):
        async with native.request_raw('POST',native.upstream,api_key=KEY,body=b'{}',content_type='application/json'):pass
    assert native.client is None


@pytest.mark.asyncio
async def test_gateway_raw_capability_failure_precedes_image_upload(tmp_path,monkeypatch):
    from codex_deepseek.native_http import NativeRawUnsupported
    native=FakeNative()
    async def unsupported():raise NativeRawUnsupported('synthetic old helper')
    native.require_raw=unsupported
    monkeypatch.setattr('codex_deepseek.gateway.NativeTransport',lambda *a,**kw:native);config(tmp_path)
    store=Store(tmp_path/'adapter.sqlite')
    try:
        async with Gateway(KEY,'LOCAL',store,{}) as gateway,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json={'model':MODEL,'input':payload()['input']},headers={'Authorization':'Bearer LOCAL'}) as response:
                assert response.status==400
            assert native.calls==[] and not (tmp_path/'image-transport/files').exists()
    finally:store.close()


@pytest.mark.asyncio
async def test_provider_terminal_before_invalid_structured_output_releases_only_resource_pin(tmp_path,monkeypatch):
    native=FakeNative();native.model_body={'id':'gen-synthetic','model':MODEL,'status':'completed','output':[{'id':'msg1','type':'message','role':'assistant','content':[{'type':'output_text','text':'INVALID'}]}]}
    monkeypatch.setattr('codex_deepseek.gateway.NativeTransport',lambda *a,**kw:native);config(tmp_path)
    store=Store(tmp_path/'adapter.sqlite')
    body={'model':MODEL,'input':payload()['input'],'text':{'format':{'type':'json_schema','name':'sample','strict':True,'schema':{'type':'object'}}}}
    try:
        async with Gateway(KEY,'LOCAL',store,{}) as gateway,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json=body,headers={'Authorization':'Bearer LOCAL'}) as response:assert response.status==502
            assert not (await gateway.images.transport().diagnostics())['unknown_upstream_calls']
            ref=store.db.execute('SELECT id FROM receipts').fetchone()[0]
            proof=gateway.images.read_receipt(gateway.images.receipts,ref)
            assert proof['adaptations']['image_transport']['provider_terminal']=='completed'
            assert store.db.execute('SELECT status FROM receipts').fetchone()[0]=='adapter_error'
    finally:store.close()


@pytest.mark.asyncio
async def test_truncated_stream_does_not_release_resource_pin(tmp_path,monkeypatch):
    native=FakeNative();native.model_body=b'data: {"type":"response.created","response":{"id":"gen-stream","status":"in_progress","output":[]}}\n\n'
    monkeypatch.setattr('codex_deepseek.gateway.NativeTransport',lambda *a,**kw:native);config(tmp_path)
    store=Store(tmp_path/'adapter.sqlite')
    try:
        async with Gateway(KEY,'LOCAL',store,{}) as gateway,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json={'model':MODEL,'input':payload()['input'],'stream':True},headers={'Authorization':'Bearer LOCAL'}) as response:
                text=await response.text();assert 'response.failed' in text and 'response.completed' not in text
            assert len((await gateway.images.transport().diagnostics())['unknown_upstream_calls'])==1
    finally:store.close()


@pytest.mark.asyncio
async def test_operator_budget_is_413_without_files_or_model_request(tmp_path,monkeypatch):
    native=FakeNative();monkeypatch.setattr('codex_deepseek.gateway.NativeTransport',lambda *a,**kw:native)
    config(tmp_path,wire_budget_bytes=128)
    store=Store(tmp_path/'adapter.sqlite')
    try:
        async with Gateway(KEY,'LOCAL',store,{}) as gateway,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json={'model':MODEL,'input':'synthetic text '*200},headers={'Authorization':'Bearer LOCAL'}) as response:
                assert response.status==413
                assert 'wire budget' in await response.text()
            assert native.calls==[]
    finally:store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('arguments,expected',[(['runtime','image-cleanup','--all-unpinned'],1),(['runtime','image-status'],0)])
async def test_disabled_cli_maintenance_has_no_store_service_or_native_side_effects(tmp_path,monkeypatch,capsys,arguments,expected):
    from codex_deepseek import cli
    monkeypatch.delenv('CODEX_DEEPSEEK_HOME',raising=False);monkeypatch.setattr(cli,'data_root',lambda:tmp_path)
    monkeypatch.setattr(cli,'isolated_path',lambda value:value)
    def forbidden(*a,**kw):raise AssertionError('maintenance must not construct native runtime ownership')
    monkeypatch.setattr(cli,'Store',forbidden);monkeypatch.setattr(cli,'ProviderService',forbidden);monkeypatch.setattr(cli,'native',forbidden)
    code=await cli.run(arguments)
    assert code==expected and json.loads(capsys.readouterr().out)['status']=='disabled'
    assert not (tmp_path/'codex-home').exists()


@pytest.mark.asyncio
async def test_generation_reconciliation_uses_native_get_with_bounded_chunked_metadata(tmp_path):
    calls=[]
    class TinyContent(Content):
        async def read(self,n=-1):return await super().read(min(7,n))
    class Native(FakeNative):
        @asynccontextmanager
        async def request(self,method,url,*,api_key,policy,**kw):
            calls.append((method,url,api_key,policy))
            data={'data':{'id':'gen-owned','model':MODEL,'provider_name':'DeepSeek','cancelled':False,'finish_reason':'stop','native_finish_reason':'stop','prompt':'NEVER_PERSIST'}}
            result=Response(200,data);result.content=TinyContent(result.body);yield result
    config(tmp_path);provider=ImageProvider(tmp_path,Native(),lambda:KEY,load_image_config(tmp_path))
    value=await provider.generation_reader({'id':'owned','response_id':'gen-owned','requested_provider':'deepseek'},lambda:KEY)
    assert value['lookup_status']=='found' and value['model_identity']['matches_requested'] is True
    assert calls==[('GET','https://openrouter.ai/api/v1/generation?id=gen-owned',KEY,'system')]
    assert 'NEVER_PERSIST' not in json.dumps(value)


def test_image_receipt_connections_close_after_commit_read_and_rollback(tmp_path, monkeypatch):
    """Receipts commit before close and exceptions roll back before close."""
    import sqlite3
    import codex_deepseek.image_provider as module

    connect = sqlite3.connect
    connections = []
    fail_insert = False

    class TrackedConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            result = super().execute(statement, *args, **kwargs)
            if fail_insert and statement.startswith("INSERT OR REPLACE INTO calls"):
                raise RuntimeError("synthetic failure after receipt write")
            return result

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, factory=TrackedConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", tracked_connect)
    try:
        provider = module.ImageProvider(tmp_path, None, lambda: "SECRET", {})
        provider.record("receipt", status="completed")
        assert provider.read_receipt(provider.receipts, "receipt")["status"] == "completed"
        fail_insert = True
        with pytest.raises(RuntimeError, match="synthetic failure"):
            provider.record("receipt", status="cancelled")
        assert provider.read_receipt(provider.receipts, "receipt")["status"] == "completed"
        assert len(connections) == 5
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    finally:
        # Retain a clean failing test if explicit close ever regresses.
        for connection in connections:
            connection.close()
