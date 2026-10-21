"""Audit complete frozen native sources and replay only the declared patches."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tomllib

from build_native import COMMIT,SOURCE_ASSET,sha

PROTECTED=['codex-rs/core/src/session/','codex-rs/core/src/agent/','codex-rs/core/src/tasks/',
           'codex-rs/core/src/tools/handlers/','codex-rs/app-server/src/thread_',
           'codex-rs/app-server/src/codex_message_processor','codex-rs/linux-sandbox/',
           'codex-rs/windows-sandbox-rs/','codex-rs/macos-sandbox/']


def main():
    p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--source-tree',type=Path,required=True);p.add_argument('--target',required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    package=Path(__file__).resolve().parent;tree=args.source_tree.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    if sha(args.archive)!=SOURCE_ASSET[1]:raise SystemExit('Upstream source archive checksum mismatch')
    windows=args.target=='x86_64-pc-windows-msvc'
    common=json.loads((package/'native/source-changes.json').read_text(encoding='utf-8'))
    expected={row['path']:row for row in common['files']}
    final={name:row['fork_sha256'] for name,row in expected.items()}
    patches=[package/'native/codex-runtime.patch']
    if windows:
        overlay=json.loads((package/'native/windows-source-changes.json').read_text(encoding='utf-8'))
        for row in overlay['files']:final[row['path']]=row['fork_sha256']
        patches.append(package/'native/windows-transport.patch')
    before={};originals={};prefix='codex-'+COMMIT+'/'
    with tarfile.open(args.archive) as archive:
        for member in archive:
            if not member.name.startswith(prefix):continue
            name=member.name.removeprefix(prefix)
            if member.isfile() or member.islnk():
                data=archive.extractfile(member).read();before[name]=hashlib.sha256(data).hexdigest()
                if name in expected:originals[name]=data
    after={p.relative_to(tree).as_posix():sha(p) for p in tree.rglob('*')
           if p.is_file() and '.git' not in p.relative_to(tree).parts and '__pycache__' not in p.relative_to(tree).parts}
    changed={name for name in before.keys()|after.keys() if before.get(name)!=after.get(name)}
    auxiliary={'codex-rs/vendor/bubblewrap/LICENSE':'b7993225104d90ddd8024fd838faf300bea5e83d91203eab98e29512acebd69c'}
    probes={'codex-rs/http-client/tests/deepseek_policy_revocation.rs':'policy-revocation-probe.rs',
            'codex-rs/secrets/tests/deepseek_fixture_secret_key.rs':'secret-key-probe.rs'}
    for name,source in probes.items():
        if name in after:auxiliary[name]=sha(package/'native'/source)
    for name,digest in auxiliary.items():
        if name in changed and after.get(name)!=digest:raise SystemExit('Unexpected auxiliary source bytes: '+name)
    if changed-set(final)-set(auxiliary) or set(final)-changed:
        raise SystemExit('Undeclared native source changes: '+repr(sorted(changed-set(final)-set(auxiliary))))
    for name,digest in final.items():
        if before.get(name)!=expected[name]['base_sha256'] or after.get(name)!=digest:raise SystemExit('Declared source hash mismatch: '+name)
    replay=out/'replay';replay.mkdir()
    for name,data in originals.items():
        path=replay/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    subprocess.run(['git','init','-q',str(replay)],check=True)
    for patch in patches:
        result=subprocess.run(['git','-c','core.autocrlf=false','-c','core.eol=lf','apply',str(patch)],cwd=replay,capture_output=True,text=True,encoding='utf-8')
        (out/(patch.name+'.log')).write_text(result.stdout+result.stderr,encoding='utf-8');result.check_returncode()
    for name,digest in final.items():
        if sha(replay/name)!=digest:raise SystemExit('Native patch replay differs: '+name)
    before_lock=tomllib.loads(originals['codex-rs/Cargo.lock'].decode());after_lock=tomllib.loads((replay/'codex-rs/Cargo.lock').read_text(encoding='utf-8'))
    external=lambda data:sorted(json.dumps(p,sort_keys=True) for p in data['package'] if p.get('source'))
    if external(before_lock)!=external(after_lock):raise SystemExit('External native dependency versions changed')
    protected=[name for name in before if any(name.startswith(prefix) for prefix in PROTECTED)]
    if len(protected)!=348 or changed.intersection(protected):raise SystemExit('Protected native runtime source differs')
    result={'passed':True,'target':args.target,'upstream_commit':COMMIT,'upstream_archive_sha256':sha(args.archive),
            'verifier_sha256':{name:sha(package/name) for name in ['verify_native_source.py','build_native.py']},
            'source_files':len(before),'changed_native_files':sorted(final),'auxiliary_test_and_license_files':{n:d for n,d in auxiliary.items() if n in after},
            'patches':{p.name:sha(p) for p in patches},'unchanged_protected_runtime_files':len(protected),
            'external_cargo_packages_unchanged':True,'full_source_hashes_verified':True,'patch_replay_passed':True}
    (out/'verdict.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
