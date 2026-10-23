"""Source preparation gates; real patch fixtures never compile or run a native binary."""
import copy,hashlib,importlib.util,json,os,re,subprocess,tarfile
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_build_recipe',ROOT/'build_native.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
TARGETS=list(build.PLATFORM_ASSETS)
WIN='x86_64-pc-windows-msvc'


def recipe_copy(tmp_path,monkeypatch,edit=None):
    value=copy.deepcopy(build.runtime_recipe())
    if edit:edit(value)
    source=tmp_path/'recipe';(source/'native').mkdir(parents=True)
    (source/'native/runtime-overlays.json').write_text(json.dumps(value))
    monkeypatch.setattr(build,'SOURCE',source)
    return value


@pytest.mark.parametrize('problem',['order','unknown','target','digest','duplicate_path','parent_path','windows_path','absolute_path','toolchain','boolean_schema'])
def test_recipe_rejects_ambiguous_or_incompatible_inputs(tmp_path,monkeypatch,problem):
    def edit(value):
        first=value['overlays'][0];rows=first['changes_by_target'][WIN]
        if problem=='order':value['overlays'][:2]=reversed(value['overlays'][:2])
        elif problem=='unknown':value['hidden_fallback']=True
        elif problem=='target':del value['required_base_inputs'][WIN]
        elif problem=='digest':first['sha256']='not-a-digest'
        elif problem=='duplicate_path':rows.append(copy.deepcopy(rows[0]))
        elif problem=='parent_path':rows[0]['path']='codex-rs/../outside'
        elif problem=='windows_path':rows[0]['path']='codex-rs/..\\outside'
        elif problem=='absolute_path':rows[0]['path']='/outside'
        elif problem=='toolchain':value['build_toolchain']='stable'
        elif problem=='boolean_schema':value['schema']=True
    recipe_copy(tmp_path,monkeypatch,edit)
    with pytest.raises(ValueError):build.runtime_recipe()


def test_duplicate_json_key_is_rejected(tmp_path,monkeypatch):
    recipe_copy(tmp_path,monkeypatch)
    path=build.SOURCE/'native/runtime-overlays.json';raw=path.read_text();path.write_text(raw.replace('"schema": 1','"schema": 1, "schema": 1',1))
    with pytest.raises(ValueError,match='Duplicate'):build.runtime_recipe()


@pytest.mark.parametrize('failure',['wrong_base','wrong_upstream','patch_mutation','wrong_source'])
def test_preflight_rejects_before_running_git_or_a_compiler(tmp_path,monkeypatch,failure):
    recipe=copy.deepcopy(build.runtime_recipe());source=tmp_path/'source';(source/'native').mkdir(parents=True)
    for item in recipe['overlays']:(source/'native'/item['file']).write_bytes((ROOT/'native'/item['file']).read_bytes())
    monkeypatch.setattr(build,'SOURCE',source)
    tree=tmp_path/'tree';(tree/'codex-rs').mkdir(parents=True)
    (tree/'codex-rs/rust-toolchain.toml').write_text('[toolchain]\nchannel="'+('1.95.1' if failure=='wrong_upstream' else '1.95.0')+'"\n')
    inputs=copy.deepcopy(recipe['required_base_inputs'][WIN])
    if failure=='wrong_base':inputs.reverse()
    if failure=='patch_mutation':(source/'native'/recipe['overlays'][0]['file']).write_bytes(b'changed patch')
    def reject(*args,**kwargs):raise AssertionError('Preflight must reject before any process starts')
    monkeypatch.setattr(build.subprocess,'run',reject)
    with pytest.raises(ValueError):build.apply_runtime_overlays(tree,WIN,inputs,recipe)


