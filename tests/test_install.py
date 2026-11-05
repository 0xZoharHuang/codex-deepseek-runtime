"""Verified archive boundaries and immutable repair preserve existing native files."""
import hashlib
import contextlib
import http.server
import ssl
import threading
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import pytest

spec=importlib.util.spec_from_file_location('dedicated_installer',Path(__file__).resolve().parents[1]/'install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


def archive_at(path,files):
    with tarfile.open(path,'w:gz') as archive:
        for name,data in files.items():
            member=tarfile.TarInfo(name);member.size=len(data);archive.addfile(member,io.BytesIO(data))
    return {'status':'ready','files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()},'archive':{'filename':path.name,'sha256':installer.sha(path)}}


def test_native_archive_repair_is_immutable_and_reused(tmp_path):
    root=tmp_path/'private';root.mkdir()
    files={name:b'#!/bin/sh\nexit 0\n' for name in installer.required_binaries()}
    source=tmp_path/'native.tar.gz';manifest=archive_at(source,files)
    first=installer.native_package(root,manifest,source)
    assert installer.valid_native(first,manifest)
    assert installer.native_package(root,manifest,source)==first
    (first/('bin/codex'+installer.EXE)).write_bytes(b'CORRUPTED_OLD_FILE')
    repaired=installer.native_package(root,manifest,source)
    assert repaired!=first and installer.valid_native(repaired,manifest)
    assert (first/('bin/codex'+installer.EXE)).read_bytes()==b'CORRUPTED_OLD_FILE'
    assert installer.native_package(root,manifest,source)==repaired


def test_verified_native_package_does_not_redownload_deleted_archive(tmp_path,monkeypatch):
    root=tmp_path/'private';root.mkdir()
    files={name:b'#!/bin/sh\nexit 0\n' for name in installer.required_binaries()}
    source=tmp_path/'native.tar.gz';manifest=archive_at(source,files)
    package=installer.native_package(root,manifest,source)
    (root/'downloads'/manifest['archive']['sha256'][:24]/source.name).unlink();manifest['archive']['url']='https://must-not-contact.invalid/native.tar.gz'
    def forbidden(*args,**kwargs):raise AssertionError('Unnecessary network download')
    monkeypatch.setattr(installer.urllib.request,'urlopen',forbidden)
    assert installer.native_package(root,manifest)==package


def test_native_archive_rejects_paths_even_when_outer_hash_matches(tmp_path):
    root=tmp_path/'private';root.mkdir();source=tmp_path/'native.tar.gz'
    manifest=archive_at(source,{'../escape':b'no'})
    with pytest.raises(SystemExit,match='Unsafe path'):installer.native_package(root,manifest,source)
    assert not (root/'escape').exists() and not (tmp_path/'escape').exists()


def test_installer_rejects_app_state_overlap_before_creating_it(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    for path in [tmp_path,tmp_path/'.codex',tmp_path/'.codex/nested']:
        with pytest.raises(SystemExit,match='overlap'):installer.isolated(path)
    assert not (tmp_path/'.codex').exists()


def test_source_identity_uses_platform_independent_case_sensitive_order():
    _,files=installer.source_identity()
    for prefix in ['native/','licenses/','src/']:
        names=[name for name in files if name.startswith(prefix)]
        assert names==sorted(names)


def test_same_archive_name_with_new_digest_preserves_both_cached_artifacts(tmp_path):
    root=tmp_path/'private';root.mkdir()
    builds=[]
    for revision in [b'FIRST',b'SECOND']:
        source=tmp_path/revision.decode()/'native.tar.gz';source.parent.mkdir()
        files={name:revision for name in installer.required_binaries()}
        manifest=archive_at(source,files)
        package=installer.native_package(root,manifest,source)
        builds.append((source,manifest,package))
    assert builds[0][2]!=builds[1][2]
    for source,manifest,package in builds:
        assert installer.valid_native(package,manifest)
        assert (root/'downloads'/manifest['archive']['sha256'][:24]/source.name).read_bytes()==source.read_bytes()


def test_build_staging_excludes_unattested_bytecode_and_previous_builds(tmp_path,monkeypatch):
    source=tmp_path/'checkout';source.mkdir();monkeypatch.setattr(installer,'SOURCE',source)
    expected={'pyproject.toml':b'[project]\nname="fixture"\n','src/fixture/__init__.py':b'VALUE=1\n'}
    for name,payload in {**expected,'src/fixture/__pycache__/__init__.cpython-313.pyc':b'stale bytecode',
                         'build/lib/fixture/removed.py':b'stale source',
                         'src/fixture.egg-info/SOURCES.txt':b'stale inventory'}.items():
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
    hashes={name:hashlib.sha256(payload).hexdigest() for name,payload in expected.items()}
    staged=installer.stage_build_source(hashes,tmp_path/'staged')
    actual={p.relative_to(staged).as_posix():p.read_bytes() for p in staged.rglob('*') if p.is_file()}
    assert actual==expected


def test_build_staging_rejects_a_changed_source_snapshot(tmp_path,monkeypatch):
    source=tmp_path/'checkout';source.mkdir();monkeypatch.setattr(installer,'SOURCE',source)
    (source/'module.py').write_bytes(b'changed source')
    with pytest.raises(SystemExit,match='Source changed before packaging: module.py'):
        installer.stage_build_source({'module.py':hashlib.sha256(b'original source').hexdigest()},tmp_path/'staged')


@pytest.mark.parametrize('system,machine,target',[('Windows','AMD64','x86_64-pc-windows-msvc'),('Linux','x86_64','x86_64-unknown-linux-gnu'),('Darwin','arm64','aarch64-apple-darwin')])
@pytest.mark.parametrize('identity',['old-ready','different-target','current'])
def test_new_source_rejects_old_ready_manifest_without_touching_it(tmp_path,monkeypatch,system,machine,target,identity):
    source=tmp_path/'source';(source/'native').mkdir(parents=True)
    original=Path(__file__).resolve().parents[1]
    (source/'build_native.py').write_bytes((original/'build_native.py').read_bytes())
    (source/'native/runtime-overlays.json').write_bytes((original/'native/runtime-overlays.json').read_bytes())
    monkeypatch.setattr(installer,'SOURCE',source)
    monkeypatch.setattr(installer.platform,'system',lambda:system);monkeypatch.setattr(installer.platform,'machine',lambda:machine)
    record={'status':'ready','target':target,'archive':{'filename':'native.tar.gz','sha256':'a'*64}}
    if identity=='current':record['native_recipe_id']=installer.expected_native_recipe(target)
    elif identity=='different-target':record['native_recipe_id']=installer.expected_native_recipe('x86_64-unknown-linux-gnu' if system!='Linux' else 'x86_64-pc-windows-msvc')
    name='runtime.json' if system=='Darwin' else 'runtime-'+target+'.json';path=source/'native'/name
    path.write_text(json.dumps(record));before=path.read_bytes()
    if identity=='current':assert installer.runtime_manifest()==record
    else:
        with pytest.raises(SystemExit,match='not qualified for this source/toolchain/patch combination'):installer.runtime_manifest()
    assert path.read_bytes()==before


@pytest.mark.parametrize('identity',['current','wrong-recipe','wrong-target'])
def test_explicit_candidate_manifest_is_verified_without_replacing_published_record(tmp_path,monkeypatch,identity):
    source=tmp_path/'source';(source/'native').mkdir(parents=True)
    monkeypatch.setattr(installer,'SOURCE',source)
    monkeypatch.setattr(installer.platform,'system',lambda:'Windows');monkeypatch.setattr(installer.platform,'machine',lambda:'AMD64')
    monkeypatch.setattr(installer,'expected_native_recipe',lambda target:'qualified-recipe')
    published=source/'native/runtime-x86_64-pc-windows-msvc.json';published.write_text('{"status":"old-ready"}');original=published.read_bytes()
    candidate=tmp_path/'candidate.json';record={'status':'ready','target':'x86_64-pc-windows-msvc','native_recipe_id':'qualified-recipe','published':False,'archive':{'filename':'native.tar.gz','sha256':'a'*64}}
    if identity=='wrong-recipe':record['native_recipe_id']='other'
    if identity=='wrong-target':record['target']='x86_64-unknown-linux-gnu'
    candidate.write_text(json.dumps(record));before=candidate.read_bytes()
    if identity=='current':assert installer.runtime_manifest(candidate)==record
    else:
        with pytest.raises(SystemExit):installer.runtime_manifest(candidate)
    assert published.read_bytes()==original and candidate.read_bytes()==before


def test_explicit_unready_manifest_stops_before_native_archive_or_installation(tmp_path,monkeypatch):
    monkeypatch.setattr(installer,'runtime_manifest',lambda path:{'status':'pending'})
    monkeypatch.setattr(installer,'native_package',lambda *a,**k:pytest.fail('unready candidate touched native package'))
    root=tmp_path/'new-install'
    with pytest.raises(SystemExit,match='not ready'):installer.install(root,native_manifest_path=tmp_path/'candidate.json',activate=False)
    assert not root.exists()


@pytest.mark.parametrize('field,value',[
    ('filename',value) for value in [
        '', '.', '..', '../escape.tar.gz', r'..\escape.tar.gz',
        '/escape.tar.gz', r'\escape.tar.gz', r'C:\escape.tar.gz', 'C:escape.tar.gz',
        r'\\server\share\escape.tar.gz', r'\\?\C:\escape.tar.gz',
        'sub//escape.tar.gz', r'sub\\escape.tar.gz', 'archive.tar.gz:stream',
        'NUL', 'con.tar.gz', 'COM1.tar.gz', 'LPT9.tar.gz', 'COM\u00b9.tar.gz',
        'archive.tar.gz ', 'archive.tar.gz.', 'bad\x00name', 'bad\nname',
        'bad\tname', 'bad\x7fname', 'bad*name', 'bad?name', 'bad|name',
        'bad<name', 'bad>name', 'bad"name', None, [], 42,
    ]
]+[
    ('sha256',value) for value in [
        '', 'a'*63, 'a'*65, 'A'*64, 'g'*64, '../'+'a'*61,
        r'..\..\\'+'a'*57, '/tmp/'+'a'*59, None, [], True,
    ]
])
def test_archive_descriptor_rejected_before_any_install_write(tmp_path,monkeypatch,field,value):
    source=tmp_path/'safe.tar.gz'
    manifest=archive_at(source,{name:b'not executed fixture' for name in installer.required_binaries()})
    manifest['archive'][field]=value
    root=tmp_path/'new-root'
    monkeypatch.setattr(installer.shutil,'copy2',lambda *a,**k:pytest.fail('invalid descriptor reached copy'))
    monkeypatch.setattr(installer.urllib.request,'urlopen',lambda *a,**k:pytest.fail('invalid descriptor reached network'))
    with pytest.raises(SystemExit,match='archive'):
        installer.native_package(root,manifest,source)
    assert not root.exists()


@pytest.mark.parametrize('field,value',[('filename','../escape.tar.gz'),('sha256','Z'*64)])
def test_public_manifest_rejects_unsafe_archive_after_recipe_gate(tmp_path,monkeypatch,field,value):
    monkeypatch.setattr(installer.platform,'system',lambda:'Windows')
    monkeypatch.setattr(installer.platform,'machine',lambda:'AMD64')
    monkeypatch.setattr(installer,'expected_native_recipe',lambda target:'fixture-recipe')
    manifest={'status':'ready','target':'x86_64-pc-windows-msvc','native_recipe_id':'fixture-recipe',
              'archive':{'filename':'safe.tar.gz','sha256':'a'*64}}
    manifest['archive'][field]=value
    path=tmp_path/'explicit.json';path.write_text(json.dumps(manifest));before=path.read_bytes()
    with pytest.raises(SystemExit,match='archive'):installer.runtime_manifest(path)
    assert path.read_bytes()==before


def test_main_archive_preflight_precedes_root_permissions_and_lock(tmp_path,monkeypatch):
    root=tmp_path/'must-not-exist'
    monkeypatch.setattr(installer.sys,'argv',['install.py','--root',str(root),'--no-activate'])
    monkeypatch.setattr(installer,'runtime_manifest',lambda path:{
        'status':'ready','archive':{'filename':'../escape','sha256':'a'*64}})
    monkeypatch.setattr(installer,'private_directory',lambda *a:pytest.fail('unsafe manifest changed directory or ACL'))
    with pytest.raises(SystemExit,match='archive'):installer.main()
    assert not root.exists()


def _redirect_directory(link,outside):
    import os
    import subprocess
    link.parent.mkdir(parents=True,exist_ok=True)
    if os.name=='nt':
        result=subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/cmd.exe'),
            '/d','/c','mklink','/J',str(link),str(outside)],capture_output=True)
        assert result.returncode==0,result.stderr.decode(errors='replace')
    else:
        link.symlink_to(outside,target_is_directory=True)


@pytest.mark.parametrize('location',['downloads','download-digest','native','native-digest','native-repair'])
def test_native_storage_rejects_real_directory_redirection_before_writes(tmp_path,monkeypatch,location):
    source=tmp_path/'safe.tar.gz'
    manifest=archive_at(source,{name:b'never executed' for name in installer.required_binaries()})
    digest=manifest['archive']['sha256'][:24]
    root=tmp_path/'install';root.mkdir()
    outside=tmp_path/'outside';outside.mkdir();canary=outside/'sentinel';canary.write_bytes(b'untouched')
    choices={'downloads':root/'downloads','download-digest':root/'downloads'/digest,
             'native':root/'native','native-digest':root/'native'/digest,
             'native-repair':root/'native'/(digest+'-repair-fixture')}
    link=choices[location];_redirect_directory(link,outside)
    monkeypatch.setattr(installer.urllib.request,'urlopen',lambda *a,**k:pytest.fail('redirect reached network'))
    try:
        with pytest.raises(SystemExit,match='[Rr]edirect|[Uu]nsafe|[Oo]wned'):
            installer.native_package(root,manifest,source)
        assert canary.read_bytes()==b'untouched'
        assert list(outside.iterdir())==[canary]
        # Nothing else in the selected tree should have been created.
        assert not (root/'downloads'/digest/source.name).is_file()
    finally:
        if link.exists():link.rmdir() if installer.WINDOWS else link.unlink()


def test_cached_archive_hardlink_is_not_an_owned_cache_file(tmp_path):
    import os
    source=tmp_path/'safe.tar.gz'
    manifest=archive_at(source,{name:b'never executed' for name in installer.required_binaries()})
    root=tmp_path/'install';cache=root/'downloads'/manifest['archive']['sha256'][:24]
    cache.mkdir(parents=True)
    leaf=cache/source.name;os.link(source,leaf);before=source.read_bytes()
    with pytest.raises(SystemExit,match='[Oo]wned|[Uu]nsafe'):
        installer.native_package(root,manifest,source)
    assert source.read_bytes()==before and not (root/'native').exists()


def test_download_uses_exclusive_staging_without_overwriting_unowned_partial(tmp_path,monkeypatch):
    import os
    source=tmp_path/'native.tar.gz'
    manifest=archive_at(source,{name:b'never executed' for name in installer.required_binaries()})
    manifest['archive']['url']='https://fixture.invalid/native.tar.gz'
    root=tmp_path/'install';cache=root/'downloads'/manifest['archive']['sha256'][:24]
    cache.mkdir(parents=True)
    outside=tmp_path/'sentinel';outside.write_bytes(b'preserve external bytes')
    prior_partial=(cache/source.name).with_suffix('.partial');os.link(outside,prior_partial)
    response = io.BytesIO(source.read_bytes()); response.status = 200; response.headers = {}
    monkeypatch.setattr(installer.urllib.request,'urlopen',lambda *a,**k:response)
    package=installer.native_package(root,manifest)
    assert installer.valid_native(package,manifest)
    assert outside.read_bytes()==b'preserve external bytes'
    assert prior_partial.read_bytes()==b'preserve external bytes'
    assert (cache/source.name).read_bytes()==source.read_bytes()


def test_new_native_cache_and_extraction_bind_owner_privacy(tmp_path,assert_private):
    source=tmp_path/'native.tar.gz'
    manifest=archive_at(source,{name:b'NONEXECUTABLE_FIXTURE' for name in installer.required_binaries()})
    root=tmp_path/'new-install'
    package=installer.native_package(root,manifest,source)
    if installer.WINDOWS:
        for path in [root/'downloads',root/'downloads'/manifest['archive']['sha256'][:24]]:
            assert_private(path)
        for path in [root,root/'native']:
            assert world_access(path)==[(0,0xa0)]
        for path in [package,package/'bin',package/'codex-resources',package/'codex-path']:
            assert world_access(path)==[(3,0x1200a9)]
        assert world_access(package/next(iter(installer.required_binaries())))==[(0,0x1200a9)]
    if not installer.WINDOWS:
        for path in [root,root/'downloads',root/'downloads'/manifest['archive']['sha256'][:24],root/'native',package]:
            assert path.stat().st_mode & 0o777 == 0o700


def test_release_private_directory_precedes_venv_creation(tmp_path,monkeypatch,assert_private):
    root=tmp_path/'new-install'
    native={'status':'ready','native_recipe_id':'fixture','upstream_version':'fixture',
            'archive':{'sha256':'a'*64},'files':{}}
    monkeypatch.setattr(installer,'runtime_manifest',lambda path=None:native)
    monkeypatch.setattr(installer,'native_package',lambda *args:root/'native/fixture')
    monkeypatch.setattr(installer,'source_identity',lambda:('source-fixture',{}))
    entry_path=installer.console_entry(root/'releases/source-fixture/venv')
    monkeypatch.setattr(installer,'valid_release',lambda path,*args:installer.console_entry(path/'venv').is_file())
    calls=[]
    real_private=installer.private_directory
    def private(path):
        calls.append(Path(path))
        return real_private(path)
    monkeypatch.setattr(installer,'private_directory',private)
    def create(environment,with_pip):
        assert environment.parent in calls and environment.parent.parent in calls
        if installer.WINDOWS:assert_private(environment.parent)
        entry_path.parent.mkdir(parents=True);entry_path.write_bytes(b'NOT_EXECUTABLE')
    monkeypatch.setattr(installer.venv,'create',create)
    from types import SimpleNamespace
    monkeypatch.setattr(installer.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0))
    result=installer.install(root,activate=False)
    assert result==root/'releases/source-fixture'
    assert result in calls
    if installer.WINDOWS:assert_private(entry_path)


