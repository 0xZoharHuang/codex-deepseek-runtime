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
import re
import stat
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
file_lock=_support.file_lock;private_directory=_support.private_directory;private_runtime_home=_support.private_runtime_home;replace_file=_support.replace_file


def required_binaries():
    names=['bin/codex','bin/codex-code-mode-host','codex-path/rg','bin/codex-http-transport']
    if WINDOWS:names+=['bin/codex-windows-sandbox-setup','bin/codex-command-runner','bin/codex-windows-managed-deny-probe','bin/codex-windows-sandbox-service']
    if WINDOWS:names+=['codex-resources/codex-windows-sandbox-setup','codex-resources/codex-command-runner']
    if sys.platform.startswith('linux'):names+=['codex-resources/bwrap']
    return {name+EXE for name in names}


def expected_native_recipe(target):
    spec=importlib.util.spec_from_file_location('dedicated_native_recipe',SOURCE/'build_native.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.native_recipe_identity(target)



def archive_info(manifest):
    """Validate cross-platform archive metadata before it becomes a path."""
    info=manifest.get('archive') if isinstance(manifest,dict) else None
    if not isinstance(info,dict):raise SystemExit('Unsafe native archive descriptor')
    name=info.get('filename');checksum=info.get('sha256')
    if (not isinstance(name,str) or not name or name in {'.','..'}
        or name.endswith((' ','.'))
        or any(ord(char)<32 or ord(char)==127 or char in '<>:"/\\|?*' for char in name)
        or name.split('.')[0].rstrip(' .').upper() in {
            'CON','PRN','AUX','NUL','CONIN$','CONOUT$',
            *('COM'+n for n in '123456789\u00b9\u00b2\u00b3'),
            *('LPT'+n for n in '123456789\u00b9\u00b2\u00b3')}):
        raise SystemExit('Unsafe native archive filename: expected a flat portable basename')
    if not isinstance(checksum,str) or re.fullmatch('[0-9a-f]{64}',checksum) is None:
        raise SystemExit('Unsafe native archive sha256: expected 64 lowercase hexadecimal characters')
    return info


def owned_native_path(root,path,*,directory=False):
    """Reject redirected cache/target components, including Windows junctions."""
    root=Path(root).absolute();path=Path(path).absolute()
    if not path.is_relative_to(root):raise SystemExit('Unsafe native storage path outside installation root')
    current=root
    parts=[root]
    for name in path.relative_to(root).parts:
        current=current/name;parts.append(current)
    for current in parts:
        try:metadata=current.lstat()
        except FileNotFoundError:continue
        except OSError as error:raise SystemExit('Cannot verify owned native storage path') from error
        if (stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400)):
            raise SystemExit('Redirected native storage path is not owned by this installation')
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink!=1:
            raise SystemExit('Shared native storage file is not an owned cache file')
        if (current!=path or directory) and not stat.S_ISDIR(metadata.st_mode):
            raise SystemExit('Unsafe native storage directory')
    if not path.resolve().is_relative_to(root.resolve()):
        raise SystemExit('Unsafe native storage path outside installation root')
    return path


def native_storage(root,manifest):
    """Preflight every native output path before reuse or any filesystem write."""
    info=archive_info(manifest);checksum=info['sha256'];root=Path(root).absolute()
    owned_native_path(root,root,directory=True)
    owned_native_path(root,root/'native',directory=True)
    owned_native_path(root,root/'downloads',directory=True)
    target=owned_native_path(root,root/'native'/checksum[:24])
    downloads=owned_native_path(root,root/'downloads'/checksum[:24],directory=True)
    archive=owned_native_path(root,downloads/info['filename'])
    if archive.exists() and not archive.is_file():raise SystemExit('Unsafe native archive cache entry')
    candidates=[target,*sorted(target.parent.glob(target.name+'-repair-*'),reverse=True)]
    for candidate in candidates:owned_native_path(root,candidate)
    return info,target,downloads,archive,candidates

