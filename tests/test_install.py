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
    files={name:b'#!/bin/sh\nexit 0\n' for name in ['bin/codex','bin/codex-code-mode-host','codex-path/rg','bin/codex-http-transport']}
    source=tmp_path/'native.tar.gz';manifest=archive_at(source,files)
    first=installer.native_package(root,manifest,source)
    assert installer.valid_native(first,manifest)
    assert installer.native_package(root,manifest,source)==first
    (first/'bin/codex').write_bytes(b'CORRUPTED_OLD_FILE')
    repaired=installer.native_package(root,manifest,source)
    assert repaired!=first and installer.valid_native(repaired,manifest)
    assert (first/'bin/codex').read_bytes()==b'CORRUPTED_OLD_FILE'
    assert installer.native_package(root,manifest,source)==repaired


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