@pytest.mark.parametrize('target',TARGETS)
def test_verified_upstream_modified_sources_accept_exact_target_stack(tmp_path,monkeypatch,target):
    archive=os.environ.get('CODEX_DEEPSEEK_TEST_SOURCE_ARCHIVE')
    if not archive:pytest.skip('requires explicitly supplied cached upstream archive')
    archive=Path(archive);assert build.sha(archive)==build.SOURCE_ASSET[1]
    git=os.environ.get('CODEX_DEEPSEEK_TEST_GIT','git')
    recipe=build.runtime_recipe();inputs=copy.deepcopy(recipe['required_base_inputs'][target])
    names=[row['file'] for row in inputs]+[row['file'] for row in recipe['overlays'] if target in row['changes_by_target']]
    paths={'codex-rs/rust-toolchain.toml'}
    for name in names:
        for line in (ROOT/'native'/name).read_text().splitlines():
            if line.startswith(('--- a/','+++ b/')):paths.add(line[6:].split('\t')[0])
    tree=tmp_path/'modified-source-fixture';tree.mkdir()
    with tarfile.open(archive) as bundle:
        for member in bundle:
            relative=member.name.removeprefix('codex-'+build.COMMIT+'/')
            if relative in paths and member.isfile():
                path=tree/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(bundle.extractfile(member).read())
    original=subprocess.run;commands=[]
    def run(command,**kwargs):
        assert command[0] in {'git',git},'Source fixture must not launch cargo/native binaries'
        command=[git,*command[1:]];commands.append(command)
        return original(command,**kwargs)
    monkeypatch.setattr(build.subprocess,'run',run)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL',os.devnull);monkeypatch.setenv('GIT_CONFIG_NOSYSTEM','1')
    run([git,'init','-q',str(tree)],check=True,capture_output=True)
    for row in inputs:
        run([git,'-c','core.longpaths=true','-c','core.autocrlf=false','-c','core.eol=lf','apply',str(ROOT/'native'/row['file'])],cwd=tree,check=True,capture_output=True)
    base_count=len(inputs)
    hashes=build.apply_runtime_overlays(tree,target,inputs,recipe)
    assert all(build.sha(tree/name)==digest for name,digest in hashes.items())
    assert [row['file'] for row in inputs]==names
    assert len(commands)==1+base_count+2*(len(names)-base_count)
    diff=build.combined_patch(tree,archive,paths-{'codex-rs/rust-toolchain.toml'})
    assert diff==build.combined_patch(tree,archive,reversed(sorted(paths-{'codex-rs/rust-toolchain.toml'})))
    assert str(tmp_path) not in diff
    assert 'raw_protocol_version' in diff and 'StatusCode::PAYLOAD_TOO_LARGE' in diff
    if target==WIN:
        assert 'Remove this isolated Codex home' in diff
        assert 'CodexSandboxOffline' in diff
    else:
        assert 'Remove this isolated Codex home' not in diff


@pytest.mark.parametrize("provided_metadata",[False,True])
def test_license_metadata_uses_same_recipe_pin_or_supplied_result(tmp_path,monkeypatch,provided_metadata):
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules,"build_native",build)
    spec=importlib.util.spec_from_file_location("native_license_recipe_test",ROOT/"native_licenses.py")
    licenses=importlib.util.module_from_spec(spec);spec.loader.exec_module(licenses)
    commands=[]
    def run(command,**kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=json.dumps({"packages":[]}))
    monkeypatch.setattr(licenses.subprocess,"run",run)
    result=licenses.collect(tmp_path/"Cargo.toml",tmp_path/"licenses",target=WIN,metadata={"packages":[]} if provided_metadata else None)
    assert result==0
    if provided_metadata:assert not commands
    else:assert commands[0][:3]==["cargo","+"+build.runtime_recipe()["build_toolchain"],"metadata"]


def test_recipe_identity_is_target_scoped():
    recipe=build.runtime_recipe();before={target:build.native_recipe_identity(target,recipe) for target in TARGETS}
    changed=copy.deepcopy(recipe);changed['overlays'][-1]['sha256']='0'*64
    after={target:build.native_recipe_identity(target,changed) for target in TARGETS}
    assert before[WIN]!=after[WIN]
    assert all(before[target]==after[target] for target in TARGETS if target!=WIN)


