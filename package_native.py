"""Create a deterministic native archive and explicit installer manifest."""
from __future__ import annotations
import argparse
import importlib.util
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
import tomllib


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def expected_native_recipe(target):
    spec=importlib.util.spec_from_file_location('package_native_recipe',Path(__file__).with_name('build_native.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    recipe=module.runtime_recipe()
    return module.native_recipe_identity(target,recipe),recipe['build_toolchain']


def package_native(package,archive,manifest_path,url):
    package=Path(package).resolve();archive=Path(archive).resolve();manifest_path=Path(manifest_path).resolve()
    if archive.exists():raise ValueError('Preserving existing native archive; choose a fresh output')
    metadata=json.loads((package/'codex-package.json').read_text(encoding='utf-8'))
    receipt=json.loads((package/'build-receipt.json').read_text(encoding='utf-8'))
    if metadata.get('distribution')!='codex-deepseek-runtime' or receipt.get('profile')!='release':
        raise ValueError('Only an attested optimized dedicated runtime package can be published')
    paths=sorted(path for path in package.rglob('*') if path.is_file())
    if any(path.is_symlink() for path in paths):raise ValueError('Native release files must not be symlinks')
    files={path.relative_to(package).as_posix():sha(path) for path in paths}
    if (receipt.get('upstream_commit')!=metadata.get('upstreamCommit')
        or receipt.get('patch_sha256')!=metadata.get('patchSha256')
        or receipt.get('files')!={name:digest for name,digest in files.items() if name!='build-receipt.json'}):
        raise ValueError('Native package changed after its build receipt was recorded')
    recipe_id,toolchain_pin=expected_native_recipe(metadata['target'])
    if (metadata.get('recipeId')!=recipe_id or receipt.get('native_recipe_id')!=recipe_id
        or receipt.get('build_toolchain_pin')!=toolchain_pin):
        raise ValueError('Native package recipe or toolchain is not qualified for this source combination')
    suffix='.exe' if metadata['target'].endswith('windows-msvc') else ''
    binaries={name+suffix for name in ['bin/codex','bin/codex-code-mode-host','codex-path/rg','bin/codex-http-transport']}
    if suffix:binaries|={name+suffix for name in ['bin/codex-windows-sandbox-setup','bin/codex-command-runner','bin/codex-windows-managed-deny-probe','bin/codex-windows-sandbox-service']}
    if suffix:binaries|={'codex-resources/codex-windows-sandbox-setup.exe','codex-resources/codex-command-runner.exe'}
    if 'linux' in metadata['target']:binaries.add('codex-resources/bwrap')
    if any(metadata.get(key)!=value for key,value in {'variant':'codex','resourcesDir':'codex-resources','pathDir':'codex-path'}.items()):
        raise ValueError('Native package must use the official canonical resource layout')
    if not binaries|{'codex-package.json','build-receipt.json'}<=files.keys():raise ValueError('Native package is incomplete')
    archive.parent.mkdir(parents=True,exist_ok=True)
    with archive.open('xb') as raw,gzip.GzipFile(fileobj=raw,filename='',mode='wb',mtime=0) as zipped,tarfile.open(fileobj=zipped,mode='w',format=tarfile.PAX_FORMAT) as bundle:
        for path in paths:
            info=bundle.gettarinfo(str(path),arcname=path.relative_to(package).as_posix())
            info.uid=info.gid=info.mtime=0;info.uname=info.gname='';info.pax_headers={}
            info.mode=0o755 if info.name in binaries else 0o644
            with path.open('rb') as content:bundle.addfile(info,content)
    manifest={'schema':1,'status':'ready','distribution':'codex-deepseek-runtime','distribution_version':tomllib.loads((Path(__file__).parent/'pyproject.toml').read_text(encoding='utf-8'))['project']['version'],
              'upstream_version':metadata['version'],'upstream_commit':metadata['upstreamCommit'],'patch_sha256':metadata['patchSha256'],
              'target':metadata['target'],'native_recipe_id':recipe_id,'build_toolchain_pin':toolchain_pin,'archive':{'filename':archive.name,'url':url,'sha256':sha(archive)},'files':files}
    manifest_path.parent.mkdir(parents=True,exist_ok=True);manifest_path.write_text(json.dumps(manifest,indent=2)+'\n', encoding='utf-8', newline='\n')
    return manifest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,required=True);parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--url',required=True);args=parser.parse_args()
    manifest=package_native(args.package,args.archive,args.manifest,args.url)
    print(json.dumps({'archive':str(args.archive),'sha256':manifest['archive']['sha256'],'files':len(manifest['files'])},indent=2))


if __name__=='__main__':main()
