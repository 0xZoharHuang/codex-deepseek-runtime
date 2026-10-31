"""Failure-preservation artifacts remain explicitly unqualified; no Cargo/native execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
builder=load("checkpoint_builder",ROOT/"build_native.py")
retention=load("retention_script",ROOT/"ci/retain_native_build.py")


def fixture(tmp_path):
    work=tmp_path/"build";build=work/"b-1234abcd";build.mkdir(parents=True)
    package=tmp_path/"package";(package/"bin").mkdir(parents=True)
    binary=package/"bin/codex";binary.write_bytes(b"ACTUAL_COMPILED_FIXTURE_BYTES_NOT_EXECUTED")
    before={"sha256":"source-inventory","files":{"codex-rs/src/main.rs":"0"*64}}
    checkpoint=builder.write_compile_checkpoint(build,package,{
        "cargo_commands":[["cargo","+1.99.1","build","--locked","--release"]],
        "upstream_commit":"f"*40,"native_recipe_id":"a"*64,
        "environment":{"CARGO_PROFILE_RELEASE_DEBUG":"0"}},before,before)
    return work,build,package,binary,checkpoint


def test_postcompile_failure_preserves_exact_bits_and_source_commands_without_ready(tmp_path):
    work,build,package,binary,checkpoint=fixture(tmp_path)
    # Model the original failure after compilation but before final receipt.
    (build/"dependency-metadata.json").write_bytes(b'{"packages":[]}')
    report=retention.retain(package,work,tmp_path/"evidence")
    assert report["ready"] is False
    assert report["package"]["artifact"]=="native-package-unqualified.tar.gz"
    assert report["package"]["qualified_by_retention"] is False
    archive=tmp_path/"evidence/native-package-unqualified.tar.gz"
    with tarfile.open(archive)as tar:
        assert tar.extractfile("./bin/codex").read()==binary.read_bytes()
        assert not any(x.name.endswith("build-receipt.json")for x in tar.getmembers())
    copied=tmp_path/"evidence/compile-checkpoints/b-1234abcd/compile-checkpoint.json"
    data=json.loads(copied.read_bytes())
    assert data["status"]=="compiled-not-qualified"and data["ready"] is False
    assert data["full_source_inventory"]["files"]=={"codex-rs/src/main.rs":"0"*64}
    assert data["cargo_commands"]==[["cargo","+1.99.1","build","--locked","--release"]]
    assert data["output_files"]["bin/codex"]["sha256"]==hashlib.sha256(binary.read_bytes()).hexdigest()
    assert copied.read_bytes()==checkpoint.read_bytes()
    assert not(package/"build-receipt.json").exists()


def test_checkpoint_rejects_source_change_and_preserves_existing_record(tmp_path):
    work,build,package,binary,checkpoint=fixture(tmp_path)
    original=checkpoint.read_bytes()
    with pytest.raises(ValueError,match="changed"):
        builder.write_compile_checkpoint(build,package,{},{"files":{}},{"files":{"new":"x"}})
    assert checkpoint.read_bytes()==original
    with pytest.raises(FileExistsError):
        builder.write_compile_checkpoint(build,package,{},{"files":{}},{"files":{}})
    assert checkpoint.read_bytes()==original


def test_full_receipt_presence_does_not_make_retention_qualification(tmp_path):
    work,build,package,binary,checkpoint=fixture(tmp_path)
    (package/"build-receipt.json").write_bytes(b"{}")
    report=retention.retain(package,work,tmp_path/"evidence")
    assert report["package"]["artifact"]=="native-package.tar.gz"
    assert report["package"]["final_build_receipt_present"] is True
    assert report["package"]["qualified_by_retention"] is False
    assert report["ready"] is False


@pytest.mark.parametrize("bad",["target-overlap","output-symlink","metadata-symlink","size"])
def test_retention_does_not_follow_redirects_or_upload_target(tmp_path,monkeypatch,bad):
    work,build,package,binary,checkpoint=fixture(tmp_path)
    evidence=tmp_path/"evidence"
    if bad=="target-overlap":evidence=tmp_path
    elif bad=="output-symlink":
        secret=tmp_path/"private";secret.write_bytes(b"NOT_AN_ARTIFACT")
        (package/"outside").symlink_to(secret)
    elif bad=="metadata-symlink":
        checkpoint.unlink();checkpoint.symlink_to(binary)
    elif bad=="size":monkeypatch.setattr(retention,"MAX_PACKAGE_BYTES",1)
    with pytest.raises(ValueError):
        retention.retain(package,work,evidence)


def test_failure_before_output_still_retains_metadata_without_inventing_binary(tmp_path):
    work=tmp_path/"work";build=work/"b-1234abcd";build.mkdir(parents=True)
    (build/"combined.patch").write_bytes(b"original source diff")
    report=retention.retain(tmp_path/"absent-package",work,tmp_path/"evidence")
    assert report["package"] is None and len(report["metadata"])==1 and report["ready"] is False

def test_provenance_cannot_override_unqualified_checkpoint_or_actual_bits(tmp_path):
    work,build,package,binary,checkpoint=fixture(tmp_path)
    other=work/"b-5678abcd";other.mkdir()
    snapshot={"files":{},"sha256":"fixture"}
    path=builder.write_compile_checkpoint(other,package,
        {"ready":True,"status":"qualified","output_files":{}},snapshot,snapshot)
    record=json.loads(path.read_bytes())
    assert record["ready"] is False and record["status"]=="compiled-not-qualified"
    assert record["output_files"]["bin/codex"]["sha256"]==hashlib.sha256(binary.read_bytes()).hexdigest()

def test_retention_scope_marker_is_exclusive_even_without_package(tmp_path):
    work=tmp_path/"absent-work";package=tmp_path/"absent-package";evidence=tmp_path/"evidence"
    retention.retain(package,work,evidence)
    marker=evidence/"retained-build-scope.json";before=marker.read_bytes()
    with pytest.raises(ValueError,match="Preserve original"):
        retention.retain(package,work,evidence)
    assert marker.read_bytes()==before
