"""Continue real tasks after explicit external acceptance feedback, preserving failures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import uuid
from verify_live import Peer
from verify_real_work import run_test,run_script
from install import valid_release


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def reviewers(root,parent):
    rows=[]
    with sqlite3.connect(next((root/'home').glob('state_*.sqlite'))) as db:
        for child,source,path,agent in db.execute('SELECT id,source,rollout_path,agent_path FROM threads WHERE agent_path IS NOT NULL'):
            origin=json.loads(source) if source.startswith('{') else {}
            if origin.get('subagent',{}).get('thread_spawn',{}).get('parent_thread_id')!=parent:continue
            p=Path(path)
            if not p.is_file() or not p.resolve().is_relative_to((root/'home').resolve()):continue
            complete=[x['payload'] for line in p.read_text().splitlines() if (x:=json.loads(line)).get('type')=='event_msg' and x.get('payload',{}).get('type')=='task_complete']
            rows.append({'thread':child,'agent_path':agent,'completed_turns':len(complete),'last_message':complete[-1].get('last_agent_message') if complete else None})
    return rows


def main():
    p=argparse.ArgumentParser();p.add_argument('--entry',type=Path,required=True);p.add_argument('--case',choices=['jinja','httpcore'],required=True);p.add_argument('--fixtures',type=Path,required=True);p.add_argument('--feedback',type=Path,required=True);p.add_argument('--timeout',type=int,default=1200);args=p.parse_args()
    entry=args.entry.resolve();release=entry.parent.parent.parent;metadata=json.loads((release/'installation.json').read_text())
    if not valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],json.loads((release/'native-runtime.json').read_text())):raise SystemExit('Installed artifact does not verify')
    root=args.fixtures.resolve()/args.case;previous=json.loads((root/'results.json').read_text())
    attempt=root/'followup-attempts'/str(uuid.uuid4());attempt.mkdir(parents=True,mode=0o700)
    for path in [root/'results.json',root/'agent.patch',*root.glob('final-*.txt')]:
        if path.is_file():shutil.copy2(path,attempt/path.name)
    feedback=args.feedback.read_text();(attempt/'feedback.txt').write_text(feedback)
    (attempt/'artifact.json').write_text(json.dumps({'entry':str(entry),'entry_sha256':sha(entry),'installation':metadata,'previous_report_sha256':sha(attempt/'results.json'),'feedback_sha256':sha(args.feedback)},indent=2))
    parent=previous['thread'];before_reviewers={x['thread']:x['completed_turns'] for x in reviewers(root,parent)}
    oracle=root/'oracle';mode=oracle.stat().st_mode&0o777
    def oracle_sources():return {str(p.relative_to(oracle)):sha(p) for p in oracle.rglob('*') if p.is_file() and not any(x in {'__pycache__','.pytest_cache','.git'} for x in p.parts)}
    before=oracle_sources();peer=None;error=None;status=None;started=time.monotonic()
    try:
        oracle.chmod(0);peer=Peer(root,command=[str(entry)])
        peer.call('thread/resume',{'threadId':parent})
        prompt='Continue this same saved native task and current working tree after independent acceptance feedback. All original restrictions and the original defect scope still apply. Do not access sibling directories or oracle tests, install dependencies, fetch upstream code/patches, change Git history or weaken tests. Use the provisioned tools311/bin/python from the original task. Finish the original fix, actual open/find/click documentation reading, and final verification. You are explicitly authorized to reuse the existing independent reviewer with followup_task, or to spawn one read-only reviewer if none is available; at most one active reviewer, with no descendants. Obtain a fresh review after addressing the feedback. Stop once the original task and checks pass, and report the mechanism and real results.\n\nIndependent feedback:\n'+feedback
        turn,start=peer.start(parent,prompt)
        try:peer.complete(parent,start,timeout=args.timeout)
        except TimeoutError:
            peer.call('turn/interrupt',{'threadId':parent,'turnId':turn});raise
        status=[e['params']['turn']['status'] for e in peer.events[start:] if e.get('method')=='turn/completed' and e.get('params',{}).get('threadId')==parent][-1]
    except Exception as exc:error=str(exc)
    finally:
        try:
            if peer:peer.close()
        finally:oracle.chmod(mode)
    unchanged=oracle_sources()==before
    work=root/'work';pythonpath=work/'src' if args.case=='jinja' else work
    validation=[run_test(root,'workspace',['-q','tests'],pythonpath),run_test(root,'upstream-heldout',['-q',str(oracle/'tests')],pythonpath)]
    if args.case=='httpcore':
        validation.append(run_test(root,'independent-cancellation',['-q',str(root/'cancellation_regression.py')],pythonpath))
        validation.extend(run_script(root,'real-tcp-'+str(i),'network_stress.py') for i in range(3))
        validation.append(run_script(root,'tls-socket','tls_socket_regression.py'))
    reviews=reviewers(root,parent);fresh=any(x['completed_turns']>before_reviewers.get(x['thread'],0) for x in reviews)
    events=[e for path in root.glob('events-*.json') for e in json.loads(path.read_text())]
    actions=sorted({e['params']['item']['tool'] for e in events if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('type')=='mcpToolCall' and e['params']['item'].get('server')=='codex_web' and e['params']['item'].get('status')=='completed' and not e['params']['item'].get('error')})
    pages=[]
    if (root/'home/web-cache/pages.sqlite').exists():
        with sqlite3.connect(root/'home/web-cache/pages.sqlite') as db:pages=[{k:page.get(k) for k in ['url','retrieved_at','source_sha256']} for (raw,) in db.execute('SELECT payload FROM pages') for page in [json.loads(raw)]]
    diff=subprocess.run(['git','diff','--check'],cwd=work,capture_output=True,text=True)
    report={**previous,'turn_status':status,'error':error,'validation':validation,'diff_check':diff.returncode,'diff_stat':subprocess.check_output(['git','diff','--stat'],cwd=work,text=True),'reviewers':reviews,'fresh_review':fresh,'oracle_guard':{'source_unchanged':unchanged and previous['oracle_guard']['source_unchanged']},'web_research':{'required':True,'pages':pages,'distinct_actual_urls':len({p['url'] for p in pages}),'native_completed_actions':actions},'followup':{'evidence':str(attempt.relative_to(root)),'previous_report_sha256':sha(attempt/'results.json'),'reason':'Native saved-task continuation after explicit independent acceptance feedback','seconds':round(time.monotonic()-started,2),'source_id':metadata['source_id']}}
    report['passed']=status=='completed' and not error and unchanged and fresh and diff.returncode==0 and all(x['exit']==0 for x in validation) and {'open','find','click'}<=set(actions) and len({p['url'] for p in pages})>=2
    (root/'agent.patch').write_text(subprocess.check_output(['git','diff','--binary'],cwd=work,text=True));(root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in {'reviewers','final_messages','validation','web_research'}}),flush=True);print('EVIDENCE',attempt,flush=True)
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
