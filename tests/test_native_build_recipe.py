"""Source preparation gates; real patch fixtures never compile or run a native binary."""
import copy,hashlib,importlib.util,json,os,re,subprocess,tarfile
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_build_recipe',ROOT/'build_native.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
TARGETS=list(build.PLATFORM_ASSETS)
WIN='x86_64-pc-windows-msvc'


def overlay_index(recipe, filename):
    indices=[index for index,item in enumerate(recipe['overlays']) if item['file']==filename]
    assert len(indices)==1,filename
    return indices[0]


def recipe_prefix_through(recipe, filename):
    return {**recipe,'overlays':recipe['overlays'][:overlay_index(recipe,filename)+1]}


def qualified_recipe_before_capacity_replay(recipe):
    """Keep historical public recipe pins independent of the new shared overlay."""
    recipe={**recipe,'overlays':[row for row in recipe['overlays'] if row['file']!='guardian-provider-transport-0164.patch']}
    index=overlay_index(recipe,'payload-request-capacity-replay.patch')
    return {**recipe,'overlays':recipe['overlays'][:index]+recipe['overlays'][index+1:]}


def serialized_recipe_sha256(recipe):
    return hashlib.sha256((json.dumps(recipe,indent=2)+'\n').encode('utf-8')).hexdigest()


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


@pytest.mark.parametrize('recipe_selection',['active','explicit_candidate','archived_067'])
@pytest.mark.parametrize('target',TARGETS)
def test_verified_upstream_modified_sources_accept_exact_target_stack(tmp_path,monkeypatch,target,recipe_selection):
    archive=os.environ.get('CODEX_DEEPSEEK_TEST_SOURCE_ARCHIVE')
    if not archive:pytest.skip('requires explicitly supplied cached upstream archive')
    archive=Path(archive);assert build.sha(archive)==build.SOURCE_ASSET[1]
    git=os.environ.get('CODEX_DEEPSEEK_TEST_GIT','git')
    recipe=(json.loads((ROOT/'native/runtime-overlays-0.6.7-acc16483.json').read_bytes()) if recipe_selection=='archived_067' else build.runtime_recipe(ROOT/'native/guardian-provider-transport-0164-candidate.json' if recipe_selection=='explicit_candidate' else None));inputs=copy.deepcopy(recipe['required_base_inputs'][target])
    names=[row['file'] for row in inputs]+[row['file'] for row in recipe['overlays'] if target in row['changes_by_target']]
    paths={'codex-rs/rust-toolchain.toml'}
    for name in names:
        for line in (ROOT/'native'/name).read_text(encoding='utf-8').splitlines():
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
    if recipe_selection!='archived_067':
        assert 'if !self.config.provider.info().supports_websockets' in diff
        assert 'provider_without_websockets_scores_over_http_without_opening_pool' in diff
    else:
        assert 'provider_without_websockets_scores_over_http_without_opening_pool' not in diff
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
    changed=copy.deepcopy(recipe);next(item for item in changed['overlays'] if item['file']=='windows-acl-lifecycle.patch')['sha256']='0'*64
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
    names=['windows-wfp-readiness.patch','windows-artifact-readroots.patch']
    entries=[next(row for row in recipe['overlays'] if row['file']==name) for name in names]
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


def test_runner_home_fix_precedes_token_coordination_and_changes_only_the_native_runner():
    recipe=build.runtime_recipe();entry=next(row for row in recipe['overlays'] if row['file']=='windows-sandbox-runner-home.patch')
    assert entry['file']=='windows-sandbox-runner-home.patch'
    assert set(entry['changes_by_target'])=={WIN}
    changed=entry['changes_by_target'][WIN]
    assert len(changed)==1
    assert changed[0]['path']=='codex-rs/windows-sandbox-rs/src/bin/command_runner/win.rs'
    previous=next(row for item in recipe['overlays'][:recipe['overlays'].index(entry)] for row in item['changes_by_target'].get(WIN,[]) if row['path']==changed[0]['path'])
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


def test_final_recovery_overlays_are_windows_only_and_follow_the_exact_runner_preimage():
    recipe=build.runtime_recipe()
    runner,coordination,deny=[next(row for row in recipe['overlays'] if row['file']==name) for name in (
        'windows-sandbox-runner-home.patch','windows-sandbox-coordination.patch','windows-managed-deny-revoke.patch')]
    assert [item['file'] for item in (runner,coordination,deny)]==[
        'windows-sandbox-runner-home.patch','windows-sandbox-coordination.patch',
        'windows-managed-deny-revoke.patch']
    assert all(set(item['changes_by_target'])=={WIN} for item in (coordination,deny))
    runner_row=runner['changes_by_target'][WIN][0]
    coordination_rows={row['path']:row for row in coordination['changes_by_target'][WIN]}
    assert coordination_rows[runner_row['path']]['before_sha256']==runner_row['after_sha256']
    assert len(coordination_rows)==8
    deny_paths={row['path'] for row in deny['changes_by_target'][WIN]}
    assert len(deny_paths)==7 and not deny_paths.intersection(coordination_rows)
    assert 'codex-rs/windows-sandbox-rs/src/deny_read_state.rs' in deny_paths
    assert 'codex-rs/windows-sandbox-rs/src/file_write.rs' in deny_paths


