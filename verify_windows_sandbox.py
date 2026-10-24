"""Native Windows setup and enforced effects in a guest or an explicitly admitted owned fixture."""
from contextlib import closing
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
import windows_acceptance_setup as preparation


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()



def close_peer_gracefully(peer):
    """Require native EOF shutdown before the generic emergency cleanup path."""
    evidence={'stdin_eof':False,'exit_code':None,'reader_eof':False}
    failure=None
    try:
        peer.process.stdin.close();evidence['stdin_eof']=True
        evidence['exit_code']=peer.process.wait(timeout=60)
        peer.reader.join(timeout=10);evidence['reader_eof']=not peer.reader.is_alive()
        if evidence['exit_code']!=0 or not evidence['reader_eof']:
            raise RuntimeError('Native EOF shutdown failed: '+repr(evidence))
    except BaseException as error:
        failure=error
    try:
        peer.close()
    except BaseException as error:
        if failure is None:failure=error
        else:failure.add_note('Emergency peer cleanup also failed: '+repr(error))
    if failure is not None:raise failure
    return evidence


def qualification_isolation(args,entry):
    """Retain the old guest gate; another host requires explicit bounded admission."""
    if os.name!='nt':raise SystemExit('Windows sandbox acceptance requires Windows')
    supplied=args.owned_preparation is not None or args.owned_preparation_sha256 is not None
    if supplied:
        if args.owned_preparation is None or args.owned_preparation_sha256 is None:
            raise SystemExit('Owned sandbox acceptance requires manifest and SHA256')
        manifest=preparation._read_manifest(args.owned_preparation,args.owned_preparation_sha256)
        if not preparation.managed_entry(manifest,entry):
            raise SystemExit('Owned sandbox verifier/entry was not admitted')
        identity=preparation._token(os.getpid())
        if (identity['sid']!=manifest['target_sid'] or identity['elevated']
                or identity['session_id']!=manifest['session_id']):
            raise SystemExit('Owned effects must run as the declared Limited interactive user')
        output=args.output.resolve()
        if not any(output.is_relative_to(Path(root).resolve()) for root in manifest['allowed_home_roots']):
            raise SystemExit('Sandbox lifecycle output is outside the owned admission')
        os.environ[preparation.MANIFEST_ENV]=str(args.owned_preparation.resolve())
        os.environ[preparation.HASH_ENV]=args.owned_preparation_sha256
        return {'kind':'owned_native_namespace','manifest':str(args.owned_preparation.resolve()),
                'manifest_sha256':args.owned_preparation_sha256,'run_id':manifest['run_id'],
                'controller':identity,'preparation_scope':'Native ProvisionOnly before elevated API refresh; permission effects run Limited.'}
    if platform.node().upper()!='CDS-QA':
        raise SystemExit('Privileged sandbox acceptance is restricted to the task-owned CDS-QA guest')
    marker=Path('C:/CodexDeepSeekQA/guest-ready.txt')
    if not marker.is_file() or marker.read_text(encoding='utf-8-sig').strip()!='READY':
        raise SystemExit('Disposable guest bootstrap marker is missing')
    if os.environ.get(preparation.MANIFEST_ENV) or os.environ.get(preparation.HASH_ENV):
        raise SystemExit('Guest qualification cannot silently inherit an owned preparation override')
    return {'kind':'disposable_guest','guest':platform.node(),'bootstrap_marker_sha256':sha(marker)}


