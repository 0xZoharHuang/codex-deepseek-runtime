"""Native Windows setup and enforced filesystem effects in a disposable guest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import uuid

from install import console_entry,valid_release
from verify_live import Peer
from codex_deepseek.platforms import private_directory


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    if not __debug__:raise SystemExit('Acceptance assertions must be enabled')
    p=argparse.ArgumentParser();p.add_argument('--entry',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if os.name!='nt' or platform.node().upper()!='CDS-QA':
        raise SystemExit('Privileged sandbox acceptance is restricted to the task-owned CDS-QA guest')
    marker=Path('C:/CodexDeepSeekQA/guest-ready.txt')
    if not marker.is_file() or marker.read_text(encoding='utf-8-sig').strip()!='READY':
        raise SystemExit('Disposable guest bootstrap marker is missing')
    entry=args.entry.resolve();release=entry.parent.parent.parent
    installation=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    native=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
    if (native['target']!='x86_64-pc-windows-msvc' or entry!=console_entry(release/'venv')
        or not valid_release(release,installation['adapter_source_sha256'],installation['model_catalog_sha256'],native)):
        raise SystemExit('A verified immutable Windows installation is required')
    root=args.output.resolve()/str(uuid.uuid4());private_directory(root)
    verifiers={name:sha(Path(__file__).with_name(name)) for name in ['verify_windows_sandbox.py','verify_live.py','install.py']}
    artifact={'installation':installation,'native_manifest':native,'target':native['target'],
              'entry':str(entry),'entry_sha256':sha(entry),'verifier_sha256':verifiers,
              'guest':platform.node(),'bootstrap_marker_sha256':sha(marker)}
    (root/'artifact.json').write_text(json.dumps(artifact,indent=2),encoding='utf-8')
    modes=[];methods=[];peer=None
    try:
        for mode in ['unelevated','elevated']:
            case=root/mode;(case/'home').mkdir(parents=True);(case/'work').mkdir()
            (case/'home/config.toml').write_text('approval_policy="never"\nsandbox_mode="workspace-write"\nweb_search="disabled"\n[features]\nplugins=false\napps=false\n[mcp_servers.codex_web]\nenabled=false\n',encoding='utf-8')
            external=case/'outside.txt';external.write_text('PRESERVE',encoding='utf-8')
            environment={'OPENROUTER_API_KEY':'NONFUNCTIONAL_NO_INFERENCE_ALLOWED','OPENAI_API_KEY':''}
            peer=Peer(case,command=[str(entry)],environment=environment)
            before=peer.call('windowsSandbox/readiness',{})
            start=len(peer.events)
            started=peer.call('windowsSandbox/setupStart',{'mode':mode,'cwd':str(case/'work')})
            assert started=={'started':True}
            event=peer.wait(lambda e:e.get('method')=='windowsSandbox/setupCompleted',start,600)
            assert event['params']=={'mode':mode,'success':True,'error':None},event
            peer.close();peer=Peer(case,command=[str(entry)],environment=environment)
            ready=peer.call('windowsSandbox/readiness',{});assert ready=={'status':'ready'},ready
            config=peer.call('config/read',{'includeLayers':False})['config']
            assert config['windows']['sandbox']==mode,config.get('windows')
            target=case/'work/allowed.txt'
            quote=lambda path:"'"+str(path).replace("'","''")+"'"
            script='$ErrorActionPreference="Stop";[IO.File]::WriteAllText('+quote(target)+',"SANDBOX_ALLOWED");try{[IO.File]::WriteAllText('+quote(external)+',"ESCAPED");exit 91}catch{if($_.Exception -is [UnauthorizedAccessException] -or $_.Exception.InnerException -is [UnauthorizedAccessException]){Write-Output "OUTSIDE_WRITE_DENIED"}else{throw}}'
            command=[str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-NonInteractive','-Command',script]
            result=peer.call('command/exec',{'command':command,'cwd':str(case/'work'),
                'sandboxPolicy':{'type':'workspaceWrite','writableRoots':[str(case/'work')],'networkAccess':False},'timeoutMs':120000})
            assert result['exitCode']==0 and 'OUTSIDE_WRITE_DENIED' in result.get('stdout',''),result
            assert target.read_text(encoding='utf-8')=='SANDBOX_ALLOWED' and external.read_text(encoding='utf-8')=='PRESERVE'
            modes.append({'mode':mode,'before':before,'setup':event['params'],'cold_readiness':ready,
                          'inside_write_verified':True,'outside_write_denied':True,'result':result})
            peer.close();peer=None
        with sqlite3.connect(root/'unelevated/home/provider-state/adapter.sqlite') as db:assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==0
        with sqlite3.connect(root/'elevated/home/provider-state/adapter.sqlite') as db:assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==0
        methods=[{'method':'windowsSandbox/setupStart','status':'passed','detail':{'modes':modes}},
                 {'method':'windowsSandbox/readiness','status':'passed','detail':{'cold_states':[m['cold_readiness'] for m in modes]}}]
        unchanged=(sha(entry)==artifact['entry_sha256'] and all(sha(Path(__file__).with_name(name))==digest for name,digest in verifiers.items())
                   and valid_release(release,installation['adapter_source_sha256'],installation['model_catalog_sha256'],native))
        assert unchanged
        report={'passed':True,'installation':installation,'target':native['target'],'methods':methods,
                'positive_lifecycle_exercised':True,'verifier_and_artifact_unchanged':True,'model_requests':0}
        (root/'results.json').write_text(json.dumps(methods,indent=2),encoding='utf-8')
        (root/'verdict.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'passed':True,'modes':[m['mode'] for m in modes],'native_filesystem_enforcement':True}))
    finally:
        if peer:peer.close()
        if not (root/'verdict.json').exists():
            (root/'failure.json').write_text(json.dumps({'passed':False,'completed_modes':modes},indent=2),encoding='utf-8')
        print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
