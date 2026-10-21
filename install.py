"""Install an immutable adapter and a complete hash-verified native distribution."""
from __future__ import annotations
import argparse
import base64
import importlib.util
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
_support_spec=importlib.util.spec_from_file_location('dedicated_platform_support',SOURCE/'src/codex_deepseek/platforms.py')
_support=importlib.util.module_from_spec(_support_spec);_support_spec.loader.exec_module(_support)
WINDOWS=_support.WINDOWS;EXE=_support.EXE
python_entry=_support.python_entry;console_entry=_support.console_entry
file_lock=_support.file_lock;private_directory=_support.private_directory;replace_file=_support.replace_file


def required_binaries():
    names=['bin/codex','bin/codex-code-mode-host','codex-path/rg','bin/codex-http-transport']
    if WINDOWS:names+=['bin/codex-windows-sandbox-setup','bin/codex-command-runner','bin/codex-windows-managed-deny-probe','bin/codex-windows-sandbox-service']
    if WINDOWS:names+=['codex-resources/codex-windows-sandbox-setup','codex-resources/codex-command-runner']
    if sys.platform.startswith('linux'):names+=['codex-resources/bwrap']
    return {name+EXE for name in names}


def runtime_manifest():
    key=(platform.system(),platform.machine().lower())
    targets={('Darwin','arm64'):'aarch64-apple-darwin',('Linux','x86_64'):'x86_64-unknown-linux-gnu',('Windows','amd64'):'x86_64-pc-windows-msvc'}
    target=targets.get(key)
    if not target:raise SystemExit('Unsupported runtime platform: '+repr(key))
    path=SOURCE/'native'/('runtime.json' if target=='aarch64-apple-darwin' else 'runtime-'+target+'.json')
    if not path.is_file():raise SystemExit('A qualified native manifest has not been published for '+target)
    manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('target')!=target:raise SystemExit('Native manifest target mismatch')
    return manifest


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def isolated(path):
    path=Path(path).expanduser().resolve();protected=(Path.home()/'.codex').resolve()
    if _support.paths_overlap(path,protected):
        raise SystemExit('Installation paths must not overlap Codex App state')
    return path


def valid_native(package,manifest):
    try:
        if not required_binaries()<=set(manifest['files']):return False
        if not all((package/name).is_dir() for name in ['bin','codex-resources','codex-path']):return False
        for name,digest in manifest['files'].items():
            path=package/name
            if not path.resolve().is_relative_to(package.resolve()) or path.is_symlink() or sha(path)!=digest:return False
        return all((package/name).is_file() and os.access(package/name,os.X_OK)
                   for name in required_binaries())
    except (OSError,ValueError,KeyError,TypeError):return False


