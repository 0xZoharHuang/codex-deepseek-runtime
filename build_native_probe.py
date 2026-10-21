"""Build a portable test executable against the pinned native HTTP implementation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--target-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--kind',choices=['network','secret-key'],default='network')
    args=parser.parse_args()
    package=Path(__file__).resolve().parent
    root=args.source_root.resolve()
    records=json.loads((package/'native/source-changes.json').read_text(encoding='utf-8'))['files']
    hashes={row['path']:row['fork_sha256'] for row in records}
    if os.name=='nt':
        for row in json.loads((package/'native/windows-source-changes.json').read_text(encoding='utf-8'))['files']:
            hashes[row['path']]=row['fork_sha256']
    for name,digest in hashes.items():
        if sha(root/name)!=digest:raise SystemExit('Native source does not match the pinned target: '+name)
    crate,test_name,source_name={
        'network':('http-client','deepseek_policy_revocation','policy-revocation-probe.rs'),
        'secret-key':('secrets','deepseek_fixture_secret_key','secret-key-probe.rs'),
    }[args.kind]
    source=package/'native'/source_name
    target=root/'codex-rs'/crate/'tests'/(test_name+'.rs')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and target.read_bytes()!=source.read_bytes():
        raise SystemExit('A different probe already exists; preserving it')
    target.write_bytes(source.read_bytes())
    output=args.output.resolve();output.mkdir(parents=True,mode=0o700,exist_ok=False)
    command=['cargo','+1.95.0','test','--locked','--release','--no-run','--message-format=json',
             '-p','codex-'+crate,'--test',test_name]
    environment=dict(os.environ,CARGO_TARGET_DIR=str(args.target_dir.resolve()),CARGO_INCREMENTAL='0',
                     CARGO_BUILD_JOBS='2',CARGO_PROFILE_RELEASE_DEBUG='0',CARGO_PROFILE_RELEASE_STRIP='symbols',
                     CARGO_PROFILE_RELEASE_LTO='false',CARGO_PROFILE_RELEASE_CODEGEN_UNITS='16')
    with (output/'cargo-stderr.log').open('w',encoding='utf-8') as log:
        result=subprocess.run(command,cwd=root/'codex-rs',env=environment,stdout=subprocess.PIPE,
                              stderr=log,text=True,encoding='utf-8')
    (output/'cargo-artifacts.jsonl').write_text(result.stdout,encoding='utf-8')
    result.check_returncode()
    artifacts=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    binaries=[Path(item['executable']) for item in artifacts
              if item.get('reason')=='compiler-artifact' and item.get('executable')
              and item.get('target',{}).get('name')==test_name]
    if len(binaries)!=1:raise SystemExit('Expected exactly one native policy test executable')
    for name,digest in hashes.items():
        if sha(root/name)!=digest:raise SystemExit('Native source changed during probe build: '+name)
    receipt={'kind':args.kind,'probe':str(binaries[0]),'probe_sha256':sha(binaries[0]),'test_source_sha256':sha(source),
             'source_root':str(root),'native_source_sha256':hashes,'command':command}
    (output/'probe-build.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(binaries[0])


if __name__=='__main__':main()
