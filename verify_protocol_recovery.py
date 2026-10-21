"""Native retry-loop recovery from backend faults; no adapter tool loop or replay."""
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


class Content:
    def __init__(self,events):self.events=events
    async def iter_any(self):
        for event in self.events:yield ('data: '+json.dumps(event)+'\n\n').encode()


class FaultResponse:
    status=200
    headers={}
    def __init__(self,kind):
        rid='resp_controlled_fault';reason={'type':'reasoning','id':'rs_fault','summary':[],'content':[{'type':'reasoning_text','text':'Fault-injection fixture reasoning, not actual model-generated thought. Continue the original user task.'}]}
        events=[{'type':'response.created','response':{'id':rid,'status':'in_progress','output':[]}},{'type':'response.output_item.done','output_index':0,'item':reason}]
        if kind=='undeclared-tool':
            item={'type':'function_call','id':'fc_fault','call_id':'call_fault','name':'nonexistent_protocol_fixture','arguments':'{}','status':'completed'}
            events.append({'type':'response.output_item.added','output_index':1,'item':item})
        else:
            bad='```json\n{"marker":"RECOVERY_NATIVE","value":437}\n```'
            item={'type':'message','id':'m_fault','role':'assistant','content':[{'type':'output_text','text':bad}],'status':'completed'}
            events.extend([{'type':'response.output_item.added','output_index':1,'item':{**item,'content':[]}},{'type':'response.output_text.delta','item_id':'m_fault','output_index':1,'content_index':0,'delta':bad},{'type':'response.output_text.done','item_id':'m_fault','output_index':1,'content_index':0,'text':bad},{'type':'response.output_item.done','output_index':1,'item':item}])
        events.append({'type':'response.completed','response':{'id':rid,'status':'completed','output':[reason,item],'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}}})
        self.content=Content(events)


class FaultOnce:
    def __init__(self,real,kind):self.real=real;self.kind=kind;self.requests=[]
    @asynccontextmanager
    async def post(self,url,**options):
        self.requests.append(options['json'])
        if len(self.requests)==1:yield FaultResponse(self.kind)
        else:
            async with self.real.post(url,**options) as response:yield response
    async def close(self):await self.real.close()


def native_turn(root,binary,catalog,port,token,kind):
    (root/'work').mkdir();home=root/'home';home.mkdir()
    config='model="deepseek/deepseek-v4.1-flash-1017"\nmodel_provider="recovery_fixture"\nmodel_catalog_json='+json.dumps(str(catalog))+'\nmodel_reasoning_effort="high"\nweb_search="live"\n[features]\ncode_mode=false\ncode_mode_only=false\napps=false\nplugins=false\nmemories=false\nmulti_agent=false\n[model_providers.recovery_fixture]\nname="Isolated protocol fault fixture"\nbase_url="http://127.0.0.1:'+str(port)+'/v1"\nenv_key="RECOVERY_FIXTURE_TOKEN"\nwire_api="responses"\nsupports_websockets=false\n'
    (home/'config.toml').write_text(config)
    env=native_environment(home,'',binary,install_shims=False)
    for key in ['CODEX_DEEPSEEK_POLICY_HOME','CODEX_DEEPSEEK_BRIDGE_TOKEN','CODEX_DEEPSEEK_BRIDGE_PORT']:env.pop(key,None)
    env['RECOVERY_FIXTURE_TOKEN']=token
    peer=Peer(root,command=[str(binary)],environment=env)
    try:
        thread=peer.new();options={}
        if kind=='schema':options['outputSchema']={'type':'object','properties':{'marker':{'type':'string','enum':['RECOVERY_NATIVE']},'value':{'type':'integer','enum':[437]}},'required':['marker','value'],'additionalProperties':False}
        items=peer.turn(thread,'Do not call tools, search or delegate. Reply exactly RECOVERY_NATIVE.' if kind=='undeclared-tool' else 'Do not call tools, search or delegate. Return marker RECOVERY_NATIVE and value 437 as JSON matching the required schema.',**options)
        messages=[x.get('text','') for x in items if x.get('type')=='agentMessage'];last=messages[-1] if messages else ''
        valid=last.strip()=='RECOVERY_NATIVE' if kind=='undeclared-tool' else json.loads(last)=={'marker':'RECOVERY_NATIVE','value':437}
        return {'thread':thread,'answer':last,'answer_valid':valid,'native_retry_errors':[e['params'] for e in peer.events if e.get('method')=='error' and e.get('params',{}).get('willRetry')],'client_tool_executions':[x for x in items if x.get('type') in {'commandExecution','fileChange','mcpToolCall'}]}
    finally:peer.close()


async def scenario(args,kind):
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True,mode=0o700);entry=args.entry.resolve();release=entry.parent.parent.parent;metadata=json.loads((release/'installation.json').read_text())
    native=Path(metadata['native_package'])/'bin/codex';catalog=release/'catalog.json';store=Store(root/'state.sqlite');result={'kind':kind,'passed':False,'installation':metadata}
    try:
        async with Gateway(credential,uuid.uuid4().hex,store,json.loads(catalog.read_text()),trace_source=lambda:str(root/'adapter-traces')) as gateway:
            proxy=FaultOnce(gateway.client,kind);gateway.client=proxy
            observed=await asyncio.to_thread(native_turn,root,native,catalog,gateway.port,gateway.token,kind)
            requests=proxy.requests
            context=any(any(item.get('name')=='cx_native_continuation_context' for item in req.get('input',[]) if isinstance(item,dict)) for req in requests[1:])
            receipts=store.db.execute('SELECT status,provider,response_id FROM receipts ORDER BY started').fetchall()
            result.update(observed,requests=len(requests),continuation_context_observed=context,receipts=receipts)
            result['passed']=observed['answer_valid'] and len(requests)>=2 and context and bool(observed['native_retry_errors']) and not observed['client_tool_executions'] and receipts[0][0]=='adapter_error' and receipts[-1][0]=='completed'
    finally:
        store.close();(root/'verdict.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in {'installation','native_retry_errors','client_tool_executions'}}),flush=True);print('EVIDENCE',root,flush=True)
    return result['passed']


async def run(args):
    passed=[]
    for kind in ['undeclared-tool','schema']:passed.append(await scenario(args,kind))
    if not all(passed):raise AssertionError('Native protocol recovery failed')


def main():
    p=argparse.ArgumentParser();p.add_argument('--entry',type=Path,required=True);p.add_argument('--output',type=Path,required=True);asyncio.run(run(p.parse_args()))


if __name__=='__main__':main()