def world_access(path):
    import win32security
    acl=win32security.GetFileSecurity(str(path),win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
    result=[]
    for index in range(acl.GetAceCount()):
        header,mask,sid=acl.GetAce(index)
        if win32security.ConvertSidToStringSid(sid)=='S-1-1-0':
            assert header[0]==win32security.ACCESS_ALLOWED_ACE_TYPE
            result.append((header[1],mask))
    return result


@pytest.mark.skipif(not installer.WINDOWS,reason='Windows native artifact DACL contract')
@pytest.mark.parametrize('with_archive',[False,True])
def test_native_reuse_repairs_only_artifact_rx_without_exposing_private_siblings(tmp_path,assert_private,with_archive):
    source=tmp_path/'native.tar.gz'
    manifest=archive_at(source,{name:b'PUBLIC_DISTRIBUTION' for name in installer.required_binaries()})
    root=tmp_path/'install'
    package=installer.native_package(root,manifest,source)
    canaries=[]
    for name in ['codex-home','releases','downloads']:
        directory=root/name;installer.private_directory(directory)
        canary=directory/'private-canary';canary.write_bytes(b'PRIVATE');canaries.append(canary)
    installer.private_directory(package)
    assert world_access(package)==[]
    restored=installer.native_package(root,manifest,source if with_archive else None)
    assert restored==package and installer.valid_native(package,manifest)
    assert world_access(package)==[(3,0x1200a9)]
    for path in [root,root/'native']:assert world_access(path)==[(0,0xa0)]
    for canary in canaries:
        assert canary.read_bytes()==b'PRIVATE'
        assert_private(canary.parent);assert_private(canary)
        assert world_access(canary)==[]


@pytest.mark.parametrize('corruption',['extra-file','hardlink','bad-hash','directory-junction'])
def test_artifact_permissions_require_complete_regular_unredirected_manifest(tmp_path,monkeypatch,corruption):
    import os
    source=tmp_path/'native.tar.gz'
    manifest=archive_at(source,{name:b'PUBLIC_DISTRIBUTION' for name in installer.required_binaries()})
    root=tmp_path/'install';package=installer.native_package(root,manifest,source)
    target=package/next(iter(installer.required_binaries()))
    external=tmp_path/'external';external.mkdir();canary=external/'sentinel';canary.write_bytes(b'PRIVATE')
    link=None
    if corruption=='extra-file':(package/'unlisted-secret').write_bytes(b'PRIVATE')
    elif corruption=='hardlink':os.link(target,external/'shared')
    elif corruption=='bad-hash':target.write_bytes(b'TAMPERED')
    else:
        link=package/'unexpected-link';_redirect_directory(link,external)
    monkeypatch.setattr(installer._support,'native_artifact_access',lambda *args:pytest.fail('Unverified package reached permission mutation'))
    try:
        assert not installer.valid_native(package,manifest)
        with pytest.raises(ValueError):installer.readable_native_package(root,package,manifest)
        assert canary.read_bytes()==b'PRIVATE'
    finally:
        if link is not None:link.rmdir() if installer.WINDOWS else link.unlink()

@contextlib.contextmanager
def native_http_fixture(responses):
    """Real HTTP framing, including incomplete bodies and a fresh URL request on retry."""
    ranges = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            index = len(ranges)
            ranges.append(self.headers.get('Range'))
            if index >= len(responses):
                self.send_error(500, 'unexpected additional request')
                return
            status, headers, body = responses[index]
            if status == 0:
                self.close_connection = True
                return
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, str(value))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    with http.server.HTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01},
                                  daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/release', ranges
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize('case', ['stable', 'no-length', 'resume', 'full-restart', 'short-ranges',
                                  'first-empty', 'disconnect-before-headers'])
