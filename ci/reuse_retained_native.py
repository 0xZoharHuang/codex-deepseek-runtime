"""Acquire one pinned native-build scope; never relabel a failed full workflow."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
import sys
import tarfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ci import reuse_qualified_native as legacy
from ci import reuse_native as archives
from ci import qualify_native as native
from ci import qualify_guardian_pool as pool
import build_native as build
import verify_native_source as sources

PINS = ROOT/"ci/retained-native-build-49da.json"


def need(ok, message):
    if not ok:
        raise ValueError(message)


def validate_scope(run, job, artifact, pins, pin, *, now=None):
    need(pins.get("scope") == "retained-native-build-and-transport-only",
         "Not a retained native-only scope")
    need(run.get("id") == pins["run_id"] and run.get("run_attempt") == pins["run_attempt"]
         and run.get("head_sha") == pins["producer_commit"]
         and run.get("path") == pins["workflow_path"]
         and run.get("repository", {}).get("id") == pins["repository_id"]
         and run.get("repository", {}).get("full_name") == pins["repository"],
         "Original run/source/repository differs")
    # Another matrix job may still run. This never turns adapter failure into success.
    need((run.get("status"), run.get("conclusion")) in
         {("in_progress", None), ("completed", "failure")},
         "Original failed-scope run has an unexpected status")
    need(job.get("id") == pin["job_id"] and job.get("run_id") == pins["run_id"]
         and job.get("run_attempt") == pins["run_attempt"]
         and job.get("head_sha") == pins["producer_commit"]
         and job.get("name") == pin["job_name"]
         and job.get("status") == "completed"
         and job.get("conclusion") == pin["job_conclusion"],
         "Original target job differs or is incomplete")
    steps = {}
    for item in job.get("steps", []):
        need(item["name"] not in steps, "Duplicate original step identity")
        steps[item["name"]] = item
    for name, conclusion in pin["required_steps"].items():
        need(name in steps and steps[name].get("status") == "completed"
             and steps[name].get("conclusion") == conclusion,
             "Original concrete step did not meet its declared scope: " + name)
    legacy.validate_artifact(artifact, pins, pin)
    expires = datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00"))
    need(artifact["expires_at"] == pin["expires_at"]
         and expires > (now or datetime.now(timezone.utc)),
         "Retained artifact has expired or its expiry changed")


def extract_scope_members(zip_path, pin, directory):
    """Only reviewed small records, in addition to legacy exact package/recipe extraction."""
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        need(len(names) == len(set(names)), "Duplicate artifact member")
        for name, descriptor in pin["members"].items():
            path = PurePosixPath(name)
            need(not path.is_absolute() and ".." not in path.parts and "\\" not in name and ":" not in name,
                 "Unsafe scope record path")
            info = archive.getinfo(name)
            need(not info.is_dir() and not info.flag_bits & 1
                 and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG)
                 and info.file_size == descriptor["bytes"] and info.file_size <= 12*1024**2,
                 "Scope record type/size differs")
            data = archive.read(info)
            need(hashlib.sha256(data).hexdigest() == descriptor["sha256"],
                 "Scope record digest differs")
            output = directory.joinpath(*path.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as stream:
                stream.write(data)


def receipt_from_archive(archive, expected_sha):
    """Read an original receipt; it is still checked against an independent artifact pin."""
    with tarfile.open(archive, "r:gz") as tar:
        items = [item for item in tar.getmembers()
                 if item.name.removeprefix("./") == "build-receipt.json"]
        need(len(items) == 1 and items[0].isfile() and items[0].size <= 2*1024**2,
             "Original package receipt is missing or unsafe")
        raw = tar.extractfile(items[0]).read()
    need(hashlib.sha256(raw).hexdigest() == expected_sha, "Original package receipt digest differs")
    receipt = legacy.unique_json(raw)
    need(isinstance(receipt.get("files"), dict) and receipt["files"],
         "Original receipt has no inventory")
    return receipt


def expected_full_source(contract):
    result = dict(contract["final"])
    for name, target in contract["links"].items():
        resolved = (PurePosixPath(name).parent/target).as_posix()
        need(resolved in contract["before"], "Unknown original archive link target")
        result[name] = contract["before"][resolved]
    return result


def native_input_hashes(contract):
    names = {"build_native.py", "native_licenses.py", "verify_native_source.py",
             "native/runtime-overlays.json", "native/source-changes.json",
             "native/license-data.json", "ci/qualify_guardian_pool.py", "ci/qualify_native.py",
             "src/codex_deepseek/processes.py", "tests/test_native_raw_http.py",
             "tests/test_native_payload_retry.py"}
    names.update("native/"+item["file"] for item in contract["patch_inputs"])
    data = json.loads((ROOT/"native/license-data.json").read_bytes())
    names.update("licenses/spdx/"+item["id"]+".txt" for item in data["texts"])
    return {name: native.sha(ROOT/name) for name in sorted(names)}


def package_invariants(identity):
    fields = ("target", "recipe_id", "upstream_commit", "compiler", "cli_sha256",
              "helper_sha256", "build_receipt_sha256", "files")
    return {key: identity[key] for key in fields}


def validate_carry(pool_record, native_record, checkpoint, identity, contract,
                   input_hashes, parsed_pool_log, parsed_native_junit):
    """Exact old Rust scope carry; no current-source software success is inferred."""
    need(pool_record.get("status") == "passed_native_guardian_pool_scope"
         and pool_record.get("processes", {}).get("passed") is True,
         "Original pool qualification did not pass")
    need(pool_record.get("tests") == parsed_pool_log
         and Counter(parsed_pool_log["tests"]) == Counter(pool.EXPECTED_TESTS)
         and parsed_pool_log["executed"] == 10 and parsed_pool_log["ignored"] == 0,
         "Original pool executions differ from exact declared tests")
    wanted = package_invariants(identity)
    for record in (pool_record, native_record):
        need(package_invariants(record["package"]) == wanted,
             "Prior evidence refers to different compiled package bytes")
        need(all(record["source_sha256"].get(name) == digest
                 for name, digest in input_hashes.items()),
             "Current native/driver/recipe/license inputs differ from original scope")
    need(native_record.get("status") == "passed_native_transport_scope"
         and native_record.get("processes", {}).get("passed") is True
         and native_record.get("junit") == parsed_native_junit,
         "Original native22 scope did not pass its exact identities")
    expected = expected_full_source(contract)
    expected_digest = sources.inventory_digest(expected)
    producer = pool_record["producer"]
    need(producer.get("full_source_inventory") == expected
         and producer.get("full_source_inventory_sha256") == expected_digest
         and producer.get("full_source_file_count") == len(expected),
         "Full original native source/lock/test inventory differs")
    need(checkpoint.get("status") == "compiled-not-qualified" and checkpoint.get("ready") is False
         and checkpoint.get("source_unchanged_through_compile") is True
         and checkpoint.get("full_source_inventory") == {"files": expected, "sha256": expected_digest}
         and checkpoint.get("native_recipe_id") == identity["recipe_id"],
         "Compile checkpoint does not bind the complete unchanged native source")
    need(all(checkpoint.get("compiler_driver_inputs", {}).get(name) == input_hashes[name]
             for name in ("build_native.py", "native_licenses.py", "verify_native_source.py")),
         "Original compiler-driver bytes differ")
    return {"scope": "exact original native build, pool10 and transport22 only",
            "current_Rust_tests_executed": 0, "carried_Rust_test_names": parsed_pool_log["tests"],
            "full_native_source_count": len(expected), "full_native_source_sha256": expected_digest,
            "original_adapter_failure_preserved": True, "current_offline_qualification_claimed": False}


def main():
    parser = argparse.ArgumentParser()
    for name in ("download", "package", "evidence", "source-archive"):
        parser.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args()
    pins = legacy.unique_json(PINS.read_bytes())
    target = build.build_target()
    need(target in pins["targets"], "No reviewed retained artifact for this platform")
    pin = pins["targets"][target]
    paths = [args.download.resolve(), args.package.resolve(), args.evidence.resolve(),
             args.source_archive.resolve()]
    need(not any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
                 for i, a in enumerate(paths) for b in paths[i+1:]),
         "Acquisition roots must be separate")
    for path in paths:
        need(not path.exists() and not path.is_relative_to(ROOT),
             "Fresh acquisition outputs outside checkout required")
    args.download.mkdir(parents=True); args.evidence.mkdir(parents=True)
    token = os.environ.get("GITHUB_TOKEN", "")
    need(bool(token), "Read-only Actions token required")
    base = "repos/"+pins["repository"]+"/actions/"
    run = legacy.api_get(base+"runs/"+str(pins["run_id"]), token)
    job = legacy.api_get(base+"jobs/"+str(pin["job_id"]), token)
    artifact = legacy.api_get(base+"artifacts/"+str(pin["artifact_id"]), token)
    validate_scope(run, job, artifact, pins, pin)
    location = legacy.api_get(base+"artifacts/"+str(pin["artifact_id"])+"/zip", token, redirect=True)
    zip_path = args.download/"artifact.zip"
    legacy.download(location, zip_path, pin["zip_sha256"], exact_bytes=pin["zip_bytes"])
    archive, recipe = legacy.zip_inputs(zip_path, pin, pins, args.download)
    extract_scope_members(zip_path, pin, args.download/"scope")
    receipt = receipt_from_archive(archive, pin["receipt_sha256"])
    manifest = {"target": target, "archive": {"sha256": pin["archive_sha256"]},
                "files": {**receipt["files"], "build-receipt.json": pin["receipt_sha256"]}}
    archives.extract_exact(archive, args.package, manifest)
    identity = native.package_identity(args.package)
    need(identity["cli_sha256"] == pin["cli_sha256"]
         and identity["helper_sha256"] == pin["helper_sha256"]
         and identity["build_receipt_sha256"] == pin["receipt_sha256"]
         and identity["recipe_id"] == pin["recipe_id"], "Acquired compiled identity differs")
    url, digest = build.build_assets(target)["source"]
    args.source_archive.parent.mkdir(parents=True, exist_ok=True)
    legacy.download(url, args.source_archive, digest)
    contract = sources.source_contract(target, args.source_archive)
    scope = args.download/"scope"
    load = lambda name: legacy.unique_json((scope/name).read_bytes())
    pool_record = load("guardian-pool/qualification.json")
    native_record = load("native-qualification/qualification.json")
    checkpoint = load(pin["checkpoint_member"])
    parsed_pool = pool.test_result((scope/"guardian-pool/native-pool.log").read_text())
    parsed_native = native.junit_result(scope/"native-qualification/native.xml",
                                       native_record.get("collected_case_names"))
    carry = validate_carry(pool_record, native_record, checkpoint, identity, contract,
                           native_input_hashes(contract), parsed_pool, parsed_native)
    # Check all compiled pre-license output bytes captured by the real checkpoint.
    for name, record in checkpoint["output_files"].items():
        need(native.sha(args.package/name) == record["sha256"],
             "Compile checkpoint output changed: "+name)
    record = {"status": "acquired_exact_prior_native_scope_not_current_adapter_qualified",
              "source": "temporary_expiring_Actions_artifact", "ready": False,
              "original_run": run, "original_job": job, "original_artifact": artifact,
              "current_source_sha256": native.source_identity(), "package": identity,
              "pins_sha256": native.sha(PINS), "source_archive_sha256": native.sha(args.source_archive),
              "carry": carry, "original_native_evidence_sha256": native.sha(scope/"native-qualification/qualification.json")}
    (args.evidence/"acquisition.json").write_bytes(json.dumps(record, indent=2).encode())
    # Current native22 and complete offline run are subsequent unchanged controllers.


if __name__ == "__main__":
    main()
