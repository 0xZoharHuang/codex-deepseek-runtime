"""Bounded CDS skill acceptance. --model uses paid API calls on an owned fixture.

Exercises native discovery, explicit skill input, real command effects and exact
session resume. It is not a replacement for the runtime qualification suite.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

from verify_live import Peer
sys.path.insert(0, str(Path(__file__).resolve().parent/'src'))
from codex_deepseek.processes import matches, terminate


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entry', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--skill', default=str(Path(__file__).resolve().parent/'skills/cds'))
    parser.add_argument('--model', action='store_true')
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root/'home').mkdir(); work=root/'work with spaces'; work.mkdir()
    skill = root/'home/skills/cds'
    shutil.copytree(args.skill, skill, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (root/'home/config.toml').write_text('sandbox_mode="workspace-write"\napproval_policy="never"\nweb_search="disabled"\nmodel_reasoning_effort="medium"\n[windows]\nsandbox="unelevated"\n[features]\napps=false\nplugins=false\nmemories=false\nmulti_agent=false\napi_key_model_discovery=false\n', encoding='utf-8')
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith(('CODEX_', 'OPENAI_')): environment.pop(key)
    environment.update(CODEX_DEEPSEEK_HOME=str(root/'home'), CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS='2')
    if not args.model: environment['OPENROUTER_API_KEY']='NONFUNCTIONAL_SKILL_DISCOVERY_FIXTURE'
    report = {'passed':False, 'platform':sys.platform, 'model_requested':args.model,
              'entry':str(Path(args.entry).resolve()), 'skill_hashes':{p.relative_to(skill).as_posix():sha(p) for p in skill.rglob('*') if p.is_file()},
              'test_scope':'Skill discovery, direct exec task and external app-server exact-session continuation. Not native subagent acceptance.'}
    peer=None
    try:
        # The CLI case uses $cds as an ordinary user would. Only owned test data.
        if args.model:
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
            report['direct_exec']={'passed':True,'thread_id':thread,'process_exit':result.returncode,'actual_output':expected,'conflicting_duplicate_rejected':True,'native_command_events':sum(e.get('item',{}).get('type')=='command_execution' and e['type']=='item.completed' for e in events)}
        peer=Peer(root,command=[args.entry],environment=environment,server_args=['-c','mcp_servers={}','app-server','--strict-config'])
        response=peer.call('skills/list',{'cwds':[str(work)],'forceReload':True})
        skills=[s for group in response['data'] for s in group['skills'] if s['name']=='cds']
        assert len(skills)==1 and skills[0]['enabled'] and Path(skills[0]['path']).resolve()==(skill/'SKILL.md').resolve()
        assert not any(group.get('errors') for group in response['data'])
        report['native_discovery']={'passed':True,'name':skills[0]['name'],'enabled':skills[0]['enabled'],'scope':skills[0]['scope']}
        if args.model:
            resumed=peer.call('thread/resume',{'threadId':thread,'cwd':str(work),'sandbox':'workspace-write','approvalPolicy':'never'})
            assert resumed['thread']['id']==thread
            start=len(peer.events)
            turn=peer.call('turn/start',{'threadId':thread,'input':[{'type':'skill','name':'cds','path':str(skill/'SKILL.md')},{'type':'text','text':'Continue the existing CSV utility in the current session. Add optional --summary-only: output only count and total, retain full default output and conflict rejection. Run both modes and save summary.json for the existing ledger. Use the same Python executable supplied earlier. Do not delegate or launch another CDS. Only modify the current work directory.'}]})['turn']['id']
            items=peer.complete(thread,start,timeout=600)
            assert json.loads((work/'summary.json').read_text(encoding='utf-8-sig'))=={'count':4,'total':'11.40'}
            default=subprocess.run([sys.executable,str(work/'summarize.py'),str(inputs/'ledger.csv')],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=20)
            concise=subprocess.run([sys.executable,str(work/'summarize.py'),str(inputs/'ledger.csv'),'--summary-only'],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=20)
            rejected=subprocess.run([sys.executable,str(work/'summarize.py'),str(conflict)],cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=20)
            assert default.returncode==0 and json.loads(default.stdout)==expected
            assert concise.returncode==0 and json.loads(concise.stdout)=={'count':4,'total':'11.40'}
            assert rejected.returncode!=0
            report['external_controller']={'passed':True,'same_thread_id':True,'turn_id':turn,'explicit_native_skill_input':True,'default_and_summary_modes_verified':True,'conflict_rejection_preserved':True,'native_items':len(items)}
        report['passed']=True
    finally:
        if peer: peer.close()
        descriptor=root/'home/provider-state/gateway.json'
        if descriptor.exists():
            owner=json.loads(descriptor.read_text(encoding='utf-8')); deadline=time.monotonic()+10
            while matches(owner['pid'],owner['birth']) and time.monotonic()<deadline: time.sleep(.1)
            if matches(owner['pid'],owner['birth']): terminate(owner['pid'],owner['birth'])
            report['provider_service_stopped']=not matches(owner['pid'],owner['birth'])
        dbpath=root/'home/provider-state/adapter.sqlite'
        if dbpath.exists():
            with sqlite3.connect(dbpath) as db:
                report['receipt_status_counts']=dict(db.execute('select status,count(*) from receipts group by status'))
                report['model_receipts']=sum(report['receipt_status_counts'].values())
        report['native_process_stopped']=peer is None or peer.process.poll() is not None
        (root/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
