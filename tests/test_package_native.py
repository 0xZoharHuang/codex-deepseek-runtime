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
