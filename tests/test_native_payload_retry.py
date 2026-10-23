"""Built CLI HTTP retry fixtures; no real provider, tools, sandbox setup or old home."""
import asyncio,hashlib,json,os,shutil,time,uuid
from pathlib import Path
from types import SimpleNamespace
import aiohttp
from aiohttp import web
import psutil
import pytest
from codex_deepseek.protocol import MODEL

TOKEN='synthetic-native-413-fixture-token'
COMPACT='CDS_NATIVE_COMPACTION_RETRY_FIXTURE_ONLY'


def events(text='FIXTURE_OK',tokens=1):
    response='resp_'+uuid.uuid4().hex
    item={'id':'msg_'+uuid.uuid4().hex,'type':'message','role':'assistant','status':'completed','phase':'final_answer','content':[{'type':'output_text','text':text,'annotations':[]}]}
    values=[{'type':'response.created','response':{'id':response,'model':MODEL,'status':'in_progress','output':[]}},
        {'type':'response.output_item.done','output_index':0,'item':item},
        {'type':'response.completed','response':{'id':response,'model':MODEL,'status':'completed','output':[item],'usage':{'input_tokens':tokens,'output_tokens':1,'total_tokens':tokens+1,'input_tokens_details':None,'output_tokens_details':None}}}]
    return ''.join('data: '+json.dumps(value)+'\n\n' for value in values)


def binary():
    path=os.environ.get('CODEX_DEEPSEEK_TEST_CLI_BINARY')
    if not path:pytest.skip('requires explicit isolated 413 CLI candidate')
    result=Path(path);assert result.is_file();return result


def configure(home,port,*,compact=False):
    home.mkdir(exist_ok=True);(home/'runtime-policy').mkdir(exist_ok=True);(home/'temp').mkdir(exist_ok=True)
    catalog=Path(__file__).resolve().parents[1]/'catalog.json'
    pairs={'model':MODEL,'model_provider':'deepseek_runtime','model_catalog_json':str(catalog),'memories.extract_model':MODEL,'memories.consolidation_model':MODEL,'sqlite_home':str(home),'log_dir':str(home/'log')}
    text=''.join(key+'='+json.dumps(value)+'\n' for key,value in pairs.items())
    text+='model_providers.deepseek_runtime={name="Synthetic owned retry fixture",base_url="http://127.0.0.1:'+str(port)+'/v1",env_key="CODEX_DEEPSEEK_BRIDGE_TOKEN",wire_api="responses",supports_websockets=false,capabilities={remote_compaction="unsupported"}}\n'
    (home/'runtime-policy/config.toml').write_text(text,'utf-8')
    # Existing native retry limits are deliberately left untouched.
    user='sandbox_mode="read-only"\ncheck_for_update_on_startup=false\n'
    if compact:user+='model_auto_compact_token_limit=1\ncompact_prompt='+json.dumps(COMPACT)+'\n'
    # This loopback HTTP classification fixture must not start unrelated cloud
    # catalog synchronization. Plugin startup/owner shutdown has its own native gate.
    user+='[features]\napps=false\nplugins=false\nmemories=false\n'
    (home/'config.toml').write_text(user,'utf-8')
    return home


def environment(home,port):
    env={k:v for k,v in os.environ.items() if not k.startswith(('CODEX_','RUST_LOG','OTEL_')) and k not in {'OPENAI_API_KEY','OPENROUTER_API_KEY','GH_TOKEN','GITHUB_TOKEN'}}
    env.update(CODEX_HOME=str(home),CODEX_DEEPSEEK_HOME=str(home),CODEX_DEEPSEEK_POLICY_HOME=str(home),CODEX_DEEPSEEK_BRIDGE_PORT=str(port),CODEX_DEEPSEEK_BRIDGE_TOKEN=TOKEN,TEMP=str(home/'temp'),TMP=str(home/'temp'),OTEL_SDK_DISABLED='true',GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM='1')
    # Never discover a helper from an installed CDS or Codex App directory.
    if os.name=='nt':
        git=shutil.which('git')
        if not git:raise RuntimeError('Native Windows retry acceptance requires Git on the controller PATH')
        git_directory=str(Path(git).resolve(strict=True).parent)
        env['PATH']=os.pathsep.join([str(binary().parent),git_directory,os.path.join(os.environ.get('SystemRoot','C:/Windows'),'System32')])
    else:env['PATH']='/usr/bin:/bin'
    return env


