"""Run explicit installed-artifact acceptance gates and preserve every exit status."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

SOURCE=Path(__file__).resolve().parent


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--fixtures',type=Path,required=True);parser.add_argument('--native-probe',type=Path,required=True);parser.add_argument('--jobs',type=int,default=2);parser.add_argument('--only',action='append');parser.add_argument('--require-web-research',action='store_true');args=parser.parse_args()
    entry=args.entry.expanduser().resolve()
    release=entry.parent.parent.parent
    metadata=json.loads((release/'installation.json').read_text())
    if entry!=release/'venv/bin/codex-deepseek':raise SystemExit('Acceptance requires an actual immutable installed entrypoint')
    root=args.output.expanduser().resolve()/str(uuid.uuid4());root.mkdir(parents=True,mode=0o700)
    environment={**os.environ,'CODEX_DEEPSEEK_TEST_ENTRY':str(entry),'CODEX_DEEPSEEK_TEST_HTTP_BINARY':str(Path(metadata['native_package'])/'bin/codex-http-transport')}
    environment['CODEX_DEEPSEEK_TEST_CATALOG']=str(release/'catalog.json')
    environment['PYTHONPATH']=str(SOURCE/'src') # verifier helpers only; installed entry and private helpers use -I.
    environment.pop('CODEX_DEEPSEEK_HOME',None);environment.pop('CODEX_HOME',None);environment.pop('CODEX_DEEPSEEK_TRACE_DIR',None)
    output=lambda name:str(root/name)
    gates={
        'tui':[sys.executable,str(SOURCE/'verify_tui_audit.py'),'--entry',str(entry),'--output',output('tui')],
        'real-work':[sys.executable,str(SOURCE/'verify_real_work.py'),'--fixtures',str(args.fixtures.resolve()),*(['--require-web-research'] if args.require_web_research else [])],
        'native-revocation':[sys.executable,str(SOURCE/'verify_native_revocation.py'),'--probe',str(args.native_probe.resolve()),'--http-binary',environment['CODEX_DEEPSEEK_TEST_HTTP_BINARY'],'--output',output('native-revocation')],
        'offline':[sys.executable,'-m','pytest','-q',str(SOURCE/'tests'),'--junitxml='+output('offline.xml')],
        'live':[sys.executable,str(SOURCE/'verify_live.py'),'--agents','--output',output('live')],
        'plugin':[sys.executable,str(SOURCE/'verify_plugin_audit.py'),'--output',output('plugin')],
        'features':[sys.executable,str(SOURCE/'verify_features.py'),'--output',output('features')],
        'modes':[sys.executable,str(SOURCE/'verify_mode_audit.py'),'--output',output('modes')],
        'agents-nested':[sys.executable,str(SOURCE/'verify_nested_agents.py'),'--output',output('agents-nested')],
        'agents-full':[sys.executable,str(SOURCE/'verify_agents.py'),'--fork','all','--output',output('agents-full')],
        'agents-none':[sys.executable,str(SOURCE/'verify_agents.py'),'--fork','none','--output',output('agents-none')],
        'guardian':[sys.executable,str(SOURCE/'verify_safety.py'),'--guardian-v2','--output',output('guardian')],
        'runtime-contract':[sys.executable,str(SOURCE/'verify_runtime_contract.py')],
        'plan-review':[sys.executable,str(SOURCE/'verify_plan_review.py')],
        'daemon':[sys.executable,str(SOURCE/'verify_daemon_audit.py'),'--entry',str(entry),'--output',output('daemon')],
        'profiles':[sys.executable,str(SOURCE/'verify_profile_audit.py'),'--entry',str(entry),'--output',output('profiles')],
        'transports':[sys.executable,str(SOURCE/'verify_transport_audit.py'),'--entry',str(entry),'--output',output('transports')],
        'local-methods':[sys.executable,str(SOURCE/'verify_local_methods.py'),'--output',output('local-methods')],
        'auth-stores':[sys.executable,str(SOURCE/'verify_auth_stores.py'),'--entry',str(entry),'--output',output('auth-stores')],
        'oauth-file':[sys.executable,str(SOURCE/'verify_independent_audit.py'),'--store','file'],
        'oauth-keyring':[sys.executable,str(SOURCE/'verify_independent_audit.py'),'--store','keyring'],
        'oauth-auto':[sys.executable,str(SOURCE/'verify_independent_audit.py'),'--store','auto'],
        'oauth-secrets':[sys.executable,str(SOURCE/'verify_independent_audit.py'),'--store','keyring','--secrets'],
        'memory-v1':[sys.executable,str(SOURCE/'verify_memory_audit.py'),'--version','v1','--output',output('memory-v1')],
        'memory-v2':[sys.executable,str(SOURCE/'verify_memory_audit.py'),'--version','v2','--output',output('memory-v2')],
        'goal-sample':[sys.executable,str(SOURCE/'verify_goal_sample_audit.py'),'--output',output('goal-sample')],
        'crash-recovery':[sys.executable,str(SOURCE/'verify_crash_recovery.py')],
        'search':[sys.executable,str(SOURCE/'verify_search_audit.py')],
        'routes':[sys.executable,str(SOURCE/'verify_routes.py')],
    }
    selected=args.only or list(gates)
    if any(name not in gates for name in selected):raise SystemExit('Unknown gate')
    verifier_files=[*sorted(SOURCE.glob('*.py')),*sorted((SOURCE/'tests').rglob('*.py')),*sorted((SOURCE/'src').rglob('*.py'))]
    verifier_hashes={str(path.relative_to(SOURCE)):digest(path) for path in verifier_files}
    for name,expected in metadata['adapter_source_sha256'].items():
        if digest(SOURCE/name)!=expected:raise SystemExit('Mutable source does not match the installed runtime: '+name)
    controller={'python':sys.executable,'prefix':sys.prefix,'version':sys.version,'packages':{name:importlib.metadata.version(name) for name in ['aiohttp','lark','zstandard','pytest','pytest-asyncio','mcp','jsonschema']}}
    attestation={'entry':str(entry),'entry_sha256':digest(entry),'release':str(release),'installation':metadata,'native_manifest':json.loads((release/'native-runtime.json').read_text()),'selected_gates':selected,'verifier_source_sha256':verifier_hashes,'controller_environment':controller,'started':time.time()}
    (root/'artifact.json').write_text(json.dumps(attestation,indent=2))
    results=[]
    def run(name):
        started=time.monotonic()
        with (root/(name+'.log')).open('w') as log:
            process=subprocess.run(gates[name],cwd=SOURCE,env=environment,stdout=log,stderr=log)
        return {'gate':name,'exit':process.returncode,'seconds':round(time.monotonic()-started,2),'log':name+'.log'}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures={pool.submit(run,name):name for name in selected}
        for future in as_completed(futures):
            result=future.result();results.append(result);(root/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(result),flush=True)
    unchanged=digest(entry)==attestation['entry_sha256'] and json.loads((release/'installation.json').read_text())==metadata
    current_files=[*SOURCE.glob('*.py'),*(SOURCE/'tests').rglob('*.py'),*(SOURCE/'src').rglob('*.py')]
    verifier_unchanged={str(path.relative_to(SOURCE)):digest(path) for path in current_files}==verifier_hashes
    verdict={'passed':all(row['exit']==0 for row in results) and unchanged and verifier_unchanged,'verifier_source_unchanged':verifier_unchanged,'all_gates_selected':set(selected)==set(gates),'entry_unchanged':unchanged,'gates':results}
    (root/'verdict.json').write_text(json.dumps(verdict,indent=2));print('EVIDENCE',root,flush=True)
    raise SystemExit(0 if verdict['passed'] else 1)


if __name__=='__main__':main()
