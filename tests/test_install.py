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
