"""Native autonomous goal continuation and cancellable ephemeral sampling."""
from contextlib import closing
import argparse
import json
from pathlib import Path
import sqlite3
import time
import uuid

from verify_live import Peer,text,qa_sandbox_mode


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sandbox-mode',choices=['workspace-write','danger-full-access'],default=qa_sandbox_mode());args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('sandbox_mode='+json.dumps(args.sandbox_mode)+'\nmodel_reasoning_effort="low"\n', encoding='utf-8')
    peer=Peer(root);results=[]
    def passed(name,condition,detail):
        record={'name':name,'passed':bool(condition),'sandbox_mode':args.sandbox_mode,'detail':detail};results.append(record)
        (root/'results.json').write_text(json.dumps(results,indent=2), encoding='utf-8');print(json.dumps(record),flush=True)
        if not condition:raise AssertionError(name)
    try:
        source=peer.new(sandbox=args.sandbox_mode);items=peer.turn(source,'Do not use tools, delegate or search. Remember SOURCE_CONTEXT_573 and reply exactly READY.')
        completed=[e['params']['turn']['id'] for e in peer.events if e.get('method')=='turn/completed' and e.get('params',{}).get('threadId')==source][-1]
        marker='SAMPLE_CANCEL_'+uuid.uuid4().hex;request_id='sample-'+uuid.uuid4().hex
        start=len(peer.events)
        peer.send({'id':request_id,'method':'thread/sample','params':{'threadId':source,'expectedTurnId':completed,'disableMcp':True,'prompt':marker+' Without tools, write an exhaustive 10000-word explanation of SQLite concurrency and idempotency. Do not stop after an outline.'}})
        receipt=None;end=time.monotonic()+30
        while time.monotonic()<end:
            for path in (root/'adapter-traces').glob('*-request.json'):
                if marker in path.read_text(encoding='utf-8'):receipt=path.name.removesuffix('-request.json');break
            if receipt:break
            time.sleep(.05)
        assert receipt,'Native sample did not reach the provider'
        peer.call('thread/sample/cancel',{'requestId':request_id})
        terminal=peer.wait(lambda e:e.get('id')==request_id and 'method' not in e,start,20)
        state=None;end=time.monotonic()+15
        while time.monotonic()<end:
            with closing(sqlite3.connect(root/'home/provider-state/adapter.sqlite')) as db, db:state=db.execute('SELECT status FROM receipts WHERE id=?',(receipt,)).fetchone()
            if state and state[0]!='running':break
            time.sleep(.05)
        passed('native_sample_cancel_aborts_provider_without_replay','cancel' in str(terminal.get('error',{})).lower() and state==('cancelled',),{'native_response':terminal,'receipt':receipt,'receipt_state':state})
        items=peer.turn(source,'Without tools or delegation, recall the exact source-context marker I asked you to remember. Do not continue the cancelled sample.')
        passed('sample_cancel_preserves_source_thread','SOURCE_CONTEXT_573' in text(items),text(items))
        goal_thread=peer.new(sandbox=args.sandbox_mode);nonce='GOAL_'+uuid.uuid4().hex;objective=f'Complete this two-stage local runtime goal without asking for more input. In the workspace, create goal-first.txt and goal-second.txt, both containing exactly {nonce} with no newline. Verify both contents, then mark this goal complete. Do not delegate or search.'
        peer.call('thread/goal/set',{'threadId':goal_thread,'objective':objective,'status':'active','tokenBudget':120000,'origin':'user'})
        start=len(peer.events)
        peer.turn(goal_thread,f'This initial turn is stage one only: do not delegate or search. Use a native tool to create goal-first.txt containing exactly {nonce} with no newline and read it back. Do not create goal-second.txt during this initial turn. Leave the goal active and finish this turn with STAGE_ONE_FINISHED. The native goal continuation must perform stage two.')
        end=time.monotonic()+180;goal={}
        while time.monotonic()<end:
            goal=peer.call('thread/goal/get',{'threadId':goal_thread}).get('goal') or {}
            if goal.get('status')=='complete':break
            time.sleep(.25)
        turns=[e['params']['turn'] for e in peer.events[start:] if e.get('method')=='turn/completed' and e.get('params',{}).get('threadId')==goal_thread]
        first=root/'work/goal-first.txt';second=root/'work/goal-second.txt'
        # Native continuation can request more sampling inside the same public
        # turn. Require the injected native goal prompt and actual stage-two
        # effects, rather than assuming two API turn/completed notifications.
        continuation_ids=set()
        for path in (root/'adapter-traces').glob('*-request.json'):
            body=json.loads(path.read_text(encoding='utf-8'))
            if body.get('client_metadata',{}).get('thread_id')!=goal_thread:continue
            for item in body.get('input',[]):
                if item.get('type')=='message' and item.get('role')=='user' and any('<codex_internal_context source="goal">' in content.get('text','') for content in item.get('content',[]) if isinstance(content,dict)):
                    continuation_ids.add(item.get('id'))
        passed('native_goal_autonomous_followup_and_completion',goal.get('status')=='complete' and bool(continuation_ids) and first.is_file() and second.is_file() and first.read_text(encoding='utf-8')==second.read_text(encoding='utf-8')==nonce,{'goal':goal,'completed_api_turns':len(turns),'native_goal_continuation_prompts':len(continuation_ids),'turn_statuses':[turn['status'] for turn in turns]})
        peer.call('thread/goal/clear',{'threadId':goal_thread,'origin':'user'})
    finally:peer.close();print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
