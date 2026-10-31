"""Reject invalid native-only reuse without download, native execution or Rust."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import io
import stat
import zipfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("retained_native_test",ROOT/"ci/reuse_retained_native.py")
reuse=importlib.util.module_from_spec(spec);spec.loader.exec_module(reuse)


def metadata_fixture(target="x86_64-unknown-linux-gnu"):
    pins=json.loads((ROOT/"ci/retained-native-build-49da.json").read_bytes())
    pin=deepcopy(pins["targets"][target])
    run={"id":pins["run_id"],"run_attempt":1,"head_sha":pins["producer_commit"],
         "path":pins["workflow_path"],"repository":{"id":pins["repository_id"],"full_name":pins["repository"]},
         "status":"in_progress","conclusion":None}
    job={"id":pin["job_id"],"run_id":pins["run_id"],"run_attempt":1,
         "head_sha":pins["producer_commit"],"name":pin["job_name"],
         "status":"completed","conclusion":"failure",
         "steps":[{"name":name,"status":"completed","conclusion":state}
                  for name,state in pin["required_steps"].items()]}
    artifact={"id":pin["artifact_id"],"name":pin["artifact_name"],"expired":False,
              "size_in_bytes":pin["zip_bytes"],"digest":"sha256:"+pin["zip_sha256"],
              "expires_at":pin["expires_at"],"workflow_run":{"id":pins["run_id"],
              "head_sha":pins["producer_commit"],"repository_id":pins["repository_id"],
              "head_repository_id":pins["repository_id"]}}
    return pins,pin,run,job,artifact


@pytest.mark.parametrize("bad",[None,"run","source","repo","job","job-incomplete","step",
                                 "missing-step","duplicate-step","adapter-greenwash",
                                 "overall-greenwash","expired","expiry-time","digest","target"])
@pytest.mark.parametrize("target",["x86_64-unknown-linux-gnu","aarch64-apple-darwin"])
def test_live_metadata_scope_preserves_failed_full_run_and_exact_native_steps(bad,target):
    pins,pin,run,job,artifact=metadata_fixture(target)
    now=datetime(2026,11,1,tzinfo=timezone.utc)
    if bad=="run":run["id"]+=1
    elif bad=="source":run["head_sha"]="0"*40
    elif bad=="repo":run["repository"]["id"]=0
    elif bad=="job":job["id"]+=1
    elif bad=="job-incomplete":job["status"]="in_progress"
    elif bad=="step":job["steps"][0]["conclusion"]="failure"
    elif bad=="missing-step":job["steps"].pop()
    elif bad=="duplicate-step":job["steps"].append(deepcopy(job["steps"][0]))
    elif bad=="adapter-greenwash":
        next(s for s in job["steps"]if s["name"]=="Check adapter and platform regressions")["conclusion"]="success"
    elif bad=="overall-greenwash":run.update(status="completed",conclusion="success")
    elif bad=="expired":artifact["expired"]=True
    elif bad=="expiry-time":now=datetime(2026,11,8,tzinfo=timezone.utc)
    elif bad=="digest":artifact["digest"]="sha256:"+"0"*64
    elif bad=="target":job["name"]="linux-x64" if target=="aarch64-apple-darwin" else "macos-arm64"
    if bad is None:
        reuse.validate_scope(run,job,artifact,pins,pin,now=now)
        run.update(status="completed",conclusion="failure")
        reuse.validate_scope(run,job,artifact,pins,pin,now=now)
    else:
        with pytest.raises(ValueError):
            reuse.validate_scope(run,job,artifact,pins,pin,now=now)


def carry_fixture(target="x86_64-unknown-linux-gnu"):
    identity={"target":target,"recipe_id":"recipe","upstream_commit":"commit",
              "compiler":"rustc 1.99.1 fixture","cli_sha256":"c","helper_sha256":"h",
              "build_receipt_sha256":"r","files":3}
    files={"codex-rs/Cargo.lock":"lock","codex-rs/pool.rs":"pool","codex-rs/pool_tests.rs":"tests"}
    contract={"final":files,"before":files,"links":{}}
    digest=reuse.sources.inventory_digest(files)
    inputs={"build_native.py":"b","native_licenses.py":"l","verify_native_source.py":"v",
            "native/pool.patch":"p",
            "ci/qualify_guardian_pool.py":"pool-controller",
            "ci/qualify_native.py":"native-controller",
            "src/codex_deepseek/processes.py":"process-lifetime",
            "tests/test_native_raw_http.py":"native-raw-tests",
            "tests/test_native_payload_retry.py":"native-payload-tests"}
    parsed={"executed":10,"failed":0,"ignored":0,"filtered_out_not_executed":117,
            "tests":list(reuse.pool.EXPECTED_TESTS)}
    junit={"cases":22,"failed":0,"skipped":0,"names":["actual22"]}
    base={"package":deepcopy(identity),"source_sha256":deepcopy(inputs),"processes":{"passed":True}}
    pool={**deepcopy(base),"status":"passed_native_guardian_pool_scope","tests":deepcopy(parsed),
          "producer":{"full_source_inventory":deepcopy(files),"full_source_inventory_sha256":digest,
                      "full_source_file_count":len(files)}}
    native={**deepcopy(base),"status":"passed_native_transport_scope","junit":deepcopy(junit)}
    checkpoint={"status":"compiled-not-qualified","ready":False,"source_unchanged_through_compile":True,
                "full_source_inventory":{"files":deepcopy(files),"sha256":digest},
                "native_recipe_id":"recipe","compiler_driver_inputs":{k:inputs[k]for k in
                ("build_native.py","native_licenses.py","verify_native_source.py")}}
    return pool,native,checkpoint,identity,contract,inputs,parsed,junit


@pytest.mark.parametrize("bad",[None,"compiled","recipe","pool-status","pool-process","test-name",
                                 "ignored","missing-full-source","lock","test-source",
                                 "driver","summary-only","checkpoint-ready","native-junit"])
def test_exact_source_compiler_tests_and_package_are_required_for_carry(bad):
    values=list(carry_fixture())
    pool,native,checkpoint,identity,contract,inputs,parsed,junit=values
    if bad=="compiled":identity["cli_sha256"]="changed"
    elif bad=="recipe":identity["recipe_id"]="changed"
    elif bad=="pool-status":pool["status"]="failed"
    elif bad=="pool-process":pool["processes"]["passed"]=False
    elif bad=="test-name":pool["tests"]["tests"][0]="replacement"
    elif bad=="ignored":pool["tests"]["ignored"]=1
    elif bad=="missing-full-source":pool["producer"].pop("full_source_inventory")
    elif bad=="lock":contract["final"]={**contract["final"],"codex-rs/Cargo.lock":"changed"}
    elif bad=="test-source":contract["final"]={**contract["final"],"codex-rs/pool_tests.rs":"changed"}
    elif bad=="driver":inputs["build_native.py"]="changed"
    elif bad=="summary-only":values[0]={"status":"passed_native_guardian_pool_scope"}
    elif bad=="checkpoint-ready":checkpoint["ready"]=True
    elif bad=="native-junit":native["junit"]={"cases":22,"names":["replacement"]}
    if bad is None:
        result=reuse.validate_carry(*values)
        assert result["current_Rust_tests_executed"]==0
        assert result["original_adapter_failure_preserved"] is True
        assert result["current_offline_qualification_claimed"] is False
    else:
        with pytest.raises((ValueError,KeyError)):
            reuse.validate_carry(*values)


@pytest.mark.parametrize("bad",[None,"digest","duplicate","traversal","symlink","DOS-drive"])
def test_only_exact_reviewed_small_scope_members_are_copied(tmp_path,bad):
    body=b'{"actual_scope":"not a summary-only pass"}'
    name="../escape"if bad=="traversal"else"C:/escape"if bad=="DOS-drive"else"pool/qualification.json"
    path=tmp_path/"artifact.zip"
    with zipfile.ZipFile(path,"w")as z:
        if bad=="symlink":
            entry=zipfile.ZipInfo(name);entry.create_system=3;entry.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(entry,body)
        else:
            z.writestr(name,body)
        if bad=="duplicate":
            # Duplicate warning is itself rejected under -Werror, so create the
            # duplicate using a local warning capture and leave validation strict.
            import warnings
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                z.writestr(name,body)
    pin={"members":{name:{"bytes":len(body),"sha256":hashlib.sha256(body).hexdigest()}}}
    if bad=="digest":pin["members"][name]["sha256"]="0"*64
    out=tmp_path/"scope";out.mkdir()
    if bad is None:
        reuse.extract_scope_members(path,pin,out)
        assert (out/name).read_bytes()==body
    else:
        with pytest.raises(ValueError):reuse.extract_scope_members(path,pin,out)
    assert not(tmp_path/"escape").exists()


@pytest.mark.parametrize("bad",[None,"wrong-receipt","missing"])
def test_original_receipt_is_digest_bound_before_exact_package_extract(tmp_path,bad):
    raw=b'{"files":{"bin/codex":"original"}}'
    archive=tmp_path/"native.tar.gz"
    with tarfile.open(archive,"w:gz")as tar:
        if bad!="missing":
            member=tarfile.TarInfo("./build-receipt.json");member.size=len(raw)
            tar.addfile(member,io.BytesIO(raw))
    wanted=hashlib.sha256(raw).hexdigest()
    if bad=="wrong-receipt":wanted="0"*64
    if bad is None:assert reuse.receipt_from_archive(archive,wanted)=={"files":{"bin/codex":"original"}}
    else:
        with pytest.raises(ValueError):reuse.receipt_from_archive(archive,wanted)

def test_registered_workflow_explicit_retained_mode_never_starts_fresh_Cargo():
    registered=(ROOT/".github/workflows/native-platform-qualification.yml").read_text()
    helper=(ROOT/".github/workflows/retained-native-qualification.yml").read_text()
    native_job=registered.split("  native:\n",1)[1].split("\n  retained:\n",1)[0]
    retained_job=registered.split("\n  retained:\n",1)[1]
    assert "type: boolean\n        default: false" in registered
    assert "inputs.retained_mode != true" in native_job
    assert "Build the pinned native package" in native_job
    assert "inputs.retained_mode == true" in retained_job
    assert "uses: ./.github/workflows/retained-native-qualification.yml" in retained_job
    assert "platform:" in retained_job and "inputs.retained_target" in retained_job
    assert "run:" not in retained_job and "build_native.py" not in retained_job
    assert "workflow_call:" in helper and "type: string" in helper
    assert "workflow_dispatch:" not in helper
    assert "ci/reuse_retained_native.py" in helper
    assert "build_native.py" not in helper and "cargo " not in helper
    assert "contents: read" in retained_job and "actions: read" in retained_job

@pytest.mark.parametrize("path",[
    "ci/qualify_guardian_pool.py","ci/qualify_native.py",
    "src/codex_deepseek/processes.py","tests/test_native_raw_http.py",
    "tests/test_native_payload_retry.py"])
@pytest.mark.parametrize("kind",["current-change","pool-missing","native-missing"])
def test_prior_scope_also_binds_exact_controller_process_and_22test_sources(path,kind):
    values=list(carry_fixture())
    if kind=="current-change":values[5][path]="changed-current-source"
    elif kind=="pool-missing":values[0]["source_sha256"].pop(path)
    else:values[1]["source_sha256"].pop(path)
    with pytest.raises(ValueError,match="inputs differ"):
        reuse.validate_carry(*values)


@pytest.mark.parametrize("target",["x86_64-unknown-linux-gnu","aarch64-apple-darwin"])
@pytest.mark.parametrize("kind",["other-job","other-artifact"])
def test_same_run_other_platform_evidence_is_not_interchangeable(target,kind):
    pins,pin,run,job,artifact=metadata_fixture(target)
    other="x86_64-unknown-linux-gnu" if target=="aarch64-apple-darwin" else "aarch64-apple-darwin"
    _,_,_,other_job,other_artifact=metadata_fixture(other)
    if kind=="other-job":job=other_job
    else:artifact=other_artifact
    with pytest.raises(ValueError):
        reuse.validate_scope(run,job,artifact,pins,pin,now=datetime(2026,11,1,tzinfo=timezone.utc))


@pytest.mark.parametrize("target",["x86_64-unknown-linux-gnu","aarch64-apple-darwin"])
def test_original_compiled_scope_target_is_not_interchangeable(target):
    values=list(carry_fixture(target))
    # Byte hashes alone do not authorize a different native target.
    values[0]["package"]["target"]=(
        "x86_64-unknown-linux-gnu" if target=="aarch64-apple-darwin" else "aarch64-apple-darwin")
    with pytest.raises(ValueError,match="compiled package bytes"):
        reuse.validate_carry(*values)