def runtime_manifest(manifest_path=None):
    key=(platform.system(),platform.machine().lower())
    targets={('Darwin','arm64'):'aarch64-apple-darwin',('Linux','x86_64'):'x86_64-unknown-linux-gnu',('Windows','amd64'):'x86_64-pc-windows-msvc'}
    target=targets.get(key)
    if not target:raise SystemExit('Unsupported runtime platform: '+repr(key))
    path=Path(manifest_path).expanduser().resolve() if manifest_path is not None else SOURCE/'native'/('runtime.json' if target=='aarch64-apple-darwin' else 'runtime-'+target+'.json')
    if not path.is_file():raise SystemExit('A qualified native manifest has not been published for '+target)
    manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('target')!=target:raise SystemExit('Native manifest target mismatch')
    if manifest.get('native_recipe_id')!=expected_native_recipe(target):
        raise SystemExit('The '+target+' native manifest is not qualified for this source/toolchain/patch combination. Existing immutable stable releases remain usable; finish target qualification before installing this candidate.')
    archive_info(manifest)
    return manifest


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def isolated(path):
    path=Path(path).expanduser().resolve();protected=(Path.home()/'.codex').resolve()
    if _support.paths_overlap(path,protected):
        raise SystemExit('Installation paths must not overlap Codex App state')
    return path


def native_package_files(package,manifest):
    """Return only a complete, physical distribution; never publish extra files."""
    package=Path(package).absolute()
    expected=manifest.get('files')
    if not isinstance(expected,dict) or not required_binaries()<=set(expected):
        raise ValueError('Incomplete native manifest')
    for name in expected:
        if (not isinstance(name,str) or not name or '\\' in name or ':' in name
            or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
            or PurePosixPath(name).as_posix()!=name):
            raise ValueError('Unsafe native manifest path')
    for ancestor in [package,*package.parents]:
        metadata=ancestor.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata,'st_file_attributes',0)&0x400):
            raise ValueError('Redirected native package ancestry')
    actual={}
    pending=[package]
    while pending:
        directory=pending.pop()
        for path in directory.iterdir():
            metadata=path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata,'st_file_attributes',0)&0x400:
                raise ValueError('Redirected native package member')
            if stat.S_ISDIR(metadata.st_mode):pending.append(path)
            elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink==1:
                actual[path.relative_to(package).as_posix()]=sha(path)
            else:raise ValueError('Nonregular or shared native package member')
    if actual!=expected:raise ValueError('Native package differs from its complete manifest')
    if not all((package/name).is_dir() for name in ['bin','codex-resources','codex-path']):
        raise ValueError('Native package layout is incomplete')
    return actual


def valid_native(package,manifest):
    try:
        native_package_files(package,manifest)
        return all(os.access(package/name,os.X_OK) for name in required_binaries())
    except (OSError,ValueError,KeyError,TypeError):return False


def readable_native_package(root,package,manifest):
    # Public executable assets need RX for the native restricted token. Keep the
    # container and private siblings non-listable; never relax the entire install.
    native_package_files(package,manifest)
    if WINDOWS:
        _support.native_artifact_access(root,package,manifest['files'])
        native_package_files(package,manifest)
    return package


