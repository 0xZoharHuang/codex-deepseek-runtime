"""Build a complete pinned native package from a fresh, verified source archive.

The source patch is deliberately small; no native tool, session, sandbox or
agent loop is replaced. Each run extracts fresh sources and retains its receipt.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

SOURCE=Path(__file__).resolve().parent
COMMIT='fe136d4e70ef2b5bbed40a2d22b3aabf267796d3'
VERSION='0.164.0-alpha.7'
ASSETS={
    'source':(f'https://codeload.github.com/openai/codex/tar.gz/{COMMIT}','3f9d7e83c9f152a438633813daefacee944d29165101c305e8ac08dc00a0e361'),
    'host':(f'https://github.com/openai/codex/releases/download/rust-v{VERSION}/codex-code-mode-host-aarch64-apple-darwin.tar.gz','aa6c15cbee1d0ec47e6a86e87a1ec2c704f12c3965fd4585ab4b9575df035d31'),
    'ripgrep':('https://github.com/BurntSushi/ripgrep/releases/download/15.2.0/ripgrep-15.2.0-aarch64-apple-darwin.tar.gz','3750b2e93f37e0c692657da574d7019a101c0084da05a790c83fd335bad973e4'),
}


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def download(cache,name,url,digest):
    destination=cache/(name+'.tar.gz')
    if destination.exists():
        if sha(destination)!=digest:raise RuntimeError('Cached archive checksum mismatch: '+str(destination))
        return destination
    temporary=destination.with_suffix('.partial')
    with urllib.request.urlopen(url,timeout=90) as response,temporary.open('wb') as output:shutil.copyfileobj(response,output)
    if sha(temporary)!=digest:raise RuntimeError('Downloaded archive checksum mismatch: '+name)
    os.replace(temporary,destination)
    return destination


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--work',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--jobs',type=int,default=2);args=parser.parse_args()
    if not 1<=args.jobs<=16:raise SystemExit('Build jobs must be between 1 and 16')
    if (platform.system(),platform.machine())!=('Darwin','arm64'):raise SystemExit('This release is validated on macOS arm64')
    work=args.work.expanduser().resolve();output=args.output.expanduser().resolve()
    if output.exists():raise SystemExit('Preserving existing output; choose a fresh path: '+str(output))
    if work==Path.home()/'.codex' or work.is_relative_to(Path.home()/'.codex') or output.is_relative_to(Path.home()/'.codex'):raise SystemExit('Build paths must be separate from Codex App state')
    cache=work/'downloads';cache.mkdir(parents=True,exist_ok=True)
    archives={name:download(cache,name,*asset) for name,asset in ASSETS.items()}
    build=Path(tempfile.mkdtemp(prefix='native-build-',dir=work))
    with tarfile.open(archives['source']) as archive:archive.extractall(build,filter='data')
    tree=build/('codex-'+COMMIT)
    patch=SOURCE/'native/codex-runtime.patch'
    subprocess.run(['patch','--batch','--forward','-p1','-i',str(patch)],cwd=tree,check=True)
    expected=json.loads((SOURCE/'native/source-changes.json').read_text())
    for entry in expected['files']:
        if sha(tree/entry['path'])!=entry['fork_sha256']:raise RuntimeError('Patched source identity mismatch: '+entry['path'])
    toolchain=subprocess.run(['rustup','run','1.95.0','rustc','--version'],check=True,capture_output=True,text=True).stdout.strip()
    environment={**os.environ,'CARGO_INCREMENTAL':'0','CARGO_BUILD_JOBS':str(args.jobs),'CARGO_PROFILE_RELEASE_DEBUG':'0','CARGO_PROFILE_RELEASE_STRIP':'symbols','CARGO_PROFILE_RELEASE_LTO':'false','CARGO_PROFILE_RELEASE_CODEGEN_UNITS':'16','CARGO_TARGET_DIR':str(work/'target')}
    command=['cargo','+1.95.0','build','--locked','--release','-p','codex-cli','-p','codex-deepseek-http-transport','--bin','codex','--bin','codex-http-transport']
    subprocess.run(command,cwd=tree/'codex-rs',env=environment,check=True)
    output.mkdir(parents=True);(output/'bin').mkdir();(output/'codex-path').mkdir();(output/'licenses').mkdir()
    shutil.copy2(work/'target/release/codex',output/'bin/codex')
    shutil.copy2(work/'target/release/codex-http-transport',output/'bin/codex-http-transport')
    with tarfile.open(archives['host']) as archive:
        member=archive.getmember('codex-code-mode-host-aarch64-apple-darwin')
        with archive.extractfile(member) as source:(output/'bin/codex-code-mode-host').write_bytes(source.read())
    with tarfile.open(archives['ripgrep']) as archive:
        for name in ['rg','COPYING','LICENSE-MIT','UNLICENSE']:
            member=next(member for member in archive.getmembers() if Path(member.name).name==name and member.isfile())
            destination=output/'codex-path/rg' if name=='rg' else output/'licenses'/('ripgrep-'+name)
            with archive.extractfile(member) as source:destination.write_bytes(source.read())
    for name in ['LICENSE','NOTICE']:shutil.copy2(tree/name,output/'licenses'/('codex-'+name))
    for executable in ['bin/codex','bin/codex-code-mode-host','codex-path/rg','bin/codex-http-transport']:(output/executable).chmod(0o755)
    from native_licenses import collect
    collect(tree/'codex-rs/Cargo.toml',output/'licenses/rust')
    metadata={'layoutVersion':1,'version':VERSION,'target':'aarch64-apple-darwin','entrypoint':'bin/codex','distribution':'codex-deepseek-runtime','upstreamCommit':COMMIT,'patchSha256':sha(patch)}
    (output/'codex-package.json').write_text(json.dumps(metadata,indent=2))
    receipt={'schema':1,'profile':'release','upstream_commit':COMMIT,'archives':{key:{'url':url,'sha256':digest} for key,(url,digest) in ASSETS.items()},'patch_sha256':sha(patch),'rustc':toolchain,'cargo_command':command,'environment':{key:environment[key] for key in ['CARGO_INCREMENTAL','CARGO_BUILD_JOBS','CARGO_PROFILE_RELEASE_DEBUG','CARGO_PROFILE_RELEASE_STRIP','CARGO_PROFILE_RELEASE_LTO','CARGO_PROFILE_RELEASE_CODEGEN_UNITS']},'files':{str(path.relative_to(output)):sha(path) for path in sorted(output.rglob('*')) if path.is_file()}}
    (output/'build-receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps({'package':str(output),'receipt':str(output/'build-receipt.json')},indent=2))


if __name__=='__main__':main()
