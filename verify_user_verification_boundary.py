"""Compare account-gated local verification without enrolling or touching user keys."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import uuid
from verify_live import Peer
from codex_deepseek.cli import native_environment


def check(binary,root):
    (root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('web_search="disabled"\n[features]\napps=false\nplugins=false\nmemories=false\n')
    env=native_environment(root/'home','',binary,install_shims=False)
    for name in ['CODEX_DEEPSEEK_POLICY_HOME','CODEX_DEEPSEEK_BRIDGE_TOKEN','CODEX_DEEPSEEK_BRIDGE_PORT']:env.pop(name,None)
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
    results={name:check(binary.resolve(),root/name) for name,binary in [('official',args.official),('patched',args.patched)]}
    report={'passed':results['official']==results['patched'],'positive_credential_lifecycle_exercised':False,'source_contract':'app-server user_verification::identity requires a ChatGPT account user ID before constructing the native credential provider; empty isolated homes have no such identity. These five methods are account-dependent, including on macOS.','binary_sha256':{name:hashlib.file_digest(binary.open('rb'),'sha256').hexdigest() for name,binary in [('official',args.official),('patched',args.patched)]},'results':results}
    (root/'verdict.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'methods':len(results['official']),'positive_credential_lifecycle_exercised':False}),flush=True);print('EVIDENCE',root,flush=True)
    if not report['passed']:raise AssertionError('Native account-gate behavior differs')


if __name__=='__main__':main()
