"""Built CLI/local HTTP fixtures; no real provider, commands, sandbox setup or old home.
Capacity fixtures use only the native plan tool with synthetic inputs.
"""
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


def events(text='FIXTURE_OK',tokens=1,*,output=None,response_id=None):
    response=response_id or 'resp_'+uuid.uuid4().hex
    item={'id':'msg_'+uuid.uuid4().hex,'type':'message','role':'assistant','status':'completed','phase':'final_answer','content':[{'type':'output_text','text':text,'annotations':[]}]}
    output=[item] if output is None else output
    values=[{'type':'response.created','response':{'id':response,'model':MODEL,'status':'in_progress','output':[]}}]
    values += [{'type':'response.output_item.done','output_index':index,'item':entry} for index,entry in enumerate(output)]
    values += [{'type':'response.completed','response':{'id':response,'model':MODEL,'status':'completed','output':output,'usage':{'input_tokens':tokens,'output_tokens':1,'total_tokens':tokens+1,'input_tokens_details':None,'output_tokens_details':None}}}]
    return ''.join('data: '+json.dumps(value)+'\n\n' for value in values)


def binary():
    path=os.environ.get('CODEX_DEEPSEEK_TEST_CLI_BINARY')
    if not path:pytest.skip('requires explicit isolated 413 CLI candidate')
    result=Path(path);assert result.is_file();return result


def configure(home,port,*,compact=False,capacity=False,token_budget=None):
    home.mkdir(exist_ok=True);(home/'runtime-policy').mkdir(exist_ok=True);(home/'temp').mkdir(exist_ok=True)
    catalog=Path(__file__).resolve().parents[1]/'catalog.json'
    pairs={'model':MODEL,'model_provider':'deepseek_runtime','model_catalog_json':str(catalog),'memories.extract_model':MODEL,'memories.consolidation_model':MODEL,'sqlite_home':str(home),'log_dir':str(home/'log')}
    text=''.join(key+'='+json.dumps(value)+'\n' for key,value in pairs.items())
    text+='model_providers.deepseek_runtime={name="Synthetic owned retry fixture",base_url="http://127.0.0.1:'+str(port)+'/v1",env_key="CODEX_DEEPSEEK_BRIDGE_TOKEN",wire_api="responses",supports_websockets=false,capabilities={remote_compaction="unsupported"}}\n'
    (home/'runtime-policy/config.toml').write_text(text,'utf-8')
    # Existing native retry limits are deliberately left untouched.
    user='sandbox_mode="read-only"\ncheck_for_update_on_startup=false\n'
    if compact:user+='model_auto_compact_token_limit=1\ncompact_prompt='+json.dumps(COMPACT)+'\n'
    elif capacity:user+='compact_prompt='+json.dumps(COMPACT)+'\n'
    # This loopback HTTP classification fixture must not start unrelated cloud
    # catalog synchronization. Plugin startup/owner shutdown has its own native gate.
    user+='[features]\napps=false\nplugins=false\nmemories=false\n'
    if token_budget is not None:user+='token_budget='+str(token_budget).lower()+'\n'
    if capacity:user+='executed_tool_call_metadata=true\n[tools.update_plan]\nenabled=true\n'
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