def main():
    if not __debug__:raise SystemExit('Acceptance assertions must be enabled')
    p=argparse.ArgumentParser();p.add_argument('--entry',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--owned-preparation',type=Path);p.add_argument('--owned-preparation-sha256');args=p.parse_args()
    entry=args.entry.resolve();release=entry.parent.parent.parent
    isolation=qualification_isolation(args,entry)
    installation=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    native=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
    if (native['target']!='x86_64-pc-windows-msvc' or entry!=console_entry(release/'venv')
        or not valid_release(release,installation['adapter_source_sha256'],installation['model_catalog_sha256'],native)):
        raise SystemExit('A verified immutable Windows installation is required')
    if isolation['kind']=='owned_native_namespace':
        admission=preparation._read_manifest(isolation['manifest'],isolation['manifest_sha256'])
        expected_native=Path(installation['native_package'])/'bin/codex.exe'
        if (Path(admission['native_binary']).resolve()!=expected_native.resolve()
                or admission['native_sha256']!=sha(expected_native)):
            raise SystemExit('Owned preparation targets another native installation')
    root=args.output.resolve()/str(uuid.uuid4());private_directory(root)
    verifiers={name:sha(Path(__file__).with_name(name)) for name in ['verify_windows_sandbox.py','verify_live.py','windows_acceptance_setup.py','install.py','qa_workspace.py']}
    artifact={'installation':installation,'native_manifest':native,'target':native['target'],
              'entry':str(entry),'entry_sha256':sha(entry),'verifier_sha256':verifiers,
              'guest':platform.node(),'isolation':isolation}
    (root/'artifact.json').write_text(json.dumps(artifact,indent=2),encoding='utf-8')
    modes=[];methods=[];peer=None;failure=None;shutdowns=[]
    try:
        for mode in ['unelevated','elevated']:
            if isolation['kind']=='owned_native_namespace':
                if mode=='unelevated':
                    os.environ.pop(preparation.MANIFEST_ENV,None);os.environ.pop(preparation.HASH_ENV,None)
                else:
                    os.environ[preparation.MANIFEST_ENV]=isolation['manifest']
                    os.environ[preparation.HASH_ENV]=isolation['manifest_sha256']
            case=root/mode;(case/'home').mkdir(parents=True);(case/'work').mkdir()
            (case/'home/config.toml').write_text('approval_policy="never"\nsandbox_mode="workspace-write"\nweb_search="disabled"\n[features]\nplugins=false\napps=false\n[mcp_servers.codex_web]\nenabled=false\n',encoding='utf-8')
            external=case/'outside.txt';external.write_text('PRESERVE',encoding='utf-8')
            if isolation['kind']=='owned_native_namespace':
                with (case/'home/config.toml').open('a',encoding='utf-8') as config:
                    config.write('[windows]\nsandbox='+json.dumps(mode)+'\n')
            environment={'OPENROUTER_API_KEY':'NONFUNCTIONAL_NO_INFERENCE_ALLOWED','OPENAI_API_KEY':'','CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS':'1'}
            peer=Peer(case,command=[str(entry)],environment=environment)
            provision=peer.windows_prepare_evidence
            before=peer.call('windowsSandbox/readiness',{})
            start=len(peer.events)
            started=peer.call('windowsSandbox/setupStart',{'mode':mode,'cwd':str(case/'work')})
            assert started=={'started':True}
            event=peer.wait(lambda e:e.get('method')=='windowsSandbox/setupCompleted',start,600)
            assert event['params']=={'mode':mode,'success':True,'error':None},event
            closing,peer=peer,None
            shutdowns.append({'mode':mode,'phase':'before_cold_reopen',**close_peer_gracefully(closing)})
            peer=Peer(case,command=[str(entry)],environment=environment)
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
            modes.append({'mode':mode,'before':before,'setup':event['params'],'cold_readiness':ready,'native_preparation':provision,
                          'inside_write_verified':True,'outside_write_denied':True,'result':result})
            closing,peer=peer,None
            shutdowns.append({'mode':mode,'phase':'after_effects',**close_peer_gracefully(closing)})
        with closing(sqlite3.connect(root/'unelevated/home/provider-state/adapter.sqlite')) as db, db:assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==0
        with closing(sqlite3.connect(root/'elevated/home/provider-state/adapter.sqlite')) as db, db:assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==0
        methods=[{'method':'windowsSandbox/setupStart','status':'passed','detail':{'modes':modes}},
                 {'method':'windowsSandbox/readiness','status':'passed','detail':{'cold_states':[m['cold_readiness'] for m in modes]}}]
        unchanged=(sha(entry)==artifact['entry_sha256'] and all(sha(Path(__file__).with_name(name))==digest for name,digest in verifiers.items())
                   and valid_release(release,installation['adapter_source_sha256'],installation['model_catalog_sha256'],native))
        if isolation['kind']=='owned_native_namespace':
            preparation._read_manifest(isolation['manifest'],isolation['manifest_sha256'])
        assert unchanged
        report={'passed':True,'installation':installation,'target':native['target'],'methods':methods,
                'positive_lifecycle_exercised':True,'verifier_and_artifact_unchanged':True,'model_requests':0,'isolation':isolation,'native_shutdowns':shutdowns}
        (root/'results.json').write_text(json.dumps(methods,indent=2),encoding='utf-8')
        (root/'verdict.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'passed':True,'modes':[m['mode'] for m in modes],'native_filesystem_enforcement':True}))
    except BaseException as error:
        failure=error
        raise
    finally:
        cleanup_failure=None
        try:
            if peer:close_peer_gracefully(peer)
        except BaseException as error:
            cleanup_failure=error
        finally:
            if not (root/'verdict.json').exists():
                error_record=lambda error:None if error is None else {'type':type(error).__name__,'error':str(error),'notes':getattr(error,'__notes__',[])}
                (root/'failure.json').write_text(json.dumps({'passed':False,'completed_modes':modes,'native_shutdowns':shutdowns,
                    'failure':error_record(failure),'cleanup_failure':error_record(cleanup_failure)},indent=2),encoding='utf-8')
            print('EVIDENCE',root,flush=True)
        if cleanup_failure is not None:
            if failure is None:raise cleanup_failure
            failure.add_note('Final peer cleanup also failed: '+repr(cleanup_failure))


if __name__=='__main__':main()
