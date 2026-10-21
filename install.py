"""Install an immutable adapter and a complete hash-verified native distribution."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
import venv

SOURCE=Path(__file__).resolve().parent


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def isolated(path):
    path=Path(path).expanduser().resolve();protected=(Path.home()/'.codex').resolve()
    if path==protected or path.is_relative_to(protected) or protected.is_relative_to(path):
        raise SystemExit('Installation paths must not overlap Codex App state')
    return path


def valid_native(package,manifest):
    try:
        if not {'bin/codex','bin/codex-code-mode-host','codex-path/rg','bin/codex-http-transport'}<=set(manifest['files']):return False
        for name,digest in manifest['files'].items():
            path=package/name
            if not path.resolve().is_relative_to(package.resolve()) or path.is_symlink() or sha(path)!=digest:return False
        return all((package/name).is_file() and os.access(package/name,os.X_OK)
                   for name in ['bin/codex','bin/codex-code-mode-host','codex-path/rg','bin/codex-http-transport'])
    except (OSError,ValueError,KeyError,TypeError):return False


def native_package(root,manifest,archive_override=None):
    info=manifest['archive'];checksum=info['sha256']
    target=root/'native'/checksum[:24]
    candidates=[target,*sorted(target.parent.glob(target.name+'-repair-*'),reverse=True)]
    reusable=next((candidate for candidate in candidates if valid_native(candidate,manifest)),None)
    if reusable and not archive_override:return reusable
    downloads=root/'downloads';downloads.mkdir(exist_ok=True)
    archive=downloads/info['filename']
    if archive_override:
        supplied=Path(archive_override).resolve()
        if sha(supplied)!=checksum:raise SystemExit('Supplied native archive checksum mismatch')
        if not archive.exists():shutil.copy2(supplied,archive)
    elif not archive.exists():
        staging=archive.with_suffix('.partial')
        with urllib.request.urlopen(info['url'],timeout=90) as response,staging.open('wb') as output:shutil.copyfileobj(response,output)
        if sha(staging)!=checksum:raise SystemExit('Downloaded native archive checksum mismatch')
        os.replace(staging,archive)
    if sha(archive)!=checksum:raise SystemExit('Cached native archive checksum mismatch; preserving it for inspection')
    if reusable:return reusable
    if target.exists():target=target.with_name(target.name+'-repair-'+uuid.uuid4().hex[:10])
    target.mkdir(parents=True,mode=0o700)
    seen=set()
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            relative=PurePosixPath(member.name)
            if relative.is_absolute() or '..' in relative.parts:raise SystemExit('Unsafe path in native archive')
            name=str(relative)
            if member.isdir():continue
            if not member.isfile() or name in seen or name not in manifest['files']:raise SystemExit('Unexpected native archive member: '+name)
            seen.add(name);destination=target/name;destination.parent.mkdir(parents=True,exist_ok=True)
            with bundle.extractfile(member) as payload,destination.open('xb') as output:shutil.copyfileobj(payload,output)
            destination.chmod(0o755 if name in {'bin/codex','bin/codex-code-mode-host','codex-path/rg','bin/codex-http-transport'} else 0o644)
    if seen!=set(manifest['files']) or not valid_native(target,manifest):raise SystemExit('Native package content failed integrity validation')
    return target


def valid_release(release,source_hashes,catalog_hash,native_manifest=None):
    environment=release/'venv'
    try:
        metadata=json.loads((release/'installation.json').read_text())
        site=next((environment/'lib').glob('python*/site-packages'))
        if (metadata['adapter_source_sha256']!=source_hashes
            or metadata['console_script_sha256']!=sha(environment/'bin/codex-deepseek')
            or sha(release/'catalog.json')!=catalog_hash
            or not all(sha(site/path.removeprefix('src/'))==digest for path,digest in source_hashes.items())):return False
        stored=json.loads((release/'native-runtime.json').read_text())
        if native_manifest is not None and stored!=native_manifest:return False
        if not valid_native(Path(metadata['native_package']),stored):return False
        return subprocess.run([str(environment/'bin/python'),'-I','-c','import aiohttp,lark,zstandard,mcp,bs4,tomlkit,pypdf,codex_deepseek.cli,codex_deepseek.web_mcp'],cwd=release,capture_output=True,timeout=15).returncode==0
    except (OSError,ValueError,KeyError,TypeError,StopIteration,subprocess.TimeoutExpired):return False


def source_identity():
    files=[SOURCE/'pyproject.toml',SOURCE/'requirements.lock',SOURCE/'catalog.json',SOURCE/'install.py',SOURCE/'build_native.py',SOURCE/'native_licenses.py',SOURCE/'package_native.py',SOURCE/'LICENSE',SOURCE/'README.md',SOURCE/'THIRD_PARTY_NOTICES.md',*sorted((SOURCE/'native').glob('*')),*sorted((SOURCE/'licenses').rglob('*')),*sorted((SOURCE/'src').rglob('*.py'))]
    files=[path for path in files if path.is_file()]
    digest=hashlib.sha256()
    for path in files:digest.update(str(path.relative_to(SOURCE)).encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()[:20],{str(path.relative_to(SOURCE)):sha(path) for path in files}


def install(root,*,native_archive=None,wheelhouse=None,activate=True):
    root=isolated(root)
    if (platform.system(),platform.machine())!=('Darwin','arm64'):raise SystemExit('This release is verified for macOS arm64 only')
    if any(app in str(Path(sys.executable).resolve()) for app in ['/ChatGPT.app/Contents/','/Codex.app/Contents/']):raise SystemExit('Run the installer with an independent Python 3.11+ interpreter')
    native_manifest=json.loads((SOURCE/'native/runtime.json').read_text())
    if native_manifest.get('status')!='ready':raise SystemExit('The native release manifest is not ready; finish and verify the release build first')
    source_id,all_hashes=source_identity();source_hashes={name:digest for name,digest in all_hashes.items() if name.startswith('src/')}
    catalog_hash=sha(SOURCE/'catalog.json');package=native_package(root,native_manifest,native_archive)
    release=root/'releases'/source_id
    candidates=[release,*sorted((root/'releases').glob(source_id+'-repair-*'),reverse=True)]
    reusable=next((candidate for candidate in candidates if valid_release(candidate,source_hashes,catalog_hash,native_manifest)),None)
    if reusable:release=reusable
    else:
        if release.exists():release=release.with_name(source_id+'-repair-'+uuid.uuid4().hex[:10])
        release.mkdir(parents=True,mode=0o700);environment=release/'venv';venv.create(environment,with_pip=True)
        python=str(environment/'bin/python')
        options=['--no-index','--find-links',str(Path(wheelhouse).resolve())] if wheelhouse else []
        subprocess.run([python,'-I','-m','pip','install','--disable-pip-version-check','--require-hashes','--only-binary=:all:',*options,'-r',str(SOURCE/'requirements.lock')],check=True)
        subprocess.run([python,'-I','-m','pip','install','--disable-pip-version-check','--no-build-isolation','--no-deps',str(SOURCE)],check=True)
        entry=environment/'bin/codex-deepseek'
        entry.write_text('#!/bin/sh\nexec '+shlex.join([python,'-I','-m','codex_deepseek.cli'])+' "$@"\n');entry.chmod(0o755)
        shutil.copy2(SOURCE/'catalog.json',release/'catalog.json');shutil.copy2(SOURCE/'native/runtime.json',release/'native-runtime.json')
        manifest={'release_id':release.name,'source_id':source_id,'release_environment':str(environment),'source':str(SOURCE),'source_sha256':all_hashes,'adapter_source_sha256':source_hashes,'console_script_sha256':sha(entry),'model_catalog_sha256':catalog_hash,'dependencies_lock_sha256':sha(SOURCE/'requirements.lock'),'native_package':str(package),'native_version':native_manifest['upstream_version'],'native_archive_sha256':native_manifest['archive']['sha256'],'model':json.loads((SOURCE/'catalog.json').read_text())['models'][0]['slug']}
        (release/'installation.json').write_text(json.dumps(manifest,indent=2))
    if not valid_release(release,source_hashes,catalog_hash,native_manifest):raise SystemExit('Installed release failed verification; preserving the current entrypoint')
    if any(sha(SOURCE/name)!=digest for name,digest in all_hashes.items()):raise SystemExit('Source changed during installation; retry from a stable checkout')
    runtime_home=root/'codex-home';runtime_home.mkdir(exist_ok=True,mode=0o700)
    config=runtime_home/'config.toml'
    if not config.exists():config.write_text('model_reasoning_effort="high"\napproval_policy="on-request"\nsandbox_mode="workspace-write"\n')
    if not activate:return release
    bin_dir=Path.home()/'.local/bin';bin_dir.mkdir(parents=True,exist_ok=True);launcher=bin_dir/'codex-deepseek'
    if launcher.exists() or launcher.is_symlink():
        managed=launcher.is_symlink() and (launcher.resolve().is_relative_to(root/'releases') or launcher.resolve()==(root/'venv/bin/codex-deepseek').resolve())
        if not managed:raise SystemExit('Preserving unrelated existing launcher: '+str(launcher))
    staging=bin_dir/'.codex-deepseek.new'
    if staging.is_symlink():staging.unlink()
    elif staging.exists():raise SystemExit('Preserving unrelated staging file: '+str(staging))
    staging.symlink_to(release/'venv/bin/codex-deepseek');os.replace(staging,launcher)
    temporary=root/'installation.new.json';shutil.copy2(release/'installation.json',temporary);os.replace(temporary,root/'installation.json')
    print('Installed immutable dedicated runtime: '+str(launcher));return release


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--native-archive',type=Path);parser.add_argument('--wheelhouse',type=Path);parser.add_argument('--no-activate',action='store_true');args=parser.parse_args()
    root=isolated(Path.home()/'.local/share/codex-deepseek');root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (root/'installation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);install(root,native_archive=args.native_archive,wheelhouse=args.wheelhouse,activate=not args.no_activate)


if __name__=='__main__':main()
