"""Installed-artifact acceptance for the frozen 64-method gap list."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

SOURCE=Path(__file__).resolve().parent
GROUPS=['catalog','threads','processes','plugins','mcp','migration','control']
BACKEND_ONLY={'mcpServer/event/stream/start','mcpServer/event/stream/stop','plugin/skill/read','plugin/reconcile'}


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--entry',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--jobs',type=int,default=2);args=p.parse_args()
    entry=args.entry.resolve();release=entry.parent.parent.parent;metadata=json.loads((release/'installation.json').read_text())
    if entry!=release/'venv/bin/codex-deepseek':raise SystemExit('Select an immutable installed entrypoint')
    for name,digest in metadata['adapter_source_sha256'].items():
        if sha(SOURCE/name)!=digest:raise SystemExit('Source differs from installed artifact: '+name)
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True,mode=0o700)
    files=[SOURCE/'verify_remaining_methods.py',SOURCE/'verify_remaining_local.py',SOURCE/'verify_live.py']
    hashes={str(f.relative_to(SOURCE)):sha(f) for f in files}
    (root/'artifact.json').write_text(json.dumps({'entry':str(entry),'entry_sha256':sha(entry),'installation':metadata,'verifier_sha256':hashes,'groups':GROUPS},indent=2))
    def run(group):
        started=time.monotonic()
        with (root/(group+'.log')).open('w') as log:
            result=subprocess.run([sys.executable,str(SOURCE/'verify_remaining_local.py'),'--entry',str(entry),'--output',str(root/group),'--group',group],stdout=log,stderr=log,env=os.environ.copy())
        return {'group':group,'exit':result.returncode,'seconds':round(time.monotonic()-started,2)}
    groups=[]
    with ThreadPoolExecutor(args.jobs) as pool:
        for future in as_completed([pool.submit(run,g) for g in GROUPS]):
            groups.append(future.result());print(json.dumps(groups[-1]),flush=True)
    methods={}
    for g in GROUPS:
        for path in (root/g).glob('*/results.json'):
            for row in json.loads(path.read_text()):
                if row['method'] in methods:raise RuntimeError('Duplicate method attestation')
                methods[row['method']]={**row,'evidence':str(path.relative_to(root))}
    expected=set(json.loads((SOURCE/'reports/runtime-v0.3-unexercised-local.json').read_text())['methods'])
    missing=sorted(expected-set(methods));unexpected=sorted(set(methods)-expected)
    invalid=[name for name,r in methods.items() if r['status']!=('backend_boundary_verified' if name in BACKEND_ONLY else 'passed')]
    unchanged=all(sha(SOURCE/name)==digest for name,digest in hashes.items()) and json.loads((release/'installation.json').read_text())==metadata
    report={'passed':not (missing or unexpected or invalid) and unchanged and all(g['exit']==0 for g in groups),'positive_local_methods':sum(r['status']=='passed' for r in methods.values()),'backend_boundaries_verified':sum(r['status']=='backend_boundary_verified' for r in methods.values()),'missing':missing,'unexpected':unexpected,'invalid':invalid,'verifier_and_artifact_unchanged':unchanged,'groups':groups,'methods':methods}
    (root/'verdict.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in {'methods','groups'}}),flush=True);print('EVIDENCE',root,flush=True)
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
