"""Run explicit installed-artifact acceptance gates and preserve every exit status."""
from __future__ import annotations
import argparse
from install import console_entry,_support,source_identity,valid_release
from assemble_release_acceptance import GATES,KEYRING_PROBE_GATES,qualification_scope
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


def pytest_deselected(items):
    """The offline gate loads this module as its auditable collection plugin."""
    if items:
        config=items[0].config
        config._codex_acceptance_deselected=getattr(config,'_codex_acceptance_deselected',0)+len(items)


def pytest_collection_finish(session):
    path=os.environ.get('CODEX_DEEPSEEK_TEST_COLLECTION_PATH')
    if path is None:return
    # Use the same pinned pytest JUnit formatter as the result writer, including
    # parametrized IDs containing brackets/colons or XML control characters.
    from _pytest.junitxml import mangle_test_address,bin_xml_escape
    items=[]
    for item in session.items:
        names=mangle_test_address(item.nodeid)
        items.append({'nodeid':item.nodeid,'classname':'.'.join(names[:-1]),'name':bin_xml_escape(names[-1])})
    Path(path).write_text(json.dumps({'schema_version':1,'items':items,'deselected':getattr(session.config,'_codex_acceptance_deselected',0)},indent=2)+'\n',encoding='utf-8')


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def native_probe_attestation(selected,revocation,keyring):
    if KEYRING_PROBE_GATES.intersection(selected) and keyring is None:
        raise SystemExit('--keyring-probe is required for native credential cleanup gates')
    probes={}
    for name,path in [('revocation',revocation),('keyring',keyring)]:
        if path is None:continue
        path=path.expanduser().resolve()
        if not path.is_file():raise SystemExit('Native '+name+' probe does not exist: '+str(path))
        probes[name]={'path':str(path),'sha256':digest(path)}
    return probes


