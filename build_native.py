"""Build a complete pinned native package from a fresh, verified source archive.

The source patch is deliberately small; no native tool, session, sandbox or
agent loop is replaced. Each run extracts fresh sources and retains its receipt.
"""
from __future__ import annotations
import argparse
import copy
import difflib
import uuid
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

SOURCE=Path(__file__).resolve().parent
COMMIT='fe136d4e70ef2b5bbed40a2d22b3aabf267796d3'
VERSION='0.164.0-alpha.7'
SOURCE_ASSET=(f'https://codeload.github.com/openai/codex/tar.gz/{COMMIT}','3f9d7e83c9f152a438633813daefacee944d29165101c305e8ac08dc00a0e361')
PLATFORM_ASSETS={
 'aarch64-apple-darwin': {
  'host':('codex-code-mode-host-aarch64-apple-darwin.tar.gz','aa6c15cbee1d0ec47e6a86e87a1ec2c704f12c3965fd4585ab4b9575df035d31'),
  'ripgrep':('ripgrep-15.2.0-aarch64-apple-darwin.tar.gz','3750b2e93f37e0c692657da574d7019a101c0084da05a790c83fd335bad973e4')},
 'x86_64-unknown-linux-gnu': {
  'host':('codex-code-mode-host-x86_64-unknown-linux-musl.tar.gz','e0f55b4891cd78092008e3ff405e81bde750e6c162ab8ca21147a2387449ea50'),
  'ripgrep':('ripgrep-15.2.0-x86_64-unknown-linux-musl.tar.gz','33e15bcf1624b25cdd2a55813a47a2f95dbe126268203e76aa6a585d1e7b149c')},
 'x86_64-pc-windows-msvc': {
  'host':('codex-code-mode-host-x86_64-pc-windows-msvc.exe.tar.gz','fdcb3b6138e093c86db35b430049b6d44814b9ac0204d0bfcdeb04c75e557165'),
  'ripgrep':('ripgrep-15.2.0-x86_64-pc-windows-msvc.zip','71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5')}}


def build_target():
    key=(platform.system(),platform.machine().lower())
    targets={('Darwin','arm64'):'aarch64-apple-darwin',('Linux','x86_64'):'x86_64-unknown-linux-gnu',('Windows','amd64'):'x86_64-pc-windows-msvc'}
    if key not in targets:raise SystemExit('Unsupported native build platform: '+repr(key))
    return targets[key]


def build_assets(target):
    result={'source':SOURCE_ASSET}
    for name,(filename,digest) in PLATFORM_ASSETS[target].items():
        base=f'https://github.com/openai/codex/releases/download/rust-v{VERSION}/' if name=='host' else 'https://github.com/BurntSushi/ripgrep/releases/download/15.2.0/'
        result[name]=(base+filename,digest)
    return result



def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def download(cache,name,url,digest):
    destination=cache/(name+'-'+url.rsplit('/',1)[1])
    if destination.exists():
        if sha(destination)!=digest:raise RuntimeError('Cached archive checksum mismatch: '+str(destination))
        return destination
    temporary=destination.with_suffix('.partial')
    with urllib.request.urlopen(url,timeout=90) as response,temporary.open('wb') as output:shutil.copyfileobj(response,output)
    if sha(temporary)!=digest:raise RuntimeError('Downloaded archive checksum mismatch: '+name)
    os.replace(temporary,destination)
    return destination