def test_native_public_http_recovers_and_verifies_actual_package(tmp_path, monkeypatch, case):
    monkeypatch.setenv('no_proxy', '127.0.0.1')
    source = tmp_path/'archive.tar.gz'
    manifest = archive_at(source, {name:b'verified fixture' for name in installer.required_binaries()})
    data = source.read_bytes()
    n = len(data)
    full = (200, {'Content-Length': n}, data)
    initial = (200, {'Content-Length': n}, data[:7])
    def part(start, stop):
        return 206, {'Content-Range':f'bytes {start}-{stop-1}/{n}',
                     'Content-Length':stop-start}, data[start:stop]
    scripts = {
        'stable': ([full], [None]),
        'no-length': ([(200, {}, data)], [None]),
        'resume': ([initial, part(7,n)], [None, 'bytes=7-']),
        'full-restart': ([initial, full], [None, 'bytes=7-']),
        'short-ranges': ([initial, part(7,8), part(8,n)], [None, 'bytes=7-', 'bytes=8-']),
        'first-empty': ([(200, {'Content-Length':n}, b''), full], [None,None]),
        'disconnect-before-headers': ([(0, {}, b''), full], [None,None]),
    }
    responses, expected_ranges = scripts[case]
    with native_http_fixture(responses) as (url, ranges):
        manifest['archive']['url'] = url
        package = installer.native_package(tmp_path/'install', manifest)
    assert ranges == expected_ranges
    assert installer.valid_native(package, manifest)
    cached = tmp_path/'install'/'downloads'/manifest['archive']['sha256'][:24]
    assert (cached/source.name).read_bytes() == data
    assert not list(cached.glob('*.partial'))