def test_junction_overlay_only_connects_the_existing_runner_sandbox_base():
    recipe=build.runtime_recipe()
    leaf=next(row for row in recipe['overlays'] if row['file']=='windows-runner-junction-root.patch')
    assert set(leaf['changes_by_target'])=={WIN}
    paths={row['path'] for row in leaf['changes_by_target'][WIN]}
    assert paths=={
        'codex-rs/windows-sandbox-rs/src/bin/command_runner/win.rs',
        'codex-rs/windows-sandbox-rs/src/bin/command_runner/win/cwd_junction.rs'}
    runner=next(row for row in leaf['changes_by_target'][WIN] if row['path'].endswith('/win.rs'))
    coordination=next(row for row in recipe['overlays'] if row['file']=='windows-sandbox-coordination.patch')
    previous=next(row for row in coordination['changes_by_target'][WIN] if row['path']==runner['path'])
    assert runner['before_sha256']==previous['after_sha256']


def test_home_metadata_overlay_is_a_single_windows_native_setup_change():
    recipe=build.runtime_recipe()
    leaf=next(item for item in recipe['overlays'] if item['file']=='windows-home-metadata.patch')
    assert set(leaf['changes_by_target'])=={WIN}
    assert len(leaf['changes_by_target'][WIN])==1
    row=leaf['changes_by_target'][WIN][0]
    assert row['path']=='codex-rs/windows-sandbox-rs/src/setup_provisioning.rs'
    predecessors=[entry for overlay in recipe['overlays'][:recipe['overlays'].index(leaf)]
                  for entry in overlay['changes_by_target'].get(WIN,[]) if entry['path']==row['path']]
    assert row['before_sha256']==predecessors[-1]['after_sha256']


def test_root_acl_sharing_overlay_is_windows_only_and_chains_the_reviewed_acl_sources():
    recipe=build.runtime_recipe()
    leaf=next(item for item in recipe['overlays'] if item['file']=='windows-root-acl-sharing.patch')
    assert set(leaf['changes_by_target'])=={WIN}
    rows=leaf['changes_by_target'][WIN]
    assert {row['path'] for row in rows}=={'codex-rs/windows-sandbox-rs/src/acl.rs','codex-rs/windows-sandbox-rs/src/acl_tests.rs'}
    for row in rows:
        predecessors=[entry for overlay in recipe['overlays'][:recipe['overlays'].index(leaf)]
                      for entry in overlay['changes_by_target'].get(WIN,[]) if entry['path']==row['path']]
        assert row['before_sha256']==predecessors[-1]['after_sha256']


def test_plugin_git_lifecycle_overlay_is_windows_only_and_declares_native_regressions():
    recipe=build.runtime_recipe()
    leaf=next(item for item in recipe['overlays'] if item['file']=='windows-plugin-git-lifecycle.patch')
    assert set(leaf['changes_by_target'])=={WIN}
    rows=leaf['changes_by_target'][WIN]
    assert {row['path'] for row in rows}=={
        'codex-rs/Cargo.lock','codex-rs/core-plugins/Cargo.toml',
        'codex-rs/core-plugins/src/startup_sync.rs',
        'codex-rs/core-plugins/src/marketplace_upgrade/git.rs',
        'codex-rs/core-plugins/src/git_lifetime_windows_tests.rs',
        'codex-rs/utils/pty/src/win/job.rs',
        'codex-rs/utils/pty/src/win/job_std_tests.rs',
    }
    new={row['path'] for row in rows if row['before_sha256'] is None}
    assert new=={'codex-rs/core-plugins/src/git_lifetime_windows_tests.rs',
                 'codex-rs/utils/pty/src/win/job_std_tests.rs'}

def test_background_helper_handle_overlay_preserves_nonwindows_recipes():
    recipe=build.runtime_recipe()
    matches=[(index,leaf) for index,leaf in enumerate(recipe['overlays'])
             if leaf['file']=='windows-setup-background-handles.patch']
    assert len(matches)==1
    index,leaf=matches[0]
    assert set(leaf['changes_by_target'])=={WIN}
    rows={row['path']:row for row in leaf['changes_by_target'][WIN]}
    existing=rows['codex-rs/windows-sandbox-rs/src/setup_provisioning.rs']
    preceding=[row for overlay in recipe['overlays'][:index]
               for row in overlay['changes_by_target'].get(WIN,[]) if row['path']==existing['path']]
    assert existing['before_sha256']==preceding[-1]['after_sha256']
    assert rows['codex-rs/windows-sandbox-rs/src/setup_background.rs']['before_sha256'] is None
    assert len(rows)==2
    for target in ['aarch64-apple-darwin','x86_64-unknown-linux-gnu']:
        before={**recipe,'overlays':recipe['overlays'][:index]+recipe['overlays'][index+1:]}
        assert build.native_recipe_identity(target,recipe)==build.native_recipe_identity(target,before)