def native_package(root,manifest,archive_override=None):
    info,target,downloads,archive,candidates=native_storage(root,manifest)
    checksum=info['sha256']
    reusable=next((candidate for candidate in candidates if valid_native(candidate,manifest)),None)
    if reusable and not archive_override:return readable_native_package(root,reusable,manifest)
    supplied=None
    if archive_override:
        supplied=Path(archive_override).resolve()
        if sha(supplied)!=checksum:raise SystemExit('Supplied native archive checksum mismatch')
    # Python 3.13 Windows mode=0700 follows the elevated token owner (BA),
    # which can exclude the same user's Limited token. Bind private storage to
    # TokenUser through the existing platform helper before creating children.
    private_directory(root)
    private_directory(downloads.parent)
    private_directory(downloads)
    native_storage(root,manifest)
    if not archive.exists():
        # Never reuse a predictable .partial path: an earlier interrupted or
        # unrelated file may be a hardlink, symlink, or an operator's evidence.
        descriptor,temporary_name=tempfile.mkstemp(prefix='.native-',suffix='.partial',dir=downloads)
        staging=Path(temporary_name)
        with os.fdopen(descriptor,'wb') as output:
            if supplied is not None:
                with supplied.open('rb') as payload:shutil.copyfileobj(payload,output)
            else:
                with urllib.request.urlopen(info['url'],timeout=90) as payload:shutil.copyfileobj(payload,output)
        if sha(staging)!=checksum:raise SystemExit('Downloaded native archive checksum mismatch')
        owned_native_path(root,staging);owned_native_path(root,archive)
        if archive.exists():raise SystemExit('Native archive cache changed during download; preserving both files')
        replace_file(staging,archive)
    if sha(archive)!=checksum:raise SystemExit('Cached native archive checksum mismatch; preserving it for inspection')
    if reusable:return readable_native_package(root,reusable,manifest)
    if target.exists():target=target.with_name(target.name+'-repair-'+uuid.uuid4().hex[:10])
    owned_native_path(root,target)
    private_directory(target.parent)
    target.mkdir()
    private_directory(target)
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
    return readable_native_package(root,target,manifest)


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
        # Cold imports on a clean, constrained Windows VM can take over 25s.
        # Keep verification bounded without rejecting a healthy environment.
        return subprocess.run([str(python_entry(environment)),'-I','-c',imports],cwd=release,capture_output=True,timeout=120).returncode==0
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


def stage_build_source(files,destination):
    """Build only attested source, never checkout bytecode or old build output."""
    destination=Path(destination);destination.mkdir()
    for name,digest in files.items():
        payload=(SOURCE/name).read_bytes()
        if hashlib.sha256(payload).hexdigest()!=digest:
            raise SystemExit('Source changed before packaging: '+name)
        path=destination/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
    return destination


