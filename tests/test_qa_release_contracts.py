"""QA contracts use pinned tiny source archives and fake package bytes, never native execution."""
import copy
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace

import pytest
import build_native as build
import verify_native_source as audit
import verify_installation as installation
import assemble_release_acceptance as acceptance

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'x86_64-unknown-linux-gnu'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


@pytest.fixture
def source_case(tmp_path, monkeypatch):
    source = tmp_path/'qa-source'; (source/'native').mkdir(parents=True)
    files = {
        'codex-rs/Cargo.lock': b'[[package]]\nname="fixture"\nversion="1.0.0"\nsource="registry+https://invalid.example"\n',
        'codex-rs/rust-toolchain.toml': b'[toolchain]\nchannel="1.95.0"\n',
        'codex-rs/deepseek-http-transport/main.rs': b'original\n',
        'codex-rs/core/src/session/untouched.rs': b'keep-native-session\n',
        'codex-rs/windows-sandbox-rs/declared.rs': b'old-policy\n',
    }
    archive = tmp_path/'upstream.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        for name, data in files.items():
            member = tarfile.TarInfo('codex-'+build.COMMIT+'/'+name); member.size=len(data)
            bundle.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(build, 'SOURCE_ASSET', ('https://invalid.example/source', build.sha(archive)))
    monkeypatch.setattr(build, 'SOURCE', source); monkeypatch.setattr(audit, 'SOURCE', source)
    monkeypatch.setattr(build, 'build_assets', lambda target: {'source': build.SOURCE_ASSET})
    for name in ['verify_native_source.py', 'build_native.py']:
        (source/name).write_bytes((ROOT/name).read_bytes())
    common_path = 'codex-rs/deepseek-http-transport/main.rs'
    protected_path = 'codex-rs/windows-sandbox-rs/declared.rs'
    def patch(name, path, before, after):
        payload=''.join(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True), fromfile='a/'+path, tofile='b/'+path))
        (source/'native'/name).write_text(payload, encoding='utf-8', newline='\n')
        return {'file':name,'sha256':build.sha(source/'native'/name)}
    base = patch('codex-runtime.patch',common_path,files[common_path],b'base\n')
    overlay = patch('fixture-overlay.patch',protected_path,files[protected_path],b'new-policy\n')
    recipe={'schema':1,'upstream_commit':build.COMMIT,'upstream_toolchain':'1.95.0','build_toolchain':'1.99.1',
            'required_base_inputs':{TARGET:[base]},'overlays':[{**overlay,'changes_by_target':{TARGET:[
                {'path':protected_path,'before_sha256':digest(files[protected_path]),'after_sha256':digest(b'new-policy\n')}]}}]}
    write_json(source/'native/runtime-overlays.json',recipe)
    write_json(source/'native/source-changes.json',{'base_commit':build.COMMIT,'files':[
        {'path':common_path,'base_sha256':digest(files[common_path]),'fork_sha256':digest(b'base\n')}]})
    monkeypatch.setattr(build,'runtime_recipe',lambda:copy.deepcopy(recipe))
    tree=tmp_path/'tree'
    for name,data in files.items():
        p=tree/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    (tree/common_path).write_bytes(b'base\n');(tree/protected_path).write_bytes(b'new-policy\n')
    contract=audit.source_contract(TARGET,archive)
    combined=digest(build.combined_patch(tree,archive,contract['declared']).encode())
    package=tmp_path/'package'
    for name in ['bin/codex','bin/codex-http-transport','bin/codex-code-mode-host','codex-path/rg','codex-resources/bwrap']:
        p=package/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'NONEXECUTABLE-'+name.encode())
    metadata={'target':TARGET,'upstreamCommit':build.COMMIT,'version':build.VERSION,
              'recipeId':contract['recipe_id'],'patchSha256':combined,'patchInputs':contract['patch_inputs']}
    write_json(package/'codex-package.json',metadata)
    package_files={p.relative_to(package).as_posix():build.sha(p) for p in package.rglob('*') if p.is_file()}
    receipt={'upstream_commit':build.COMMIT,'native_recipe_id':contract['recipe_id'],
             'patch_inputs':contract['patch_inputs'],'source_files':contract['declared'],
             'patch_sha256':combined,'combined_patch_sha256':combined,
             'build_toolchain_pin':'1.99.1','upstream_toolchain_pin':'1.95.0','rustc':'rustc 1.99.1 fixture',
             'overlay_recipe_sha256':build.sha(source/'native/runtime-overlays.json'),
             'archives':{'source':{'url':build.SOURCE_ASSET[0],'sha256':build.SOURCE_ASSET[1]}},
             'files':package_files}
    write_json(package/'build-receipt.json',receipt)
    native={'status':'ready','target':TARGET,'upstream_commit':build.COMMIT,'upstream_version':build.VERSION,
            'native_recipe_id':contract['recipe_id'],'build_toolchain_pin':'1.99.1','patch_sha256':combined,
            'files':{p.relative_to(package).as_posix():build.sha(p) for p in package.rglob('*') if p.is_file()}}
    real_run=subprocess.run
    git=os.environ.get('CODEX_DEEPSEEK_TEST_GIT','git')
    def git_only(command,**kwargs):
        assert command[0]=='git','Source QA must not launch a compiler, native executable or installer'
        return real_run([git,*command[1:]],**kwargs)
    monkeypatch.setattr(subprocess,'run',git_only)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL',os.devnull);monkeypatch.setenv('GIT_CONFIG_NOSYSTEM','1')
    return SimpleNamespace(archive=archive,tree=tree,package=package,native=native,source=source,
                           output=tmp_path/'audit',contract=contract,protected=protected_path)


