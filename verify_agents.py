"""Focused native V2 regression for task delivery, wait, and follow-up."""

import argparse
import json
from pathlib import Path
import uuid

from verify_live import Peer, prepare, text


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fork',choices=['none','all'],default='none')
    args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4())
    prepare(root)
    peer=Peer(root)
    try:
        thread=peer.new()
        items=peer.turn(thread,f'This is an explicitly authorized isolated native V2 test. Spawn exactly one subagent named arithmetic with fork_turns {args.fork}. Its task: compute 19*23, use no tools or descendants, and reply exactly CHILD-437. Wait for its result. Then send that same child a follow-up task to reply FOLLOWUP-OK, and wait for its completion. Report both answers. Never substitute shell execution for delegation.')
        children=[]
        for rollout in (root/'home/sessions').rglob('*.jsonl'):
            records=[json.loads(line) for line in rollout.read_text().splitlines()]
            meta=records[0]['payload'];source=meta.get('source',{})
            spawn=source.get('subagent',{}).get('thread_spawn',{}) if isinstance(source,dict) else {}
            if spawn.get('parent_thread_id')==thread:
                payloads=[e.get('payload',{}) for e in records if e.get('type')=='response_item']
                answers=[p for p in payloads if p.get('type')=='message' and p.get('role')=='assistant']
                children.append({'id':meta['id'],'provider':meta.get('model_provider'),'called_tools':any(p.get('type') in {'function_call','custom_tool_call'} for p in payloads),'answers':answers})
        answer=text(items)
        requests=[json.loads(path.read_text()) for path in (root/'adapter-traces').glob('*-request.json')]
        parent_requests=[request for request in requests if request.get('prompt_cache_key')==thread]
        deliveries={marker:[item.get('call_id') for request in parent_requests for item in request.get('input',[]) if item.get('type')=='function_call_output' and marker in json.dumps(item)] for marker in ['CHILD-437','FOLLOWUP-OK']}
        completed=[event['params']['item'] for event in peer.events if event.get('method')=='item/completed' and event.get('params',{}).get('threadId')==thread and event['params'].get('item',{}).get('type')=='subAgentActivity' and event['params']['item'].get('kind')=='completed']
        child_completion_ids={item['id'] for item in completed if children and item.get('agentThreadId')==children[0]['id']}
        passed=len(children)==1 and children[0]['provider']=='deepseek_runtime' and not children[0]['called_tools'] and 'CHILD-437' in json.dumps(children[0]['answers']) and 'FOLLOWUP-OK' in json.dumps(children[0]['answers']) and len(child_completion_ids)>=2 and all(deliveries.values()) and bool(answer.strip())
        result={'passed':passed,'scope':'native child task delivery, follow-up, completion and parent context delivery','children':children,'parent_answer':answer,'native_child_completion_ids':sorted(child_completion_ids),'parent_delivery_call_ids':{k:sorted(set(v)) for k,v in deliveries.items()},'model_parent_echo_metric':{'both_values_repeated_in_final_prose':'CHILD-437' in answer and 'FOLLOWUP-OK' in answer,'scope':'Model summarization metric; not the runtime delivery acceptance gate'}}
        (root/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if not passed:raise AssertionError('Native child task/follow-up regression')
    finally:
        peer.close()
        print('EVIDENCE',str(root),flush=True)


if __name__=='__main__':main()
