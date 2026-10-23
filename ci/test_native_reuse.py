"""Small rejection tests for CI reuse; no real native binary or network call."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ci import qualify_native as native
from ci import qualify_adapter as adapter
from ci import reuse_native as reuse
import build_native as build


def digest(data):
    return hashlib.sha256(data).hexdigest()


def recipe_fixture(tmp_path, monkeypatch):
    target = "aarch64-apple-darwin"
    win = "x86_64-pc-windows-msvc"
    recipe = {"schema": 1, "upstream_commit": "c"*40,
              "upstream_toolchain": "1.99.0", "build_toolchain": "1.99.1",
              "required_base_inputs": {target: [], "x86_64-unknown-linux-gnu": [], win: []},
              "overlays": []}
    (tmp_path/"native").mkdir()
    (tmp_path/"native/runtime-overlays.json").write_text(json.dumps(recipe))
    monkeypatch.setattr(native, "ROOT", tmp_path)
    return target, win, recipe


def test_archived_recipe_retains_default_strict_mode_and_accepts_only_windows_delta(tmp_path, monkeypatch):
    target, win, current = recipe_fixture(tmp_path, monkeypatch)
    original = deepcopy(current)
    current["overlays"].append({"file": "windows-only.patch", "sha256": "a"*64,
                               "changes_by_target": {win: [{"path": "windows.rs"}]}})
    (tmp_path/"native/runtime-overlays.json").write_text(json.dumps(current))
    archive = tmp_path/"original.json";archive.write_text(json.dumps(original))
    receipt = {"overlay_recipe_sha256": native.sha(archive)}
    with pytest.raises(ValueError, match="Full recipe"):
        native.recipe_scope_identity(receipt, current, target, None)
    proof = native.recipe_scope_identity(receipt, current, target, archive)
    assert proof["only_windows_recipe_changes"] and proof["original_sha256"] == native.sha(archive)


@pytest.mark.parametrize("bad", ["shared", "global", "hash", "duplicate", "target"])
def test_archived_recipe_rejects_unrelated_drift(tmp_path, monkeypatch, bad):
    target, win, current = recipe_fixture(tmp_path, monkeypatch)
    original = deepcopy(current);archive = tmp_path/"original.json"
    if bad == "shared":original["required_base_inputs"][target] = [{"file": "changed", "sha256": "a"*64}]
    elif bad == "global":original["build_toolchain"] = "other"
    archive.write_text(json.dumps(original))
    if bad == "duplicate":archive.write_text('{"schema":1,"schema":2}')
    receipt = {"overlay_recipe_sha256": native.sha(archive) if bad != "hash" else "0"*64}
    with pytest.raises((ValueError, KeyError)):
        native.recipe_scope_identity(receipt, current, win if bad == "target" else target, archive)


def package_fixture(tmp_path, monkeypatch):
    target, win, recipe = recipe_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "build_target", lambda: target)
    monkeypatch.setattr(build, "runtime_recipe", lambda: recipe)
    monkeypatch.setattr(build, "COMMIT", recipe["upstream_commit"])
    monkeypatch.setattr(build, "VERSION", "0.1")
    monkeypatch.setattr(build, "build_assets", lambda target: {"source": ("https://example.test/source", "a"*64)})
    monkeypatch.setattr(build, "overlay_source_hashes", lambda target, recipe: {})
    (tmp_path/"native/source-changes.json").write_text('{"files":[]}')
    package = tmp_path/"package";(package/"bin").mkdir(parents=True)
    (package/"bin/codex").write_bytes(b"synthetic cli")
    (package/"bin/codex-http-transport").write_bytes(b"synthetic helper")
    rid = build.native_recipe_identity(target, recipe)
    metadata = {"target": target, "recipeId": rid, "patchInputs": [], "upstreamCommit": recipe["upstream_commit"],
                "version": "0.1", "patchSha256": "b"*64}
    (package/"codex-package.json").write_text(json.dumps(metadata))
    files = {p.relative_to(package).as_posix():native.sha(p) for p in package.rglob("*") if p.is_file()}
    receipt = {"native_recipe_id": rid, "upstream_commit": recipe["upstream_commit"],
               "build_toolchain_pin": recipe["build_toolchain"], "rustc": "rustc 1.99.1 fixture",
               "overlay_recipe_sha256": native.sha(tmp_path/"native/runtime-overlays.json"),
               "patch_inputs": [], "archives": {"source": {"url":"https://example.test/source","sha256":"a"*64}},
               "source_files": {}, "patch_sha256":"b"*64, "combined_patch_sha256":"b"*64, "files":files}
    (package/"build-receipt.json").write_text(json.dumps(receipt))
    manifest = {"status":"ready", "target":target, "native_recipe_id":rid,
                "upstream_commit":recipe["upstream_commit"], "patch_sha256":"b"*64,
                "files":{p.relative_to(package).as_posix():native.sha(p) for p in package.rglob("*") if p.is_file()}}
    (tmp_path/"native/runtime.json").write_text(json.dumps(manifest))
    return package, tmp_path/"native/runtime-overlays.json", receipt


def test_reused_package_requires_current_manifest_even_when_receipt_is_self_consistent(tmp_path, monkeypatch):
    package, recipe, receipt = package_fixture(tmp_path, monkeypatch)
    assert native.package_identity(package, recipe)["files"] == 3
    (package/"bin/codex").write_bytes(b"changed binary")
    receipt["files"]["bin/codex"] = native.sha(package/"bin/codex")
    (package/"build-receipt.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="complete current target manifest"):
        native.package_identity(package, recipe)


def archive_fixture(tmp_path, entries):
    archive = tmp_path/"native.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, content, kind, mode in entries:
            item = tarfile.TarInfo(name);item.mode = mode;item.type = kind
            if kind == tarfile.REGTYPE:
                item.size = len(content);bundle.addfile(item, io.BytesIO(content))
            else:
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):item.linkname = "../outside"
                bundle.addfile(item)
    return archive


def test_exact_archive_roundtrip(tmp_path):
    archive = archive_fixture(tmp_path, [("./bin", b"", tarfile.DIRTYPE, 0o755),
                                        ("./bin/codex", b"fixture", tarfile.REGTYPE, 0o755)])
    manifest = {"archive":{"sha256":native.sha(archive)}, "files":{"bin/codex":digest(b"fixture")}}
    assert reuse.extract_exact(archive, tmp_path/"output", manifest) == manifest["files"]
    assert (tmp_path/"output/bin/codex").read_bytes() == b"fixture"


@pytest.mark.parametrize("bad", ["traversal","absolute","backslash","colon","duplicate","link","hardlink","fifo","privileged","missing","changed","extra"])
def test_archive_rejects_unsafe_or_incomplete_members_before_extraction(tmp_path, bad):
    entries = [("bin/codex", b"fixture", tarfile.REGTYPE, 0o755)]
    expected = {"bin/codex":digest(b"fixture")}
    names = {"traversal":"../outside","absolute":"/outside","backslash":"bin\\codex","colon":"C:outside"}
    if bad in names:entries[0] = (names[bad], b"fixture", tarfile.REGTYPE, 0o755)
    elif bad == "duplicate":entries.append(entries[0])
    elif bad in {"link","hardlink","fifo"}:
        entries[0] = ("bin/codex", b"", {"link":tarfile.SYMTYPE,"hardlink":tarfile.LNKTYPE,"fifo":tarfile.FIFOTYPE}[bad], 0o755)
    elif bad == "privileged":entries[0] = ("bin/codex", b"fixture", tarfile.REGTYPE, 0o4755)
    elif bad == "missing":expected["bin/absent"] = digest(b"x")
    elif bad == "changed":entries[0] = ("bin/codex", b"different", tarfile.REGTYPE, 0o755)
    else:entries.append(("unlisted", b"x", tarfile.REGTYPE, 0o600))
    archive = archive_fixture(tmp_path, entries);manifest={"archive":{"sha256":native.sha(archive)},"files":expected}
    with pytest.raises(ValueError):reuse.extract_exact(archive, tmp_path/"output", manifest)
    assert not (tmp_path/"output").exists()


@pytest.mark.parametrize("bad", [None,"attempt","head","status","path","expired","digest","id"])
def test_fixed_run_and_artifact_metadata_are_bound(bad):
    target="aarch64-apple-darwin";selection=reuse.ARTIFACTS[target]
    run={"id":reuse.RUN,"head_sha":reuse.HEAD,"status":"completed","conclusion":"success","run_attempt":1,
         "repository":{"full_name":reuse.REPOSITORY},"path":".github/workflows/native-platform-qualification.yml"}
    item={"name":selection["name"],"id":selection["id"],"expired":False,"digest":selection["github_digest"],
          "workflow_run":{"id":reuse.RUN,"head_sha":reuse.HEAD}}
    if bad=="attempt":run["run_attempt"]=2
    elif bad=="head":run["head_sha"]="other"
    elif bad=="status":run["conclusion"]="failure"
    elif bad=="path":run["path"]="another.yml"
    elif bad=="expired":item["expired"]=True
    elif bad=="digest":item["digest"]="sha256:"+"0"*64
    elif bad=="id":item["id"]+=1
    if bad is None:assert reuse.verify_download_metadata(run,{"artifacts":[item]},target)==item
    else:
        with pytest.raises(ValueError):reuse.verify_download_metadata(run,{"artifacts":[item]},target)


def partition_fixture():
    native_items=[{"nodeid":native.SUITES[0]+"::case"+str(i),"classname":"native","name":"case"+str(i)} for i in range(15)]
    rest=[{"nodeid":"tests/test_example.py::test_a","classname":"example","name":"test_a"},
          {"nodeid":"tests/test_example.py::test_b","classname":"example","name":"test_b"}]
    cases=[ET.Element("testcase",classname=x["classname"],name=x["name"]) for x in rest]
    ET.SubElement(cases[1],"skipped",message="Windows-only fixture")
    allowed=[{"classname":"example","name":"test_b","message":"Windows-only fixture"}]
    return native_items+rest,{"names":[x["name"] for x in native_items]},cases,rest,allowed


@pytest.mark.parametrize("bad", [None,"missing","duplicate","native-name","wrong-collection","extra-skip","failure"])
def test_complete_collection_partition_and_exact_skips(bad):
    all_items,prior,cases,own,allowed=partition_fixture()
    if bad=="missing":cases.pop()
    elif bad=="duplicate":cases.append(deepcopy(cases[0]))
    elif bad=="native-name":prior["names"][0]="other"
    elif bad=="wrong-collection":own=own[:-1]
    elif bad=="extra-skip":ET.SubElement(cases[0],"skipped",message="unplanned")
    elif bad=="failure":ET.SubElement(cases[0],"failure",message="failed")
    if bad is None:
        assert adapter.validate_partition(all_items,prior,cases,own,allowed)["every_collected_case_executed_once"]
    else:
        with pytest.raises(ValueError):adapter.validate_partition(all_items,prior,cases,own,allowed)


@pytest.mark.parametrize("bad", ["deselected","duplicate","empty"])
def test_collection_cannot_omit_or_repeat_cases(tmp_path,bad):
    items=[{"nodeid":"test::one","classname":"x","name":"one"}]
    data={"deselected":0,"items":items}
    if bad=="deselected":data["deselected"]=1
    elif bad=="duplicate":items.append(deepcopy(items[0]))
    else:data["items"]=[]
    path=tmp_path/"collection.json";path.write_text(json.dumps(data))
    with pytest.raises(ValueError):adapter.collection(path)


def test_observed_test_process_success_and_timeout_are_distinct(tmp_path):
    import os
    from codex_deepseek.processes import matches
    for name, seconds, timeout in (("success", .15, 5), ("timeout", 30, .15)):
        with (tmp_path/(name+".log")).open("wb") as log:
            result = native.run_observed(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep("+str(seconds)+")"],
                cwd=tmp_path, env=dict(os.environ), log=log, timeout=timeout)
        assert result["passed"] is (name=="success")
        assert result["timed_out"] is (name=="timeout")
        assert not result["remaining_after_cleanup"] and not result["unknown_live"], result
        assert all(not matches(row["pid"],row["birth"]) for row in result["observed"])


def test_leaked_owned_child_is_cleaned_without_becoming_a_pass(tmp_path):
    import os
    from codex_deepseek.processes import matches
    child="import time;time.sleep(30)"
    parent="import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-B','-c',"+repr(child)+"]);time.sleep(.3)"
    with (tmp_path/"leaked.log").open("wb") as log:
        result=native.run_observed([sys.executable,"-I","-B","-c",parent],
                                  cwd=tmp_path,env=dict(os.environ),log=log,timeout=10)
    assert result["returncode"]==0 and not result["passed"]
    assert result["remaining_before_cleanup"] and result["forced_cleanup"]
    assert not result["remaining_after_cleanup"] and not result["unknown_live"], result
    assert all(not matches(row["pid"],row["birth"]) for row in result["observed"])


def test_pinned_macos_layout_may_contain_its_empty_resource_directory(tmp_path):
    archive=archive_fixture(tmp_path,[("codex-resources",b"",tarfile.DIRTYPE,0o755),
                                      ("bin/codex",b"fixture",tarfile.REGTYPE,0o755)])
    manifest={"target":"aarch64-apple-darwin","archive":{"sha256":native.sha(archive)},
              "files":{"bin/codex":digest(b"fixture")}}
    assert reuse.extract_exact(archive,tmp_path/"mac",manifest)==manifest["files"]
    assert (tmp_path/"mac/codex-resources").is_dir()
    assert not list((tmp_path/"mac/codex-resources").iterdir())
    manifest["target"]="x86_64-unknown-linux-gnu"
    with pytest.raises(ValueError,match="Unrecorded archive directory"):
        reuse.checked_members(archive,manifest)