def test_capacity_overlay_is_complete_common_source_and_changes_all_target_identities():
    recipe=build.runtime_recipe()
    index=next(i for i,item in enumerate(recipe['overlays']) if item['file']=='payload-request-capacity.patch')
    common=recipe['overlays'][index]
    assert common['file']=='payload-request-capacity.patch'
    expected={
        'codex-rs/Cargo.lock','codex-rs/core/Cargo.toml',
        'codex-rs/app-server/src/request_processors/thread_delete.rs',
        'codex-rs/core/src/compact.rs','codex-rs/core/src/compact_remote_v2_installation.rs',
        'codex-rs/core/src/compact_tests.rs','codex-rs/core/src/compact_token_budget.rs',
        'codex-rs/core/src/context_manager/history.rs',
        'codex-rs/core/src/context_manager/history_tests.rs',
        'codex-rs/core/src/context_manager/normalize.rs','codex-rs/core/src/lib.rs',
        'codex-rs/core/src/session/mod.rs','codex-rs/core/src/session/request_capacity.rs',
        'codex-rs/core/src/session/retained_context.rs',
        'codex-rs/core/src/session/retained_context_tests.rs',
        'codex-rs/core/src/session/turn.rs','codex-rs/core/src/state/session.rs',
        'codex-rs/core/tests/common/responses.rs',
    }
    assert set(common['changes_by_target'])==set(TARGETS)
    previous={**recipe,'overlays':recipe['overlays'][:index]+recipe['overlays'][index+1:]}
    mutated=copy.deepcopy(recipe);mutated['overlays'][index]['sha256']='0'*64
    for target,rows in common['changes_by_target'].items():
        assert len(rows)==18 and {row['path'] for row in rows}==expected
        assert {row['path'] for row in rows if row['before_sha256'] is None}=={
            'codex-rs/core/src/session/request_capacity.rs'}
        assert build.native_recipe_identity(target,recipe)!=build.native_recipe_identity(target,previous)
        assert build.native_recipe_identity(target,recipe)!=build.native_recipe_identity(target,mutated)
    by_target={target:{row['path']:row for row in rows}
               for target,rows in common['changes_by_target'].items()}
    # The same common hunk follows different qualified target stacks. The
    # whole Cargo.lock identities must not borrow Windows hashes on Unix.
    for target in TARGETS:
        if target==WIN:continue
        assert by_target[target]['codex-rs/Cargo.lock']['before_sha256']!=by_target[WIN]['codex-rs/Cargo.lock']['before_sha256']
        assert by_target[target]['codex-rs/Cargo.lock']['after_sha256']!=by_target[WIN]['codex-rs/Cargo.lock']['after_sha256']


@pytest.mark.parametrize('target',TARGETS)
def test_capacity_overlay_cannot_omit_a_platform(tmp_path,monkeypatch,target):
    recipe_copy(tmp_path,monkeypatch,
        lambda value:next(item for item in value['overlays'] if item['file']=='payload-request-capacity.patch')['changes_by_target'].pop(target))
    with pytest.raises(ValueError,match='target coverage'):
        build.runtime_recipe()