def main():
    if not __debug__:raise SystemExit('Acceptance must run with Python assertions enabled')
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--fixtures',type=Path,required=True);parser.add_argument('--native-probe',type=Path,required=True);parser.add_argument('--keyring-probe',type=Path);parser.add_argument('--jobs',type=int,default=2);parser.add_argument('--only',action='append');parser.add_argument('--require-web-research',action='store_true');parser.add_argument('--sandbox-mode',choices=['workspace-write','danger-full-access'],default='workspace-write');args=parser.parse_args()
    entry=args.entry.expanduser().resolve()
    release=entry.parent.parent.parent
    metadata=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    native=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'));scope=qualification_scope(native)
    if entry!=console_entry(release/'venv'):raise SystemExit('Acceptance requires an actual immutable installed entrypoint')
    root=args.output.expanduser().resolve()/str(uuid.uuid4());root.mkdir(parents=True,mode=0o700)
    environment={**os.environ,'CODEX_DEEPSEEK_TEST_ENTRY':str(entry),'CODEX_DEEPSEEK_TEST_HTTP_BINARY':str(_support.executable(Path(metadata['native_package'])/'bin','codex-http-transport'))}
    environment['CODEX_DEEPSEEK_TEST_CATALOG']=str(release/'catalog.json')
    environment['PYTHONPATH']=str(SOURCE/'src') # verifier helpers only; installed entry and private helpers use -I.
    environment['PYTHONUTF8']='1'
    environment['CODEX_DEEPSEEK_TEST_EVIDENCE_ROOT']=str(root/'evidence')
    environment['CODEX_DEEPSEEK_TEST_COLLECTION_PATH']=str(root/'offline-collection.json')
    environment.pop('PYTEST_ADDOPTS',None);environment.pop('PYTEST_PLUGINS',None)
    environment.pop('PYTHONOPTIMIZE',None)
    environment.pop('CODEX_DEEPSEEK_HOME',None);environment.pop('CODEX_HOME',None);environment.pop('CODEX_DEEPSEEK_TRACE_DIR',None)
    output=lambda name:str(root/name)
    gates={
        'tui':[sys.executable,str(SOURCE/'verify_tui_audit.py'),'--entry',str(entry),'--output',output('tui'),'--sandbox-mode',args.sandbox_mode],
        'tui-interrupt':[sys.executable,str(SOURCE/'verify_tui_interrupt.py'),'--entry',str(entry),'--output',output('tui-interrupt'),'--sandbox-mode',args.sandbox_mode],
        'real-work':[sys.executable,str(SOURCE/'verify_real_work.py'),'--fixtures',str(args.fixtures.resolve()),'--sandbox-mode',args.sandbox_mode,*(['--require-web-research'] if args.require_web_research else [])],
        'native-revocation':[sys.executable,str(SOURCE/'verify_native_revocation.py'),'--probe',str(args.native_probe.resolve()),'--http-binary',environment['CODEX_DEEPSEEK_TEST_HTTP_BINARY'],'--output',output('native-revocation')],
        'offline':[sys.executable,'-m','pytest','-p','run_acceptance','-q',str(SOURCE/'tests'),'--junitxml='+output('offline.xml')],
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
        'local-account':[sys.executable,str(SOURCE/'verify_local_account.py'),'--entry',str(entry),'--output',output('local-account')],
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
    # Real development tasks have their own immutable artifact and oracle checks
    # in the assembler. Keep them explicitly runnable without silently adding an
    # extra gate to the exact standard regression set.
    selected=args.only or sorted(GATES)
    if any(name not in gates for name in selected):raise SystemExit('Unknown gate')
    if len(selected)!=len(set(selected)):raise SystemExit('Duplicate acceptance gate')
    probes=native_probe_attestation(selected,args.native_probe,args.keyring_probe)
    environment.pop('CODEX_DEEPSEEK_TEST_KEYRING_PROBE',None)
    if 'keyring' in probes:environment['CODEX_DEEPSEEK_TEST_KEYRING_PROBE']=probes['keyring']['path']
    verifier_files=[*sorted(SOURCE.glob('*.py')),*sorted((SOURCE/'tests').rglob('*.py')),*sorted((SOURCE/'src').rglob('*.py'))]
    verifier_hashes={str(path.relative_to(SOURCE)):digest(path) for path in verifier_files}
    for name,expected in metadata['adapter_source_sha256'].items():
        if digest(SOURCE/name)!=expected:raise SystemExit('Mutable source does not match the installed runtime: '+name)
    source_id,source_hashes=source_identity()
    if source_id!=metadata['source_id'] or source_hashes!=metadata['source_sha256'] or not valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native):
        raise SystemExit('The full source/native artifact must match the selected immutable installation')
    controller={'python':sys.executable,'prefix':sys.prefix,'version':sys.version,'optimize':sys.flags.optimize,'packages':{name:importlib.metadata.version(name) for name in ['aiohttp','lark','zstandard','pytest','pytest-asyncio','mcp','jsonschema']}}
    attestation={'entry':str(entry),'entry_sha256':digest(entry),'release':str(release),'installation':metadata,'native_manifest':native,'target':scope['target'],'tui_sandbox_mode':args.sandbox_mode,'required_local_methods':scope['local_methods'],'baseline_matrix_sha256':scope['baseline_matrix_sha256'],'selected_gates':selected,'native_probes':probes,'evidence_roots':[str(root)],'verifier_source_sha256':verifier_hashes,'controller_environment':controller,'started':time.time()}
    (root/'artifact.json').write_text(json.dumps(attestation,indent=2),encoding='utf-8')
    results=[]
    def run(name):
        started=time.monotonic()
        with (root/(name+'.log')).open('w',encoding='utf-8') as log:
            process=subprocess.run(gates[name],cwd=SOURCE,env=environment,stdout=log,stderr=log)
        return {'gate':name,'exit':process.returncode,'seconds':round(time.monotonic()-started,2),'log':name+'.log'}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures={pool.submit(run,name):name for name in selected}
        for future in as_completed(futures):
            result=future.result();results.append(result);(root/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8');print(json.dumps(result),flush=True)
    unchanged=digest(entry)==attestation['entry_sha256'] and json.loads((release/'installation.json').read_text(encoding='utf-8'))==metadata
    current_files=[*SOURCE.glob('*.py'),*(SOURCE/'tests').rglob('*.py'),*(SOURCE/'src').rglob('*.py')]
    verifier_unchanged={str(path.relative_to(SOURCE)):digest(path) for path in current_files}==verifier_hashes
    probes_unchanged=all(Path(p['path']).is_file() and digest(p['path'])==p['sha256'] for p in probes.values())
    collection=root/'offline-collection.json';collection_hash=digest(collection) if 'offline' in selected and collection.is_file() else None
    verdict={'passed':all(row['exit']==0 for row in results) and unchanged and verifier_unchanged and probes_unchanged and ('offline' not in selected or collection_hash is not None),'target':scope['target'],'verifier_source_unchanged':verifier_unchanged,'native_probes_unchanged':probes_unchanged,'offline_collection_sha256':collection_hash,'all_gates_selected':set(selected)==GATES,'entry_unchanged':unchanged,'gates':results}
    (root/'verdict.json').write_text(json.dumps(verdict,indent=2),encoding='utf-8');print('EVIDENCE',root,flush=True)
    raise SystemExit(0 if verdict['passed'] else 1)


if __name__=='__main__':main()