def activate_windows(root,release,bin_dir):
    """Copy the verified immutable entry; preserve unknown or locked launchers."""
    root=Path(root).resolve();release=Path(release).resolve();bin_dir=Path(bin_dir)
    if release.parent!=root/'releases':raise SystemExit('Activation requires an owned immutable release')
    entry=console_entry(release/'venv');metadata_payload=(release/'installation.json').read_bytes()
    metadata=json.loads(metadata_payload);receipt_digest=hashlib.sha256(metadata_payload).hexdigest()
    digest=sha(entry)
    if digest!=metadata['console_script_sha256']:raise SystemExit('Installed launcher checksum mismatch')
    bin_dir.mkdir(parents=True,exist_ok=True);launcher=bin_dir/'codex-deepseek.exe';legacy=bin_dir/'codex-deepseek.cmd'
    prefix='@rem codex-deepseek managed '+base64.b64encode(os.fsencode(root)).decode()+'\n'
    previous_entry=None;previous_digest=None;previous_receipt_digest=None
    try:
        previous_payload=(root/'installation.json').read_bytes()
        previous_receipt_digest=hashlib.sha256(previous_payload).hexdigest();previous=json.loads(previous_payload)
        environment=Path(previous['release_environment']).resolve();candidate=console_entry(environment)
        if (environment.name=='venv' and environment.parent.parent==root/'releases'
            and not candidate.is_symlink() and previous['console_script_sha256']==sha(candidate)):
            previous_entry=candidate;previous_digest=previous['console_script_sha256']
    except (OSError,ValueError,KeyError,TypeError):pass
    legacy_digest=None
    if legacy.exists() or legacy.is_symlink():
        expected=(prefix+'@echo off\nsetlocal DisableDelayedExpansion\nchcp 65001 >nul\n"'
                  +str(previous_entry).replace('%','%%')+'" %*\n')
        if legacy.is_symlink() or previous_entry is None:
            raise SystemExit('Preserving unrelated launcher: '+str(legacy))
        legacy_payload=legacy.read_bytes()
        if legacy_payload.decode('utf-8').replace('\r\n','\n').replace('\r','\n')!=expected:
            raise SystemExit('Preserving unrelated launcher: '+str(legacy))
        legacy_digest=hashlib.sha256(legacy_payload).hexdigest()
    # Older installations may have a byte-for-byte managed short entry. Never
    # create a new alias or claim an unrelated command just because of its name.
    short=bin_dir/'cds.exe'
    launchers=[launcher]
    if short.exists() or short.is_symlink():launchers.append(short)
    original_digests={}
    for target in launchers:
        managed=False;original_digests[target]=None
        if not target.is_symlink() and target.is_file():
            actual=sha(target);original_digests[target]=actual
            managed=actual==digest or (previous_digest is not None and actual==previous_digest)
        if target.exists() or target.is_symlink():
            if not managed:raise SystemExit('Preserving unrelated launcher: '+str(target))
    receipt=root/'installation.json'
    if receipt.is_symlink():raise SystemExit('Preserving unrelated installation receipt: '+str(receipt))
    staged=[];published=[];rollback_incomplete=False
    def stage(source,target,expected_digest=None):
        descriptor,name=tempfile.mkstemp(prefix='.codex-deepseek-',suffix=target.suffix,dir=target.parent);os.close(descriptor)
        path=Path(name);staged.append(path);shutil.copy2(source,path)
        if sha(path)!=(expected_digest if expected_digest is not None else sha(source)):
            raise SystemExit('Copied activation file checksum mismatch')
        return path
    def unchanged(target,expected_digest):
        if expected_digest is None:
            changed=target.exists() or target.is_symlink()
        else:
            changed=target.is_symlink() or not target.is_file() or sha(target)!=expected_digest
        if changed:raise SystemExit('Activation target changed; preserving concurrent file: '+str(target))
    try:
        # Prepare every new file and rollback copy before changing an entry.
        replacements=[(target,stage(entry,target,digest)) for target in launchers]
        replacements.append((receipt,stage(release/'installation.json',receipt,receipt_digest)))
        original_digests[receipt]=previous_receipt_digest
        backups={target:stage(target,target,original_digests[target]) if target.exists() else None
                 for target,_ in replacements}
        legacy_backup=stage(legacy,legacy,legacy_digest) if legacy.exists() else None
        try:
            for target,temporary in replacements[:-1]:
                unchanged(target,original_digests[target])
                replace_file(temporary,target);published.append((target,digest,backups[target]))
            if legacy_backup is not None:
                unchanged(legacy,legacy_digest)
                legacy.unlink();published.append((legacy,None,legacy_backup))
            target,temporary=replacements[-1]
            unchanged(target,original_digests[target])
            replace_file(temporary,target);published.append((target,receipt_digest,backups[target]))
        except BaseException as error:
            rollback_errors=[]
            for target,published_digest,backup in reversed(published):
                try:
                    # Do not overwrite a concurrently changed command or receipt.
                    if published_digest is None:
                        if target.exists() or target.is_symlink():raise OSError('Activation rollback path changed: '+str(target))
                    elif target.is_symlink() or not target.is_file() or sha(target)!=published_digest:
                        raise OSError('Activation rollback file changed: '+str(target))
                    if backup is None:target.unlink()
                    else:replace_file(backup,target)
                except OSError as rollback_error:rollback_errors.append(str(rollback_error))
            if rollback_errors:
                rollback_incomplete=True
                raise RuntimeError('Windows activation failed; rollback incomplete; retained recovery copies: '
                                   +', '.join(str(path) for path in staged if path.exists())+'; '+'; '.join(rollback_errors)) from error
            raise
    finally:
        if not rollback_incomplete:
            for temporary in staged:temporary.unlink(missing_ok=True)
    return launcher