@pytest.mark.parametrize("candidate_recipe",[False,True])
def test_windows_main_reaches_overlay_after_original_diff_without_compiler(tmp_path, monkeypatch,candidate_recipe):
    """Exercise main's actual Windows baseline extraction/diff, stop before Cargo."""
    import io,sys
    source = tmp_path/"checkout";(source/"native").mkdir(parents=True)
    common = b"SYNTHETIC BASE PATCH\n"; windows = b"SYNTHETIC WINDOWS PATCH\n"
    (source/"native/codex-runtime.patch").write_bytes(common)
    (source/"native/windows-transport.patch").write_bytes(windows)
    name = "codex-rs/synthetic-source.txt"; content = b"UNCHANGED FIXTURE SOURCE\n"
    digest = lambda value:hashlib.sha256(value).hexdigest()
    (source/"native/source-changes.json").write_text(json.dumps({"files":[{"path":name,"fork_sha256":digest(content)}]}))
    (source/"native/windows-source-changes.json").write_text(json.dumps({
        "base_patch_sha256":digest(common),"files":[],"combined_patch_sha256":digest(b"")}))
    archive = tmp_path/"source.tar"
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo("codex-"+build.COMMIT+"/"+name)
        member.size = len(content);stream.addfile(member, io.BytesIO(content))
    monkeypatch.setattr(build,"SOURCE",source)
    monkeypatch.setattr(build,"build_target",lambda:"x86_64-pc-windows-msvc")
    monkeypatch.setattr(build.platform,"system",lambda:"Windows")
    monkeypatch.setattr(build,"build_assets",lambda target:{"source":("https://invalid.test/not-used",digest(archive.read_bytes()))})
    monkeypatch.setattr(build,"download",lambda *args:archive)
    monkeypatch.setattr(build,"native_license_collector",lambda:None)
    original_extract = build.extract_source_archive
    def extract_without_host_path_change(stream, tree, *, windows):
        assert windows is True
        original_extract(stream,tree,windows=False)
    monkeypatch.setattr(build,"extract_source_archive",extract_without_host_path_change)
    commands=[]
    def no_process(command,**kwargs):
        assert command[0] == "git", "compiler/native process is forbidden"
        commands.append(command)
    monkeypatch.setattr(build.subprocess,"run",no_process)
    recipe = {"fixture":"NO MODEL OR PRODUCTION RECIPE"}
    selected=source/"native"/("guardian-provider-transport-0164-candidate.json"if candidate_recipe else"runtime-overlays.json")
    def selected_recipe(path):
        assert path==selected
        return recipe
    monkeypatch.setattr(build,"runtime_recipe",selected_recipe)
    monkeypatch.setattr(build,"native_recipe_identity",lambda target,recipe:"fixture-recipe")
    class ReachedOverlay(Exception):pass
    def stop_at_overlay(tree,target,patch_inputs,received):
        assert target == "x86_64-pc-windows-msvc" and received is recipe
        assert (tree/name).read_bytes() == content
        assert patch_inputs == [
            {"file":"codex-runtime.patch","sha256":digest(common)},
            {"file":"windows-transport.patch","sha256":digest(windows)}]
        raise ReachedOverlay
    monkeypatch.setattr(build,"apply_runtime_overlays",stop_at_overlay)
    argv=["build_native.py","--work",str(tmp_path/"work"),"--output",str(tmp_path/"output")]
    if candidate_recipe:argv+=["--recipe",str(selected)]
    monkeypatch.setattr(sys,"argv",argv)
    with pytest.raises(ReachedOverlay):
        build.main()
    assert len(commands) == 3


def test_actual_readable_daemon_overlay_remains_before_readiness_and_keeps_unix_recipes():
    # Freeze the complete qualified 0.6.4 prefix, including all common inputs.
    recipe=recipe_prefix_through(qualified_recipe_before_capacity_replay(build.runtime_recipe()),'windows-critical-read-deny-readiness.patch')
    assert serialized_recipe_sha256(recipe)=='a0ba8ba8d62fa9a85c177251f1b1cdb35321612013ba6564fcbbfa7faebf0146'
    index=overlay_index(recipe,'windows-readable-daemon-scratch.patch')
    last=recipe['overlays'][index]
    assert index+1==overlay_index(recipe,'windows-critical-read-deny-readiness.patch')
    assert last['file']=='windows-readable-daemon-scratch.patch'
    assert last['sha256']==build.sha(ROOT/'native'/last['file'])=='1dc6f292394dfc9fe4af5600245ff32f0c93ca0790039fd8a5ddb1021b463859'
    assert set(last['changes_by_target'])=={WIN}
    assert {row['path'] for row in last['changes_by_target'][WIN]}=={
        'codex-rs/windows-sandbox-rs/src/setup_provisioning.rs',
        'codex-rs/app-server-daemon/src/backend/pid_start.rs',
        'codex-rs/app-server-daemon/src/backend/pid_tests.rs',
        'codex-rs/app-server-daemon/src/backend/pid_workdir_windows.rs'}
    previous={**recipe,'overlays':recipe['overlays'][:index]}
    assert build.native_recipe_identity(WIN,recipe_prefix_through(recipe,'windows-readable-daemon-scratch.patch'))=='048b4e2840ba6d77c68bdada51316f73b96a4154e1192303be40769120606c15'
    for target in TARGETS:
        if target==WIN:continue
        assert build.native_recipe_identity(target,recipe)==build.native_recipe_identity(target,previous)

@pytest.mark.parametrize('problem',['omit','reorder','duplicate','wrong_target'])
def test_readable_daemon_overlay_cannot_be_omitted_reordered_or_retargeted(tmp_path,monkeypatch,problem):
    def edit(recipe):
        index=overlay_index(recipe,'windows-readable-daemon-scratch.patch')
        if problem=='omit':recipe['overlays'].pop(index)
        elif problem=='reorder':recipe['overlays'][index-1:index+1]=reversed(recipe['overlays'][index-1:index+1])
        elif problem=='duplicate':recipe['overlays'].append(copy.deepcopy(recipe['overlays'][index]))
        else:
            last=recipe['overlays'][index]
            last['changes_by_target']['x86_64-unknown-linux-gnu']=last['changes_by_target'].pop(WIN)
    recipe_copy(tmp_path,monkeypatch,edit)
    with pytest.raises(ValueError):build.runtime_recipe()