def validate_native_layout(tree,package,target):
    """Run the frozen upstream's own package validator, including native resources."""
    code=('from pathlib import Path;import sys;sys.path.insert(0,sys.argv[1]);'
          'from codex_package.layout import validate_package_dir;'
          'from codex_package.targets import PACKAGE_VARIANTS,TARGET_SPECS;'
          'validate_package_dir(Path(sys.argv[2]),PACKAGE_VARIANTS["codex"],TARGET_SPECS[sys.argv[3]])')
    subprocess.run([sys.executable,'-I','-c',code,str(tree/'scripts'),str(package),target],
                   env={**os.environ,'CODEX_REPO_ROOT':str(tree)},check=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--work',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--jobs',type=int,default=2);args=parser.parse_args()
    if not 1<=args.jobs<=16:raise SystemExit('Build jobs must be between 1 and 16')
    target=build_target();assets=build_assets(target);suffix='.exe' if platform.system()=='Windows' else ''
    work=args.work.expanduser().resolve();output=args.output.expanduser().resolve()
    if output.exists():raise SystemExit('Preserving existing output; choose a fresh path: '+str(output))
    if work==Path.home()/'.codex' or work.is_relative_to(Path.home()/'.codex') or output.is_relative_to(Path.home()/'.codex'):raise SystemExit('Build paths must be separate from Codex App state')
    cache=work/'downloads';cache.mkdir(parents=True,exist_ok=True)
    archives={name:download(cache,name,*asset) for name,asset in assets.items()}
    build=work/('b-'+uuid.uuid4().hex[:8]);build.mkdir();tree=build/'src';tree.mkdir()
    prefix='codex-'+COMMIT+'/'
    with tarfile.open(archives['source']) as archive:
        members=[]
        for original in archive.getmembers():
            if original.name.rstrip('/')==prefix.rstrip('/'):continue
            if not original.name.startswith(prefix):raise RuntimeError('Unexpected source archive root')
            member=copy.copy(original);member.name=member.name.removeprefix(prefix)
            if member.islnk():member.linkname=member.linkname.removeprefix(prefix)
            members.append(member)
        destination=('\\\\?\\'+str(tree)) if suffix else str(tree)
        archive.extractall(destination,members=members,filter='data')
    patch=SOURCE/'native/codex-runtime.patch'
    subprocess.run(['git','init','-q',str(tree)],check=True)
    subprocess.run(['git','-c','core.longpaths=true','-c','core.autocrlf=false','-c','core.eol=lf','apply',str(patch)],cwd=tree,check=True)
    patch_id=sha(patch)
    expected=json.loads((SOURCE/'native/source-changes.json').read_text(encoding='utf-8'))
    for entry in expected['files']:
        if sha(tree/entry['path'])!=entry['fork_sha256']:raise RuntimeError('Patched source identity mismatch: '+entry['path'])
    patch_inputs=[{'file':'codex-runtime.patch','sha256':sha(patch)}]
    if suffix:
        overlay=SOURCE/'native/windows-transport.patch'
        extra=json.loads((SOURCE/'native/windows-source-changes.json').read_text(encoding='utf-8'))
        if extra['base_patch_sha256']!=sha(patch):raise RuntimeError('Windows overlay base identity mismatch')
        subprocess.run(['git','-c','core.autocrlf=false','-c','core.eol=lf','apply',str(overlay)],cwd=tree,check=True)
        for row in extra['files']:
            if sha(tree/row['path'])!=row['fork_sha256']:raise RuntimeError('Windows patched source identity mismatch: '+row['path'])
        originals={}
        wanted={row['path'] for row in expected['files']}
        with tarfile.open(archives['source']) as bundle:
            for member in bundle:
                name=member.name.removeprefix(prefix)
                if name in wanted and member.isfile():originals[name]=bundle.extractfile(member).read().decode()
        combined=''
        for row in expected['files']:
            name=row['path'];before=originals.get(name,'');after=(tree/name).read_text(encoding='utf-8')
            combined+=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+name if name in originals else '/dev/null',tofile='b/'+name))
        patch_id=hashlib.sha256(combined.encode()).hexdigest()
        if patch_id!=extra['combined_patch_sha256']:raise RuntimeError('Combined Windows fork identity mismatch')
        patch_inputs.append({'file':'windows-transport.patch','sha256':sha(overlay)})
    toolchain=subprocess.run(['rustup','run','1.95.0','rustc','--version'],check=True,capture_output=True,text=True,encoding='utf-8').stdout.strip()
    environment={**os.environ,'CARGO_INCREMENTAL':'0','CARGO_BUILD_JOBS':str(args.jobs),'CARGO_PROFILE_RELEASE_DEBUG':'0','CARGO_PROFILE_RELEASE_STRIP':'symbols','CARGO_PROFILE_RELEASE_LTO':'false','CARGO_PROFILE_RELEASE_CODEGEN_UNITS':'16','CARGO_TARGET_DIR':str(work/'target')}
    bwrap_command=None
    if 'linux' in target:
        # Match upstream release ordering: build/strip the resource first, then
        # embed the digest of the exact shipped bytes into the native CLI.
        bwrap_command=['cargo','+1.95.0','build','--locked','--release','-p','codex-bwrap','--bin','bwrap']
        subprocess.run(bwrap_command,cwd=tree/'codex-rs',env=environment,check=True)
        bwrap=work/'target/release/bwrap'
        subprocess.run(['strip','--strip-debug','--strip-unneeded',str(bwrap)],check=True)
        environment['CODEX_BWRAP_SHA256']=sha(bwrap)
    command=['cargo','+1.95.0','build','--locked','--release','-p','codex-cli','-p','codex-deepseek-http-transport','--bin','codex','--bin','codex-http-transport']
    if suffix:
        command+=['-p','codex-windows-sandbox','-p','codex-windows-sandbox-service','--bin','codex-windows-sandbox-setup','--bin','codex-command-runner','--bin','codex-windows-managed-deny-probe','--bin','codex-windows-sandbox-service']
    subprocess.run(command,cwd=tree/'codex-rs',env=environment,check=True)
    output.mkdir(parents=True);(output/'bin').mkdir();(output/'codex-path').mkdir();(output/'codex-resources').mkdir();(output/'licenses').mkdir()
    shutil.copy2(work/('target/release/codex'+suffix),output/('bin/codex'+suffix))
    shutil.copy2(work/('target/release/codex-http-transport'+suffix),output/('bin/codex-http-transport'+suffix))
    if suffix:
        for name in ['codex-windows-sandbox-setup','codex-command-runner','codex-windows-managed-deny-probe','codex-windows-sandbox-service']:
            shutil.copy2(work/('target/release/'+name+suffix),output/('bin/'+name+suffix))
        for name in ['codex-windows-sandbox-setup','codex-command-runner']:
            shutil.copy2(work/('target/release/'+name+suffix),output/('codex-resources/'+name+suffix))
    if 'linux' in target:shutil.copy2(bwrap,output/'codex-resources/bwrap')
    with tarfile.open(archives['host']) as archive:
        member=next(member for member in archive.getmembers() if member.isfile() and Path(member.name).name.startswith('codex-code-mode-host'))
        with archive.extractfile(member) as source:(output/('bin/codex-code-mode-host'+suffix)).write_bytes(source.read())
    if assets['ripgrep'][0].endswith('.zip'):
        with zipfile.ZipFile(archives['ripgrep']) as archive:
            for name in ['rg.exe','COPYING','LICENSE-MIT','UNLICENSE']:
                member=next(member for member in archive.namelist() if Path(member).name==name)
                destination=output/'codex-path/rg.exe' if name=='rg.exe' else output/'licenses'/('ripgrep-'+name)
                destination.write_bytes(archive.read(member))
    else:
        with tarfile.open(archives['ripgrep']) as archive:
            for name in ['rg','COPYING','LICENSE-MIT','UNLICENSE']:
                member=next(member for member in archive.getmembers() if Path(member.name).name==name and member.isfile())
                destination=output/'codex-path/rg' if name=='rg' else output/'licenses'/('ripgrep-'+name)
                with archive.extractfile(member) as source:destination.write_bytes(source.read())
    for name in ['LICENSE','NOTICE']:shutil.copy2(tree/name,output/'licenses'/('codex-'+name))
    if 'linux' in target:
        # Retain the native resource's own license and exact vendored sources.
        shutil.copy2(tree/'codex-rs/vendor/bubblewrap/LICENSE',output/'licenses/bubblewrap-LICENSE')
        with tarfile.open(output/'licenses/bubblewrap-source.tar.gz','w:gz') as bundle:
            for name in ['codex-rs/bwrap','codex-rs/vendor/bubblewrap']:
                bundle.add(tree/name,arcname=name)
        (output/'licenses/bubblewrap-build.json').write_text(json.dumps({
            'upstream_source':{'url':SOURCE_ASSET[0],'sha256':SOURCE_ASSET[1]},
            'command':bwrap_command,'rustc':toolchain,'embedded_sha256':environment['CODEX_BWRAP_SHA256'],
            'post_build':['strip','--strip-debug','--strip-unneeded','bwrap'],
        },indent=2),encoding='utf-8')
    for executable in ['bin/codex','bin/codex-code-mode-host','codex-path/rg','bin/codex-http-transport']:(output/(executable+suffix)).chmod(0o755)
    from native_licenses import collect
    collect(tree/'codex-rs/Cargo.toml',output/'licenses/rust',target=target)
    metadata={'layoutVersion':1,'version':VERSION,'target':target,'variant':'codex','resourcesDir':'codex-resources','pathDir':'codex-path','entrypoint':'bin/codex'+suffix,'distribution':'codex-deepseek-runtime','upstreamCommit':COMMIT,'patchSha256':patch_id,'patchInputs':patch_inputs}
    (output/'codex-package.json').write_text(json.dumps(metadata,indent=2), encoding='utf-8')
    validate_native_layout(tree,output,target)
    receipt={'schema':1,'profile':'release','upstream_commit':COMMIT,'archives':{key:{'url':url,'sha256':digest} for key,(url,digest) in assets.items()},'patch_sha256':patch_id,'patch_inputs':patch_inputs,'rustc':toolchain,'cargo_command':command,'environment':{key:environment[key] for key in ['CARGO_INCREMENTAL','CARGO_BUILD_JOBS','CARGO_PROFILE_RELEASE_DEBUG','CARGO_PROFILE_RELEASE_STRIP','CARGO_PROFILE_RELEASE_LTO','CARGO_PROFILE_RELEASE_CODEGEN_UNITS']},'files':{path.relative_to(output).as_posix():sha(path) for path in sorted(output.rglob('*')) if path.is_file()}}
    if bwrap_command:receipt['bwrap']={'command':bwrap_command,'embedded_sha256':environment['CODEX_BWRAP_SHA256'],'resource':'codex-resources/bwrap'}
    (output/'build-receipt.json').write_text(json.dumps(receipt,indent=2), encoding='utf-8');print(json.dumps({'package':str(output),'receipt':str(output/'build-receipt.json')},indent=2))


if __name__=='__main__':main()