def run_case(case):
    return audit.audit(case.archive,case.tree,TARGET,case.package,case.native,case.output)


def test_current_overlays_replay_and_bind_the_same_package(source_case):
    c=source_case;proof=run_case(c)
    result=acceptance.validate_native_source_audit(proof,c.native)
    assert result['upstream_source_files']==len(c.contract['before'])
    assert result['unchanged_protected_runtime_files']==1
    assert result['declared_protected_source_sha256']=={c.protected:digest(b'new-policy\n')}
    assert result['native_binary_sha256']['bin/codex']==build.sha(c.package/'bin/codex')
    assert set(proof['patches'])=={'codex-runtime.patch','fixture-overlay.patch'}


@pytest.mark.parametrize('change',['source_count','protected_count','full_scan','replay','external',
                                  'missing_overlay','extra_changed','protected_inventory','missing_verifier',
                                  'recipe','package_binary','build_receipt','native_manifest'])
def test_aggregate_rejects_tampered_source_or_package_proof(source_case,change):
    c=source_case;proof=run_case(c)
    if change=='source_count':proof['source_files']-=1
    elif change=='protected_count':proof['unchanged_protected_runtime_files']+=1
    elif change=='full_scan':proof['full_source_hashes_verified']=False
    elif change=='replay':proof['patch_replay_passed']=False
    elif change=='external':proof['external_cargo_packages_unchanged']=False
    elif change=='missing_overlay':proof['patch_inputs'].pop()
    elif change=='extra_changed':proof['changed_native_files'].append('undeclared.rs')
    elif change=='protected_inventory':proof['unchanged_protected_source_sha256'].clear()
    elif change=='missing_verifier':proof['verifier_sha256'].pop('build_native.py')
    elif change=='recipe':proof['native_recipe_id']='0'*64
    elif change=='package_binary':(c.package/'bin/codex').write_bytes(b'CHANGED-BINARY')
    elif change=='build_receipt':(c.package/'build-receipt.json').write_text('{}')
    elif change=='native_manifest':c.native['native_recipe_id']='0'*64
    with pytest.raises(SystemExit):
        acceptance.validate_native_source_audit(proof,c.native)


@pytest.mark.parametrize('change',['protected','extra','missing','declared'])
def test_complete_inventory_rejects_undeclared_or_wrong_source_bytes(source_case,change):
    c=source_case
    if change=='protected':(c.tree/'codex-rs/core/src/session/untouched.rs').write_bytes(b'changed')
    elif change=='extra':(c.tree/'unlisted.rs').write_bytes(b'new')
    elif change=='missing':(c.tree/'codex-rs/Cargo.lock').unlink()
    else:(c.tree/c.protected).write_bytes(b'wrong overlay output')
    with pytest.raises(ValueError,match='Complete native source inventory'):
        run_case(c)
    assert not c.output.exists()


def test_archive_digest_is_rechecked_before_accepting_inventory(source_case):
    source_case.archive.write_bytes(b'not the pinned archive')
    with pytest.raises(ValueError,match='archive checksum'):
        run_case(source_case)