async def invoke(home,port,work,*,resume=False,ephemeral=True):
    args=[str(binary()),'exec','--json','--skip-git-repo-check']
    if ephemeral:args.append('--ephemeral')
    if resume:args+=['resume','--last']
    args+=['CDS_NATIVE_RETRY_FIXTURE: Return FIXTURE_OK. Do not run tools or inspect any files.']
    process=await asyncio.create_subprocess_exec(*args,cwd=work,env=environment(home,port),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    birth=psutil.Process(process.pid).create_time();children=set();stop=asyncio.Event()
    async def observe():
        while not stop.is_set():
            try:
                parent=psutil.Process(process.pid)
                if abs(parent.create_time()-birth)<.01:
                    for child in parent.children(recursive=True):children.add((child.pid,child.create_time(),child.name()))
            except psutil.Error:pass
            await asyncio.sleep(.05)
    watcher=asyncio.create_task(observe());started=time.monotonic()
    try:
        out,err=await asyncio.wait_for(process.communicate(),120)
        return {'argv':args,'exit_code':process.returncode,'stdout':out.decode('utf-8','replace'),'stderr':err.decode('utf-8','replace'),'seconds':time.monotonic()-started,'children':[{'pid':pid,'birth':stamp,'name':name} for pid,stamp,name in sorted(children)]}
    finally:
        stop.set();await watcher
        if process.returncode is None:process.terminate();await process.wait()
        for pid,stamp,name in children:
            try:
                child=psutil.Process(pid)
                if abs(child.create_time()-stamp)<.01:child.terminate()
            except psutil.Error:pass


class Provider:
    def __init__(self,status=None,retry_after=None,mode='ordinary'):
        self.status=status;self.retry_after=retry_after;self.mode=mode;self.calls=[];self.compact_calls=0;self.seed=False
    async def handle(self,request):
        assert request.headers.get('Authorization')=='Bearer '+TOKEN
        raw=await request.read();compaction=COMPACT.encode() in raw
        self.calls.append({'at':time.monotonic(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'compaction':compaction})
        if compaction:self.compact_calls+=1
        number=self.compact_calls if self.mode=='compact' else len(self.calls)
        fail=(not self.seed and self.status is not None and (self.mode!='compact' or compaction) and (self.retry_after is None and self.status==413 or number==1))
        if fail:
            headers={'Retry-After':self.retry_after} if self.retry_after is not None else {}
            return web.Response(status=self.status,text='<html>synthetic payload or temporary failure</html>',headers=headers,content_type='text/html')
        return web.Response(text=events(tokens=200000 if self.seed else 1),content_type='text/event-stream')


async def server(provider):
    app=web.Application(client_max_size=96*1024*1024);app.router.add_post('/v1/responses',provider.handle)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();return runner,runner.addresses[0][1]


def evidence(tmp_path,name,value):
    target=Path(os.environ.get('CDS_RETRY_EVIDENCE_DIR',str(tmp_path)))
    target.mkdir(parents=True,exist_ok=True);(target/(name+'.json')).write_text(json.dumps(value,indent=2),'utf-8')


def no_tools(record):
    observed=' '.join(child['name'] for child in record['children']).lower()
    assert 'sandbox-setup' not in observed and 'command-runner' not in observed
    assert 'Code Mode is unavailable' not in record['stderr']
    assert '"type":"command_execution"' not in record['stdout'].replace(' ','')


@pytest.mark.asyncio
@pytest.mark.parametrize('status,retry_after,expected',[ (413,None,1),(413,'1',2),(503,None,2),(429,'1',2) ])
async def test_real_cli_retry_classification(tmp_path,monkeypatch,status,retry_after,expected):
    binary();provider=Provider(status,retry_after);chain=await native_chain(tmp_path,provider,monkeypatch);port=chain.port
    home=configure(tmp_path/'home',port);work=tmp_path/'work';work.mkdir()
    try:
        record=await invoke(home,port,work)
        evidence(tmp_path,f'ordinary-{status}-'+('advice' if retry_after else 'none'),{'native':record,'requests':provider.calls,'gateway_requests':chain.requests,'status':status,'retry_after':retry_after})
        assert len(provider.calls)==len(chain.requests)==expected,record
        if expected==1:
            assert record['exit_code']!=0 and '413' in record['stdout']+record['stderr'],record
            assert 'context_window_exceeded' not in (record['stdout']+record['stderr']).lower()
        else:
            assert record['exit_code']==0 and 'FIXTURE_OK' in record['stdout'],record
            if retry_after:assert provider.calls[1]['at']-provider.calls[0]['at']>=.8
        no_tools(record)
    finally:await chain.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('retry_after',[None,'1'])
async def test_real_cli_compaction_413_uses_same_native_retry_semantics(tmp_path,monkeypatch,retry_after):
    binary();provider=Provider(413,retry_after,mode='compact');provider.seed=True;chain=await native_chain(tmp_path,provider,monkeypatch);port=chain.port
    home=configure(tmp_path/'home',port);work=tmp_path/'work';work.mkdir()
    try:
        initial=await invoke(home,port,work,ephemeral=False)
        assert initial['exit_code']==0 and 'FIXTURE_OK' in initial['stdout'],initial
        provider.seed=False;provider.calls.clear();chain.requests.clear();configure(home,port,compact=True)
        resumed=await invoke(home,port,work,resume=True,ephemeral=False)
        evidence(tmp_path,'compact-'+('advice' if retry_after else 'none'),{'seed':initial,'native':resumed,'requests':provider.calls,'gateway_requests':chain.requests,'compaction_requests':provider.compact_calls,'retry_after':retry_after,'test_only_compaction_trigger':1,'synthetic_seed_input_usage':200000})
        assert provider.compact_calls==(2 if retry_after else 1),resumed
        if retry_after:
            assert resumed['exit_code']==0 and len(provider.calls)>=3,resumed
            compacts=[row for row in provider.calls if row['compaction']];assert compacts[1]['at']-compacts[0]['at']>=.8
        else:assert resumed['exit_code']!=0 and len(provider.calls)==1,resumed
        no_tools(initial);no_tools(resumed)
    finally:await chain.close()


class NoOwnerState:
    # A provider evidence test double. Never constructs runtime-owner reconciliation.
    def __init__(self,path):self.path=path;self.rows={};self.items={};self.database_id='synthetic-no-owner'
    def start(self,*args,**kwargs):
        identity=uuid.uuid4().hex;self.rows[identity]={'status':'running'};return identity
    def finish(self,identity,status,*args):self.rows[identity]['status']=status
    def progress(self,*args):pass
    def get(self,identity):return self.items.get(identity)
    def put(self,identity,value):self.items[identity]=value
    def has_completed_native_item(self,*args,**kwargs):return False


async def native_chain(tmp_path,provider,monkeypatch):
    helper=os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY');assert helper and Path(helper).is_file()
    from codex_deepseek import gateway as module
    from test_network import clear_proxies
    clear_proxies(monkeypatch);monkeypatch.delenv('CODEX_CA_CERTIFICATE',raising=False);monkeypatch.delenv('SSL_CERT_FILE',raising=False)
    upstream_runner,upstream_port=await server(provider)
    monkeypatch.setattr(module,'UPSTREAM',f'http://127.0.0.1:{upstream_port}/v1/responses')
    state_root=tmp_path/'provider';state_root.mkdir()
    (state_root/'image-transport.json').write_text(json.dumps({'schema_version':1,'enabled':True,'native_outbound_policy':'default','activate_above_bytes':1}))
    class ObservedGateway(module.Gateway):
        async def responses(self,request):
            body=await request.read();row={'at':time.monotonic(),'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()};self.requests.append(row)
            response=await super().responses(request);row['status']=response.status;return response
    gateway=ObservedGateway(TOKEN,TOKEN,NoOwnerState(state_root/'adapter.sqlite'),{},native_http_binary=Path(helper),trace_source=lambda:None);gateway.requests=[]
    try:await gateway.__aenter__()
    except BaseException:await upstream_runner.cleanup();raise
    async def close():
        try:await gateway.__aexit__(None,None,None)
        finally:await upstream_runner.cleanup()
    return SimpleNamespace(port=gateway.port,requests=gateway.requests,close=close)


@pytest.mark.asyncio
async def test_real_cli_local_operator_budget_413_is_not_replayed(tmp_path,monkeypatch):
    binary();helper=os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY');assert helper and Path(helper).is_file()
    from codex_deepseek import gateway as module
    upstream=Provider();upstream_runner,upstream_port=await server(upstream)
    monkeypatch.setattr(module,'UPSTREAM',f'http://127.0.0.1:{upstream_port}/v1/responses')
    state_root=tmp_path/'provider';state_root.mkdir()
    (state_root/'image-transport.json').write_text(json.dumps({'schema_version':1,'enabled':True,'native_outbound_policy':'default','activate_above_bytes':1,'wire_budget_bytes':128}))
    state=NoOwnerState(state_root/'adapter.sqlite');calls=[]
    front_runner=None
    try:
        async with module.Gateway(TOKEN,TOKEN,state,{},native_http_binary=Path(helper),trace_source=lambda:None) as gateway,aiohttp.ClientSession(trust_env=False) as local:
            async def forward(request):
                raw=await request.read();headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'}
                if 'x-codex-deepseek-network-policy' in request.headers:headers['x-codex-deepseek-network-policy']=request.headers['x-codex-deepseek-network-policy']
                async with local.post(f'http://127.0.0.1:{gateway.port}/v1/responses',data=raw,headers=headers) as response:
                    body=await response.read();calls.append({'status':response.status,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
                    return web.Response(status=response.status,body=body,content_type='application/json')
            app=web.Application(client_max_size=96*1024*1024);app.router.add_post('/v1/responses',forward)
            front_runner=web.AppRunner(app);await front_runner.setup();await web.TCPSite(front_runner,'127.0.0.1',0).start();port=front_runner.addresses[0][1]
            home=configure(tmp_path/'home',port);work=tmp_path/'work';work.mkdir()
            record=await invoke(home,port,work)
            evidence(tmp_path,'local-operator-budget',{'native':record,'gateway_requests':calls,'upstream_requests':upstream.calls,'store_constructor_used':False})
            assert calls and len(calls)==1 and calls[0]['status']==413,record
            assert record['exit_code']!=0 and not upstream.calls
            no_tools(record)
    finally:
        if front_runner:await front_runner.cleanup()
        await upstream_runner.cleanup()