@pytest.mark.parametrize('case', [
    'unexpected-206', 'bad-start', 'bad-end', 'new-total', 'length-mismatch', 'no-content-range',
    'whole-200-with-range', 'zero', 'three-short', 'corrupt', '401', '403', '404', '429',
])
def test_native_public_http_rejects_incomplete_or_invalid_archive(tmp_path, monkeypatch, case):
    monkeypatch.setenv('no_proxy', '127.0.0.1')
    source = tmp_path/'archive.tar.gz'
    manifest = archive_at(source, {name:b'never run' for name in installer.required_binaries()})
    data = source.read_bytes()
    n = len(data)
    initial = (200, {'Content-Length':n}, data[:7])
    empty = (200, {'Content-Length':n}, b'')
    if case.isdigit():
        responses, match = [(int(case), {}, b'')], case
    elif case == 'corrupt':
        responses, match = [(200, {'Content-Length':n}, b'x' + data[1:])], 'checksum mismatch'
    elif case == 'zero':
        responses, match = [empty] * 3, 'after 3 attempts'
    elif case == 'three-short':
        responses = [initial] + [(206, {'Content-Range':f'bytes {i}-{n-1}/{n}',
                                         'Content-Length':n-i}, data[i:i+1]) for i in (7,8)]
        match = 'after 3 attempts'
    elif case == 'unexpected-206':
        responses, match = [(206, {'Content-Range':f'bytes 0-{n-1}/{n}'}, data)], 'HTTP status 206'
    elif case == 'whole-200-with-range':
        responses = [initial, (200, {'Content-Range':f'bytes 7-{n-1}/{n}'}, data)]
        match = 'Unexpected Content-Range'
    else:
        bounds = {'bad-start': f'bytes 8-{n-1}/{n}', 'bad-end': f'bytes 7-{n}/{n}',
                  'new-total': f'bytes 7-{n-1}/{n+1}', 'length-mismatch': f'bytes 7-{n-1}/{n}',
                  'no-content-range': None}[case]
        headers = {'Content-Length': n-7+(case=='length-mismatch')}
        if bounds:
            headers['Content-Range'] = bounds
        responses, match = [initial, (206, headers, data[7:])], 'Content-Range'
    root = tmp_path/'install'
    cache = root/'downloads'/manifest['archive']['sha256'][:24]
    cache.mkdir(parents=True)
    previous = cache/'.native-other-run.partial'
    previous.write_bytes(b'unrelated file')
    with native_http_fixture(responses) as (url, ranges):
        manifest['archive']['url'] = url
        error = installer.urllib.error.HTTPError if case.isdigit() else SystemExit
        with pytest.raises(error, match=match):
            installer.native_package(root, manifest)
    assert len(ranges) == len(responses)
    assert previous.read_bytes() == b'unrelated file'
    assert set(cache.iterdir()) == {previous}
    assert not (root/'native').exists()