def test_replay_cannot_be_replaced_by_declared_output_hashes(source_case,monkeypatch):
    c=source_case
    monkeypatch.setattr(build,'apply_runtime_overlays',lambda *args:None)
    with pytest.raises(ValueError,match='patch replay differs'):
        run_case(c)


def test_installation_verifier_forwards_manifest_and_preserves_windows_launchers(tmp_path,monkeypatch):
    home=tmp_path/'user';bin_dir=home/'.local/bin';bin_dir.mkdir(parents=True)
    for name in ['codex-deepseek.exe','codex-deepseek.cmd']:(bin_dir/name).write_bytes(('old-'+name).encode())
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    manifest=tmp_path/'candidate.json';manifest.write_text('candidate')
    native={'upstream_version':'0.164.0-alpha.7','native_recipe_id':'recipe'}
    calls=[];releases=[]
    def install(root,**options):
        calls.append(options)
        assert options=={'native_archive':tmp_path/'archive','native_manifest_path':manifest,'wheelhouse':None,'activate':False}
        for release in reversed(releases):
            if entry(release/'venv').read_bytes()==b'VALID':return release
        release=root/'releases'/str(len(releases));target=entry(release/'venv');target.parent.mkdir(parents=True);target.write_bytes(b'VALID');releases.append(release);return release
    def entry(venv):return venv/'Scripts/codex-deepseek.exe'
    fake=SimpleNamespace(WINDOWS=True,runtime_manifest=lambda selected:native if selected==manifest else pytest.fail('manifest omitted'),
        sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else 'catalog',
        install=install,console_entry=entry,source_identity=lambda:('fixture',{'src/example.py':'digest'}),
        valid_release=lambda release,*args:entry(release/'venv').read_bytes()==b'VALID')
    monkeypatch.setattr(installation.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='codex-cli '+native['upstream_version'],stderr=''))
    args=SimpleNamespace(archive=tmp_path/'archive',native_manifest=manifest,output=tmp_path/'out',wheelhouse=None)
    report=installation.verify(args,fake)
    assert report['passed'] and len(calls)==3 and len(releases)==2
    assert all((bin_dir/name).read_bytes()==('old-'+name).encode() for name in ['codex-deepseek.exe','codex-deepseek.cmd'])


def test_invalid_explicit_manifest_fails_before_output_or_install(tmp_path):
    def reject(path):raise ValueError('wrong recipe')
    fake=SimpleNamespace(runtime_manifest=reject)
    args=SimpleNamespace(native_manifest=tmp_path/'bad',output=tmp_path/'out')
    with pytest.raises(ValueError,match='wrong recipe'):installation.verify(args,fake)
    assert not args.output.exists()


def test_windows_launcher_snapshot_detects_exe_change(tmp_path):
    path=tmp_path/'codex-deepseek.exe';path.write_bytes(b'original')
    before=installation.launcher_snapshot(path);path.write_bytes(b'changed')
    assert installation.launcher_snapshot(path)!=before


@pytest.mark.parametrize('target,materialized,corrupt,allowed',[
    ('x86_64-pc-windows-msvc',False,False,True),
    ('x86_64-pc-windows-msvc',True,False,True),
    ('x86_64-pc-windows-msvc',True,True,False),
    ('x86_64-unknown-linux-gnu',False,False,False),
    ('x86_64-unknown-linux-gnu',True,False,True),
])
def test_only_known_windows_upstream_license_alias_may_be_omitted(source_case,target,materialized,corrupt,allowed):
    c=source_case;contract=copy.deepcopy(c.contract);contract['target']=target
    copying='codex-rs/vendor/bubblewrap/COPYING';link='codex-rs/vendor/bubblewrap/LICENSE'
    p=c.tree/copying;p.parent.mkdir(parents=True);p.write_bytes(b'PINNED-LICENSE')
    contract['before'][copying]=contract['final'][copying]=build.sha(p)
    contract['links'][link]='COPYING'
    if materialized:(c.tree/link).write_bytes(b'WRONG' if corrupt else p.read_bytes())
    if allowed:audit.checked_source_inventory(c.tree,contract)
    else:
        with pytest.raises(ValueError,match='Complete native source inventory'):
            audit.checked_source_inventory(c.tree,contract)
