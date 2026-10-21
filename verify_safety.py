"""Native automatic approval and cancellation of a known disposable process tree."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from verify_live import Peer, prepare, text
from codex_deepseek.processes import identity,matches,terminate
from process_acceptance import python_command,shell_command,fixture_identity,write_process_tree_fixture


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--guardian-v2',action='store_true')
    args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4())
    prepare(root)
    if args.guardian_v2:
        config=root/'home/config.toml'
        config.write_text(config.read_text().replace('[features]\n','[features]\nguardianv2 = {enabled=true,persist_scores=true,review_scope={computer_use_only=false,sandboxed_exec_commands=true}}\n'))
    fixture=root/'work/cancellable.py'
    write_process_tree_fixture(fixture,root/'work/processes.json')
    peer=Peer(root)
    peer_closed=False
    results=[]
    def check(name,condition,detail):
        result={'name':name,'passed':bool(condition),'detail':detail};results.append(result)
        (root/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False),flush=True)
        if not condition:raise AssertionError(name)
    try:
        thread=peer.new(approvalsReviewer='auto_review')
        start=len(peer.events)
        items=peer.turn(thread,'Do not delegate. This is a user-authorized harmless approval fixture. Call exec_command with cmd `printf APPROVAL_NATIVE_OK`, sandbox_permissions `require_escalated`, and justification `isolated native approval fixture`. Do not replace it with another tool or omit the explicit permission request. Report the actual command result.')
        reviews=[e for e in peer.events[start:] if e.get('method')=='item/autoApprovalReview/completed']
        check('native_automatic_approval',bool(reviews) and 'APPROVAL_NATIVE_OK' in text(items),{'reviews':reviews,'answer':text(items)})
        if args.guardian_v2:
            samples=[]
            for path in (root/'adapter-traces').glob('*-request.json'):
                payload=json.loads(path.read_text())
                if str(payload.get('prompt_cache_key','')).startswith('guardian-v2:'):
                    samples.append({'receipt':path.name.removesuffix('-request.json'),'model':payload.get('model'),'provider':payload.get('provider')})
            check('native_guardian_v2_classifier_uses_selected_deepseek',bool(samples) and all(sample['model']=='deepseek/deepseek-v4.1-flash-1017' for sample in samples),samples)
            import sqlite3
            with sqlite3.connect(root/'home/provider-state/adapter.sqlite') as db:
                receipts=[db.execute('SELECT status,upstream_status FROM receipts WHERE id=?',(sample['receipt'],)).fetchone() for sample in samples]
            scores=[json.loads(line) for path in (root/'home/sessions').rglob('*.jsonl') for line in path.read_text().splitlines() if json.loads(line).get('type')=='security_risk_score']
            check('native_guardian_v2_completed_classification_and_applied_score',all(row==('completed',200) for row in receipts) and bool(scores),{'receipts':receipts,'scores':scores})

        thread=peer.new()
        turn,start=peer.start(thread,'Do not delegate. Run '+shell_command([python_command('')[0],'-I',str(fixture)])+' using exec_command with a long yield. Wait for completion.')
        peer.wait(lambda e:e.get('method')=='item/started' and e.get('params',{}).get('threadId')==thread and e.get('params',{}).get('item',{}).get('type')=='commandExecution',start)
        pids_file=root/'work/processes.json'
        end=time.monotonic()+15
        while not pids_file.exists() and time.monotonic()<end:time.sleep(0.05)
        if not pids_file.exists():raise AssertionError('Fixture process did not start')
        markers=json.loads(pids_file.read_text(encoding='utf-8'));assert len(markers)==2
        births=dict(fixture_identity(marker,fixture) for marker in markers);assert len(births)==2
        pids=list(births)
        peer.call('turn/interrupt',{'threadId':thread,'turnId':turn})
        peer.complete(thread,start)
        terminal=[e for e in peer.events[start:] if e.get('method')=='turn/completed' and e.get('params',{}).get('threadId')==thread][-1]
        check('native_turn_interrupt_status',terminal['params']['turn']['status']=='interrupted',terminal['params']['turn']['status'])
        # A yielded unified-exec session is owned by the native process manager,
        # not the sampling turn. Preserve that native lifetime instead of adding
        # an adapter-side kill policy. Closing its owner must clean it up.
        peer.close();peer_closed=True
        end=time.monotonic()+5
        alive=[]
        while time.monotonic()<end:
            alive=[]
            for pid in pids:
                if matches(pid,births[pid]):alive.append({'pid':pid,'birth':births[pid]})
            if not alive:break
            time.sleep(0.05)
        check('native_owner_shutdown_cleans_process_tree',not alive,{'fixture_pids':pids,'still_alive':alive})
    finally:
        if not peer_closed:peer.close()
        for pid,birth in locals().get('births',{}).items():terminate(pid,birth)
        print('EVIDENCE',str(root),flush=True)


if __name__=='__main__':main()