def test_download_cleanup_never_removes_replaced_staging_file(tmp_path):
    staging = tmp_path/'.native-owned.partial'
    staging.write_bytes(b'original')
    identity = staging.stat()
    staging.rename(tmp_path/'moved-original')
    staging.write_bytes(b'replacement')
    installer.discard_owned_download(tmp_path, staging, identity)
    assert staging.read_bytes() == b'replacement'

@pytest.mark.parametrize('reason, expected_attempts', [
    (TimeoutError('transport timed out'), 3),
    (ConnectionRefusedError('connection refused'), 3),
    (ssl.SSLCertVerificationError('untrusted certificate'), 1),
])
def test_only_transient_network_open_failures_retry(monkeypatch, reason, expected_attempts):
    calls = []
    def fail_open(request, timeout):
        calls.append((request.full_url, request.get_header('Range'), timeout))
        raise installer.urllib.error.URLError(reason)
    monkeypatch.setattr(installer.urllib.request, 'urlopen', fail_open)
    output = io.BytesIO()
    url = 'https://release.invalid/native'
    error = SystemExit if expected_attempts == 3 else installer.urllib.error.URLError
    with pytest.raises(error, match=str(reason)):
        installer.download_native_archive(url, output)
    assert calls == [(url,None,90)] * expected_attempts
    assert not output.getvalue()


def test_native_public_http_resumes_across_read_buffers(monkeypatch):
    monkeypatch.setenv('no_proxy', '127.0.0.1')
    data = hashlib.shake_256(b'large transfer regression').digest(300000)
    cut = 2 * 65536 + 117
    responses = [(200, {'Content-Length':len(data)}, data[:cut]),
                 (206, {'Content-Range':f'bytes {cut}-{len(data)-1}/{len(data)}'}, data[cut:])]
    output = io.BytesIO()
    with native_http_fixture(responses) as (url, ranges):
        installer.download_native_archive(url, output)
    assert ranges == [None, f'bytes={cut}-']
    assert output.getvalue() == data
