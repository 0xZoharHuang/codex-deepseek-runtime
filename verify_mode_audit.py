"""Native selectable direct/hybrid/only tools and v1/v2 collaboration contracts."""
import argparse
import json
import os
from pathlib import Path
import uuid

from verify_live import Peer,text


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('model_reasoning_effort="low"\n[features]\ncode_mode=false\ncode_mode_only=false\nmulti_agent_v2=false\nmulti_agent=false\n')
    results=[];peer=Peer(root)
    def passed(name,condition,detail):
        record={'name':name,'passed':bool(condition),'detail':detail};results.append(record)
        (root/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(record),flush=True)
        if not condition:raise AssertionError(name)
    try:
        effective=peer.call('config/read',{'includeLayers':False})['config']
        passed('user_feature_settings_override_factory_defaults',all(effective['features'][name] is False for name in ['code_mode','code_mode_only','multi_agent_v2','multi_agent']),effective['features'])
        thread=peer.new()
        nonce='PATCH_'+uuid.uuid4().hex
        items=peer.turn(thread,f'Do not delegate or search. Use the native apply_patch tool directly to create direct-mode.txt with one line containing exactly {nonce}. Then run a native shell command to read it and report the actual contents. Do not create the file through shell redirection or Python.')
        requests=[json.loads(path.read_text()) for path in (root/'adapter-traces').glob('*-request.json')]
        direct_tools=[tool.get('name') for body in requests for tool in body.get('tools',[]) if isinstance(tool,dict)]
        custom_calls=[]
        for path in (root/'home/sessions').rglob('*.jsonl'):
            for line in path.read_text().splitlines():
                event=json.loads(line);payload=event.get('payload',{})
                if event.get('type')=='response_item' and payload.get('type')=='custom_tool_call':custom_calls.append(payload.get('name'))
        passed('native_direct_apply_patch_and_exec', (root/'work/direct-mode.txt').read_text()==nonce+'\n' and nonce in text(items) and 'apply_patch' in custom_calls and not any(name=='exec' for name in direct_tools),{'custom_calls':custom_calls,'published_tools':direct_tools,'answer':text(items)})
        # Per-thread overrides select hybrid mode, while the profile remains direct.
        hybrid=peer.new(config={'features.code_mode':True,'features.code_mode_only':False})
        items=peer.turn(hybrid,'Do not delegate or search. Use Code Mode to call the native exec_command tool with `printf HYBRID_MODE_OK`. Report the actual result.')
        hybrid_payloads=[event['payload'] for path in (root/'home/sessions').rglob('*.jsonl') if hybrid in path.name for line in path.read_text().splitlines() if (event:=json.loads(line)).get('type')=='response_item']
        code_calls={item['call_id'] for item in hybrid_payloads if item.get('type')=='custom_tool_call' and item.get('name')=='exec' and 'exec_command' in item.get('input','')}
        successful_code=[]
        for item in hybrid_payloads:
            if item.get('type')!='custom_tool_call_output' or item.get('call_id') not in code_calls or not isinstance(item.get('output'),list):continue
            for part in item['output']:
                try:value=json.loads(part.get('text',''))
                except ValueError:continue
                if isinstance(value,dict) and value.get('exit_code')==0 and value.get('output')=='HYBRID_MODE_OK':successful_code.append(item['call_id'])
        passed('native_hybrid_code_mode_selection',bool(successful_code) and 'HYBRID_MODE_OK' in text(items) and any(item.get('type')=='commandExecution' and 'HYBRID_MODE_OK' in item.get('aggregatedOutput','') for item in items),{'native_successful_code_mode_call_ids':successful_code,'answer':text(items)})
        # V1 lifecycle uses the original Native tool implementation and fork context.
        v1=peer.new(config={'features.multi_agent':True,'features.multi_agent_v2':False,'features.code_mode':True,'features.code_mode_only':True})
        items=peer.turn(v1,'You are explicitly authorized to create one native child agent for this test. Spawn exactly one child with task: do not use tools or delegate, reply exactly LEGACY_CHILD_417. Wait for its actual completion, close it, then report its returned answer. Do not simulate its answer or create descendants.')
        lifecycle=[item for item in items if item.get('type')=='collabAgentToolCall']
        tools={item.get('tool') for item in lifecycle if item.get('status')=='completed'}
        returned=any(state.get('status')=='completed' and state.get('message')=='LEGACY_CHILD_417' for item in lifecycle for state in item.get('agentsStates',{}).values())
        passed('native_legacy_v1_collaboration_lifecycle','LEGACY_CHILD_417' in text(items) and returned and {'spawnAgent','wait','closeAgent'}<=tools,{'answer':text(items),'native_lifecycle':lifecycle})
        # V2 is exercised in the separate full/none fork lifecycle and real-work suites.
    finally:peer.close();print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
