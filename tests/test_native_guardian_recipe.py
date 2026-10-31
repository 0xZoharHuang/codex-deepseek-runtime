"""Guardian native candidate admission; real source replay only, no Cargo or models."""
import copy,hashlib,importlib.util,json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("guardian_native_recipe",ROOT/"build_native.py")
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
CANDIDATE=ROOT/"native/guardian-provider-transport-0164-candidate.json"
PATCH="guardian-provider-transport-0164.patch"

def test_candidate_changes_all_target_recipe_ids_without_relabeling_ready_assets():
    stable=json.loads((ROOT/"native/runtime-overlays-0.6.7-acc16483.json").read_bytes())
    candidate=build.runtime_recipe()
    assert candidate==build.runtime_recipe(CANDIDATE)
    assert build.sha(ROOT/"native/runtime-overlays.json")=="29ffae4eeae3733592626ea1cc0c8154867006af4e761f61cb3f03e17dac9fca"
    assert (ROOT/"native/runtime-overlays.json").read_bytes()==CANDIDATE.read_bytes()
    assert hashlib.sha256((ROOT/"native/runtime-overlays-0.6.7-acc16483.json").read_bytes()).hexdigest()=="acc164834bdf4bc3be257156ed82917eda3e81720a5ed6b7008be24c83f7f6db"
    assert candidate["upstream_commit"]==stable["upstream_commit"]=="fe136d4e70ef2b5bbed40a2d22b3aabf267796d3"
    assert candidate["build_toolchain"]==stable["build_toolchain"]
    assert candidate["required_base_inputs"]==stable["required_base_inputs"]
    assert candidate["overlays"][:-1]==stable["overlays"]
    entry=candidate["overlays"][-1]
    assert entry["file"]==PATCH
    assert entry["sha256"]==hashlib.sha256((ROOT/"native"/PATCH).read_bytes()).hexdigest()
    assert set(entry["changes_by_target"])==set(build.PLATFORM_ASSETS)
    for target in build.PLATFORM_ASSETS:
        manifest_name="runtime.json"if target=="aarch64-apple-darwin"else"runtime-"+target+".json"
        manifest=json.loads((ROOT/"native"/manifest_name).read_text(encoding="utf-8"))
        assert manifest["distribution_version"]=="0.6.7"
        assert build.native_recipe_identity(target,stable)==manifest["native_recipe_id"]
        assert build.native_recipe_identity(target,candidate)!=manifest["native_recipe_id"]
        changes=entry["changes_by_target"][target]
        assert [row["path"]for row in changes]==[
            "codex-rs/ext/guardian-v2/src/async_scorer/sampler/connection_pool.rs",
            "codex-rs/ext/guardian-v2/src/async_scorer/sampler/connection_pool_tests.rs",
        ]
    assert entry["changes_by_target"]["aarch64-apple-darwin"]==entry["changes_by_target"]["x86_64-pc-windows-msvc"]

@pytest.mark.parametrize("selection",["runtime-overlays.json",CANDIDATE.name])
@pytest.mark.parametrize("problem",["omitted_guard","wrong_order","windows_only","extra_patch"])
def test_active_and_candidate_cannot_omit_or_weaken_shared_guard(tmp_path,monkeypatch,problem,selection):
    value=copy.deepcopy(build.runtime_recipe(CANDIDATE))
    if problem=="omitted_guard":value["overlays"].pop()
    elif problem=="wrong_order":value["overlays"][-2:]=reversed(value["overlays"][-2:])
    elif problem=="windows_only":
        entry=value["overlays"][-1]
        entry["changes_by_target"]={"x86_64-pc-windows-msvc":entry["changes_by_target"]["x86_64-pc-windows-msvc"]}
    else:value["overlays"].append(copy.deepcopy(value["overlays"][-1]))
    source=tmp_path/"source";(source/"native").mkdir(parents=True)
    path=source/"native"/selection;path.write_bytes(json.dumps(value).encode())
    monkeypatch.setattr(build,"SOURCE",source)
    with pytest.raises(ValueError):build.runtime_recipe(path)

def test_recipe_path_cannot_select_unreviewed_external_input(tmp_path):
    outside=tmp_path/CANDIDATE.name
    outside.write_bytes(CANDIDATE.read_bytes())
    with pytest.raises(ValueError,match="active or explicit Guardian candidate"):
        build.runtime_recipe(outside)

def test_default_recipe_cannot_revert_to_archived_stack_after_source_promotion(tmp_path,monkeypatch):
    source=tmp_path/"source";(source/"native").mkdir(parents=True)
    (source/"native/runtime-overlays.json").write_bytes((ROOT/"native/runtime-overlays-0.6.7-acc16483.json").read_bytes())
    monkeypatch.setattr(build,"SOURCE",source)
    with pytest.raises(ValueError,match="order"):
        build.runtime_recipe()


def test_default_compiler_commands_keep_existing_rustup_path():
    compiler,cargo,environment=build.compiler_commands("1.99.1")
    assert compiler==["rustup","run","1.99.1","rustc","--version"]
    assert cargo==["cargo","+1.99.1"]
    assert environment=={}

def test_explicit_compiler_directory_is_pinned_without_mutating_environment(tmp_path,monkeypatch):
    monkeypatch.setattr(build.platform,"system",lambda:"Windows")
    for name in ["rustc","rustdoc","cargo"]:(tmp_path/(name+".exe")).write_bytes(b"owned-not-executed")
    compiler,cargo,environment=build.compiler_commands("1.99.1",tmp_path)
    assert compiler==[str(tmp_path/"rustc.exe"),"--version"]
    assert cargo==[str(tmp_path/"cargo.exe")]
    assert environment=={"RUSTC":str(tmp_path/"rustc.exe"),"RUSTDOC":str(tmp_path/"rustdoc.exe")}
    (tmp_path/"rustdoc.exe").unlink()
    with pytest.raises(ValueError,match="rustc, rustdoc and cargo"):
        build.compiler_commands("1.99.1",tmp_path)


def test_archived_067_recipe_is_not_a_build_selection():
    with pytest.raises(ValueError,match="active or explicit Guardian candidate"):
        build.runtime_recipe(ROOT/"native/runtime-overlays-0.6.7-acc16483.json")


@pytest.mark.parametrize("target",list(build.PLATFORM_ASSETS))
def test_067_ready_manifest_identity_cannot_qualify_as_guarded_package(tmp_path,monkeypatch,target):
    # The original ready manifest is rollback provenance, not a new binary claim.
    import sys
    qualification_spec=importlib.util.spec_from_file_location("guardian_old_package_qualification",ROOT/"ci/qualify_native.py")
    native=importlib.util.module_from_spec(qualification_spec);qualification_spec.loader.exec_module(native)
    name="runtime.json" if target=="aarch64-apple-darwin" else "runtime-"+target+".json"
    old=json.loads((ROOT/"native"/name).read_bytes())
    assert old["status"]=="ready" and old["distribution_version"]=="0.6.7"
    assert old["native_recipe_id"]!=build.native_recipe_identity(target)
    package=tmp_path/"old-package";package.mkdir()
    (package/"codex-package.json").write_bytes(json.dumps({
        "target":target,"recipeId":old["native_recipe_id"]}).encode())
    (package/"build-receipt.json").write_bytes(b"{}")
    monkeypatch.setitem(sys.modules,"build_native",build)
    monkeypatch.setattr(build,"build_target",lambda:target)
    with pytest.raises(ValueError,match="Package target or recipe"):
        native.package_identity(package)
