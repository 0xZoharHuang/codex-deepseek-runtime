"""Verified archive boundaries and immutable repair preserve existing native files."""
import hashlib
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
    monkeypatch.setattr(installer.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(source.read_bytes()))
    package=installer.native_package(root,manifest)
    assert installer.valid_native(package,manifest)
    assert outside.read_bytes()==b'preserve external bytes'
    assert prior_partial.read_bytes()==b'preserve external bytes'
    assert (cache/source.name).read_bytes()==source.read_bytes()
