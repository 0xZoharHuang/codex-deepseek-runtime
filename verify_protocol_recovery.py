"""Native retry-loop recovery from backend faults; no adapter tool loop or replay."""
from codex_deepseek.platforms import executable
import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import uuid
from codex_deepseek.cli import credential,native_environment
from codex_deepseek.gateway import Gateway
from codex_deepseek.state import Store
from verify_live import Peer,text
from install import console_entry,valid_release,source_identity

VERIFIERS=('verify_protocol_recovery.py','verify_live.py','install.py')


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


class Content:
    def __init__(self,events):self.events=events
    async def iter_any(self):
        for event in self.events:yield ('data: '+json.dumps(event)+'\n\n').encode()


class FaultResponse:
    status=200
    headers={}
    def __init__(self,kind,tool_name=None):
        rid='resp_controlled_fault';reason={'type':'reasoning','id':'rs_fault','summary':[],'content':[{'type':'reasoning_text','text':'Fault-injection fixture reasoning, not actual model-generated thought. Continue the original user task.'}]}
        events=[{'type':'response.created','response':{'id':rid,'status':'in_progress','output':[]}},{'type':'response.output_item.done','output_index':0,'item':reason}]
        if kind=='undeclared-tool':
            item={'type':'function_call','id':'fc_fault','call_id':'call_fault','name':'nonexistent_protocol_fixture','arguments':'{}','status':'completed'}
            events.append({'type':'response.output_item.added','output_index':1,'item':item})
        else:
            bad='{"marker":"RECOVERY_NATIVE","value":1e999}' if kind=='schema-overflow' else '```json\n{"marker":"RECOVERY_NATIVE","value":437}\n```'
            item={'type':'message','id':'m_fault','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':bad}],'status':'completed'}
            if kind=='schema-multiple':item.pop('phase')
            events.extend([{'type':'response.output_item.added','output_index':1,'item':{**item,'content':[]}},{'type':'response.output_text.delta','item_id':'m_fault','output_index':1,'content_index':0,'delta':bad},{'type':'response.output_text.done','item_id':'m_fault','output_index':1,'content_index':0,'text':bad},{'type':'response.output_item.done','output_index':1,'item':item}])
        output=[reason,item]
        if kind=='schema-multiple':output.append({'type':'message','id':'m_good','role':'assistant','content':[{'type':'output_text','text':'{"marker":"RECOVERY_NATIVE","value":437}'}],'status':'completed'})
        if kind in {'schema-tool-final','schema-tool-before-terminal'}:
            if not tool_name:raise AssertionError('Controlled fault requires an actually declared dynamic native tool')
            output.append({'type':'function_call','id':'fc_controlled','call_id':'call_controlled','name':tool_name,'arguments':'{}','status':'completed'})
            if kind=='schema-tool-before-terminal':events.append({'type':'response.output_item.done','output_index':2,'item':output[-1]})
        events.append({'type':'response.completed','response':{'id':rid,'status':'completed','output':output,'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}}})
        self.content=Content(events)


class FaultOnce:
    def __init__(self,real,kind):self.real=real;self.kind=kind;self.requests=[]
    @asynccontextmanager
    async def post(self,url,**options):
        self.requests.append(options['json'])
        if len(self.requests)==1:
            matching=[t['name'] for t in options['json'].get('tools',[]) if t.get('type')=='function' and 'Controlled schema fault fixture' in t.get('description','')]
            yield FaultResponse(self.kind,matching[0] if matching else None)
        else:
            async with self.real.post(url,**options) as response:yield response
    async def close(self):await self.real.close()


def native_turn(root,binary,catalog,port,token,kind):
    (root/'work').mkdir();home=root/'home';home.mkdir()
    config='model="deepseek/deepseek-v4.1-flash-1017"\nmodel_provider="recovery_fixture"\nmodel_catalog_json='+json.dumps(str(catalog))+'\nmodel_reasoning_effort="high"\nweb_search="live"\n[features]\ncode_mode=false\ncode_mode_only=false\napps=false\nplugins=false\nmemories=false\nmulti_agent=false\n[model_providers.recovery_fixture]\nname="Isolated protocol fault fixture"\nbase_url="http://127.0.0.1:'+str(port)+'/v1"\nenv_key="RECOVERY_FIXTURE_TOKEN"\nwire_api="responses"\nsupports_websockets=false\n'
    (home/'config.toml').write_text(config,encoding='utf-8')
    env=native_environment(home,'',binary,install_shims=False)
    for key in ['CODEX_DEEPSEEK_POLICY_HOME','CODEX_DEEPSEEK_BRIDGE_TOKEN','CODEX_DEEPSEEK_BRIDGE_PORT']:env.pop(key,None)
    env['RECOVERY_FIXTURE_TOKEN']=token
    peer=Peer(root,command=[str(binary)],environment=env)
    try:
        dynamic=[{'name':'forbidden_fault_fixture','description':'Controlled schema fault fixture: must never be executed.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}}] if kind in {'schema-tool-final','schema-tool-before-terminal'} else []
        peer.dynamic_answers[(None,'forbidden_fault_fixture')]='UNEXPECTED_TOOL_EXECUTION'
        thread=peer.new(**({'dynamicTools':dynamic} if dynamic else {}));options={}
        if kind.startswith('schema'):
            options['outputSchema']={'type':'object','properties':{'marker':{'type':'string','enum':['RECOVERY_NATIVE']},'value':{'type':'number'} if kind=='schema-overflow' else {'type':'integer','enum':[437]}},'required':['marker','value'],'additionalProperties':False}
        items=peer.turn(thread,'Do not call tools, search or delegate. Reply exactly RECOVERY_NATIVE.' if kind=='undeclared-tool' else 'Do not call tools, search or delegate. Return marker RECOVERY_NATIVE and value 437 as JSON matching the required schema.',**options)
        messages=[x.get('text','') for x in items if x.get('type')=='agentMessage'];last=messages[-1] if messages else ''
        valid=last.strip()=='RECOVERY_NATIVE' if kind=='undeclared-tool' else json.loads(last)=={'marker':'RECOVERY_NATIVE','value':437}
        return {'thread':thread,'answer':last,'answer_valid':valid,'items':items,'native_retry_errors':[e['params'] for e in peer.events if e.get('method')=='error' and e.get('params',{}).get('willRetry')],'client_tool_executions':[x for x in items if x.get('type') in {'commandExecution','fileChange','mcpToolCall'}]+[e for e in peer.callbacks if e.get('method')=='item/tool/call']}
    finally:peer.close()


async def scenario(args,kind):
    entry=args.entry.resolve();release=entry.parent.parent.parent;metadata=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    manifest=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
    if entry!=console_entry(release/'venv') or not valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],manifest):
        raise SystemExit('Protocol recovery requires an intact immutable installed entry')
    source_id,source_hashes=source_identity()
    if source_id!=metadata['source_id'] or source_hashes!=metadata['source_sha256']:
        raise SystemExit('Protocol recovery controller runtime source differs from the installed artifact')
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True,mode=0o700)
    native=executable(Path(metadata['native_package'])/'bin','codex');catalog=release/'catalog.json';store=Store(root/'state.sqlite')
    artifact={'kind':kind,'entry':str(entry),'entry_sha256':sha(entry),'installation':metadata,'native_manifest':manifest,
              'target':manifest['target'],'native_binary_sha256':sha(native),'controller_optimize':0,
              'verifier_sha256':{name:sha(Path(__file__).with_name(name)) for name in VERIFIERS}}
    (root/'artifact.json').write_text(json.dumps(artifact,indent=2),encoding='utf-8')
    result={'kind':kind,'passed':False,'installation':metadata,'target':manifest['target']};requests=[];receipts=[];observed={}
    try:
        async with Gateway(credential,uuid.uuid4().hex,store,json.loads(catalog.read_text()),native_http_binary=executable(Path(metadata['native_package'])/'bin','codex-http-transport'),trace_source=lambda:str(root/'adapter-traces')) as gateway:
            proxy=FaultOnce(gateway.client,kind);gateway.client=proxy
            observed=await asyncio.to_thread(native_turn,root,native,catalog,gateway.port,gateway.token,kind)
            requests=proxy.requests
            context=any(any(item.get('name')=='cx_native_continuation_context' for item in req.get('input',[]) if isinstance(item,dict)) for req in requests[1:])
            receipts=store.db.execute('SELECT status,provider,response_id,detail FROM receipts ORDER BY started').fetchall()
            result.update({k:v for k,v in observed.items() if k!='items'},requests=len(requests),continuation_context_observed=context,receipts=receipts)
            result['passed']=observed['answer_valid'] and len(requests)>=2 and context and bool(observed['native_retry_errors']) and not observed['client_tool_executions'] and receipts[0][0]=='adapter_error' and receipts[-1][0]=='completed' and (kind=='undeclared-tool' or 'structured output' in receipts[0][3])
    finally:
        store.close()
        for name,value in [('request-bodies.json',requests),('receipts.json',receipts),('observed.json',observed)]:
            (root/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        raw=[root/name for name in ['request-bodies.json','receipts.json','observed.json','client-requests.jsonl','state.sqlite']]
        raw.extend(root.glob('events-*.json'));raw.extend(root.glob('launch-*.json'))
        result['evidence_sha256']={path.name:sha(path) for path in raw if path.is_file()}
        unchanged=(sha(entry)==artifact['entry_sha256'] and sha(native)==artifact['native_binary_sha256']
                   and json.loads((release/'installation.json').read_text(encoding='utf-8'))==metadata
                   and valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],manifest)
                   and source_identity()==(source_id,source_hashes)
                   and all(sha(Path(__file__).with_name(name))==digest for name,digest in artifact['verifier_sha256'].items()))
        result['verifier_and_artifact_unchanged']=unchanged;result['passed']=result['passed'] and unchanged
        (root/'verdict.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in {'installation','native_retry_errors','client_tool_executions','evidence_sha256'}}),flush=True);print('EVIDENCE',root,flush=True)
    return result['passed']


async def run(args):
    passed=[]
    for kind in ['undeclared-tool','schema','schema-multiple','schema-tool-final','schema-tool-before-terminal','schema-overflow']:passed.append(await scenario(args,kind))
    if not all(passed):raise AssertionError('Native protocol recovery failed')


def main():
    if not __debug__:raise SystemExit('Acceptance must run with Python assertions enabled')
    p=argparse.ArgumentParser();p.add_argument('--entry',type=Path,required=True);p.add_argument('--output',type=Path,required=True);asyncio.run(run(p.parse_args()))


if __name__=='__main__':main()