def test_public_package_and_install_recipe_readers_use_the_current_entrypoint():
    package_spec=importlib.util.spec_from_file_location('public_package_recipe_entry',ROOT/'package_native.py')
    package=importlib.util.module_from_spec(package_spec);package_spec.loader.exec_module(package)
    install_spec=importlib.util.spec_from_file_location('public_install_recipe_entry',ROOT/'install.py')
    installer=importlib.util.module_from_spec(install_spec);install_spec.loader.exec_module(installer)
    recipe=build.runtime_recipe()
    for target in TARGETS:
        expected=build.native_recipe_identity(target,recipe)
        assert package.expected_native_recipe(target)==(expected,recipe['build_toolchain'])
        assert installer.expected_native_recipe(target)==expected


def test_actual_critical_read_deny_overlay_is_last_windows_only_with_exact_sources():
    # This layer remains last in its historical prefix, not in every future release.
    recipe=recipe_prefix_through(qualified_recipe_before_capacity_replay(build.runtime_recipe()),'windows-critical-read-deny-readiness.patch')
    index=overlay_index(recipe,'windows-critical-read-deny-readiness.patch')
    last=recipe['overlays'][index]
    assert index==len(recipe['overlays'])-1
    assert last == {'file': 'windows-critical-read-deny-readiness.patch', 'sha256': '6ea930fedafdd8bcc1fa8c2563845661cf038c5be86d50488ebfd918fa4bb0bc', 'changes_by_target': {'x86_64-pc-windows-msvc': [{'path': 'codex-rs/windows-sandbox-rs/src/acl.rs', 'before_sha256': '6f3fa9486602687a4ee6649f78d28ca420106cde448cb4d23891a162d703b33b', 'after_sha256': 'a6ebc8688c24231f0e3f931cc4d3c245da475e88735d5d4f64e2a4d06c7a3f3f'}, {'path': 'codex-rs/windows-sandbox-rs/src/setup_provisioning.rs', 'before_sha256': 'b526bb85b7428d7c3e4f96fb5f035273e7cfbe6491fa1426369beb824242fe6a', 'after_sha256': '435f353e1ffeace970e15f408901596eee44c98442e4cd0bb2952692839d2697'}]}}
    assert build.sha(ROOT/'native'/last['file'])==last['sha256']
    assert serialized_recipe_sha256(recipe)=='a0ba8ba8d62fa9a85c177251f1b1cdb35321612013ba6564fcbbfa7faebf0146'
    assert build.native_recipe_identity(WIN,recipe)=='6a86bdef519c5d3e45ef402cb33437c3fcf4aec7ed0e0a1432a314f87ae87e1f'
    previous={**recipe,'overlays':recipe['overlays'][:index]}
    assert build.native_recipe_identity(WIN,previous)=='048b4e2840ba6d77c68bdada51316f73b96a4154e1192303be40769120606c15'
    for target in TARGETS:
        if target != WIN:
            assert build.native_recipe_identity(target,recipe)==build.native_recipe_identity(target,previous)

@pytest.mark.parametrize('problem',['omit','reorder','duplicate','wrong_target'])
def test_critical_read_deny_overlay_cannot_be_omitted_reordered_or_retargeted(tmp_path,monkeypatch,problem):
    def edit(recipe):
        index=overlay_index(recipe,'windows-critical-read-deny-readiness.patch')
        if problem=='omit':recipe['overlays'].pop(index)
        elif problem=='reorder':recipe['overlays'][index-1:index+1]=reversed(recipe['overlays'][index-1:index+1])
        elif problem=='duplicate':recipe['overlays'].append(copy.deepcopy(recipe['overlays'][index]))
        else:
            last=recipe['overlays'][index]
            last['changes_by_target']['x86_64-unknown-linux-gnu']=last['changes_by_target'].pop(WIN)
    recipe_copy(tmp_path,monkeypatch,edit)
    with pytest.raises(ValueError):build.runtime_recipe()


