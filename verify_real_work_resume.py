"""Complete bounded real-work attempts through native saved-session continuation.

Preserves the full original report and test output, then verifies the completed
work independently. Only failed wall-clock-budget attempts may be continued.
"""
from contextlib import closing
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

from install import valid_release,_support
from run_acceptance import digest,SOURCE
from verify_live import Peer
from verify_real_work import run_test,run_script


def continue_case(root,entry):
    previous=json.loads((root/'results.json').read_text())
    if previous['passed']:return previous
    if previous.get('error')!='Real development workflow exceeded 30 minutes':
        raise RuntimeError('Only bounded incomplete work can use this continuation protocol')
    history=root/'completion-attempts'/str(uuid.uuid4());history.mkdir(parents=True)
    for path in [root/'results.json',root/'agent.patch',*root.glob('final-*.txt'),*root.glob('final-*.json')]:
        if path.is_file():shutil.copy2(path,history/path.name)
    config=root/'home/config.toml';config.write_text(config.read_text().replace('model_reasoning_effort = "high"','model_reasoning_effort = "low"'))
    oracle=root/'oracle';mode=oracle.stat().st_mode&0o777
    def sources():
        return {str(p.relative_to(oracle)):digest(p) for p in oracle.rglob('*') if p.is_file() and not any(x in {'__pycache__','.pytest_cache','.git'} for x in p.parts)}
    before=sources();peer=None;started=time.monotonic();turn_status=None
    try:
        oracle.chmod(0)
        peer=Peer(root,command=[str(entry)])
        thread=previous['thread']
        peer.call('thread/resume',{'threadId':thread,'config':{'model_reasoning_effort':'low'}})
        turn,start=peer.start(thread,'The previous client interrupted the turn at its wall-clock budget; continue this same task and existing patch. Keep all original restrictions. The full workspace suite already passed, but the task must now converge. Inspect the current diff, use the existing reviewer for a concise final review of the current changes (followup_task triggers an idle reviewer; send_message alone does not), address concrete relevant findings, run the full suite with the explicitly provisioned tools311/bin/python and PYTHONPATH from the original task, and finish with the mechanism and actual results. Do not expand into proving all compiler behavior, add unrelated features, fetch code, install dependencies, access sibling directories or oracles, or weaken tests. Use low reasoning effort for this bounded finalization phase. Stop after the original defect and regression scope are verified.')
        try:peer.complete(thread,start,timeout=600)
        except TimeoutError:
            peer.call('turn/interrupt',{'threadId':thread,'turnId':turn});raise
        terminal=[e for e in peer.events[start:] if e.get('method')=='turn/completed' and e.get('params',{}).get('threadId')==thread][-1]
        turn_status=terminal['params']['turn']['status']
    finally:
        try:
            if peer:peer.close()
        finally:oracle.chmod(mode)
    unchanged=sources()==before
    work=root/'work';pp=work/'src' if previous['case']=='jinja' else work
    validation=[run_test(root,'workspace',['-q','tests'],pp),run_test(root,'upstream-heldout',['-q',str(oracle/'tests')],pp)]
    if previous['case']=='httpcore':
        validation.append(run_test(root,'independent-cancellation',['-q',str(root/'cancellation_regression.py')],pp))
        validation.extend([run_script(root,'real-tcp','network_stress.py'),run_script(root,'tls-socket','tls_socket_regression.py')])
    reviewers=[]
    with closing(sqlite3.connect(next((root/'home').glob('state_*.sqlite')))) as db, db:
        for child,source,path,agent in db.execute('SELECT id,source,rollout_path,agent_path FROM threads WHERE agent_path IS NOT NULL'):
            origin=json.loads(source) if source.startswith('{') else {}
            if origin.get('subagent',{}).get('thread_spawn',{}).get('parent_thread_id')!=previous['thread']:continue
            if not _support.comparison_path(path).is_relative_to(_support.comparison_path(root/'home')):continue
            complete=[v['payload'] for line in Path(path).read_text().splitlines() if (v:=json.loads(line)).get('type')=='event_msg' and v.get('payload',{}).get('type')=='task_complete']
            reviewers.append({'thread':child,'agent_path':agent,'completed':bool(complete),'completed_turns':len(complete),'final_message':complete[-1].get('last_agent_message') if complete else None})
    check=subprocess.run(['git','diff','--check'],cwd=work,capture_output=True,text=True)
    report={**previous,'error':None,'turn_status':turn_status,'validation':validation,'diff_check':check.returncode,'reviewers':reviewers,'seconds':previous['seconds']+round(time.monotonic()-started,2),'continuation':{'initial_report':str((history/'results.json').relative_to(root)),'initial_report_sha256':digest(history/'results.json'),'reason':'native cold continuation after a bounded model-workflow timeout','model_reasoning_effort':'low','oracle_sources_unchanged':unchanged,'seconds':round(time.monotonic()-started,2)}}
    report['passed']=turn_status=='completed' and unchanged and previous['oracle_guard']['source_unchanged'] and check.returncode==0 and all(row['exit']==0 for row in validation) and any(row['completed'] for row in reviewers)
    (root/'agent.patch').write_text(subprocess.run(['git','diff','--binary'],cwd=work,capture_output=True,text=True).stdout)
    (root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--fixtures',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    entry=args.entry.resolve();release=entry.parent.parent.parent;metadata=json.loads((release/'installation.json').read_text());native=json.loads((release/'native-runtime.json').read_text())
    if not valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native):raise SystemExit('Installed artifact failed integrity validation')
    hashes={str(p.relative_to(SOURCE)):digest(p) for p in [*sorted(SOURCE.glob('*.py')),*sorted((SOURCE/'tests').rglob('*.py')),*sorted((SOURCE/'src').rglob('*.py'))]}
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True,mode=0o700)
    artifact={'entry':str(entry),'entry_sha256':digest(entry),'release':str(release),'installation':metadata,'native_manifest':native,'selected_gates':['real-work'],'verifier_source_sha256':hashes,'started':time.time(),'protocol':'saved native workflow continuation with original failure retained'}
    (root/'artifact.json').write_text(json.dumps(artifact,indent=2));started=time.monotonic();success=False
    try:
        reports=[continue_case(args.fixtures.resolve()/name,entry) for name in ['jinja','httpcore']]
        (args.fixtures.resolve()/'results.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2));success=all(report['passed'] for report in reports)
    finally:
        stable=all(digest(SOURCE/name)==value for name,value in hashes.items())
        row={'gate':'real-work','exit':0 if success else 1,'seconds':round(time.monotonic()-started,2),'log':'real-work.log'}
        (root/'real-work.log').write_text('EVIDENCE '+str(args.fixtures.resolve())+'\n')
        verdict={'passed':success and stable and digest(entry)==artifact['entry_sha256'],'verifier_source_unchanged':stable,'entry_unchanged':digest(entry)==artifact['entry_sha256'],'all_gates_selected':False,'gates':[row]}
        (root/'verdict.json').write_text(json.dumps(verdict,indent=2));print('EVIDENCE',root,flush=True)
    if not verdict['passed']:raise SystemExit(1)


if __name__=='__main__':main()
