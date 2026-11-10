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
import verify_real_work as real_work
from verify_real_work import run_test,run_script,tool_python,tool_environment
from acceptance_assertions import OracleGuard,current_browser_evidence,real_work_passed,real_work_input_hashes,real_work_tools_binding,complete_work_patch
import sys


def continue_case(root,entry):
    previous=json.loads((root/'results.json').read_text(encoding='utf-8'))
    if previous.get('error')!='Real development workflow exceeded 30 minutes':
        raise RuntimeError('Only bounded incomplete work can use this continuation protocol')
    tools_before=real_work_tools_binding(root,tool_environment(root))
    controls=real_work.load_work_controls(root,previous['case'],tools_before,initial=False)
    controlled_inputs=real_work_input_hashes(root,previous['case'])
    history=root/'completion-attempts'/str(uuid.uuid4());history.mkdir(parents=True)
    for path in [root/'results.json',root/'agent.patch',*root.glob('final-*.txt'),*root.glob('final-*.json'),*root.glob('final-*.xml'),*root.glob('final-*.bin')]:
        if path.is_file():shutil.copy2(path,history/path.name)
    oracle=root/'oracle';oracle_guard=OracleGuard(oracle)
    before=real_work.source_inventory(oracle)
    before_reviews={row['thread']:set(row['completed_turn_ids']) for row in real_work.reviewer_evidence(root,previous['thread'])}
    peer=None;started=time.monotonic();turn_status=None;error=None;turn=None;start=0
    report={**previous,'error':None,'primary_error':None,'cleanup_errors':[]}
    report['git_preflight']=real_work.job_git_preflight(root)
    try:
        if not report['git_preflight']['passed']:raise RuntimeError('Job Git preflight failed before peer creation')
        oracle_guard.lock()
        peer=Peer(root,command=[str(entry)],environment={'PYTHONDONTWRITEBYTECODE':'1'})
        thread=previous['thread']
        peer.call('thread/resume',{'threadId':thread,'cwd':str(real_work.workspace_path(root))})
        prompt=('The previous client interrupted the turn at its wall-clock budget. Continue this same saved task and existing patch with all original restrictions. Inspect current files and command results before assuming any operation finished. Reuse the existing native reviewer via followup_task for a fresh independent read-only review of the final changes; no descendants. Address concrete relevant findings, run the full original suite with the provisioned Python '+str(tool_python(root))+' -B and the original PYTHONPATH, and finish with the mechanism and actual results. Do not fetch patches, install dependencies, change Git history, weaken tests or expand the original defect scope.')
        if previous.get('web_research',{}).get('required'):
            prompt+=' Use the bundled codex_web browser for actual open/find/click to a different official documentation page in this turn and relate the documentation to the fix.'
        turn,start=peer.start(thread,prompt)
        try:_,interrupted=real_work.complete_real_work_turn(peer,root,thread,start,turn,timeout=600)
        except TimeoutError:
            peer.call('turn/interrupt',{'threadId':thread,'turnId':turn});raise
        if interrupted is not None:
            report['operator_interrupt']=interrupted
            turn_status='interrupted' if interrupted['native_confirmed'] else None
            raise RuntimeError('Operator requested native turn interruption' if interrupted['native_confirmed'] else 'Operator native interruption failed')
        terminal=[event for event in peer.events[start:] if event.get('method')=='turn/completed' and event.get('params',{}).get('threadId')==thread][-1]
        turn_status=terminal['params']['turn']['status']
    except Exception as exc:
        error=str(exc);report['error']=error
        report['primary_error']={'type':type(exc).__name__,'error':str(exc)}
    finally:
        report['seconds']=previous['seconds']+round(time.monotonic()-started,2)
        cleanup_ok=real_work.cleanup_real_work(report,peer,oracle_guard,root)
    if not cleanup_ok:return report
    real_work.validation_checkpoint(root,report)
    if not report['git_preflight']['passed'] or report.get('operator_interrupt'):
        report['turn_status']=turn_status
        report['validation_status']='not_started_after_preflight_or_operator_stop'
        (root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report
    unchanged=real_work.source_inventory(oracle)==before
    work=Path(real_work.workspace_path(root));pp=work/'src' if previous['case']=='jinja' else work
    validation=[run_test(root,'workspace',['-q','tests'],pp),run_test(root,'upstream-heldout',['-q',str(oracle/'tests')],pp)]
    if previous['case']=='httpcore':
        validation.append(run_test(root,'independent-cancellation',['-q',str(root/'cancellation_regression.py')],pp))
        validation.extend(run_script(root,'real-tcp-'+str(index),'network_stress.py') for index in range(3))
        validation.append(run_script(root,'tls-socket','tls_socket_regression.py'))
    reviewers=real_work.reviewer_evidence(root,previous['thread'])
    check=subprocess.run(['git','diff','--check'],cwd=work,capture_output=True,text=True)
    report={**report,'error':error,'turn_status':turn_status,'validation':validation,'diff_check':check.returncode,
        'reviewers':reviewers,'fresh_review':real_work.fresh_reviewer_completed(reviewers,turn,before_reviews),
        'oracle_guard':{**oracle_guard.evidence,'source_unchanged':unchanged and previous['oracle_guard']['source_unchanged']},
        'seconds':previous['seconds']+round(time.monotonic()-started,2),'final_turn_id':turn,
        'continuation':{'initial_report':str((history/'results.json').relative_to(root)),
            'initial_report_sha256':digest(history/'results.json'),'reason':'native cold continuation after a bounded model-workflow timeout',
            'original_effort_preserved':True,'oracle_sources_unchanged':unchanged,'seconds':round(time.monotonic()-started,2)}}
    report['web_research']={**previous.get('web_research',{}),'current_turn':current_browser_evidence(peer.events if peer else [],previous['thread'],start,turn=turn)}
    events=[event for path in root.glob('events-*.json') for event in json.loads(path.read_text(encoding='utf-8'))]
    report['host_platform']=sys.platform
    report['native_host_execution']=real_work.observed_host_execution(events,root,controls,real_work.qualified_entry_release(entry))
    current_inputs=real_work_input_hashes(root,previous['case'])
    report['fixture_inputs']={'before':controlled_inputs,'after':current_inputs,'unchanged':controlled_inputs==current_inputs}
    tools_after=real_work_tools_binding(root,tool_environment(root))
    report['tool_environment']={'before':tools_before,'after':tools_after,'unchanged':tools_before==tools_after}
    controls_after=real_work.load_work_controls(root,previous['case'],tools_after,initial=False)
    report['qualification_controls']={'path':controls['control_path'],'sha256':controls['control_sha256'],
        'baseline_sha256':{suite:baseline['sha256'] for suite,baseline in controls['pytest_baselines'].items()}}
    report['validation_status']='completed'
    report['passed']=real_work_passed(report,pytest_baselines=controls['pytest_baselines']) and tools_before==tools_after and controls==controls_after
    (root/'agent.patch').write_bytes(complete_work_patch(work))
    (root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--fixtures',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--controls',type=Path,required=True);parser.add_argument('--tools-root',type=Path);args=parser.parse_args()
    real_work.CONTROLS=args.controls.resolve(strict=True)
    real_work.TOOLS_ROOT=args.tools_root.resolve(strict=True) if args.tools_root else None
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