async def invoke(home,port,work,*,resume=False,ephemeral=True,prompt=None,images=()):
    args=[str(binary()),'exec','--json','--skip-git-repo-check']
    if ephemeral:args.append('--ephemeral')
    if resume:args+=['resume','--last']
    for image in images:args+=['--image',str(image)]
    text=prompt if prompt is not None else 'CDS_NATIVE_RETRY_FIXTURE: Return FIXTURE_OK. Do not run tools or inspect any files.'
    stdin_prompt=prompt is not None
    args+=['-' if stdin_prompt else text]
    process=await asyncio.create_subprocess_exec(*args,cwd=work,env=environment(home,port),
        stdin=asyncio.subprocess.PIPE if stdin_prompt else None,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    birth=psutil.Process(process.pid).create_time();children=set();stop=asyncio.Event()
    async def observe():
        while not stop.is_set():
            try:
                parent=psutil.Process(process.pid)
                if parent.create_time()==birth:
                    for child in parent.children(recursive=True):children.add((child.pid,child.create_time(),child.name()))
            except psutil.Error:pass
            await asyncio.sleep(.05)
    def remaining():
        rows=[]
        for pid,stamp,name in sorted(children):
            try:
                child=psutil.Process(pid)
                if child.create_time()==stamp:rows.append({'pid':pid,'birth':stamp,'name':name,'role':'child'})
            except psutil.NoSuchProcess:pass
            except psutil.Error as error:
                # An unreadable registered identity is unknown, never a clean retirement.
                rows.append({'pid':pid,'birth':stamp,'name':name,'role':'child','identity_error':type(error).__name__})
        if process.returncode is None:rows.append({'pid':process.pid,'birth':birth,'name':Path(args[0]).name,'role':'main'})
        return rows
    record={'argv':args,'pid':process.pid,'birth':birth,'stdin_prompt_bytes':len(text.encode()) if stdin_prompt else 0,
        'natural_main_exit':False,'main_timed_out':False,'remaining_before_cleanup':[],
        'forced_owned_cleanup':[],'remaining_after_cleanup':[],'cleanup_errors':[],
        'natural_child_exit_grace_seconds':3.0}
    watcher=asyncio.create_task(observe());started=time.monotonic()
    try:
        out,err=await asyncio.wait_for(process.communicate(input=text.encode() if stdin_prompt else None),120)
        record.update(exit_code=process.returncode,stdout=out.decode('utf-8','replace'),stderr=err.decode('utf-8','replace'))
    except asyncio.TimeoutError:
        record['main_timed_out']=True
        raise
    finally:
        stop.set();await watcher
        record['natural_main_exit']=process.returncode is not None and not record['main_timed_out']
        # Observe ordinary native shutdown before any controller retirement action.
        # No runtime/host stop is sent on behalf of the candidate.
        if process.returncode is not None:
            deadline=time.monotonic()+record['natural_child_exit_grace_seconds']
            while remaining() and time.monotonic()<deadline:await asyncio.sleep(.05)
        record['remaining_before_cleanup']=remaining()
        if process.returncode is None:
            record['forced_owned_cleanup'].append({'pid':process.pid,'birth':birth,'role':'main','action':'terminate'})
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(),5)
            except ProcessLookupError:pass
            except asyncio.TimeoutError:
                record['forced_owned_cleanup'].append({'pid':process.pid,'birth':birth,'role':'main','action':'kill'})
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(),5)
                except ProcessLookupError:pass
                except Exception as error:record['cleanup_errors'].append({'role':'main','action':'kill','error':type(error).__name__})
            except Exception as error:record['cleanup_errors'].append({'role':'main','error':type(error).__name__})
        for row in record['remaining_before_cleanup']:
            if row['role']!='child' or row.get('identity_error'):continue
            try:
                child=psutil.Process(row['pid'])
                if child.create_time()!=row['birth']:continue
                record['forced_owned_cleanup'].append({**row,'action':'terminate'})
                child.terminate()
            except psutil.NoSuchProcess:pass
            except psutil.Error as error:record['cleanup_errors'].append({**row,'error':type(error).__name__})
        if record['forced_owned_cleanup']:
            deadline=time.monotonic()+2
            while remaining() and time.monotonic()<deadline:await asyncio.sleep(.05)
            for row in remaining():
                if row['role']!='child' or row.get('identity_error'):continue
                try:
                    child=psutil.Process(row['pid'])
                    if child.create_time()!=row['birth']:continue
                    record['forced_owned_cleanup'].append({**row,'action':'kill'})
                    child.kill()
                except psutil.NoSuchProcess:pass
                except psutil.Error as error:record['cleanup_errors'].append({**row,'error':type(error).__name__})
            deadline=time.monotonic()+2
            while remaining() and time.monotonic()<deadline:await asyncio.sleep(.05)
        record['remaining_after_cleanup']=remaining()
        record['seconds']=time.monotonic()-started
        record['children']=[{'pid':pid,'birth':stamp,'name':name} for pid,stamp,name in sorted(children)]
        # Preserve timeout/cancellation lifecycle too, when no record is returned.
        evidence(work.parent,'native-invoke-lifecycle-'+str(process.pid)+'-'+uuid.uuid4().hex,record)
    return record


