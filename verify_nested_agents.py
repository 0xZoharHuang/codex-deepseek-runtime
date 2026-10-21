"""Native v2 sibling messaging and nested agent ownership with real file effects."""
import argparse
import json
from pathlib import Path
import uuid
from verify_live import Peer,text


def delivered_peer_messages(requests,recipient,sender,kind,markers):
    header=f'Message Type: {kind}\nTask name: {recipient}\nSender: {sender}\nPayload:\n'
    found=set()
    for request in requests:
        for item in request.get('input',[]):
            if item.get('type')!='function_call_output' or not str(item.get('call_id','')).startswith('peer_'):continue
            output=item.get('output')
            if not isinstance(output,list):continue
            for part in output:
                value=part.get('text','')
                if part.get('type')=='input_text' and value.startswith(header) and all(marker in value[len(header):] for marker in markers):found.add(item['call_id'])
    return sorted(found)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sandbox-mode',choices=['workspace-write','danger-full-access'],default='workspace-write');args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('sandbox_mode='+json.dumps(args.sandbox_mode)+'\nmodel_reasoning_effort="low"\n[agents]\nmax_depth=3\n', encoding='utf-8')
    (root/'work/inputs.json').write_text(json.dumps({'credits':[125,250,375],'currency':'cents','audit_id':uuid.uuid4().hex}), encoding='utf-8')
    nonce='NESTED_'+uuid.uuid4().hex
    peer=Peer(root);result={}
    try:
        thread=peer.new(sandbox=args.sandbox_mode)
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
        # JSON's stdlib byte decoder recognizes the BOM emitted by Windows
        # PowerShell's UTF-8 writer without changing the actual artifact bytes.
        totals=json.loads((root/'work/totals.json').read_bytes());audit=json.loads((root/'work/audit.json').read_bytes())
        agents=[];messages=[]
        for path in (root/'home/sessions').rglob('*.jsonl'):
            events=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            meta=events[0]['payload'];source=meta.get('source',{})
            spawn=source.get('subagent',{}).get('thread_spawn',{}) if isinstance(source,dict) else {}
            if spawn:
                calls=[event['payload'] for event in events if event.get('type')=='response_item' and event.get('payload',{}).get('type') in {'function_call','custom_tool_call'}]
                complete=[event for event in events if event.get('type')=='event_msg' and event.get('payload',{}).get('type')=='task_complete']
                agents.append({'id':meta['id'],'parent':spawn.get('parent_thread_id'),'path':spawn.get('agent_path'),'tools_used':bool(calls),'completed':bool(complete),'completed_turn_ids':sorted({event['payload']['turn_id'] for event in complete if event['payload'].get('turn_id')})})
            messages.extend(event['payload'] for event in events if event.get('type')=='response_item' and event.get('payload',{}).get('type')=='agent_message')
        direct=[agent for agent in agents if agent['parent']==thread]
        nested=[agent for agent in agents if agent['parent'] in {a['id'] for a in direct}]
        by_path={agent['path']:agent for agent in agents}
        requests=[json.loads(path.read_text(encoding='utf-8')) for path in (root/'adapter-traces').glob('*-request.json')]
        # Native siblings share the root prompt-cache key. It is a cache hint,
        # not the actual request owner; use the native client thread metadata.
        parent_requests=[request for request in requests if request.get('client_metadata',{}).get('thread_id')==thread]
        auditor=by_path.get('/root/auditor',{})
        auditor_requests=[request for request in requests if request.get('client_metadata',{}).get('thread_id')==auditor.get('id')]
        deliveries={
            'producer_progress':delivered_peer_messages(parent_requests,'/root','/root/producer','MESSAGE',['PRODUCER_READY',nonce]),
            'parent_to_auditor':delivered_peer_messages(auditor_requests,'/root/auditor','/root','MESSAGE',['producer','integer']),
            'auditor_followup':delivered_peer_messages(parent_requests,'/root','/root/auditor','FINAL_ANSWER',['FOLLOWUP_VERIFIED',nonce]),
        }
        answer=text(items)
        passed=len(direct)==2 and len(nested)==1 and len(agents)==3 and set(by_path)=={'/root/producer','/root/auditor','/root/producer/totals'} and all(a['completed'] and a['tools_used'] for a in agents) and len(auditor.get('completed_turn_ids',[]))>=2 and all(deliveries.values()) and totals.get('total_cents')==750 and totals.get('marker')==nonce and audit.get('passed') is True and audit.get('marker')==nonce and 'FOLLOWUP_VERIFIED' in answer and nonce in answer
        result={'passed':passed,'sandbox_mode':args.sandbox_mode,'agents':agents,'totals':totals,'audit':audit,'peer_message_count':len(messages),'verified_peer_deliveries':deliveries,'answer':answer}
        (root/'results.json').write_text(json.dumps(result,indent=2), encoding='utf-8');print(json.dumps(result),flush=True)
        if not passed:raise AssertionError('Nested agent lifecycle/effects did not satisfy the declared ownership tree')
    finally:peer.close();print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