def test_package_layout_validator_disables_source_tree_bytecode(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(build.subprocess,'run',lambda args,**kwargs:calls.append((args,kwargs)))
    build.validate_native_layout(tmp_path/'source',tmp_path/'package',WIN)
    assert len(calls)==1 and calls[0][0][1:4]==['-I','-B','-c']


def test_windows_network_overlay_is_explicit_and_does_not_qualify_other_platforms():
    recipe=build.runtime_recipe();entry=next(row for row in recipe['overlays'] if row['file']=='windows-loopback-policy.patch')
    assert set(entry['changes_by_target'])=={WIN}
    paths={row['path'] for row in entry['changes_by_target'][WIN]}
    assert 'codex-rs/windows-sandbox-rs/src/wfp/loopback.rs' in paths
    assert 'codex-rs/windows-sandbox-rs/src/helper_materialization/copy.rs' in paths


def test_final_windows_readiness_and_artifacts_are_ordered_and_target_scoped():
    recipe=build.runtime_recipe()
    entries=recipe['overlays'][-3:-1]
    assert [x['file'] for x in entries]==['windows-wfp-readiness.patch','windows-artifact-readroots.patch']
    assert all(set(x['changes_by_target'])=={WIN} for x in entries)
    first={x['path']:x['after_sha256'] for x in entries[0]['changes_by_target'][WIN]}
    second={x['path']:x for x in entries[1]['changes_by_target'][WIN]}
    for name in ['setup.rs','setup_provisioning.rs']:
        path='codex-rs/windows-sandbox-rs/src/'+name
        assert second[path]['before_sha256']==first[path]
    assert 'codex-rs/windows-sandbox-rs/src/artifact_read_roots.rs' in second


def test_final_overlay_omission_cannot_reuse_a_previous_ready_manifest(tmp_path,monkeypatch):
    recipe_copy(tmp_path,monkeypatch,lambda value:value['overlays'].pop())
    with pytest.raises(ValueError,match='order'):build.runtime_recipe()


def test_runner_home_fix_is_last_and_changes_only_the_native_runner():
    recipe=build.runtime_recipe();entry=recipe['overlays'][-1]
    assert entry['file']=='windows-sandbox-runner-home.patch'
    assert set(entry['changes_by_target'])=={WIN}
    changed=entry['changes_by_target'][WIN]
    assert len(changed)==1
    assert changed[0]['path']=='codex-rs/windows-sandbox-rs/src/bin/command_runner/win.rs'
    previous=next(row for item in recipe['overlays'][:-1] for row in item['changes_by_target'].get(WIN,[]) if row['path']==changed[0]['path'])
    assert changed[0]['before_sha256']==previous['after_sha256']


def test_isolated_native_build_loads_checkout_license_inputs(tmp_path):
    import sys
    # A real isolated interpreter must load both build helpers from their exact
    # checkout paths; a caller-owned module with either name must not execute.
    for name in ('build_native.py','native_licenses.py'):
        (tmp_path/name).write_text('raise AssertionError("FOREIGN_BUILD_MODULE")\n')
    child=r'''import importlib.util,json,pathlib,sys,types
root=pathlib.Path(sys.argv[1])
spec=importlib.util.spec_from_file_location("fixed_native_build",root/"build_native.py")
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
collect=build.native_license_collector()
assert pathlib.Path(collect.__code__.co_filename).resolve()==(root/"native_licenses.py").resolve()
metadata={"packages":[]}
assert collect(pathlib.Path("fixture-Cargo.toml"),pathlib.Path("with-metadata"),metadata=metadata,target="x86_64-unknown-linux-gnu")==0
calls=[]
def fake_run(command,**kwargs):
    calls.append(command)
    assert command[:4]==["cargo","+"+build.runtime_recipe()["build_toolchain"],"metadata","--locked"]
    return types.SimpleNamespace(stdout=json.dumps(metadata))
collect.__globals__["subprocess"].run=fake_run
assert collect(pathlib.Path("fixture-Cargo.toml"),pathlib.Path("without-metadata"),target="x86_64-unknown-linux-gnu")==0
assert len(calls)==1
assert sys.flags.isolated==1
print("ISOLATED_BUILD_LICENSE_INPUTS_OK")
'''
    env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','CODEX_','CDS_','OPENROUTER_','OPENAI_','GITHUB_','ACTIONS_'))}
    result=subprocess.run([sys.executable,'-I','-X','utf8','-c',child,str(ROOT)],cwd=tmp_path,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=='ISOLATED_BUILD_LICENSE_INPUTS_OK'
