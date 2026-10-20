"""Native v2 sibling messaging and nested agent ownership with real file effects."""
import argparse
import json
from pathlib import Path
import uuid
from verify_live import Peer,text


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('model_reasoning_effort="low"\n[agents]\nmax_depth=3\n')
    (root/'work/inputs.json').write_text(json.dumps({'credits':[125,250,375],'currency':'cents','audit_id':uuid.uuid4().hex}))
    nonce='NESTED_'+uuid.uuid4().hex
    peer=Peer(root);result={}
    try:
        thread=peer.new()
        task=f'''This is an explicitly authorized isolated native v2 lifecycle acceptance.
Use exactly three native children in the following ownership tree: parent /root;
its two direct children /root/producer and /root/auditor; and one child owned by
producer named totals. No other children or descendants. These tasks are authorized.
Spawn producer with fork_turns=all. Its job: spawn its one totals child with
fork_turns=none; that totals child must read inputs.json using a real native tool,
compute the total integer cents and write totals.json with total_cents=750 and
marker={nonce!r}. Producer must wait for its actual completion, read totals.json,
and send parent a progress MESSAGE containing PRODUCER_READY and the marker.
Producer then reports its result. Do not spawn totals yourself.
Spawn auditor with fork_turns=none. Its job: wait until totals.json exists, read it
and inputs.json with real tools, independently verify the sum and write audit.json
with passed=true and the marker. Auditor must not spawn children.
After spawning both direct children, send auditor a native MESSAGE stating that
producer owns the nested worker and that the target is integer cents. Wait for
both actual final results. Use followup_task on the same auditor to ask it to
verify both artifacts again and reply FOLLOWUP_VERIFIED plus the marker. Wait for
that actual followup completion. Then read both JSON files yourself, verify them,
and report the marker and FOLLOWUP_VERIFIED. Do not simulate tool outputs or
reassign incoming peer evidence as a fresh parent task. Only use this workspace;
do not search, access App IPC, or modify files outside it.'''
        turn,start=peer.start(thread,task)
        items=peer.complete(thread,start,timeout=600)
        totals=json.loads((root/'work/totals.json').read_text());audit=json.loads((root/'work/audit.json').read_text())
        agents=[];messages=[]
        for path in (root/'home/sessions').rglob('*.jsonl'):
            events=[json.loads(line) for line in path.read_text().splitlines()]
            meta=events[0]['payload'];source=meta.get('source',{})
            spawn=source.get('subagent',{}).get('thread_spawn',{}) if isinstance(source,dict) else {}
            if spawn:
                calls=[event['payload'] for event in events if event.get('type')=='response_item' and event.get('payload',{}).get('type') in {'function_call','custom_tool_call'}]
                complete=[event for event in events if event.get('type')=='event_msg' and event.get('payload',{}).get('type')=='task_complete']
                agents.append({'id':meta['id'],'parent':spawn.get('parent_thread_id'),'path':spawn.get('agent_path'),'tools_used':bool(calls),'completed':bool(complete)})
            messages.extend(event['payload'] for event in events if event.get('type')=='response_item' and event.get('payload',{}).get('type')=='agent_message')
        direct=[agent for agent in agents if agent['parent']==thread]
        nested=[agent for agent in agents if agent['parent'] in {a['id'] for a in direct}]
        answer=text(items)
        passed=len(direct)==2 and len(nested)==1 and len(agents)==3 and all(a['completed'] and a['tools_used'] for a in agents) and totals.get('total_cents')==750 and totals.get('marker')==nonce and audit.get('passed') is True and audit.get('marker')==nonce and 'FOLLOWUP_VERIFIED' in answer and nonce in answer
        result={'passed':passed,'agents':agents,'totals':totals,'audit':audit,'peer_message_count':len(messages),'answer':answer}
        (root/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
        if not passed:raise AssertionError('Nested agent lifecycle/effects did not satisfy the declared ownership tree')
    finally:peer.close();print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
