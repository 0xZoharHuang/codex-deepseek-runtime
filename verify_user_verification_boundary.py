"""Compare account-gated local verification without enrolling or touching user keys."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
from verify_live import Peer
from codex_deepseek.cli import native_environment,NATIVE_VERSION


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def check(binary,root):
    (root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('web_search="disabled"\n[features]\napps=false\nplugins=false\nmemories=false\n',encoding='utf-8')
    env=native_environment(root/'home','',binary,install_shims=False)
    for name in ['CODEX_DEEPSEEK_POLICY_HOME','CODEX_DEEPSEEK_BRIDGE_TOKEN','CODEX_DEEPSEEK_BRIDGE_PORT']:env.pop(name,None)
    version=subprocess.run([str(binary),'--version'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
    if version.returncode or version.stdout.strip()!='codex-cli '+NATIVE_VERSION:raise AssertionError('Native comparison requires the same frozen CLI version')
    peer=Peer(root,command=[str(binary)],environment=env);results={}
    try:
        status=peer.call('userVerification/status',{});results['userVerification/status']=status
        if status.get('credentialId') is not None or status.get('unavailableReason')!='providerUnavailable':
            raise RuntimeError('Unexpected account eligibility; do not run enrollment/delete against this identity')
        operations=[('userVerification/enroll',{}),('userVerification/delete',{}),('userVerification/verify',{'challenge':base64.urlsafe_b64encode(b'isolated-native-boundary').decode().rstrip('='),'title':'Isolated account boundary audit','description':'No account identity is present; native guard must reject before credential access.'})]
        for method,params in operations:
            try:peer.call(method,params);raise AssertionError('Account gate unexpectedly allowed '+method)
            except RuntimeError as error:
                if 'providerUnavailable' not in str(error):raise
                results[method]={'scope':'account-unavailable boundary','rejected':True,'error':str(error)}
        results['userVerification/cancel']=peer.call('userVerification/cancel',{'requestId':'nonexistent-isolated-request'})
    finally:peer.close()
    return results


def main():
    p=argparse.ArgumentParser();p.add_argument('--official',type=Path,required=True);p.add_argument('--patched',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True)
    digests={name:sha(binary) for name,binary in [('official',args.official),('patched',args.patched)]}
    verifiers={name:sha(Path(__file__).parent/name) for name in ['verify_user_verification_boundary.py','verify_live.py','src/codex_deepseek/cli.py']}
    results={name:check(binary.resolve(),root/name) for name,binary in [('official',args.official),('patched',args.patched)]}
    unchanged=all(sha(binary)==digests[name] for name,binary in [('official',args.official),('patched',args.patched)]) and all(sha(Path(__file__).parent/name)==digest for name,digest in verifiers.items())
    report={'passed':results['official']==results['patched'] and unchanged,'positive_credential_lifecycle_exercised':False,'source_contract':'app-server user_verification::identity requires a ChatGPT account user ID before constructing the native credential provider; empty isolated homes have no such identity. These five methods are account-dependent, including on macOS.','binary_sha256':digests,'verifier_sha256':verifiers,'binary_and_verifier_unchanged':unchanged,'results':results}
    (root/'verdict.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps({'passed':report['passed'],'methods':len(results['official']),'positive_credential_lifecycle_exercised':False}),flush=True);print('EVIDENCE',root,flush=True)
    if not report['passed']:raise AssertionError('Native account-gate behavior differs')


if __name__=='__main__':main()
