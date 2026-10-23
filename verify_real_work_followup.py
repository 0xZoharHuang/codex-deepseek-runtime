"""Continue real tasks after explicit external acceptance feedback, preserving failures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
import subprocess
import sys
import time
import uuid
from verify_live import Peer,workspace_path
import verify_real_work as real_work
from verify_real_work import run_test,run_script,tool_python,tool_environment
from install import valid_release,python_entry,_support
from acceptance_assertions import current_browser_evidence,complete_work_patch,OracleGuard,real_work_passed,real_work_input_hashes,native_host_execution,real_work_tools_binding


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def reviewers(root,parent):
    return real_work.reviewer_evidence(root,parent)


def main():
    p=argparse.ArgumentParser();p.add_argument('--entry',type=Path,required=True);p.add_argument('--case',choices=['jinja','httpcore'],required=True);p.add_argument('--fixtures',type=Path,required=True);p.add_argument('--feedback',type=Path,required=True);p.add_argument('--timeout',type=int,default=1200);p.add_argument('--tools-root',type=Path);args=p.parse_args()
    real_work.TOOLS_ROOT=args.tools_root.resolve(strict=True) if args.tools_root else None
    entry=args.entry.resolve();release=entry.parent.parent.parent;metadata=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    if not valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))):raise SystemExit('Installed artifact does not verify')
    root=args.fixtures.resolve()/args.case;previous=json.loads((root/'results.json').read_text(encoding='utf-8'))
    controlled_inputs=real_work_input_hashes(root,args.case)
    tools_before=real_work_tools_binding(root,tool_environment(root))
    if not tools_before['same_bytes']:raise ValueError('Selected real-work tools differ from the pinned original')
    attempt=root/'followup-attempts'/str(uuid.uuid4());attempt.mkdir(parents=True,mode=0o700)
    for path in [root/'results.json',root/'agent.patch',*root.glob('final-*.txt'),*root.glob('final-*.json')]:
        if path.is_file():shutil.copy2(path,attempt/path.name)
    feedback=args.feedback.read_text(encoding='utf-8');(attempt/'feedback.txt').write_text(feedback, encoding='utf-8')
    verifiers=[Path(__file__),Path(__file__).with_name('verify_real_work.py'),Path(__file__).with_name('verify_live.py'),Path(__file__).with_name('acceptance_assertions.py'),Path(__file__).with_name('host_execution_acceptance.py'),Path(__file__).with_name('real_work_imports.py'),Path(__file__).with_name('windows_acceptance_setup.py'),Path(__file__).with_name('qa_workspace.py')]
    (attempt/'artifact.json').write_text(json.dumps({'entry':str(entry),'entry_sha256':sha(entry),'installation':metadata,'previous_report_sha256':sha(attempt/'results.json'),'feedback_sha256':sha(args.feedback),'verifier_sha256':{p.name:sha(p) for p in verifiers}},indent=2), encoding='utf-8')
    parent=previous['thread'];before_reviewers={x['thread']:set(x['completed_turn_ids']) for x in reviewers(root,parent)}
    oracle=root/'oracle';oracle_guard=OracleGuard(oracle)
    def oracle_sources():return {str(p.relative_to(oracle)):sha(p) for p in oracle.rglob('*') if p.is_file() and not any(x in {'__pycache__','.pytest_cache','.git'} for x in p.parts)}
    before=oracle_sources();peer=None;error=None;status=None;turn=None;start=0;started=time.monotonic()
    try:
        oracle_guard.lock();peer=Peer(root,command=[str(entry)],environment={'PYTHONDONTWRITEBYTECODE':'1'})
        peer.call('thread/resume',{'threadId':parent,'cwd':str(workspace_path(root))})
        prompt='Continue this same saved native task and current working tree after independent acceptance feedback. All original restrictions and the original defect scope still apply. Do not access sibling directories or oracle tests, install dependencies, fetch upstream code/patches, change Git history or weaken tests. Use the provisioned tools311/bin/python from the original task. Finish the original fix, actual open/find/click documentation reading, and final verification. You are explicitly authorized to reuse the existing independent reviewer with followup_task, or to spawn one read-only reviewer if none is available; at most one active reviewer, with no descendants. Obtain a fresh review after addressing the feedback. Stop once the original task and checks pass, and report the mechanism and real results.\n\nIndependent feedback:\n'+feedback
        prompt=prompt.replace('tools311/bin/python',str(tool_python(root))+' -B')
        turn,start=peer.start(parent,prompt)
        try:peer.complete(parent,start,timeout=args.timeout)
        except TimeoutError:
            peer.call('turn/interrupt',{'threadId':parent,'turnId':turn});raise
        status=[e['params']['turn']['status'] for e in peer.events[start:] if e.get('method')=='turn/completed' and e.get('params',{}).get('threadId')==parent][-1]
    except Exception as exc:error=str(exc)
    finally:
        try:
            if peer:peer.close()
        finally:oracle_guard.restore()
    unchanged=oracle_sources()==before
    work=root/'work';pythonpath=work/'src' if args.case=='jinja' else work
    validation=[run_test(root,'workspace',['-q','tests'],pythonpath),run_test(root,'upstream-heldout',['-q',str(oracle/'tests')],pythonpath)]
    if args.case=='httpcore':
        validation.append(run_test(root,'independent-cancellation',['-q',str(root/'cancellation_regression.py')],pythonpath))
        validation.extend(run_script(root,'real-tcp-'+str(i),'network_stress.py') for i in range(3))
        validation.append(run_script(root,'tls-socket','tls_socket_regression.py'))
    reviews=reviewers(root,parent);fresh=real_work.fresh_reviewer_completed(reviews,turn,before_reviewers)
    events=[e for path in root.glob('events-*.json') for e in json.loads(path.read_text(encoding='utf-8'))]
    actions=sorted({e['params']['item']['tool'] for e in events if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('type')=='mcpToolCall' and e['params']['item'].get('server')=='codex_web' and e['params']['item'].get('status')=='completed' and not e['params']['item'].get('error')})
    pages=[]
    if (root/'home/web-cache/pages.sqlite').exists():
        with closing(sqlite3.connect(root/'home/web-cache/pages.sqlite')) as db, db:pages=[{k:page.get(k) for k in ['url','retrieved_at','source_sha256']} for (raw,) in db.execute('SELECT payload FROM pages') for page in [json.loads(raw)]]
    diff=subprocess.run(['git','diff','--check'],cwd=work,capture_output=True,text=True)
    report={**previous,'turn_status':status,'error':error,'validation':validation,'diff_check':diff.returncode,'diff_stat':subprocess.check_output(['git','diff','--stat'],cwd=work,text=True),'reviewers':reviews,'fresh_review':fresh,'oracle_guard':{**oracle_guard.evidence,'source_unchanged':unchanged and previous['oracle_guard']['source_unchanged']},'web_research':{'required':True,'pages':pages,'distinct_actual_urls':len({p['url'] for p in pages}),'native_completed_actions':actions},'followup':{'evidence':str(attempt.relative_to(root)),'previous_report_sha256':sha(attempt/'results.json'),'reason':'Native saved-task continuation after explicit independent acceptance feedback','seconds':round(time.monotonic()-started,2),'source_id':metadata['source_id']}}
    report['final_turn_id']=turn
    report['host_platform']=sys.platform
    report['native_host_execution']=native_host_execution(events,tool_python(root),sys.platform)
    current_inputs=real_work_input_hashes(root,args.case)
    report['fixture_inputs']={'before':controlled_inputs,'after':current_inputs,'unchanged':controlled_inputs==current_inputs}
    current=current_browser_evidence(peer.events,parent,start,turn=turn) if peer and status else {'passed':False}
    report['web_research']['current_turn']=current
    tools_after=real_work_tools_binding(root,tool_environment(root))
    report['tool_environment']={'before':tools_before,'after':tools_after,'unchanged':tools_before==tools_after}
    report['passed']=real_work_passed(report) and tools_before==tools_after
    (root/'agent.patch').write_bytes(complete_work_patch(work));(root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'reviewers','final_messages','validation','web_research'}}),flush=True);print('EVIDENCE',attempt,flush=True)
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