def test_metadata_owner_overlay_is_last_windows_only_and_preserves_the_qualified_prefix():
    # Freeze the published 0.6.5 prefix, not every later Windows-only leaf.
    recipe=recipe_prefix_through(qualified_recipe_before_capacity_replay(build.runtime_recipe()),'windows-metadata-owner-policy.patch')
    index=overlay_index(recipe,'windows-metadata-owner-policy.patch')
    assert index==len(recipe['overlays'])-1
    assert index==overlay_index(recipe,'windows-critical-read-deny-readiness.patch')+1
    last=recipe['overlays'][index]
    assert last=={
        'file':'windows-metadata-owner-policy.patch',
        'sha256':'82d9c9d7ee9b57603e359092ef891a0842209df325128db7241eaceb0067944b',
        'changes_by_target':{WIN:[{
            'path':'codex-rs/windows-sandbox-rs/src/ancestor_metadata.rs',
            'before_sha256':'54149f0d1e81b356bef6772a0259bc8bb0320c51e1ca81a2f4f8169f120b99a8',
            'after_sha256':'0ef82ccd9e196bb91b7059637f691f9b413c39c1a49504152b33beac386b59ba',
        }]},
    }
    assert build.sha(ROOT/'native'/last['file'])==last['sha256']
    assert serialized_recipe_sha256(recipe)=='119f16e9d9847d988e6f453d0dcb37bc10ef6d1eb4d52cc71cdc42b4693a4c76'
    previous=recipe_prefix_through(recipe,'windows-critical-read-deny-readiness.patch')
    assert previous['overlays']==recipe['overlays'][:index]
    assert serialized_recipe_sha256(previous)=='a0ba8ba8d62fa9a85c177251f1b1cdb35321612013ba6564fcbbfa7faebf0146'
    assert build.native_recipe_identity(WIN,previous)=='6a86bdef519c5d3e45ef402cb33437c3fcf4aec7ed0e0a1432a314f87ae87e1f'
    assert build.native_recipe_identity(WIN,recipe)=='3a2a8b850db835ea3aac609dad8171c12f18c41154a6ce942a2cf558d512de32'
    expected_unix={
        'aarch64-apple-darwin':'02e413453fbecd93bdebad2ba5f7033a7ea201206b3a708e5593b84b62b424d1',
        'x86_64-unknown-linux-gnu':'3e752d53b8122d0402cd89850d1368585c8ff6dc800336e796a7875028a745ad',
    }
    assert set(expected_unix)==set(TARGETS)-{WIN}
    for target,expected in expected_unix.items():
        assert build.native_recipe_identity(target,recipe)==build.native_recipe_identity(target,previous)==expected


@pytest.mark.parametrize('problem',['omit','reorder','duplicate','wrong_target'])
def test_metadata_owner_overlay_cannot_be_omitted_reordered_or_retargeted(tmp_path,monkeypatch,problem):
    def edit(recipe):
        index=overlay_index(recipe,'windows-metadata-owner-policy.patch')
        if problem=='omit':recipe['overlays'].pop(index)
        elif problem=='reorder':recipe['overlays'][index-1:index+1]=reversed(recipe['overlays'][index-1:index+1])
        elif problem=='duplicate':recipe['overlays'].append(copy.deepcopy(recipe['overlays'][index]))
        else:
            last=recipe['overlays'][index]
            last['changes_by_target']['x86_64-unknown-linux-gnu']=last['changes_by_target'].pop(WIN)
    recipe_copy(tmp_path,monkeypatch,edit)
    with pytest.raises(ValueError):build.runtime_recipe()