class Provider:
    def __init__(self,status=None,retry_after=None,mode='ordinary'):
        self.status=status;self.retry_after=retry_after;self.mode=mode;self.calls=[];self.compact_calls=0;self.seed=False
    async def handle(self,request):
        assert request.headers.get('Authorization')=='Bearer '+TOKEN
        raw=await request.read();compaction=COMPACT.encode() in raw
        self.calls.append({'at':time.monotonic(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'compaction':compaction})
        if compaction:
            self.compact_calls+=1
            body=json.loads(raw)
            self.calls[-1]['input']=body['input']
            self.calls[-1]['body_json']=body
            self.calls[-1]['canonical_body_sha256']=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            self.calls[-1]['input_item_sha256']=[hashlib.sha256(capacity_exact(item)).hexdigest() for item in body['input']]
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
    capacity_natural_exit_assertions(record)
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


def compaction_wire_capacity_progress(rows,expected_base):
    """Auto compactor: confirmed prefix first, then native local oldest-item removal."""
    prompt={'type':'message','role':'user','content':[{'type':'input_text','text':COMPACT}]}
    selected=[]
    for row in rows:
        assert row['compaction'] is True
        items=row['input']
        assert items and items[0]==expected_base,'Exact independently reconstructed native base must be present first'
        assert sum(item==expected_base for item in items)==1
        assert items[-1]==prompt,'Only the final exact control prompt is excluded from history'
        assert sum(item==prompt for item in items)==1
        hashes=[hashlib.sha256(capacity_exact(item)).hexdigest() for item in items]
        assert hashes==row['input_item_sha256'],'Stored item evidence must bind the complete actual JSON'
        selected.append(hashes[1:-1])
    assert len(selected)>1 and all(selected)
    # First rejection can switch from full input to a previously confirmed send snapshot.
    assert len(selected[1])<len(selected[0]) and selected[1]==selected[0][:len(selected[1])]
    # Existing Auto compaction trims oldest local items; Manual uses a separate prefix partition.
    for previous,current in zip(selected[1:],selected[2:]):
        assert len(current)<len(previous) and current==previous[-len(current):]
    assert len(rows)<=len(selected[0])+1,'Every recovery request must make finite item progress'
    assert len({row['sha256'] for row in rows})==len(rows),'No advice means no identical-body transport replay'
    assert all(right['bytes']<left['bytes'] for left,right in zip(rows,rows[1:]))
    return {'request_input_items':[len(items) for items in selected],
            'first_transition':'strict_confirmed_prefix','later_transition':'strict_local_suffix',
            'all_full_item_json_hashes_preserved':True,'base_sha256':hashlib.sha256(capacity_exact(expected_base)).hexdigest()}


def seeded_native_base(rollouts):
    rows=[json.loads(line) for raw in rollouts.values() for line in raw.splitlines()]
    meta=[row['payload'] for row in rows if row.get('type')=='session_meta']
    assert len(meta)==1
    text=meta[0]['base_instructions']['text']
    assert isinstance(text,str) and text
    namespace=uuid.uuid5(uuid.NAMESPACE_OID,meta[0]['session_id'])
    # Fixed native client.rs985-1027 and protocol Registry.history developer->system mapping.
    return {'type':'message','id':'msg_'+str(uuid.uuid5(namespace,text)),'role':'system',
            'content':[{'type':'input_text','text':text}]}


@pytest.mark.asyncio
@pytest.mark.parametrize('retry_after',[None,'1'])
async def test_real_cli_compaction_413_uses_same_native_retry_semantics(tmp_path,monkeypatch,retry_after):
    binary();provider=Provider(413,retry_after,mode='compact');provider.seed=True;chain=await native_chain(tmp_path,provider,monkeypatch);port=chain.port
    home=configure(tmp_path/'home',port);work=tmp_path/'work';work.mkdir()
    try:
        initial=await invoke(home,port,work,ephemeral=False)
        assert initial['exit_code']==0 and 'FIXTURE_OK' in initial['stdout'],initial
        seeded_rollouts={path:path.read_bytes() for path in home.rglob('rollout-*.jsonl')}
        assert len(seeded_rollouts)==1
        seeded_checkpoint=capacity_checkpoints(home);assert len(seeded_checkpoint)==1
        expected_base=seeded_native_base(seeded_rollouts)
        provider.seed=False;provider.calls.clear();chain.requests.clear();configure(home,port,compact=True)
        resumed=await invoke(home,port,work,resume=True,ephemeral=False)
        evidence(tmp_path,'compact-'+('advice' if retry_after else 'none'),{'seed':initial,'native':resumed,'requests':provider.calls,'gateway_requests':chain.requests,'compaction_requests':provider.compact_calls,'retry_after':retry_after,'test_only_compaction_trigger':1,'synthetic_seed_input_usage':200000})
        compacts=[row for row in provider.calls if row['compaction']]
        assert provider.compact_calls==len(compacts)
        if retry_after:
            assert provider.compact_calls==2,resumed
            assert compacts[0]['body_json']==compacts[1]['body_json']
            assert compacts[0]['canonical_body_sha256']==compacts[1]['canonical_body_sha256'],'Retry advice may reorder object keys, but no JSON field or array order may change'
            assert compacts[1]['at']-compacts[0]['at']>=.8
            assert resumed['exit_code']==0 and len(provider.calls)>=3,resumed
        else:
            output=resumed['stdout']+resumed['stderr']
            assert resumed['exit_code']!=0 and '413' in output and 'context_window_exceeded' not in output.lower(),resumed
            assert len(provider.calls)==provider.compact_calls==len(chain.requests),'A failed compactor must not start ordinary inference'
            progress=compaction_wire_capacity_progress(compacts,expected_base)
            evidence(tmp_path,'compact-no-advice-item-progress',progress)
            current=capacity_checkpoints(home);assert len(current)==1
            for key in ('origin','response_id','completed_epoch','prefix_len','prefix_hash','history_anchor','thread_id'):
                assert current[0].get(key)==seeded_checkpoint[0].get(key),'Failed compaction advanced checkpoint: '+key
            appended_rows=[]
            for path,original in seeded_rollouts.items():
                raw=path.read_bytes()
                assert raw.startswith(original),'Failed local attempts rewrote original native history'
                appended=[json.loads(line) for line in raw[len(original):].splitlines() if line]
                appended_rows.extend(appended)
                assert not any(row.get('type')=='compacted' for row in appended),'A failed summary installed successful compaction'
            started=[row['payload']['turn_id'] for row in appended_rows if row.get('type')=='event_msg' and row.get('payload',{}).get('type')=='task_started']
            assert len(started)==1 and seeded_checkpoint[0]['recovery_turn_id'] is None
            assert current[0]['recovery_turn_id']==started[0],'Persisted recovery fence must bind this real native turn and remain after failure'
            evidence(tmp_path,'compact-no-advice-checkpoint-fence',{'seed':seeded_checkpoint,'after':current,'actual_native_turn_id':started[0],'original_rollout_byte_prefix_retained':True,'new_compacted_items':0})
            assert 'Request-byte capacity' in output
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


async def native_chain(tmp_path,provider,monkeypatch,*,image_config=None):
    helper=os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY');assert helper and Path(helper).is_file()
    from codex_deepseek import gateway as module
    from test_network import clear_proxies
    clear_proxies(monkeypatch);monkeypatch.delenv('CODEX_CA_CERTIFICATE',raising=False);monkeypatch.delenv('SSL_CERT_FILE',raising=False)
    upstream_runner,upstream_port=await server(provider)
    monkeypatch.setattr(module,'UPSTREAM',f'http://127.0.0.1:{upstream_port}/v1/responses')
    state_root=tmp_path/'provider';state_root.mkdir()
    (state_root/'image-transport.json').write_text(json.dumps(image_config if image_config is not None else {'schema_version':1,'enabled':True,'native_outbound_policy':'default','activate_above_bytes':1}))
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


CAPACITY_BUDGET=256*1024
CAPACITY_UNSEEN='CAPACITY_UNCONSUMED_PAIR_R43'
CAPACITY_COLD_INPUT='CAPACITY_COLD_UNSEEN_INPUT_R43'
CAPACITY_WARNING='Request-byte capacity was exceeded (HTTP 413).'


def capacity_checkpoints(home):
    """Read only bounded metadata from this owned fixture cache, never a rollout body."""
    cache=home/'cache';rows=[]
    if not cache.exists():return rows
    assert not cache.is_symlink()
    for path in cache.rglob('*.json'):
        if path.is_symlink() or any(part.is_symlink() for part in path.parents if part!=home.parent):
            raise AssertionError('linked fixture checkpoint path')
        with path.open('rb') as stream:raw=stream.read(16385)
        if len(raw)>16384:continue
        try:value=json.loads(raw)
        except (ValueError,UnicodeError):continue
        if not isinstance(value,dict) or value.get('ordinary_scope')!='ordinary_non_basic':continue
        checkpoint=value['checkpoint'];binding=checkpoint['binding']
        rows.append({'path':str(path.relative_to(home)),'sha256':hashlib.sha256(raw).hexdigest(),
            **{key:checkpoint.get(key) for key in ('prefix_len','prefix_hash','response_id','completed_epoch','origin','recovery_turn_id')},
            'thread_id':binding['thread_id'],'history_anchor':binding['history_anchor']})
    return rows


def capacity_native_rollout_metadata(home,pair_kind="custom"):
    """Only own persisted typed metadata: export counts/digests, never prompt or values."""
    hot=('cell_id','executed_tool_calls','tool_calls_complete')
    values={key:[] for key in hot}
    pair_types=('function_call','function_call_output') if pair_kind=='function' else ('custom_tool_call','custom_tool_call_output')
    pairs={key:{kind:0 for kind in pair_types} for key in ('capacity-call-1','capacity-call-2')}
    files=[]
    def metadata(value):
        if isinstance(value,dict):
            item=value.get('internal_chat_message_metadata_passthrough')
            if isinstance(item,dict):
                for key in hot:
                    if item.get(key) is not None:values[key].append(item[key])
            for key,item in value.items():
                if key!='internal_chat_message_metadata_passthrough':metadata(item)
        elif isinstance(value,list):
            for item in value:metadata(item)
    for path in (home/'sessions').rglob('*.jsonl'):
        assert not path.is_symlink()
        files.append({'path':str(path.relative_to(home)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                row=json.loads(line)
                if row.get('type')!='response_item':continue
                payload=row.get('payload',{})
                item=payload.get('item',payload) if isinstance(payload,dict) else {}
                kind=item.get('type');call_id=item.get('call_id')
                if call_id in pairs and kind in pairs[call_id]:
                    pairs[call_id][kind]+=1
                    if kind=='custom_tool_call':assert item['name']=='exec'
                    if kind=='function_call':assert item['name']=='view_image'
                metadata(payload)
    return {'hot_counts':{key:len(items) for key,items in values.items()},
        'raw_hot_producer_positive':all(values[key] for key in hot),
        'executed_nonempty_count':sum(isinstance(value,list) and bool(value) for value in values['executed_tool_calls']),
        'complete_true_count':sum(value is True for value in values['tool_calls_complete']),
        'scope':'Raw CodeMode field production observed only; if false, the separate Direct cold hash-projection gate is still pending',
        'hot_value_sha256':{key:hashlib.sha256(json.dumps(items,sort_keys=True,separators=(',',':')).encode()).hexdigest() for key,items in values.items()},
        'native_custom_pairs':pairs,'native_pair_kind':pair_kind,'source_files':files}


def capacity_code_mode_host_input():
    cli=binary().resolve(strict=True)
    host=cli.parent/('codex-code-mode-host.exe' if os.name=='nt' else 'codex-code-mode-host')
    assert host.is_file() and not host.is_symlink(),'Capacity fixture requires its native package sibling CodeMode host; no global fallback'
    digest=hashlib.sha256(host.read_bytes()).hexdigest()
    if os.name=='nt':
        assert digest=='671d0ac396098bd56c346a43282faca151affd5455c4a1dc5d875f21e2784cf1','Windows CodeMode host differs from frozen official baseline'
    return {'path':str(host.resolve()),'sha256':digest,'bytes':host.stat().st_size,'scope':'normal sibling install-context lookup; qualified full package inventory supplies platform provenance'}


def capacity_pair(items,call_id):
    return [item for item in items if item.get('type') in {'function_call','function_call_output'}
            and item.get('call_id')==call_id]


def capacity_exact(items):
    return json.dumps(items,ensure_ascii=False,separators=(',',':')).encode()


class CapacityProvider(Provider):
    """Deterministic fault injection, not a new agent/tool/runtime implementation."""
    def __init__(self,warm,token_budget=False):
        super().__init__();self.warm=warm;self.token_budget=token_budget;self.home=None;self.phase='seed';self.seed_requests=0
        self.seed_response_ids=[];self.seed_pair=None;self.protected=None;self.budget=None
        self.fenced=[];self.translated=[];self.before_capacity=None;self.continuation_response_id=None
        self.seed_image_hashes=[];self.remaining_image_hashes=[]
    def activate(self):
        # This deliberate mock stage change is recorded, not production budget adaptation.
        assert self.seed_requests==2
        self.phase='capacity';self.budget=CAPACITY_BUDGET
    def tool(self,body,number):
        from codex_deepseek.protocol import Registry
        names={Registry().add('exec',namespace,'custom').wire for namespace in (None,'functions')}
        exposed=[tool['name'] for tool in body.get('tools',[]) if tool.get('type')=='function' and tool.get('name') in names
                 and tool.get('parameters',{}).get('properties',{}).get('input',{}).get('type')=='string']
        assert len(exposed)==1,'capacity fixture requires advertised native CodeMode exec'
        marker=CAPACITY_UNSEEN if number==2 else 'CAPACITY_CONFIRMED_PAIR'
        arguments={'explanation':marker,'plan':[{'step':'CAPACITY_NATIVE_PLAN_STEP_'+str(number),'status':'in_progress'}]}
        result_name='capacityPlanResult'+str(number)
        script='const '+result_name+' = await tools.update_plan('+json.dumps(arguments)+'); text(JSON.stringify('+result_name+')); text('+json.dumps(marker)+');'
        return {'id':'capacity_exec_'+str(number),'type':'function_call','status':'completed',
            'call_id':'capacity-call-'+str(number),'name':exposed[0],
            'arguments':json.dumps({'input':script})}
    def validate_seed_result(self):
        assert 'CAPACITY_CONFIRMED_PAIR' in json.dumps(self.seed_pair[1]['output']),'CodeMode plan output did not complete before next ordinary step'
    def image_hashes(self,body):
        return [hashlib.sha256(part['image_url'].encode()).hexdigest()
            for item in body.get('input',[]) if isinstance(item,dict)
            for part in (item.get('content') if isinstance(item.get('content'),list) else [])
            if isinstance(part,dict) and part.get('type')=='input_image' and isinstance(part.get('image_url'),str)]
    async def handle(self,request):
        assert request.headers.get('Authorization')=='Bearer '+TOKEN
        raw=await request.read();body=json.loads(raw);compaction=COMPACT.encode() in raw
        row={'at':time.monotonic(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
             'compaction':compaction,'phase':self.phase,'budget':self.budget}
        self.calls.append(row)
        if self.phase=='seed':
            assert not compaction,'token-triggered compaction is not the request-byte fixture'
            self.seed_requests+=1
            response_id='resp_capacity_seed_'+str(self.seed_requests)+'_'+uuid.uuid4().hex
            self.seed_response_ids.append(response_id);row['status']=200
            if self.seed_requests==1:
                self.seed_image_hashes=self.image_hashes(body)
                assert self.seed_image_hashes,'tiny synthetic native image must be present'
                reply=events(output=[self.tool(body,1)],response_id=response_id)
            else:
                assert self.seed_requests==2
                self.seed_pair=capacity_pair(body['input'],'capacity-call-1')
                assert [item['type'] for item in self.seed_pair]==['function_call','function_call_output']
                self.validate_seed_result()
                if self.warm:
                    reply=events(output=[self.tool(body,2)],response_id=response_id)
                    self.activate()
                else:reply=events('CAPACITY_SEED_DONE',response_id=response_id)
            return web.Response(text=reply,content_type='text/event-stream')
        if compaction:
            self.compact_calls+=1
            assert CAPACITY_UNSEEN.encode() not in raw and CAPACITY_COLD_INPUT.encode() not in raw
            assert not capacity_pair(body.get('input',[]),'capacity-call-2')
            if not self.fenced:self.fenced=capacity_checkpoints(self.home)
        else:
            if self.before_capacity is None:self.before_capacity=capacity_checkpoints(self.home)
            current=(capacity_pair(body['input'],'capacity-call-2') if self.warm else
                [item for item in body['input'] if CAPACITY_COLD_INPUT in json.dumps(item)])
            assert current,'protected new native input must survive every ordinary request'
            if self.protected is None:self.protected=capacity_exact(current)
            else:assert capacity_exact(current)==self.protected,'native recovery changed unconsumed suffix bytes/order'
            if self.warm:
                assert [item['type'] for item in current]==['function_call','function_call_output']
                assert CAPACITY_UNSEEN in json.dumps(current[1]['output']),'Unconsumed CodeMode output must be a completed native result'
            self.remaining_image_hashes=self.image_hashes(body)
        # Every capacity-stage request, including compaction, obeys the same byte rule.
        if len(raw)>self.budget:
            row['status']=413
            return web.Response(status=413,text='<html>synthetic byte capacity</html>',content_type='text/html')
        row['status']=200
        if compaction:return web.Response(text=events('CAPACITY_PREFIX_SUMMARY'),content_type='text/event-stream')
        self.translated=capacity_checkpoints(self.home)
        self.continuation_response_id='resp_capacity_final_'+uuid.uuid4().hex
        return web.Response(text=events(response_id=self.continuation_response_id),content_type='text/event-stream')


def capacity_native_plan_effects(record):
    """Count actual native TODO notifications, not the model's tool-call claims or wire echo."""
    markers={str(number):'CAPACITY_NATIVE_PLAN_STEP_'+str(number) for number in (1,2)}
    counts={key:0 for key in markers}
    effects=[]
    for line in record['stdout'].splitlines():
        if not line.strip():continue
        event=json.loads(line)
        item=event.get('item',{})
        if event.get('type') not in {'item.started','item.updated'} or item.get('type')!='todo_list':continue
        text=json.dumps(item,ensure_ascii=False)
        matched=[key for key,marker in markers.items() if marker in text]
        for key in matched:counts[key]+=1
        if matched:effects.append({'type':event['type'],'item_id':item.get('id'),'steps':matched})
    return {'counts':counts,'events':effects,'scope':'Only native stdout todo_list item.started/item.updated; final item.completed replay is excluded'}


def capacity_natural_exit_assertions(record):
    """Cleanup remains safe, but cannot turn a leaking candidate into a green gate."""
    assert record['natural_main_exit'] and not record['main_timed_out'],record
    assert record['remaining_before_cleanup']==[],record
    assert record['forced_owned_cleanup']==[],record
    assert record['remaining_after_cleanup']==[] and record['cleanup_errors']==[],record


def capacity_assertions(provider,record,seed_checkpoint=None):
    capacity_natural_exit_assertions(record)
    assert record['exit_code']==0 and 'FIXTURE_OK' in record['stdout'],record
    active=[row for row in provider.calls if row['phase']=='capacity']
    assert active and not active[0]['compaction'] and active[0]['status']==413
    compact=[row for row in active if row['compaction']]
    if provider.token_budget:
        # The reset is local: no summary request or invented summary response.
        assert compact==[] and provider.compact_calls==0
        assert [row['status'] for row in active]==[413,200]
        assert len([row for row in provider.calls if row['status']==200])==3
        assert provider.fenced==[]  # No HTTP observer is fabricated for the local reset.
    else:
        assert len(compact)>=2 and compact[0]['status']==413
        assert compact[-1]['status']==200 and compact[-1]['bytes']<=provider.budget
        assert compact[-1]['bytes']<compact[0]['bytes']
        assert not any(not row['compaction'] and row['status']==200 for row in active[:active.index(compact[-1])])
    assert all(row['status']==(413 if row['bytes']>provider.budget else 200) for row in active)
    assert active[-1]['status']==200 and not active[-1]['compaction']
    assert provider.protected and provider.seed_pair
    effects=capacity_native_plan_effects(record)
    assert effects['counts']==({'1':1,'2':1} if provider.warm else {'1':0,'2':0}),'Native plan effects were missing or replayed'
    raw=capacity_native_rollout_metadata(provider.home,'function' if getattr(provider,'direct_image',False) else 'custom')
    provider.raw_hot_projection_exercised=all(raw['hot_counts'][key]>0 for key in ('cell_id','executed_tool_calls','tool_calls_complete'))
    if getattr(provider,'direct_image',False):
        assert raw['native_custom_pairs']['capacity-call-1']=={'function_call':1,'function_call_output':1}
        assert raw['hot_counts']['cell_id']==0
        assert raw['executed_nonempty_count']>0 and raw['complete_true_count']>0
    else:
        assert raw['native_custom_pairs']['capacity-call-1']=={'custom_tool_call':1,'custom_tool_call_output':1}
        if provider.warm:assert raw['native_custom_pairs']['capacity-call-2']=={'custom_tool_call':1,'custom_tool_call_output':1}
    assert len(provider.before_capacity)==len(provider.translated)==1
    source,translated=provider.before_capacity[0],provider.translated[0]
    assert source['response_id']==provider.seed_response_ids[-1] and source['completed_epoch']>=2
    assert source['origin']=='ordinary_completed' and source['recovery_turn_id'] is None
    assert translated['origin']=='translated_prefix'
    assert translated['completed_epoch']==source['completed_epoch']
    assert translated['response_id']==source['response_id']
    assert translated['recovery_turn_id']
    assert str(uuid.UUID(translated['recovery_turn_id']))==translated['recovery_turn_id']
    assert translated['prefix_hash']!=source['prefix_hash']
    if not provider.token_budget:
        assert len(provider.fenced)==1
        fenced=provider.fenced[0]
        assert fenced['prefix_hash']==source['prefix_hash']
        assert fenced['origin']==source['origin']
        assert fenced['completed_epoch']==source['completed_epoch']
        assert translated['recovery_turn_id']==fenced['recovery_turn_id']
    if seed_checkpoint is not None:
        assert source['prefix_hash']==seed_checkpoint['prefix_hash']
        assert source['history_anchor']==seed_checkpoint['history_anchor']
        assert source['completed_epoch']==seed_checkpoint['completed_epoch']
    for checkpoint in (source,translated):
        prefix,number=checkpoint['history_anchor'].rsplit(':',1)
        assert prefix==checkpoint['thread_id'] and number.isdecimal()
    assert int(translated['history_anchor'].rsplit(':',1)[1])>int(source['history_anchor'].rsplit(':',1)[1])
    # The adapter may not rewrite image bytes. Native reset/compaction may discard consumed images.
    iterator=iter(provider.seed_image_hashes)
    assert all(any(old==current for old in iterator) for current in provider.remaining_image_hashes)
    output=record['stdout']+record['stderr']
    assert CAPACITY_WARNING in output
    assert 'context_window_exceeded' not in output.lower()
    no_tools(record)
    final=capacity_checkpoints(provider.home)
    assert len(final)==1 and final[0]['origin']=='ordinary_completed'
    assert final[0]['completed_epoch']>source['completed_epoch'] and final[0]['recovery_turn_id'] is None
    assert final[0]['response_id']==provider.continuation_response_id


async def capacity_fixture(tmp_path,monkeypatch,warm,token_budget):
    from test_image_transport import png
    host=capacity_code_mode_host_input()  # Validate input before creating listeners or native work.
    provider=CapacityProvider(warm,token_budget);provider.host_input=host
    chain=await native_chain(tmp_path,provider,monkeypatch,image_config={
        'schema_version':1,'enabled':True,'native_outbound_policy':'default',
        'activate_above_bytes':96*1024*1024,'wire_budget_bytes':128*1024*1024})
    home=configure(tmp_path/'home',chain.port,capacity=True,token_budget=token_budget);provider.home=home
    work=tmp_path/'work';work.mkdir();image=work/'synthetic-tiny.png';image.write_bytes(png())
    prompt='CAPACITY_OWN_SYNTHETIC_SEED_R43 '+('x'*(512*1024))
    return provider,chain,home,work,image,prompt


@pytest.mark.asyncio
@pytest.mark.parametrize('token_budget',[False,True],ids=['summary','token-budget-local-reset'])
async def test_real_cli_ordinary_capacity_prefix_preserves_unconsumed_tool_pair(tmp_path,monkeypatch,token_budget):
    binary();provider,chain,home,work,image,prompt=await capacity_fixture(tmp_path,monkeypatch,True,token_budget)
    try:
        record=await invoke(home,chain.port,work,ephemeral=False,prompt=prompt,images=[image])
        evidence(tmp_path,'ordinary-capacity-prefix-warm-'+('token-budget' if token_budget else 'summary'),{'native':record,'requests':provider.calls,'native_plan_effects':capacity_native_plan_effects(record),'code_mode_host':provider.host_input,'plan_tool_opt_in':True,'executed_metadata_opt_in':True,
            'gateway_requests':chain.requests,'raw_hot_metadata':capacity_native_rollout_metadata(home),'mock_budget_activated_after_two_completed_seed_requests':provider.budget,'summary_http_requests':provider.compact_calls,
            'token_budget_explicitly_enabled':token_budget,'before_capacity':provider.before_capacity,'fenced':provider.fenced,'translated':provider.translated,
            'protected_sha256':hashlib.sha256(provider.protected).hexdigest() if provider.protected else None})
        capacity_assertions(provider,record)
    finally:await chain.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('token_budget',[False,True],ids=['summary','token-budget-local-reset'])
async def test_real_cli_cold_capacity_checkpoint_qualifies_rebuilt_envelopes(tmp_path,monkeypatch,token_budget):
    binary();provider,chain,home,work,image,prompt=await capacity_fixture(tmp_path,monkeypatch,False,token_budget)
    try:
        seed=await invoke(home,chain.port,work,ephemeral=False,prompt=prompt,images=[image])
        assert seed['exit_code']==0 and 'CAPACITY_SEED_DONE' in seed['stdout'],seed
        capacity_natural_exit_assertions(seed)
        try:assert abs(psutil.Process(seed['pid']).create_time()-seed['birth'])>=.01
        except psutil.NoSuchProcess:pass
        saved=capacity_checkpoints(home)
        seed_effects=capacity_native_plan_effects(seed)
        assert seed_effects['counts']=={'1':1,'2':0},'Cold seed must execute exactly one actual native plan effect'
        seed_hot=capacity_native_rollout_metadata(home)
        seed_hot['all_three_raw_hot_fields_present']=all(seed_hot['hot_counts'][key]>0 for key in ('cell_id','executed_tool_calls','tool_calls_complete'))
        seed_hot['coverage_scope']='Actual CodeMode raw fields only; false is a separate pending Direct cold projection gate, not a projection pass'
        assert seed_hot['native_custom_pairs']['capacity-call-1']=={'custom_tool_call':1,'custom_tool_call_output':1}
        provider.activate()
        record=await invoke(home,chain.port,work,resume=True,ephemeral=False,prompt=CAPACITY_COLD_INPUT)
        evidence(tmp_path,'ordinary-capacity-prefix-cold-'+('token-budget' if token_budget else 'summary'),{'seed':seed,'seed_native_plan_effects':seed_effects,'native':record,'requests':provider.calls,'native_plan_effects':capacity_native_plan_effects(record),'code_mode_host':provider.host_input,'plan_tool_opt_in':True,'executed_metadata_opt_in':True,
            'gateway_requests':chain.requests,'saved':saved,'seed_hot_metadata':seed_hot,'token_budget_explicitly_enabled':token_budget,'before_capacity':provider.before_capacity,'fenced':provider.fenced,'translated':provider.translated,
            'mock_budget_activated_only_after_seed_natural_exit':provider.budget,'summary_http_requests':provider.compact_calls,
            'protected_sha256':hashlib.sha256(provider.protected).hexdigest() if provider.protected else None})
        assert (seed['pid'],seed['birth'])!=(record['pid'],record['birth'])
        assert len(saved)==1 and saved[0]['response_id']==provider.seed_response_ids[-1]
        capacity_assertions(provider,record,saved[0])
        no_tools(seed)
    finally:await chain.close()


def capacity_restore_semantics(items):
    """Test comparison only: exactly the three inbound-ignored warehouse fields are excluded."""
    copied=json.loads(json.dumps(items))
    for item in copied:
        metadata=item.get('internal_chat_message_metadata_passthrough')
        if isinstance(metadata,dict):
            for key in ('cell_id','executed_tool_calls','tool_calls_complete'):metadata.pop(key,None)
    return capacity_exact(copied)


def capacity_typed_image_evidence(output):
    from codex_deepseek.image_transport import _image_parts
    import base64
    evidence=[]
    for _,part in _image_parts({'input':[output]}):
        url=part['image_url'];raw=base64.b64decode(url.split(',',1)[1],validate=True)
        evidence.append({'url_sha256':hashlib.sha256(url.encode()).hexdigest(),
            'raw_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'detail':part.get('detail')})
    return evidence


class DirectImageCapacityProvider(CapacityProvider):
    def __init__(self,token_budget,image):
        super().__init__(False,token_budget);self.image=image;self.direct_image=True;self.restored_pair=None
    def tool(self,body,number):
        from codex_deepseek.protocol import Registry
        assert number==1
        wanted={Registry().add('view_image',namespace,'function').wire for namespace in (None,'functions')}
        forbidden={Registry().add(name,namespace,'custom').wire for name in ('exec','wait') for namespace in (None,'functions')}
        tools=body.get('tools',[])
        assert not any(tool.get('name') in forbidden for tool in tools),'Native Direct must be actual tool inventory, not flags alone'
        exposed=[tool['name'] for tool in tools if tool.get('type')=='function' and tool.get('name') in wanted]
        assert len(exposed)==1,'Native Direct must advertise view_image from the explicit pinned model/catalog'
        return {'id':'capacity_direct_image','type':'function_call','status':'completed',
            'call_id':'capacity-call-1','name':exposed[0],'arguments':json.dumps({'path':str(self.image)})}
    def validate_seed_result(self):
        self.typed_seed_image=capacity_typed_image_evidence(self.seed_pair[1])
        assert self.typed_seed_image,'Only the native view_image implementation may supply this typed image output'
        self.seed_image_hashes += [row['url_sha256'] for row in self.typed_seed_image]
    async def handle(self,request):
        if self.phase=='capacity' and self.before_capacity is None:
            raw=await request.read();body=json.loads(raw)
            assert COMPACT.encode() not in raw
            self.restored_pair=capacity_pair(body['input'],'capacity-call-1')
            assert [item['type'] for item in self.restored_pair]==['function_call','function_call_output']
            assert capacity_restore_semantics(self.restored_pair)==capacity_restore_semantics(self.seed_pair),'Restorable IDs/data/other typed metadata changed on cold reload'
            assert capacity_typed_image_evidence(self.restored_pair[1])==self.typed_seed_image
        return await super().handle(request)


@pytest.mark.asyncio
@pytest.mark.parametrize('token_budget',[False,True],ids=['summary','token-budget-local-reset'])
async def test_real_cli_direct_image_metadata_cold_capacity(tmp_path,monkeypatch,token_budget):
    binary();from test_image_transport import png
    work=tmp_path/'work';work.mkdir();image=work/'native-view-only.png';image.write_bytes(png())
    source_sha=hashlib.sha256(image.read_bytes()).hexdigest()
    provider=DirectImageCapacityProvider(token_budget,image)
    chain=await native_chain(tmp_path,provider,monkeypatch,image_config={
        'schema_version':1,'enabled':True,'native_outbound_policy':'default',
        'activate_above_bytes':96*1024*1024,'wire_budget_bytes':128*1024*1024})
    try:
        home=configure(tmp_path/'home',chain.port,capacity=True,token_budget=token_budget);provider.home=home
        # Raw native CLI must read the same explicit model/catalog/provider in its user layer.
        policy=(home/'runtime-policy/config.toml').read_text()
        user=(home/'config.toml').read_text().replace('[features]\n','[features]\ncode_mode=false\ncode_mode_only=false\n',1)
        (home/'config.toml').write_text(policy+user,encoding='utf-8')
        effective=__import__('tomllib').loads((home/'config.toml').read_text())
        assert effective['model']==MODEL and effective['model_provider']=='deepseek_runtime'
        assert Path(effective['model_catalog_json']).is_file()
        assert effective['features']['code_mode'] is False and effective['features']['code_mode_only'] is False
        assert effective['features']['executed_tool_call_metadata'] is True
        seed=await invoke(home,chain.port,work,ephemeral=False,
            prompt='DIRECT_IMAGE_OWN_SEED '+('x'*(512*1024)),images=[image])
        assert seed['exit_code']==0 and 'CAPACITY_SEED_DONE' in seed['stdout'],seed
        capacity_natural_exit_assertions(seed)
        assert capacity_native_plan_effects(seed)['counts']=={'1':0,'2':0}
        seed_meta=capacity_native_rollout_metadata(home,'function')
        assert seed_meta['native_custom_pairs']['capacity-call-1']=={'function_call':1,'function_call_output':1}
        assert seed_meta['executed_nonempty_count']>0 and seed_meta['complete_true_count']>0
        assert seed_meta['hot_counts']['cell_id']==0,'Native Direct has no CodeMode cell; never fabricate the third hot field'
        saved=capacity_checkpoints(home);assert len(saved)==1
        provider.activate()
        record=await invoke(home,chain.port,work,resume=True,ephemeral=False,prompt=CAPACITY_COLD_INPUT)
        evidence(tmp_path,'direct-image-cold-'+('token-budget' if token_budget else 'summary'),{
            'seed':seed,'native':record,'requests':provider.calls,'gateway_requests':chain.requests,
            'seed_raw_native_metadata':seed_meta,'typed_image_output':getattr(provider,'typed_seed_image',None),
            'source_png_sha256':source_sha,'source_png_unchanged':hashlib.sha256(image.read_bytes()).hexdigest()==source_sha,
            'saved':saved,'before_capacity':provider.before_capacity,'fenced':provider.fenced,'translated':provider.translated,
            'native_direct_explicit':True,'actual_view_image_declared':True,
            'stdout_image_view_event':'Not exported by fixed native exec JSON enum; raw native call/output and actual typed image are the independent effect proof'})
        assert (seed['pid'],seed['birth'])!=(record['pid'],record['birth'])
        assert hashlib.sha256(image.read_bytes()).hexdigest()==source_sha
        assert provider.restored_pair is not None and provider.typed_seed_image
        capacity_assertions(provider,record,saved[0])
        no_tools(seed)
    finally:await chain.close()