def install(root,*,native_archive=None,native_manifest_path=None,wheelhouse=None,activate=True):
    root=isolated(root)
    native_manifest=runtime_manifest(native_manifest_path)
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
        private_directory(release.parent)
        release.mkdir()
        private_directory(release)
        environment=release/'venv';venv.create(environment,with_pip=True)
        python=str(python_entry(environment))
        options=['--no-index','--find-links',str(Path(wheelhouse).resolve())] if wheelhouse else []
        subprocess.run([python,'-I','-m','pip','install','--disable-pip-version-check','--require-hashes','--only-binary=:all:',*options,'-r',str(SOURCE/'requirements.lock')],check=True)
        with tempfile.TemporaryDirectory(prefix='codex-deepseek-build-') as temporary:
            staged=stage_build_source(all_hashes,Path(temporary)/'source')
            subprocess.run([python,'-I','-m','pip','install','--disable-pip-version-check','--no-build-isolation','--no-deps',str(staged)],check=True)
        entry=console_entry(environment)
        if WINDOWS:
            program='from codex_deepseek.windows_launchers import make_installed_entry;make_installed_entry('+repr(str(entry))+')'
            subprocess.run([python,'-I','-c',program],check=True)
        else:entry.write_text('#!/bin/sh\nexec '+shlex.join([python,'-I','-X','utf8','-m','codex_deepseek.cli'])+' "$@"\n', encoding='utf-8');entry.chmod(0o755)
        shutil.copy2(SOURCE/'catalog.json',release/'catalog.json');(release/'native-runtime.json').write_text(json.dumps(native_manifest,indent=2), encoding='utf-8')
        manifest={'release_id':release.name,'source_id':source_id,'release_environment':str(environment),'source':str(SOURCE),'source_sha256':all_hashes,'adapter_source_sha256':source_hashes,'console_script_sha256':sha(entry),'model_catalog_sha256':catalog_hash,'dependencies_lock_sha256':sha(SOURCE/'requirements.lock'),'native_package':str(package),'native_version':native_manifest['upstream_version'],'native_archive_sha256':native_manifest['archive']['sha256'],'native_recipe_id':native_manifest['native_recipe_id'],'model':json.loads((SOURCE/'catalog.json').read_text(encoding='utf-8'))['models'][0]['slug']}
        (release/'installation.json').write_text(json.dumps(manifest,indent=2), encoding='utf-8')
    if not valid_release(release,source_hashes,catalog_hash,native_manifest):raise SystemExit('Installed release failed verification; preserving the current entrypoint')
    if any(sha(SOURCE/name)!=digest for name,digest in all_hashes.items()):raise SystemExit('Source changed during installation; retry from a stable checkout')
    runtime_home=root/'codex-home';private_runtime_home(runtime_home)
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
    parser=argparse.ArgumentParser();parser.add_argument('--native-archive',type=Path);parser.add_argument('--native-manifest',type=Path,help='Use an explicit qualified local native manifest; platform, recipe and archive verification remain mandatory');parser.add_argument('--wheelhouse',type=Path);parser.add_argument('--no-activate',action='store_true');parser.add_argument('--root',type=Path);args=parser.parse_args()
    default=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'codex-deepseek' if WINDOWS else Path.home()/'.local/share/codex-deepseek'
    root=isolated(args.root or default)
    manifest=runtime_manifest(args.native_manifest)
    if manifest.get('status')!='ready':raise SystemExit('The native release manifest is not ready; finish and verify the release build first')
    native_storage(root,manifest)
    private_directory(root)
    with (root/'installation.lock').open('a') as lock,file_lock(lock):
        install(root,native_archive=args.native_archive,native_manifest_path=args.native_manifest,wheelhouse=args.wheelhouse,activate=not args.no_activate)


if __name__=='__main__':main()