def test_native_metadata_minimal_overlay_is_last_windows_only_with_exact_sources():
    recipe=qualified_recipe_before_capacity_replay(build.runtime_recipe())
    index=overlay_index(recipe,'windows-native-metadata-minimal.patch')
    assert index==len(recipe['overlays'])-1
    assert index==overlay_index(recipe,'windows-metadata-owner-policy.patch')+1
    last=recipe['overlays'][index]
    expected_sources={
        'codex-rs/windows-sandbox-rs/src/acl.rs':(
            'a6ebc8688c24231f0e3f931cc4d3c245da475e88735d5d4f64e2a4d06c7a3f3f',
            '1f9214f88e220f0f5a2fdef602bfa0e5f4b735365d0580bd6161bcbc0f8bbb5d'),
        'codex-rs/windows-sandbox-rs/src/ancestor_metadata.rs':(
            '0ef82ccd9e196bb91b7059637f691f9b413c39c1a49504152b33beac386b59ba',
            '65813376be9f034714bde6806fc1b910ad731824ee094e434bc97a71180b1d6b'),
        'codex-rs/windows-sandbox-rs/src/namespace_coordination.rs':(
            '516dbd693b69b0e6dc4f4da7824725ab278cc4a68cd3a48f227132a01c25257e',
            '7155b3379cb5529d4f8841efc40cb0d52c7fc3f0b29f4f0eb9b048ba9203a814'),
        'codex-rs/windows-sandbox-rs/src/setup_provisioning.rs':(
            '435f353e1ffeace970e15f408901596eee44c98442e4cd0bb2952692839d2697',
            '41db70097e3e9158738603f8cf82787dcf2006b1f1b2ea1306d00b34684b27b9'),
        'codex-rs/windows-sandbox-rs/src/uninstall_windows.rs':(
            'cacefb5b6340c3686959c9a00dba7800c724d36f2cf02d549b45f45573ede721',
            '2d851619c369c6c6eec69de8d5a616fc50d1f81a7c002dd72d94e644317ca42b'),
    }
    assert last=={
        'file':'windows-native-metadata-minimal.patch',
        'sha256':'3f676554a68bafb74ceaa1f78021fe30f9f5fc8ffc9c572a8e182a07a0cda071',
        'changes_by_target':{WIN:[
            {'path':path,'before_sha256':before,'after_sha256':after}
            for path,(before,after) in expected_sources.items()
        ]},
    }
    patch=ROOT/'native'/last['file']
    assert build.sha(patch)==last['sha256']
    patch_paths={
        line[6:].split('\t')[0] for line in patch.read_text(encoding='utf-8').splitlines()
        if line.startswith('+++ b/')
    }
    assert patch_paths==set(expected_sources)
    # The metadata reduction must not replace the CDS namespace or its retained
    # background-handle implementation with the App's separate identities/code.
    protected_paths={
        'codex-rs/windows-sandbox-rs/src/namespace.rs',
        'codex-rs/windows-sandbox-rs/src/namespace_key.rs',
        'codex-rs/windows-sandbox-rs/src/setup_background.rs',
    }
    assert patch_paths.isdisjoint(protected_paths)
    previous=recipe_prefix_through(recipe,'windows-metadata-owner-policy.patch')
    assert previous['overlays']==recipe['overlays'][:index]
    assert serialized_recipe_sha256(previous)=='119f16e9d9847d988e6f453d0dcb37bc10ef6d1eb4d52cc71cdc42b4693a4c76'
    previous_sources=build.overlay_source_hashes(WIN,previous)
    assert {path:previous_sources[path] for path in expected_sources}=={
        path:before for path,(before,_) in expected_sources.items()
    }
    # Reconstruct the exact historical public recipe without the new common
    # replay overlay. Current recipe bytes are pinned separately below.
    document=(json.dumps(recipe,indent=2)+'\n').encode('utf-8')
    assert hashlib.sha256(document).hexdigest()==serialized_recipe_sha256(recipe)=='4729f350664964b19fd6b8876e40a2e4fa4166b388b4a18bf3b438bab554f4ff'
    assert build.native_recipe_identity(WIN,previous)=='3a2a8b850db835ea3aac609dad8171c12f18c41154a6ce942a2cf558d512de32'
    assert build.native_recipe_identity(WIN,recipe)=='410a7343667baecf2a8de222e32a09a858eaa626706ba9b65222fc5135dc1ef2'
    expected_unix={
        'aarch64-apple-darwin':'02e413453fbecd93bdebad2ba5f7033a7ea201206b3a708e5593b84b62b424d1',
        'x86_64-unknown-linux-gnu':'3e752d53b8122d0402cd89850d1368585c8ff6dc800336e796a7875028a745ad',
    }
    assert set(expected_unix)==set(TARGETS)-{WIN}
    changed=copy.deepcopy(recipe)
    changed['overlays'][-1]['sha256']='0'*64
    assert build.native_recipe_identity(WIN,changed)!=build.native_recipe_identity(WIN,recipe)
    for target,expected in expected_unix.items():
        assert build.native_recipe_identity(target,recipe)==build.native_recipe_identity(target,previous)==build.native_recipe_identity(target,changed)==expected


@pytest.mark.parametrize('problem',['omit','reorder','duplicate','wrong_target'])
def test_native_metadata_minimal_overlay_cannot_be_omitted_reordered_or_retargeted(tmp_path,monkeypatch,problem):
    def edit(recipe):
        index=overlay_index(recipe,'windows-native-metadata-minimal.patch')
        if problem=='omit':recipe['overlays'].pop(index)
        elif problem=='reorder':recipe['overlays'][index-1:index+1]=reversed(recipe['overlays'][index-1:index+1])
        elif problem=='duplicate':recipe['overlays'].append(copy.deepcopy(recipe['overlays'][index]))
        else:
            leaf=recipe['overlays'][index]
            leaf['changes_by_target']['x86_64-unknown-linux-gnu']=leaf['changes_by_target'].pop(WIN)
    recipe_copy(tmp_path,monkeypatch,edit)
    with pytest.raises(ValueError):build.runtime_recipe()


