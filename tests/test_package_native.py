"""Packaging must not bless bytes changed after the native build was attested."""
import importlib.util
import json
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('native_packager',Path(__file__).resolve().parents[1]/'package_native.py')
packager=importlib.util.module_from_spec(spec);spec.loader.exec_module(packager)


def native_fixture(root):
    recipe_id,toolchain_pin=packager.expected_native_recipe('x86_64-unknown-linux-gnu')
    for name in ['bin/codex','bin/codex-code-mode-host','bin/codex-http-transport','codex-path/rg','codex-resources/bwrap']:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'ATTESTED_FIXTURE_BYTES')
    (root/'codex-package.json').write_text(json.dumps({
        'distribution':'codex-deepseek-runtime','target':'x86_64-unknown-linux-gnu',
        'variant':'codex','resourcesDir':'codex-resources','pathDir':'codex-path',
        'version':'fixture','upstreamCommit':'fixed-commit','patchSha256':'fixed-patch','recipeId':recipe_id,
    }),encoding='utf-8')
    receipt={'profile':'release','upstream_commit':'fixed-commit','patch_sha256':'fixed-patch','native_recipe_id':recipe_id,'build_toolchain_pin':toolchain_pin,
             'files':{p.relative_to(root).as_posix():packager.sha(p) for p in root.rglob('*') if p.is_file()}}
    (root/'build-receipt.json').write_text(json.dumps(receipt),encoding='utf-8')


@pytest.mark.parametrize('change',['bytes','extra','missing','different-patch'])
def test_post_build_mutation_is_rejected_before_creating_an_archive(tmp_path,change):
    package=tmp_path/'native';native_fixture(package)
    if change=='bytes':(package/'bin/codex').write_bytes(b'UNATTESTED_REPLACEMENT')
    elif change=='extra':(package/'extra').write_bytes(b'UNATTESTED_FILE')
    elif change=='missing':(package/'codex-path/rg').unlink()
    else:
        path=package/'build-receipt.json';receipt=json.loads(path.read_text());receipt['patch_sha256']='other';path.write_text(json.dumps(receipt))
    archive=tmp_path/'native.tar.gz'
    with pytest.raises(ValueError,match='changed after its build receipt'):
        packager.package_native(package,archive,tmp_path/'manifest.json','https://example.invalid/fixture')
    assert not archive.exists()


def test_unchanged_build_can_be_reproduced_as_identical_archives(tmp_path):
    package=tmp_path/'native';native_fixture(package)
    one=packager.package_native(package,tmp_path/'one.tar.gz',tmp_path/'one.json','https://example.invalid/one')
    two=packager.package_native(package,tmp_path/'two.tar.gz',tmp_path/'two.json','https://example.invalid/two')
    assert one['archive']['sha256']==two['archive']['sha256']
    assert one['files']==two['files']


@pytest.mark.parametrize('change',['missing-recipe','different-recipe','different-toolchain'])
def test_self_consistent_old_combination_cannot_be_blessed_as_new(tmp_path,change):
    package=tmp_path/'native';native_fixture(package)
    meta=package/'codex-package.json';metadata=json.loads(meta.read_text())
    receipt_path=package/'build-receipt.json';receipt=json.loads(receipt_path.read_text())
    if change=='missing-recipe':metadata.pop('recipeId');receipt.pop('native_recipe_id')
    elif change=='different-recipe':metadata['recipeId']=receipt['native_recipe_id']='0'*64
    else:receipt['build_toolchain_pin']='1.95.0'
    meta.write_text(json.dumps(metadata))
    receipt['files']={p.relative_to(package).as_posix():packager.sha(p) for p in package.rglob('*') if p.is_file() and p.name!='build-receipt.json'}
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='recipe or toolchain'):
        packager.package_native(package,tmp_path/'native.tar.gz',tmp_path/'manifest.json','https://example.invalid/fixture')
    assert not (tmp_path/'native.tar.gz').exists()



@pytest.mark.parametrize('autocrlf', ['false', 'true'])
def test_manifest_utf8_LF_is_stable_through_windows_default_and_Git_roundtrip(tmp_path, monkeypatch, autocrlf):
    import hashlib
    import subprocess

    package=tmp_path/'native';native_fixture(package)
    checkout=tmp_path/'git-checkout';checkout.mkdir()
    attributes=Path(__file__).resolve().parents[1]/'.gitattributes'
    (checkout/'.gitattributes').write_bytes(attributes.read_bytes())
    manifest_path=checkout/'native/runtime-fixture.json'
    original_write=Path.write_text
    selected_newline=[]
    def windows_default_write(path,data,encoding=None,errors=None,newline=None):
        selected_newline.append(newline)
        if newline is None:
            # Exercise the Windows default on every CI OS, not only on Windows.
            return path.write_bytes(data.replace('\r\n','\n').replace('\n','\r\n').encode(encoding or 'utf-8'))
        return original_write(path,data,encoding=encoding,errors=errors,newline=newline)
    monkeypatch.setattr(Path,'write_text',windows_default_write)
    manifest=packager.package_native(package,tmp_path/'native.tar.gz',manifest_path,'https://example.invalid/fixture')
    raw=manifest_path.read_bytes()
    assert selected_newline==['\n']
    assert raw.endswith(b'\n') and b'\r' not in raw and json.loads(raw.decode('utf-8'))==manifest
    def git(*args):
        return subprocess.run(['git','-C',str(checkout),'-c','core.autocrlf='+autocrlf,*args],
                              check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
    git('init','-q')
    raw_object=git('hash-object','--no-filters','native/runtime-fixture.json').strip()
    cleaned=git('hash-object','--path=native/runtime-fixture.json','native/runtime-fixture.json').strip()
    assert raw_object==cleaned
    git('add','.gitattributes','native/runtime-fixture.json')
    blob=git('cat-file','blob',':native/runtime-fixture.json')
    assert blob==raw and hashlib.sha256(blob).digest()==hashlib.sha256(raw).digest()
    manifest_path.unlink()
    git('checkout-index','--force','--','native/runtime-fixture.json')
    assert manifest_path.read_bytes()==raw


def test_hardlinked_staging_is_regular_content_and_deterministic(tmp_path):
    import hashlib
    import os
    import tarfile

    package = tmp_path / "native"
    native_fixture(package)
    original = package / "bin/codex"
    linked = package / "bin/codex-http-transport"
    linked.unlink()
    os.link(original, linked)
    assert original.stat().st_ino == linked.stat().st_ino
    assert original.stat().st_nlink >= 2

    one = packager.package_native(
        package, tmp_path / "one.tar.gz", tmp_path / "one.json",
        "https://example.invalid/one")
    two = packager.package_native(
        package, tmp_path / "two.tar.gz", tmp_path / "two.json",
        "https://example.invalid/two")
    assert one["archive"]["sha256"] == two["archive"]["sha256"]
    assert one["files"] == two["files"]
    with tarfile.open(tmp_path / "one.tar.gz") as bundle:
        members = bundle.getmembers()
        assert {item.name for item in members} == set(one["files"])
        # The strict normal installer accepts regular data, never TAR hardlinks.
        assert all(item.isfile() and not item.linkname for item in members)
        for item in members:
            stream = bundle.extractfile(item)
            assert stream is not None
            with stream:
                assert hashlib.sha256(stream.read()).hexdigest() == one["files"][item.name]