def native_package(root,manifest,archive_override=None):
    info=manifest['archive'];checksum=info['sha256']
    target=root/'native'/checksum[:24]
    candidates=[target,*sorted(target.parent.glob(target.name+'-repair-*'),reverse=True)]
    reusable=next((candidate for candidate in candidates if valid_native(candidate,manifest)),None)
    if reusable and not archive_override:return reusable
    downloads=root/'downloads'/checksum[:24];downloads.mkdir(parents=True,exist_ok=True)
    archive=downloads/info['filename']
    if archive_override:
        supplied=Path(archive_override).resolve()
        if sha(supplied)!=checksum:raise SystemExit('Supplied native archive checksum mismatch')
        if not archive.exists():shutil.copy2(supplied,archive)
    elif not archive.exists():
        staging=archive.with_suffix('.partial')
        with urllib.request.urlopen(info['url'],timeout=90) as response,staging.open('wb') as output:shutil.copyfileobj(response,output)
        if sha(staging)!=checksum:raise SystemExit('Downloaded native archive checksum mismatch')
        replace_file(staging,archive)
    if sha(archive)!=checksum:raise SystemExit('Cached native archive checksum mismatch; preserving it for inspection')
    if reusable:return reusable
    if target.exists():target=target.with_name(target.name+'-repair-'+uuid.uuid4().hex[:10])
    target.mkdir(parents=True,mode=0o700)
    for name in ['bin','codex-resources','codex-path']:(target/name).mkdir()
    seen=set()
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            relative=PurePosixPath(member.name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in member.name or ':' in member.name:raise SystemExit('Unsafe path in native archive')
            name=str(relative)
            if member.isdir():continue
            if not member.isfile() or name in seen or name not in manifest['files']:raise SystemExit('Unexpected native archive member: '+name)
            seen.add(name);destination=target/name;destination.parent.mkdir(parents=True,exist_ok=True)
            with bundle.extractfile(member) as payload,destination.open('xb') as output:shutil.copyfileobj(payload,output)
            destination.chmod(0o755 if name in required_binaries() else 0o644)
    if seen!=set(manifest['files']) or not valid_native(target,manifest):raise SystemExit('Native package content failed integrity validation')
    return target


def valid_release(release,source_hashes,catalog_hash,native_manifest=None):
    environment=release/'venv'
    try:
        metadata=json.loads((release/'installation.json').read_text(encoding='utf-8'))
        site=_support.site_packages(environment)
        if (metadata['adapter_source_sha256']!=source_hashes
            or metadata['console_script_sha256']!=sha(console_entry(environment))
            or sha(release/'catalog.json')!=catalog_hash
            or not all(sha(site/path.removeprefix('src/'))==digest for path,digest in source_hashes.items())):return False
        stored=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
        if native_manifest is not None and stored!=native_manifest:return False
        if not valid_native(Path(metadata['native_package']),stored):return False
        imports='import aiohttp,lark,zstandard,mcp,bs4,tomlkit,pypdf,codex_deepseek.cli,codex_deepseek.web_mcp'
        if WINDOWS:imports+=';import distlib;from codex_deepseek.windows_launchers import DISTLIB_VERSION;assert distlib.__version__==DISTLIB_VERSION'
        return subprocess.run([str(python_entry(environment)),'-I','-c',imports],cwd=release,capture_output=True,timeout=15).returncode==0
    except (OSError,ValueError,KeyError,TypeError,StopIteration,subprocess.TimeoutExpired):return False


def source_identity():
    # pathlib compares Windows paths case-insensitively. Hash input order must
    # instead be the same bytewise POSIX relative-name order on every host.
    order=lambda path:path.relative_to(SOURCE).as_posix()
    files=[SOURCE/'.gitattributes',SOURCE/'pyproject.toml',SOURCE/'requirements.lock',SOURCE/'catalog.json',SOURCE/'install.py',SOURCE/'build_native.py',SOURCE/'build_native_probe.py',SOURCE/'native_licenses.py',SOURCE/'package_native.py',SOURCE/'LICENSE',SOURCE/'README.md',SOURCE/'THIRD_PARTY_NOTICES.md',*sorted((SOURCE/'native').glob('*'),key=order),*sorted((SOURCE/'licenses').rglob('*'),key=order),*sorted((SOURCE/'src').rglob('*.py'),key=order)]
    files=[path for path in files if path.is_file()]
    digest=hashlib.sha256()
    for path in files:digest.update(path.relative_to(SOURCE).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()[:20],{path.relative_to(SOURCE).as_posix():sha(path) for path in files}


def activate_windows(root,release,bin_dir):
    """Copy the verified immutable entry; preserve unknown or locked launchers."""
    root=Path(root).resolve();release=Path(release).resolve();bin_dir=Path(bin_dir)
    if release.parent!=root/'releases':raise SystemExit('Activation requires an owned immutable release')
    entry=console_entry(release/'venv');metadata=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    digest=sha(entry)
    if digest!=metadata['console_script_sha256']:raise SystemExit('Installed launcher checksum mismatch')
    bin_dir.mkdir(parents=True,exist_ok=True);launcher=bin_dir/'codex-deepseek.exe';legacy=bin_dir/'codex-deepseek.cmd'
    prefix='@rem codex-deepseek managed '+base64.b64encode(os.fsencode(root)).decode()+'\n'
    previous_entry=None;previous_digest=None
    try:
        previous=json.loads((root/'installation.json').read_text(encoding='utf-8'))
        environment=Path(previous['release_environment']).resolve();candidate=console_entry(environment)
        if (environment.name=='venv' and environment.parent.parent==root/'releases'
            and not candidate.is_symlink() and previous['console_script_sha256']==sha(candidate)):
            previous_entry=candidate;previous_digest=previous['console_script_sha256']
    except (OSError,ValueError,KeyError,TypeError):pass
    if legacy.exists() or legacy.is_symlink():
        expected=(prefix+'@echo off\nsetlocal DisableDelayedExpansion\nchcp 65001 >nul\n"'
                  +str(previous_entry).replace('%','%%')+'" %*\n')
        if legacy.is_symlink() or previous_entry is None or legacy.read_text(encoding='utf-8')!=expected:
            raise SystemExit('Preserving unrelated launcher: '+str(legacy))
    if launcher.exists() or launcher.is_symlink():
        managed=False
        if not launcher.is_symlink():
            actual=sha(launcher)
            if actual==digest:managed=True
            else:managed=previous_digest is not None and actual==previous_digest
        if not managed:raise SystemExit('Preserving unrelated launcher: '+str(launcher))
    descriptor,temporary_name=tempfile.mkstemp(prefix='.codex-deepseek-',suffix='.exe',dir=bin_dir);os.close(descriptor)
    temporary=Path(temporary_name)
    try:
        shutil.copyfile(entry,temporary)
        if sha(temporary)!=digest:raise SystemExit('Copied launcher checksum mismatch')
        replace_file(temporary,launcher)
    finally:
        temporary.unlink(missing_ok=True)
    temporary=root/'installation.new.json';shutil.copy2(release/'installation.json',temporary);replace_file(temporary,root/'installation.json')
    if legacy.exists():legacy.unlink()
    return launcher


def install(root,*,native_archive=None,wheelhouse=None,activate=True):
    root=isolated(root)
    native_manifest=runtime_manifest()
    if any(app in str(Path(sys.executable).resolve()) for app in ['/ChatGPT.app/Contents/','/Codex.app/Contents/']):raise SystemExit('Run the installer with an independent Python 3.11+ interpreter')
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
        python=str(python_entry(environment))
        options=['--no-index','--find-links',str(Path(wheelhouse).resolve())] if wheelhouse else []
        subprocess.run([python,'-I','-m','pip','install','--disable-pip-version-check','--require-hashes','--only-binary=:all:',*options,'-r',str(SOURCE/'requirements.lock')],check=True)
        subprocess.run([python,'-I','-m','pip','install','--disable-pip-version-check','--no-build-isolation','--no-deps',str(SOURCE)],check=True)
        entry=console_entry(environment)
        if WINDOWS:
            program='from codex_deepseek.windows_launchers import make_installed_entry;make_installed_entry('+repr(str(entry))+')'
            subprocess.run([python,'-I','-c',program],check=True)
        else:entry.write_text('#!/bin/sh\nexec '+shlex.join([python,'-I','-X','utf8','-m','codex_deepseek.cli'])+' "$@"\n', encoding='utf-8');entry.chmod(0o755)
        shutil.copy2(SOURCE/'catalog.json',release/'catalog.json');(release/'native-runtime.json').write_text(json.dumps(native_manifest,indent=2), encoding='utf-8')
        manifest={'release_id':release.name,'source_id':source_id,'release_environment':str(environment),'source':str(SOURCE),'source_sha256':all_hashes,'adapter_source_sha256':source_hashes,'console_script_sha256':sha(entry),'model_catalog_sha256':catalog_hash,'dependencies_lock_sha256':sha(SOURCE/'requirements.lock'),'native_package':str(package),'native_version':native_manifest['upstream_version'],'native_archive_sha256':native_manifest['archive']['sha256'],'model':json.loads((SOURCE/'catalog.json').read_text(encoding='utf-8'))['models'][0]['slug']}
        (release/'installation.json').write_text(json.dumps(manifest,indent=2), encoding='utf-8')
    if not valid_release(release,source_hashes,catalog_hash,native_manifest):raise SystemExit('Installed release failed verification; preserving the current entrypoint')
    if any(sha(SOURCE/name)!=digest for name,digest in all_hashes.items()):raise SystemExit('Source changed during installation; retry from a stable checkout')
    runtime_home=root/'codex-home';private_directory(runtime_home)
    config=runtime_home/'config.toml'
    if not config.exists():config.write_text('model_reasoning_effort="high"\napproval_policy="on-request"\nsandbox_mode="workspace-write"\n', encoding='utf-8')
    if not activate:return release
    if WINDOWS:
        launcher=activate_windows(root,release,Path.home()/'.local/bin')
        print('Installed immutable dedicated runtime: '+str(launcher));return release
    bin_dir=Path.home()/'.local/bin';bin_dir.mkdir(parents=True,exist_ok=True);launcher=bin_dir/'codex-deepseek'
    if launcher.exists() or launcher.is_symlink():
        managed=launcher.is_symlink() and (launcher.resolve().is_relative_to(root/'releases') or launcher.resolve()==(root/'venv/bin/codex-deepseek').resolve())
        if not managed:raise SystemExit('Preserving unrelated existing launcher: '+str(launcher))
    staging=bin_dir/'.codex-deepseek.new'
    if staging.is_symlink():staging.unlink()
    elif staging.exists():raise SystemExit('Preserving unrelated staging file: '+str(staging))
    staging.symlink_to(release/'venv/bin/codex-deepseek');replace_file(staging,launcher)
    temporary=root/'installation.new.json';shutil.copy2(release/'installation.json',temporary);replace_file(temporary,root/'installation.json')
    print('Installed immutable dedicated runtime: '+str(launcher));return release


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--native-archive',type=Path);parser.add_argument('--wheelhouse',type=Path);parser.add_argument('--no-activate',action='store_true');parser.add_argument('--root',type=Path);args=parser.parse_args()
    default=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'codex-deepseek' if WINDOWS else Path.home()/'.local/share/codex-deepseek'
    root=isolated(args.root or default);private_directory(root)
    with (root/'installation.lock').open('a') as lock,file_lock(lock):
        install(root,native_archive=args.native_archive,wheelhouse=args.wheelhouse,activate=not args.no_activate)


if __name__=='__main__':main()
