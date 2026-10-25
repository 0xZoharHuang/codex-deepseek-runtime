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
import importlib.util
import json
import os
from pathlib import Path,PurePosixPath
import platform
import re
import tomllib
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
    code=('from pathlib import Path,PurePosixPath;import sys;sys.path.insert(0,sys.argv[1]);'
          'from codex_package.layout import validate_package_dir;'
          'from codex_package.targets import PACKAGE_VARIANTS,TARGET_SPECS;'
          'validate_package_dir(Path(sys.argv[2]),PACKAGE_VARIANTS["codex"],TARGET_SPECS[sys.argv[3]])')
    subprocess.run([sys.executable,'-I','-B','-c',code,str(tree/'scripts'),str(package),target],
                   env={**os.environ,'CODEX_REPO_ROOT':str(tree)},check=True)



def extract_source_archive(archive, tree, *, windows):
    """Preserve the pinned source archive; materialize its license link on Windows."""
    tree = Path(tree)
    prefix = 'codex-' + COMMIT + '/'
    members = []
    materialized = []
    for original in archive.getmembers():
        if original.name.rstrip('/') == prefix.rstrip('/'):
            continue
        if not original.name.startswith(prefix):
            raise RuntimeError('Unexpected source archive root')
        member = copy.copy(original)
        member.name = member.name.removeprefix(prefix)
        if member.islnk():
            member.linkname = member.linkname.removeprefix(prefix)
        if windows and (member.issym() or member.islnk()):
            # This fixed upstream has one relative source link. Resolve against
            # original TarInfo names, before prefix stripping, without os.symlink.
            expected = 'codex-rs/vendor/bubblewrap/LICENSE'
            target = 'codex-rs/vendor/bubblewrap/COPYING'
            if member.name != expected or not original.issym() or original.linkname != 'COPYING':
                raise RuntimeError('Unexpected Windows source archive link')
            target_member = archive.getmember(prefix + target)
            if not target_member.isfile():
                raise RuntimeError('Windows source archive link target must be a regular file')
            stream = archive.extractfile(target_member)
            if stream is None:
                raise RuntimeError('Windows source archive link target has no data')
            with stream:
                content = stream.read()
            if len(content) != target_member.size:
                raise RuntimeError('Windows source archive link target is truncated')
            materialized.append((member.name, content))
            continue
        members.append(member)
    destination = ('\\\\?\\' + str(tree)) if windows else str(tree)
    archive.extractall(destination, members=members, filter='data')
    for name, content in materialized:
        path = Path(destination).joinpath(*PurePosixPath(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as output:
            output.write(content)
        if path.is_symlink() or path.read_bytes() != content:
            raise RuntimeError('Windows source archive link materialization mismatch')


def runtime_recipe():
    """Read the explicit patch stack; target qualification remains a separate gate."""
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:raise ValueError('Duplicate native build recipe key: '+key)
            result[key]=value
        return result
    recipe=json.loads((SOURCE/'native/runtime-overlays.json').read_text(encoding='utf-8'),object_pairs_hook=pairs)
    if set(recipe)!={'schema','upstream_commit','upstream_toolchain','build_toolchain','required_base_inputs','overlays'} or type(recipe['schema']) is not int or recipe['schema']!=1 or recipe['upstream_commit']!=COMMIT:
        raise ValueError('Native build recipe has an incompatible schema or upstream baseline')
    if any(not isinstance(recipe[key],str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',recipe[key]) for key in ['upstream_toolchain','build_toolchain']):
        raise ValueError('Native toolchains must be explicit release pins')
    if set(recipe['required_base_inputs'])!=set(PLATFORM_ASSETS):raise ValueError('Native recipe target set mismatch')
    order=['payload-http-transport.patch','payload-too-large.patch','windows-sandbox-isolation.patch','windows-sandbox-help.patch','windows-loopback-policy.patch','windows-wfp-readiness.patch','windows-artifact-readroots.patch','windows-sandbox-runner-home.patch','windows-sandbox-coordination.patch','windows-managed-deny-revoke.patch','windows-runner-junction-root.patch','windows-home-metadata.patch','windows-root-acl-sharing.patch','windows-plugin-git-lifecycle.patch','windows-setup-background-handles.patch','windows-ancestor-metadata.patch','windows-acl-inheritance-lock.patch','windows-critical-read.patch','windows-acl-lifecycle.patch','payload-request-capacity.patch']
    if not isinstance(recipe['overlays'],list) or [item.get('file') for item in recipe['overlays']]!=order:raise ValueError('Native overlay order mismatch')
    digest=lambda value:isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value)
    for target,inputs in recipe['required_base_inputs'].items():
        names=['codex-runtime.patch']+(['windows-transport.patch'] if target.endswith('windows-msvc') else [])
        if not isinstance(inputs,list) or [item.get('file') for item in inputs]!=names or any(set(item)!={'file','sha256'} or not digest(item['sha256']) for item in inputs):raise ValueError('Invalid required native baseline inputs')
    for item in recipe['overlays']:
        if set(item)!={'file','sha256','changes_by_target'} or not digest(item['sha256']):raise ValueError('Invalid native overlay identity')
        expected_targets=set(PLATFORM_ASSETS) if item['file'].startswith('payload-') else {'x86_64-pc-windows-msvc'}
        if set(item['changes_by_target'])!=expected_targets:raise ValueError('Invalid native overlay target coverage')
        for changes in item['changes_by_target'].values():
            if not isinstance(changes,list) or not changes:raise ValueError('Native overlay needs source hash assertions')
            paths=[]
            for row in changes:
                if set(row)!={'path','before_sha256','after_sha256'} or not isinstance(row['path'],str):raise ValueError('Invalid native source hash assertion')
                path=PurePosixPath(row['path'])
                if path.is_absolute() or '..' in path.parts or '\\' in row['path'] or not row['path'].startswith('codex-rs/'):raise ValueError('Unsafe native overlay source path')
                if row['before_sha256'] is not None and not digest(row['before_sha256']):raise ValueError('Invalid native source input hash')
                if not digest(row['after_sha256']):raise ValueError('Invalid native source output hash')
                paths.append(row['path'])
            if len(paths)!=len(set(paths)):raise ValueError('Duplicate native overlay source path')
    return recipe


def overlay_source_hashes(target,recipe=None):
    recipe=recipe or runtime_recipe()
    if target not in recipe['required_base_inputs']:raise ValueError('Unsupported native overlay target')
    return {row['path']:row['after_sha256'] for item in recipe['overlays'] for row in item['changes_by_target'].get(target,[])}


def native_recipe_identity(target,recipe=None):
    recipe=recipe or runtime_recipe()
    if target not in recipe['required_base_inputs']:raise ValueError('Unsupported native recipe target')
    selected={'schema':1,'target':target,'upstream_commit':recipe['upstream_commit'],
              'upstream_toolchain':recipe['upstream_toolchain'],'build_toolchain':recipe['build_toolchain'],
              'base_inputs':recipe['required_base_inputs'][target],
              'overlays':[{'file':item['file'],'sha256':item['sha256'],'changes':item['changes_by_target'][target]}
                          for item in recipe['overlays'] if target in item['changes_by_target']]}
    return hashlib.sha256(json.dumps(selected,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def apply_runtime_overlays(tree,target,patch_inputs,recipe=None):
    recipe=recipe or runtime_recipe();tree=Path(tree)
    if patch_inputs!=recipe['required_base_inputs'].get(target):raise ValueError('Native overlays require the exact published baseline patch stack')
    upstream=tomllib.loads((tree/'codex-rs/rust-toolchain.toml').read_text(encoding='utf-8'))['toolchain']['channel']
    if upstream!=recipe['upstream_toolchain']:raise ValueError('Native upstream toolchain baseline changed')
    selected=[item for item in recipe['overlays'] if target in item['changes_by_target']]
    for item in selected:
        if sha(SOURCE/'native'/item['file'])!=item['sha256']:raise ValueError('Native overlay patch checksum mismatch: '+item['file'])
    for item in selected:
        changes=item['changes_by_target'][target]
        for row in changes:
            path=tree/row['path'];wanted=row['before_sha256']
            if path.is_symlink() or ((path.exists() or path.is_symlink()) if wanted is None else (not path.is_file() or sha(path)!=wanted)):
                raise ValueError('Native overlay input identity mismatch: '+row['path'])
        command=['git','-c','core.longpaths=true','-c','core.autocrlf=false','-c','core.eol=lf','apply']
        subprocess.run([*command,'--check',str(SOURCE/'native'/item['file'])],cwd=tree,check=True)
        subprocess.run([*command,str(SOURCE/'native'/item['file'])],cwd=tree,check=True)
        for row in changes:
            if sha(tree/row['path'])!=row['after_sha256']:raise ValueError('Native overlay output identity mismatch: '+row['path'])
        patch_inputs.append({'file':item['file'],'sha256':item['sha256']})
    return overlay_source_hashes(target,recipe)


def combined_patch(tree,archive,paths):
    """Identity of the final source diff, independent of staging path or target OS."""
    paths=sorted(set(paths));originals={};prefix='codex-'+COMMIT+'/'
    with tarfile.open(archive) as bundle:
        for member in bundle:
            name=member.name.removeprefix(prefix)
            if name in paths and member.isfile():originals[name]=bundle.extractfile(member).read().decode('utf-8')
    result=''
    for name in paths:
        before=originals.get(name,'');after=(Path(tree)/name).read_text(encoding='utf-8')
        result+=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+name if name in originals else '/dev/null',tofile='b/'+name))
    return result


def native_license_collector():
    # Resolve this build input from the verified checkout even under Python -I;
    # never import a same-named module from the caller's working directory.
    spec=importlib.util.spec_from_file_location('cds_native_licenses',SOURCE/'native_licenses.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.collect


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--work',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--jobs',type=int,default=2);args=parser.parse_args()
    if not 1<=args.jobs<=16:raise SystemExit('Build jobs must be between 1 and 16')
    target=build_target();assets=build_assets(target);suffix='.exe' if platform.system()=='Windows' else ''
    collect=native_license_collector()
    work=args.work.expanduser().resolve();output=args.output.expanduser().resolve()
    if output.exists():raise SystemExit('Preserving existing output; choose a fresh path: '+str(output))
    if work==Path.home()/'.codex' or work.is_relative_to(Path.home()/'.codex') or output.is_relative_to(Path.home()/'.codex'):raise SystemExit('Build paths must be separate from Codex App state')
    cache=work/'downloads';cache.mkdir(parents=True,exist_ok=True)
    archives={name:download(cache,name,*asset) for name,asset in assets.items()}
    build=work/('b-'+uuid.uuid4().hex[:8]);build.mkdir();tree=build/'src';tree.mkdir()
    with tarfile.open(archives['source']) as archive:
        extract_source_archive(archive, tree, windows=bool(suffix))
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
        originals={};prefix='codex-'+COMMIT+'/'
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
    recipe=runtime_recipe();recipe_id=native_recipe_identity(target,recipe);overlay_hashes=apply_runtime_overlays(tree,target,patch_inputs,recipe)
    source_paths={entry['path'] for entry in expected['files']}|set(overlay_hashes)
    if suffix:source_paths.update(row['path'] for row in extra['files'])
    combined=combined_patch(tree,archives['source'],source_paths);(build/'combined.patch').write_bytes(combined.encode())
    patch_id=hashlib.sha256(combined.encode()).hexdigest()
    toolchain_pin=recipe['build_toolchain']
    toolchain=subprocess.run(['rustup','run',toolchain_pin,'rustc','--version'],check=True,capture_output=True,text=True,encoding='utf-8').stdout.strip()
    if not toolchain.startswith('rustc '+toolchain_pin+' '):raise RuntimeError('Native compiler differs from the explicit qualification pin')
    cargo=['cargo','+'+toolchain_pin]
    environment={**os.environ,'CARGO_INCREMENTAL':'0','CARGO_BUILD_JOBS':str(args.jobs),'CARGO_PROFILE_RELEASE_DEBUG':'0','CARGO_PROFILE_RELEASE_STRIP':'symbols','CARGO_PROFILE_RELEASE_LTO':'false','CARGO_PROFILE_RELEASE_CODEGEN_UNITS':'16','CARGO_TARGET_DIR':str(work/'target')}
    bwrap_command=None
    if 'linux' in target:
        # Match upstream release ordering: build/strip the resource first, then
        # embed the digest of the exact shipped bytes into the native CLI.
        bwrap_command=[*cargo,'build','--locked','--release','-p','codex-bwrap','--bin','bwrap']
        subprocess.run(bwrap_command,cwd=tree/'codex-rs',env=environment,check=True)
        bwrap=work/'target/release/bwrap'
        subprocess.run(['strip','--strip-debug','--strip-unneeded',str(bwrap)],check=True)
        environment['CODEX_BWRAP_SHA256']=sha(bwrap)
    helper_command=[*cargo,'build','--locked','--release','-p','codex-deepseek-http-transport','--bin','codex-http-transport']
    subprocess.run(helper_command,cwd=tree/'codex-rs',env=environment,check=True)
    command=[*cargo,'build','--locked','--release','-p','codex-cli','--bin','codex']
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
    metadata_command=[*cargo,'metadata','--locked','--format-version','1','--manifest-path',str(tree/'codex-rs/Cargo.toml'),'--filter-platform',target]
    dependency_metadata=json.loads(subprocess.run(metadata_command,env=environment,check=True,capture_output=True,text=True,encoding='utf-8').stdout)
    collect(tree/'codex-rs/Cargo.toml',output/'licenses/rust',metadata=dependency_metadata,target=target)
    metadata={'layoutVersion':1,'version':VERSION,'target':target,'variant':'codex','resourcesDir':'codex-resources','pathDir':'codex-path','entrypoint':'bin/codex'+suffix,'distribution':'codex-deepseek-runtime','upstreamCommit':COMMIT,'patchSha256':patch_id,'patchInputs':patch_inputs,'recipeId':recipe_id}
    (output/'codex-package.json').write_text(json.dumps(metadata,indent=2), encoding='utf-8')
    validate_native_layout(tree,output,target)
    receipt={'schema':1,'profile':'release','upstream_commit':COMMIT,'archives':{key:{'url':url,'sha256':digest} for key,(url,digest) in assets.items()},'patch_sha256':patch_id,'patch_inputs':patch_inputs,'rustc':toolchain,'cargo_command':command,'cargo_commands':[helper_command,command],'native_recipe_id':recipe_id,'build_toolchain_pin':toolchain_pin,'upstream_toolchain_pin':recipe['upstream_toolchain'],'overlay_recipe_sha256':sha(SOURCE/'native/runtime-overlays.json'),'source_files':{name:sha(tree/name) for name in sorted(source_paths)},'combined_patch_sha256':patch_id,'environment':{key:environment[key] for key in ['CARGO_INCREMENTAL','CARGO_BUILD_JOBS','CARGO_PROFILE_RELEASE_DEBUG','CARGO_PROFILE_RELEASE_STRIP','CARGO_PROFILE_RELEASE_LTO','CARGO_PROFILE_RELEASE_CODEGEN_UNITS']},'files':{path.relative_to(output).as_posix():sha(path) for path in sorted(output.rglob('*')) if path.is_file()}}
    if bwrap_command:receipt['bwrap']={'command':bwrap_command,'embedded_sha256':environment['CODEX_BWRAP_SHA256'],'resource':'codex-resources/bwrap'}
    (output/'build-receipt.json').write_text(json.dumps(receipt,indent=2), encoding='utf-8');print(json.dumps({'package':str(output),'receipt':str(output/'build-receipt.json')},indent=2))


if __name__=='__main__':main()