def test_capacity_replay_overlay_is_single_file_shared_and_preserves_historical_inputs():
    recipe=json.loads((ROOT/'native/runtime-overlays-0.6.7-acc16483.json').read_bytes())
    index=overlay_index(recipe,'payload-request-capacity-replay.patch')
    assert index==overlay_index(recipe,'payload-request-capacity.patch')+1
    leaf=recipe['overlays'][index]
    assert leaf['sha256']==build.sha(ROOT/'native'/leaf['file'])=='bce3a17c66fafbdeaa6112afbbbdd28c2fd70852fb0c436cb5d7ccc767b58fee'
    path='codex-rs/core/src/session/request_capacity.rs'
    expected=[{'path':path,
               'before_sha256':'c0b5a8f2c9db95f41d4bedee91431e809deccfd043217232c903a8c156ce3535',
               'after_sha256':'3b2be41c23ffbf16b4aa5ac690e4a055392e095a92631df56e413b9eb2666120'}]
    assert leaf['changes_by_target']=={target:expected for target in TARGETS}
    patch=(ROOT/'native'/leaf['file']).read_text(encoding='utf-8')
    assert {line[6:] for line in patch.splitlines() if line.startswith('+++ b/')}=={path}
    historical=qualified_recipe_before_capacity_replay(recipe)
    assert serialized_recipe_sha256(historical)=='4729f350664964b19fd6b8876e40a2e4fa4166b388b4a18bf3b438bab554f4ff'
    assert build.sha(ROOT/'native/payload-request-capacity.patch')=='011fbfe849736f44cdb5d95bfc9326e99377469239f5c9fe35c38cbba9ef1beb'
    expected_ids={'aarch64-apple-darwin': '61d852ba54f522b90a40e5bf6e698a8345ea03bfb95903979654851fcb60e0b3', 'x86_64-unknown-linux-gnu': '2ce6b6cf6d960f3250bb8d3cfa74e7c8a86e98d99cc4b295f276d2f3cb44b100', 'x86_64-pc-windows-msvc': '15c906cca6c2b9cc9b8761fed6e999a6d751e54e06a371a37fd6db1ff06714f9'}
    assert {target:build.native_recipe_identity(target,recipe) for target in TARGETS}==expected_ids
    for target in TARGETS:
        assert build.native_recipe_identity(target,recipe)!=build.native_recipe_identity(target,historical)
        assert build.overlay_source_hashes(target,recipe)[path]==expected[0]['after_sha256']
    document=(ROOT/'native/runtime-overlays-0.6.7-acc16483.json').read_bytes()
    assert document==(json.dumps(recipe,indent=2)+'\n').encode('utf-8')
    assert hashlib.sha256(document).hexdigest()=='acc164834bdf4bc3be257156ed82917eda3e81720a5ed6b7008be24c83f7f6db'


@pytest.mark.parametrize('target',TARGETS)
def test_capacity_replay_patch_applies_to_reconstructed_original_capacity_file(tmp_path,target):
    git=os.environ.get('CODEX_DEEPSEEK_TEST_GIT','git')
    path='codex-rs/core/src/session/request_capacity.rs'
    tree=tmp_path/'single-file-capacity';tree.mkdir()
    command=[git,'-c','core.autocrlf=false','-c','core.eol=lf','apply']
    subprocess.run([*command,'--include='+path,str(ROOT/'native/payload-request-capacity.patch')],cwd=tree,check=True,capture_output=True)
    assert build.sha(tree/path)=='c0b5a8f2c9db95f41d4bedee91431e809deccfd043217232c903a8c156ce3535'
    leaf=next(item for item in build.runtime_recipe()['overlays'] if item['file']=='payload-request-capacity-replay.patch')
    assert leaf['changes_by_target'][target][0]['before_sha256']==build.sha(tree/path)
    subprocess.run([*command,'--check',str(ROOT/'native'/leaf['file'])],cwd=tree,check=True,capture_output=True)
    subprocess.run([*command,str(ROOT/'native'/leaf['file'])],cwd=tree,check=True,capture_output=True)
    assert build.sha(tree/path)==leaf['changes_by_target'][target][0]['after_sha256']
    source=(tree/path).read_text(encoding='utf-8')
    assert 'native_compacted_parallel_metadata_replay_hash_is_stable_and_strict' in source
    assert 'unmatched_legacy_checkpoint_lineage_can_advance_to_ordinary_v3' in source
    assert '#[cfg(any())]' not in source


@pytest.mark.parametrize('problem',['omit','reorder','duplicate','wrong_target'])
def test_capacity_replay_overlay_cannot_be_omitted_reordered_duplicated_or_retargeted(tmp_path,monkeypatch,problem):
    def edit(recipe):
        index=overlay_index(recipe,'payload-request-capacity-replay.patch')
        if problem=='omit':recipe['overlays'].pop(index)
        elif problem=='reorder':recipe['overlays'][index-1:index+1]=reversed(recipe['overlays'][index-1:index+1])
        elif problem=='duplicate':recipe['overlays'].append(copy.deepcopy(recipe['overlays'][index]))
        else:recipe['overlays'][index]['changes_by_target'].pop(TARGETS[0])
    recipe_copy(tmp_path,monkeypatch,edit)
    with pytest.raises(ValueError):build.runtime_recipe()
