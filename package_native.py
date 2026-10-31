"""Create a deterministic native archive and explicit installer manifest."""
from __future__ import annotations
import argparse
import importlib.util
import gzip
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import tarfile
import tomllib


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def expected_native_recipe(target):
    spec=importlib.util.spec_from_file_location('package_native_recipe',Path(__file__).with_name('build_native.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    recipe=module.runtime_recipe()
    return module.native_recipe_identity(target,recipe),recipe['build_toolchain']


def _validate_package(metadata,receipt,files):
    if metadata.get('distribution')!='codex-deepseek-runtime' or receipt.get('profile')!='release':
        raise ValueError('Only an attested optimized dedicated runtime package can be published')
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
    return recipe_id,toolchain_pin,binaries


def _manifest(metadata,files,archive,url,recipe_id,toolchain_pin):
    return {'schema':1,'status':'ready','distribution':'codex-deepseek-runtime','distribution_version':tomllib.loads((Path(__file__).parent/'pyproject.toml').read_text(encoding='utf-8'))['project']['version'],
              'upstream_version':metadata['version'],'upstream_commit':metadata['upstreamCommit'],'patch_sha256':metadata['patchSha256'],
              'target':metadata['target'],'native_recipe_id':recipe_id,'build_toolchain_pin':toolchain_pin,'archive':{'filename':archive.name,'url':url,'sha256':sha(archive)},'files':files}


def package_native(package,archive,manifest_path,url):
    package=Path(package).resolve();archive=Path(archive).resolve();manifest_path=Path(manifest_path).resolve()
    if archive.exists():raise ValueError('Preserving existing native archive; choose a fresh output')
    metadata=json.loads((package/'codex-package.json').read_text(encoding='utf-8'))
    receipt=json.loads((package/'build-receipt.json').read_text(encoding='utf-8'))
    paths=sorted(path for path in package.rglob('*') if path.is_file())
    if any(path.is_symlink() for path in paths):raise ValueError('Native release files must not be symlinks')
    files={path.relative_to(package).as_posix():sha(path) for path in paths}
    recipe_id,toolchain_pin,binaries=_validate_package(metadata,receipt,files)
    archive.parent.mkdir(parents=True,exist_ok=True)
    with archive.open('xb') as raw,gzip.GzipFile(fileobj=raw,filename='',mode='wb',mtime=0) as zipped,tarfile.open(fileobj=zipped,mode='w',format=tarfile.PAX_FORMAT,dereference=True) as bundle:
        for path in paths:
            info=bundle.gettarinfo(str(path),arcname=path.relative_to(package).as_posix())
            info.uid=info.gid=info.mtime=0;info.uname=info.gname='';info.pax_headers={}
            info.mode=0o755 if info.name in binaries else 0o644
            with path.open('rb') as content:bundle.addfile(info,content)
    manifest=_manifest(metadata,files,archive,url,recipe_id,toolchain_pin)
    manifest_path.parent.mkdir(parents=True,exist_ok=True);manifest_path.write_text(json.dumps(manifest,indent=2)+'\n', encoding='utf-8', newline='\n')
    return manifest


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate native archive JSON field: '+key)
        result[key]=value
    return result


def _archive_json(raw,name):
    def invalid(value):raise ValueError('Non-finite native archive JSON value: '+value)
    try:value=json.loads(raw.decode('utf-8'),object_pairs_hook=_unique_object,parse_constant=invalid)
    except (UnicodeError,json.JSONDecodeError) as error:
        raise ValueError('Invalid native archive metadata: '+name) from error
    if not isinstance(value,dict):raise ValueError('Native archive metadata must be an object: '+name)
    return value


def _archive_name(member):
    raw=member.name
    relative=PurePosixPath(raw)
    if (not raw or relative.is_absolute() or '..' in relative.parts
        or '\\' in raw or ':' in raw or any(ord(c)<32 or ord(c)==127 for c in raw)):
        raise ValueError('Unsafe path in existing native archive: '+repr(raw))
    name=str(relative)
    if name=='.':
        if member.isdir() and raw in {'.','./'}:return ''
        raise ValueError('Invalid native archive root member')
    allowed={name,'./'+name}
    if member.isdir():allowed|={name+'/','./'+name+'/'}
    if raw not in allowed:
        raise ValueError('Noncanonical path in existing native archive: '+raw)
    return name


def manifest_from_existing_archive(archive,manifest_path,url,*,expected_target,archive_sha256,archive_size):
    """Validate original TAR bytes and emit the normal manifest without repacking.

    The ready status is the existing artifact contract, not publication, URL
    existence, runtime acceptance or evidence that the release is publicly live.
    """
    archive=Path(archive)
    manifest_path=Path(manifest_path)
    if not isinstance(archive_size,int) or isinstance(archive_size,bool) or archive_size<=0:
        raise ValueError('A positive trusted archive size is required')
    if (not isinstance(archive_sha256,str) or len(archive_sha256)!=64
        or any(c not in '0123456789abcdef' for c in archive_sha256)):
        raise ValueError('A lowercase trusted archive SHA256 is required')
    if not isinstance(expected_target,str) or not expected_target:
        raise ValueError('An explicit expected native target is required')
    if archive.is_symlink() or not archive.is_file():
        raise ValueError('Existing native archive must be a regular file')
    if manifest_path.exists() or manifest_path.is_symlink():
        raise ValueError('Preserving existing manifest; choose a fresh output')
    archive=archive.resolve();manifest_path=manifest_path.resolve()
    files={};modes={};directories=set();seen=set();folded=set();metadata_bytes={}
    with archive.open('rb') as raw:
        import os
        identity=os.fstat(raw.fileno())
        if identity.st_size!=archive_size or hashlib.file_digest(raw,'sha256').hexdigest()!=archive_sha256:
            raise ValueError('Existing native archive differs from its trusted size or SHA256')
        raw.seek(0)
        try:
            with gzip.GzipFile(fileobj=raw,mode='rb') as zipped:
                with tarfile.open(fileobj=zipped,mode='r|') as bundle:
                    for member in bundle:
                        name=_archive_name(member)
                        if name in seen:raise ValueError('Duplicate native archive member: '+name)
                        if expected_target.endswith('windows-msvc') and name.casefold() in folded:
                            raise ValueError('Case-colliding Windows native archive member: '+name)
                        seen.add(name);folded.add(name.casefold())
                        if (member.linkname or member.issparse()
                            or set(member.pax_headers)-{'path','mtime'}
                            or not math.isfinite(member.mtime)
                            or member.uid<0 or member.gid<0):
                            raise ValueError('Unsupported native archive metadata: '+name)
                        if member.isdir():
                            if member.size!=0 or member.mode!=0o755:
                                raise ValueError('Invalid native archive directory mode or size: '+name)
                            directories.add(name)
                            continue
                        if member.type not in {tarfile.REGTYPE,tarfile.AREGTYPE} or member.mode not in {0o644,0o755}:
                            raise ValueError('Unsupported native archive member type or mode: '+name)
                        if member.size<0:raise ValueError('Invalid native archive file size: '+name)
                        capture=name in {'codex-package.json','build-receipt.json'}
                        if capture and member.size>16*1024*1024:
                            raise ValueError('Native archive metadata is too large: '+name)
                        digest=hashlib.sha256();size=0;parts=[]
                        with bundle.extractfile(member) as payload:
                            for block in iter(lambda:payload.read(1024*1024),b''):
                                size+=len(block);digest.update(block)
                                if capture:parts.append(block)
                        if size!=member.size:raise ValueError('Truncated native archive member: '+name)
                        files[name]=digest.hexdigest();modes[name]=member.mode
                        if capture:metadata_bytes[name]=b''.join(parts)
                    if bundle.fileobj.tell()!=bundle.offset+tarfile.BLOCKSIZE:
                        raise ValueError('Native archive is missing its TAR end marker')
                    padding=0
                    # Drain through TAR's stream, including its buffered bytes;
                    # reading gzip directly could miss data hidden in that buffer.
                    for block in iter(lambda:bundle.fileobj.read(65536),b''):
                        padding+=len(block)
                        if padding>1024*1024 or any(block):
                            raise ValueError('Unexpected trailing data in native archive')
                    if padding<tarfile.BLOCKSIZE:
                        raise ValueError('Native archive is missing its second TAR end block')
        except (tarfile.TarError,EOFError,OSError) as error:
            raise ValueError('Invalid or truncated existing native archive') from error
        raw.seek(0)
        after=os.fstat(raw.fileno())
        if (after.st_size!=identity.st_size or after.st_mtime_ns!=identity.st_mtime_ns
            or after.st_ino!=identity.st_ino
            or hashlib.file_digest(raw,'sha256').hexdigest()!=archive_sha256):
            raise ValueError('Existing native archive changed during validation')
    if set(metadata_bytes)!={'codex-package.json','build-receipt.json'}:
        raise ValueError('Native archive is missing package metadata or build receipt')
    metadata=_archive_json(metadata_bytes['codex-package.json'],'codex-package.json')
    receipt=_archive_json(metadata_bytes['build-receipt.json'],'build-receipt.json')
    if metadata.get('target')!=expected_target:
        raise ValueError('Native archive target differs from the explicit expected target')
    recipe_id,toolchain_pin,binaries=_validate_package(metadata,receipt,files)
    for name,mode in modes.items():
        if mode!=(0o755 if name in binaries else 0o644):
            raise ValueError('Native archive executable/data mode mismatch: '+name)
    allowed_directories={'','bin','codex-resources','codex-path'}
    for name in files:
        for parent in PurePosixPath(name).parents:
            parent=str(parent)
            if parent=='.':continue
            if parent in files:raise ValueError('Native archive file is also a parent directory: '+parent)
            allowed_directories.add(parent)
    if not directories<=allowed_directories:
        raise ValueError('Native archive contains unattested directories')
    manifest=_manifest(metadata,files,archive,url,recipe_id,toolchain_pin)
    if manifest['archive']['sha256']!=archive_sha256:
        raise ValueError('Existing native archive changed before manifest publication')
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    with manifest_path.open('xb') as output:
        output.write((json.dumps(manifest,indent=2)+'\n').encode('utf-8'))
    return manifest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',type=Path)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--archive',type=Path,help='Create a fresh deterministic native archive from --package')
    mode.add_argument('--existing-archive',type=Path,help='Validate an immutable original archive; never repack it')
    parser.add_argument('--expected-target')
    parser.add_argument('--archive-sha256')
    parser.add_argument('--archive-size',type=int)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--url',required=True,help='Declared download URL; this utility does not publish or test URL existence')
    args=parser.parse_args()
    if args.existing_archive:
        if args.package or not all((args.expected_target,args.archive_sha256,args.archive_size)):
            parser.error('--existing-archive requires --expected-target, --archive-sha256 and --archive-size, and forbids --package')
        manifest=manifest_from_existing_archive(args.existing_archive,args.manifest,args.url,
            expected_target=args.expected_target,archive_sha256=args.archive_sha256,archive_size=args.archive_size)
        archive=args.existing_archive
    else:
        if not args.package or any(value is not None for value in (args.expected_target,args.archive_sha256,args.archive_size)):
            parser.error('Fresh --archive requires --package and forbids existing-archive identity arguments')
        manifest=package_native(args.package,args.archive,args.manifest,args.url)
        archive=args.archive
    summary={'archive':str(archive),'sha256':manifest['archive']['sha256'],'files':len(manifest['files'])}
    if args.existing_archive:summary.update(archive_reused=True,public_release_asserted=False)
    print(json.dumps(summary,indent=2))



if __name__=='__main__':main()
