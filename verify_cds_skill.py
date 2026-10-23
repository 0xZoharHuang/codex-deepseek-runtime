"""Bounded CDS skill acceptance. --model uses paid API calls on an owned fixture.

Exercises native discovery, explicit skill input, real command effects and exact
session resume. It is not a replacement for the runtime qualification suite.
"""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

from verify_live import Peer,prepare_workspace
sys.path.insert(0, str(Path(__file__).resolve().parent/'src'))
from codex_deepseek.processes import matches, terminate


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture_skill_selection(response, skill_file):
    """Native names may repeat; qualify only the exact owned skill file."""
    expected = Path(skill_file).resolve(strict=True)
    named = [row for group in response['data'] for row in group['skills']
             if row.get('name') == 'cds']
    selected = [row for row in named if Path(row['path']).resolve() == expected]
    assert len(selected) == 1, 'Require one exact fixture skill path'
    assert selected[0].get('enabled') is True, 'Exact fixture skill is disabled'
    others = [{'path':row['path'], 'canonical_path':str(Path(row['path']).resolve()),
               'scope':row.get('scope'), 'plugin_id':row.get('pluginId'),
               'enabled':row.get('enabled')}
              for row in named if row is not selected[0]]
    return selected[0], others


def final_checks(report, *, source_unchanged, skill_unchanged, artifact_unchanged, cleanup_errors):
    """A successful model turn cannot conceal failed cleanup or changed inputs."""
    counts = report.get('receipt_status_counts', {})
    count = report.get('model_receipts', 0)
    model_ok = (count > 0 and set(counts) == {'completed'} and counts['completed'] == count
                if report['model_requested'] else count == 0)
    return bool(report.get('passed') is True and source_unchanged and skill_unchanged
                and artifact_unchanged and not cleanup_errors and model_ok
                and report.get('native_process_stopped') is True
                and report.get('provider_service_stopped') is True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entry', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--skill', default=str(Path(__file__).resolve().parent/'skills/cds'))
    parser.add_argument('--model', action='store_true')
    parser.add_argument('--windows-backend', choices=('unelevated', 'elevated'), default='unelevated')
    args = parser.parse_args()
    from install import console_entry, source_identity, valid_release
    entry = Path(args.entry).resolve(strict=True)
    release = entry.parent.parent.parent
    metadata = json.loads((release/'installation.json').read_text(encoding='utf-8'))
    native = json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
    assert entry == console_entry(release/'venv'), 'Require an actual immutable installed entry'
    assert valid_release(release, metadata['adapter_source_sha256'], metadata['model_catalog_sha256'], native)
    source_id, source_map = source_identity()
    assert (source_id, source_map) == (metadata['source_id'], metadata['source_sha256']), 'Product source differs from installation'
    source = Path(__file__).resolve().parent
    verifier_files = ['verify_cds_skill.py','verify_live.py','windows_acceptance_setup.py',
                      'qa_workspace.py','install.py','src/codex_deepseek/processes.py']
    artifact = {'entry':str(entry), 'entry_sha256':sha(entry), 'installation':metadata,
                'native_manifest':native, 'verifier_sha256':{name:sha(source/name) for name in verifier_files}}
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root/'home').mkdir(); work=root/'work with spaces'; work.mkdir(); work=prepare_workspace(root,'work with spaces')
    skill = root/'home/skills/cds'
    shutil.copytree(args.skill, skill, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (root/'home/config.toml').write_text('sandbox_mode="workspace-write"\napproval_policy="never"\nweb_search="disabled"\nmodel_reasoning_effort="medium"\n[windows]\nsandbox="{windows_backend}"\n[features]\napps=false\nplugins=false\nmemories=false\nmulti_agent=false\napi_key_model_discovery=false\n'.replace('{windows_backend}', args.windows_backend), encoding='utf-8')
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith(('CODEX_', 'OPENAI_')): environment.pop(key)
    environment.update(CODEX_DEEPSEEK_HOME=str(root/'home'), CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS='2')
    if not args.model: environment['OPENROUTER_API_KEY']='NONFUNCTIONAL_SKILL_DISCOVERY_FIXTURE'
    report = {'passed':False, 'platform':sys.platform, 'model_requested':args.model,
              'windows_backend':args.windows_backend,
              'entry':str(Path(args.entry).resolve()), 'skill_hashes':{p.relative_to(skill).as_posix():sha(p) for p in skill.rglob('*') if p.is_file()},
              'test_scope':'Ordinary CLI skill-name resolution, exact fixture discovery and candidate typed skill/path continuation. Not native subagent acceptance.'}
    (root/'artifact.json').write_text(json.dumps(artifact,indent=2)+'\n',encoding='utf-8')
    report['artifact_sha256'] = sha(root/'artifact.json')
    peer=None
    failure=None
    try:
        # The CLI case uses $cds as an ordinary user would. Only owned test data.
        if args.model:
            from windows_acceptance_setup import prepare_terminal_fixture
            prepare_terminal_fixture(root, args.entry)
            inputs=work/'Input 数据'; inputs.mkdir()
            (inputs/'ledger.csv').write_text('id,name,amount\nA001,青松,10.20\nA002,Maple,-1.10\nA001,青松,10.20\nA003,青松,0.30\nA004,Maple,2.00\n', encoding='utf-8-sig')
            prompt='''Use $cds. You are already running inside CDS; use the current native tools. Only work in the given cwd; no networking, credentials, delegation or nested CDS. Implement summarize.py using Python stdlib: read a UTF-8/BOM CSV path as positional argument; deduplicate exact repeated IDs, reject conflicting rows with the same ID with nonzero exit; sum amounts exactly with Decimal; print JSON containing count (unique IDs), total (two-decimal string), and by_name (two-decimal totals). Preserve Unicode names. Run it on "Input 数据/ledger.csv" and save the result in result.json. Write a brief usage note. Use this Python executable for execution: '''+sys.executable
            argv=[args.entry,'-s','workspace-write','exec','--skip-git-repo-check','--json','-C',str(work),'-']
            with (root/'exec.jsonl').open('w',encoding='utf-8') as stdout,(root/'exec.stderr').open('w',encoding='utf-8') as stderr:
                result=subprocess.run(argv,input=prompt,text=True,encoding='utf-8',env=environment,cwd=work,stdout=stdout,stderr=stderr,timeout=600)
            assert result.returncode==0, 'Direct exec failed; original evidence retained'
            events=[json.loads(line) for line in (root/'exec.jsonl').read_text(encoding='utf-8').splitlines()]
            thread=next(e['thread_id'] for e in events if e['type']=='thread.started')
            assert events[-1]['type']=='turn.completed'
            expected={'count':4,'total':'11.40','by_name':{'青松':'10.50','Maple':'0.90'}}
            assert json.loads((work/'result.json').read_text(encoding='utf-8-sig'))==expected
            actual=subprocess.run([sys.executable,str(work/'summarize.py'),str(inputs/'ledger.csv')],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=20)
            assert actual.returncode==0 and json.loads(actual.stdout)==expected
            conflict=inputs/'conflict.csv'; conflict.write_text('id,name,amount\nX,A,1.00\nX,A,2.00\n',encoding='utf-8')
            invalid=subprocess.run([sys.executable,str(work/'summarize.py'),str(conflict)],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=20)
            assert invalid.returncode!=0, 'Conflicting duplicate silently accepted'
            assert any(e.get('item',{}).get('type')=='command_execution' and e['type']=='item.completed' for e in events), 'No actual native command execution'
            report['direct_exec']={'passed':True,'thread_id':thread,'process_exit':result.returncode,'actual_output':expected,'conflicting_duplicate_rejected':True,'native_command_events':sum(e.get('item',{}).get('type')=='command_execution' and e['type']=='item.completed' for e in events)}
        peer=Peer(root,command=[args.entry],environment=environment,server_args=['-c','mcp_servers={}','app-server','--strict-config'])
        response=peer.call('skills/list',{'cwds':[str(work)],'forceReload':True})
        selected, same_name_others=fixture_skill_selection(response,skill/'SKILL.md')
        assert not any(group.get('errors') for group in response['data'])
        report['native_discovery']={'passed':True,'name':selected['name'],'enabled':selected['enabled'],'scope':selected['scope'],
                                    'path':str(Path(selected['path']).resolve()),'same_name_others':same_name_others}
        if args.model:
            resumed=peer.call('thread/resume',{'threadId':thread,'cwd':str(work),'sandbox':'workspace-write','approvalPolicy':'never'})
            assert resumed['thread']['id']==thread
            start=len(peer.events)
            turn=peer.call('turn/start',{'threadId':thread,'input':[{'type':'skill','name':'cds','path':str(skill/'SKILL.md')},{'type':'text','text':'Continue the existing CSV utility in the current session. Add optional --summary-only: output only count and total, retain full default output and conflict rejection. Run both modes and save summary.json for the existing ledger. Use the same Python executable supplied earlier. Do not delegate or launch another CDS. Only modify the current work directory.'}]})['turn']['id']
            items=peer.complete(thread,start,timeout=600,turn_id=turn)
            assert json.loads((work/'summary.json').read_text(encoding='utf-8-sig'))=={'count':4,'total':'11.40'}
            default=subprocess.run([sys.executable,str(work/'summarize.py'),str(inputs/'ledger.csv')],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=20)
            concise=subprocess.run([sys.executable,str(work/'summarize.py'),str(inputs/'ledger.csv'),'--summary-only'],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=20)
            rejected=subprocess.run([sys.executable,str(work/'summarize.py'),str(conflict)],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=20)
            assert default.returncode==0 and json.loads(default.stdout)==expected
            assert concise.returncode==0 and json.loads(concise.stdout)=={'count':4,'total':'11.40'}
            assert rejected.returncode!=0
            report['external_controller']={'passed':True,'same_thread_id':True,'turn_id':turn,'explicit_native_skill_input':True,'default_and_summary_modes_verified':True,'conflict_rejection_preserved':True,'native_items':len(items)}
        report['passed']=True
    except BaseException as error:
        failure=error
        report.update(passed=False,error_type=type(error).__name__)
        raise
    finally:
        cleanup_errors=[]
        if peer:
            try:peer.close()
            except Exception as error:cleanup_errors.append({'stage':'native_close','error_type':type(error).__name__})
        descriptor=root/'home/provider-state/gateway.json'
        report['provider_service_stopped']=False
        try:
            if descriptor.exists():
                owner=json.loads(descriptor.read_text(encoding='utf-8')); deadline=time.monotonic()+10
                while matches(owner['pid'],owner['birth']) and time.monotonic()<deadline:time.sleep(.1)
                if matches(owner['pid'],owner['birth']):terminate(owner['pid'],owner['birth'])
                report['provider_service_stopped']=not matches(owner['pid'],owner['birth'])
            else:
                report['provider_service_stopped']=True
        except Exception as error:cleanup_errors.append({'stage':'provider_close','error_type':type(error).__name__})
        dbpath=root/'home/provider-state/adapter.sqlite'
        report.update(receipt_status_counts={},model_receipts=0)
        try:
            if dbpath.exists():
                with contextlib.closing(sqlite3.connect('file:'+dbpath.as_posix()+'?mode=ro',uri=True)) as db:
                    report['receipt_status_counts']=dict(db.execute('select status,count(*) from receipts group by status'))
                    report['model_receipts']=sum(report['receipt_status_counts'].values())
        except Exception as error:cleanup_errors.append({'stage':'receipt_read','error_type':type(error).__name__})
        report['native_process_stopped']=peer is None or peer.process.poll() is not None
        report['cleanup_errors']=cleanup_errors
        try:
            report['verifier_unchanged']=all(sha(source/name)==value for name,value in artifact['verifier_sha256'].items())
            report['skill_unchanged']={p.relative_to(skill).as_posix():sha(p) for p in skill.rglob('*') if p.is_file()}==report['skill_hashes']
            report['artifact_unchanged']=(sha(entry)==artifact['entry_sha256']
                and json.loads((release/'installation.json').read_text(encoding='utf-8'))==metadata
                and sha(root/'artifact.json')==report['artifact_sha256']
                and source_identity()==(source_id,source_map))
        except Exception as error:
            cleanup_errors.append({'stage':'integrity_readback','error_type':type(error).__name__})
            report.update(verifier_unchanged=False,skill_unchanged=False,artifact_unchanged=False)
        report['passed']=final_checks(report,source_unchanged=report['verifier_unchanged'],
            skill_unchanged=report['skill_unchanged'],artifact_unchanged=report['artifact_unchanged'],cleanup_errors=cleanup_errors)
        (root/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if not report['passed'] and failure is None:
            raise AssertionError('Skill acceptance cleanup, receipts or input integrity failed')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
