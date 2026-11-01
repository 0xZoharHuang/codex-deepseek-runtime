"""Acquire one exact native build scope without Rust or relabeling old failures."""
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
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ci import reuse_qualified_native as legacy
from ci import reuse_native as archives
from ci import qualify_native as native
from ci import qualify_guardian_pool as pool
import build_native as build
import verify_native_source as sources

PINS = ROOT/"ci/qualified-native-build-0.6.9.json"
LEGACY_SCOPE = "retained-native-build-and-transport-only"
QUALIFIED_SCOPE = "qualified-native-build-state-and-transport-only"


def need(ok, message):
    if not ok:
        raise ValueError(message)


def validate_original_scope(run, job, artifact, pins, pin):
    scope = pins.get("scope")
    need(pins.get("schema") == 1 and scope in {LEGACY_SCOPE, QUALIFIED_SCOPE},
         "Not a reviewed retained native-only scope")
    need(run.get("id") == pins["run_id"] and run.get("run_attempt") == pins["run_attempt"]
         and run.get("head_sha") == pins["producer_commit"]
         and run.get("path") == pins["workflow_path"]
         and run.get("repository", {}).get("id") == pins["repository_id"]
         and run.get("repository", {}).get("full_name") == pins["repository"],
         "Original run/source/repository differs")
    # Historical failed scope is never upgraded to success. New P27 scope requires
    # the exact completed successful producer, including all concrete steps below.
    if scope == QUALIFIED_SCOPE:
        required = {name: "success" for name in (
            "Build the pinned native package",
            "Qualify actual State migration EOL compatibility",
            "Qualify actual Guardian provider transport and cancellation",
            "Run real native payload and recovery fixtures",
            "Check adapter and platform regressions",
            "Retain package provenance and original failures",
            "Upload bounded qualification artifacts")}
        need(pin.get("job_conclusion") == "success" and pin.get("required_steps") == required,
             "P27 requires the complete exact successful original step scope")
        checkpoint = pin.get("checkpoint_member", "")
        need(re.fullmatch(r"compile-checkpoints/b-[0-9a-f]{8}/compile-checkpoint\.json", checkpoint)
             and set(pin.get("members", {})) == {
                 "state-migrations/qualification.json", "state-migrations/native-state.log",
                 "guardian-pool/qualification.json", "guardian-pool/native-pool.log",
                 "native-qualification/qualification.json", "native-qualification/native.xml",
                 checkpoint, "retained-build-scope.json"} | (
                    {"runtime-overlays.json", "source-commit.txt", "producer/run.json",
                     "producer/jobs.json", "producer/artifacts.json"}
                    if pins.get("source") == "release-asset" else set()),
             "P27 requires all exact original State/pool/native/checkpoint records")
        need(native.sha(ROOT/"native/runtime-overlays.json") == pins.get("recipe_sha256"),
             "Current full P27 recipe differs from its compiled pin")
    expected = ({("completed", "success")} if scope == QUALIFIED_SCOPE else
                {("in_progress", None), ("completed", "failure")})
    need((run.get("status"), run.get("conclusion")) in expected,
         "Original declared-scope run has an unexpected status")
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
    need(artifact["expires_at"] == pin["expires_at"], "Original artifact expiry fact changed")


def validate_scope(run, job, artifact, pins, pin, *, now=None):
    """Current Actions delivery must still be live; no expiry bypass option."""
    validate_original_scope(run, job, artifact, pins, pin)
    expires = datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00"))
    need(artifact["expires_at"] == pin["expires_at"]
         and expires > (now or datetime.now(timezone.utc)),
         "Retained artifact has expired or its expiry changed")


def download_artifact(location, destination, digest, size):
    """Accept only the official Actions storage location, without API credentials."""
    need(type(size) is int and 0 < size <= legacy.MAX_SOURCE_BYTES,
         "Artifact size must be an exact bounded byte count")
    value = urllib.parse.urlsplit(location)
    need(value.scheme == "https" and value.hostname is not None
         and re.fullmatch(r"productionresultssa[0-9]+\.blob\.core\.windows\.net", value.hostname)
         and value.port in (None, 443) and not value.username and not value.password
         and value.path.startswith("/actions-results/") and not value.fragment,
         "Unreviewed Actions artifact storage location")
    # The API gives a final signed storage URL. Do not follow a second redirect,
    # forward its token, or record the signed query in evidence/errors.
    opener = urllib.request.build_opener(legacy.NoRedirect)
    try:
        return legacy.download(location, destination, digest, exact_bytes=size, opener=opener)
    except urllib.error.HTTPError as error:
        if error.code in {301, 302, 303, 307, 308}:
            raise ValueError("Unexpected Actions storage redirect") from None
        raise


def extract_scope_members(zip_path, pin, directory, *, exact=False):
    """Only reviewed small records, in addition to legacy exact package/recipe extraction."""
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        need(len(names) == len(set(names)), "Duplicate artifact member")
        if exact:
            need(set(names) == set(pin["members"]), "Unexpected permanent scope bundle member")
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
                   input_hashes, parsed_pool_log, parsed_native_junit, *,
                   state_record=None, parsed_state_log=None):
    """Exact old Rust scope carry; no current-source software success is inferred."""
    need(pool_record.get("status") == "passed_native_guardian_pool_scope"
         and pool_record.get("processes", {}).get("passed") is True,
         "Original pool qualification did not pass")
    need(pool_record.get("tests") == parsed_pool_log
         and Counter(parsed_pool_log["tests"]) == Counter(pool.EXPECTED_TESTS)
         and parsed_pool_log["executed"] == 10 and parsed_pool_log["ignored"] == 0,
         "Original pool executions differ from exact declared tests")
    records = [pool_record, native_record]
    rust_records = [pool_record]
    if state_record is not None or parsed_state_log is not None:
        need(isinstance(state_record, dict) and isinstance(parsed_state_log, dict),
             "Original State qualification/log pair is required")
        need(state_record.get("status") == "passed_native_state_migrations_scope"
             and state_record.get("suite") == "state-migrations"
             and state_record.get("processes", {}).get("passed") is True
             and state_record.get("processes", {}).get("returncode") == 0,
             "Original State qualification did not pass")
        need(state_record.get("tests") == parsed_state_log
             and Counter(parsed_state_log["tests"]) == Counter(pool.STATE_TESTS)
             and parsed_state_log["executed"] == 22 and parsed_state_log["failed"] == 0
             and parsed_state_log["ignored"] == 0,
             "Original State executions differ from exact declared tests")
        records.append(state_record)
        rust_records.append(state_record)
    wanted = package_invariants(identity)
    for record in records:
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
    for record in rust_records:
        producer = record["producer"]
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
    result = {"scope": "exact original native build, " + ("state22, " if state_record is not None else "") + "pool10 and transport22 only",
            "current_Rust_tests_executed": 0,
            "carried_State_tests": parsed_state_log if state_record is not None else None,
            "carried_Rust_test_names": parsed_pool_log["tests"] + (parsed_state_log["tests"] if state_record is not None else []),
            "full_native_source_count": len(expected), "full_native_source_sha256": expected_digest,
            "original_workflow_status_preserved": True, "current_offline_qualification_claimed": False,
            "coverage_limit": "The named pool10 carry does not independently prove cancellation with an explicitly false provider config."}
    if state_record is None:
        result["original_adapter_failure_preserved"] = True
    return result


def release_download(url, destination, digest, size, release_base):
    """Public versioned asset only; the API credential never reaches storage."""
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            value = urllib.parse.urlsplit(newurl)
            need(value.scheme == "https" and value.hostname == "release-assets.githubusercontent.com"
                 and value.port in (None, 443) and not value.username and not value.password
                 and value.path.startswith("/github-production-release-asset/") and not value.fragment,
                 "Unreviewed permanent asset storage redirect")
            return super().redirect_request(request, fp, code, msg, headers, newurl)
    prefix = "https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/"
    need(release_base in {prefix+"v0.6.2", prefix+"v0.6.9"}
         and url.startswith(release_base + "/")
         and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}", url[len(release_base)+1:]),
         "Unreviewed versioned asset URL")
    return legacy.download(url, destination, digest, exact_bytes=size,
                           opener=urllib.request.build_opener(Redirect))


def acquire_permanent(pins, pin, target, directory, token):
    """Validate current release delivery separately from historical Actions identity."""
    need(pins.get("scope") == QUALIFIED_SCOPE and pins.get("source") == "release-asset",
         "Permanent P27 scope must be explicitly selected")
    _, manifest = legacy.selected_pin(pins, target)
    inputs = legacy.release_inputs(pins, pin, manifest)
    version, tag, release_base, _ = legacy.release_spec(pins)
    need(version == "0.6.9", "Permanent State/pool carry requires the reviewed P27 release")
    release = pins["release"]
    need(type(release.get("id")) is int and release["id"] > 0
         and re.fullmatch(r"[0-9a-f]{40}", release.get("commit", "")), "Real release identity required")
    base = "repos/" + pins["repository"] + "/"
    metadata = legacy.api_get(base + "releases/" + str(release["id"]), token)
    need(metadata.get("id") == release["id"] and metadata.get("tag_name") == tag
         and metadata.get("draft") is False and bool(metadata.get("published_at"))
         and metadata.get("html_url") == release_base.replace("/download/", "/tag/"),
         "Permanent release is not the exact published tag")
    ref = legacy.api_get(base + "git/ref/tags/" + tag, token)
    need(ref.get("ref") == "refs/tags/" + tag, "Release tag ref differs")
    obj = ref["object"]
    if obj.get("type") == "tag":
        annotated = legacy.api_get(base + "git/tags/" + obj["sha"], token)
        need(annotated.get("tag") == tag, "Annotated release tag differs")
        obj = annotated["object"]
    need(obj.get("type") == "commit" and obj.get("sha") == release["commit"],
         "Resolved release commit differs from reviewed publication")
    descriptors = [(pin["release_archive"], inputs["archive"], directory/"native-package.tar.gz"),
                   (release["recipe_asset"], inputs["recipe"], directory/"runtime-overlays.json"),
                   (pin["release_scope"], None, directory/"qualification-scope.zip")]
    delivered = []
    for descriptor, expected, path in descriptors:
        name = descriptor.get("name", "")
        need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}", name)
             and type(descriptor.get("id")) is int and descriptor["id"] > 0
             and type(descriptor.get("bytes")) is int and 0 < descriptor["bytes"] <= legacy.MAX_SOURCE_BYTES
             and re.fullmatch(r"[0-9a-f]{64}", descriptor.get("sha256", "")), "Real exact asset descriptor required")
        url = release_base + "/" + name
        if expected is not None:
            need(expected == {"url":url,"sha256":descriptor["sha256"],"bytes":descriptor["bytes"]},
                 "Permanent asset and compiled input disagree")
        asset = legacy.api_get(base + "releases/assets/" + str(descriptor["id"]), token)
        need(asset.get("id") == descriptor["id"] and asset.get("name") == name
             and asset.get("state") == "uploaded" and asset.get("size") == descriptor["bytes"]
             and asset.get("browser_download_url") == url
             and asset.get("url") == legacy.API + base + "releases/assets/" + str(descriptor["id"])
             and asset.get("digest") in (None, "sha256:" + descriptor["sha256"]), "Permanent asset metadata differs")
        release_download(url, path, descriptor["sha256"], descriptor["bytes"], release_base)
        delivered.append(asset)
    scope = directory/"scope"
    extract_scope_members(directory/"qualification-scope.zip", pin, scope, exact=True)
    need(native.sha(scope/"runtime-overlays.json") == pins["recipe_sha256"]
         and (scope/"source-commit.txt").read_bytes() == (pins["producer_commit"]+"\n").encode(),
         "Permanent scope recipe/producer bytes differ")
    load = lambda name: legacy.unique_json((scope/name).read_bytes())
    run = load("producer/run.json")
    job = next(j for j in load("producer/jobs.json")["jobs"] if j["id"] == pin["job_id"])
    artifact = next(a for a in load("producer/artifacts.json")["artifacts"] if a["id"] == pin["artifact_id"])
    validate_original_scope(run, job, artifact, pins, pin)
    return directory/"native-package.tar.gz", directory/"runtime-overlays.json", run, job, artifact, {
        "source":"permanent_release_assets", "release_metadata":metadata, "resolved_release_commit":obj["sha"],
        "assets":delivered, "historical_Actions_metadata_only":True,
        "historical_artifact_current_availability_checked":False}


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
    need(bool(token), "Read-only GitHub metadata token required")
    mode = pins.get("source", "actions-artifact")
    provenance = {"source":"temporary_expiring_Actions_artifact"}
    if mode == "release-asset":
        archive, recipe, run, job, artifact, provenance = acquire_permanent(
            pins, pin, target, args.download, token)
    elif mode == "actions-artifact":
        base = "repos/"+pins["repository"]+"/actions/"
        run = legacy.api_get(base+"runs/"+str(pins["run_id"]), token)
        job = legacy.api_get(base+"jobs/"+str(pin["job_id"]), token)
        artifact = legacy.api_get(base+"artifacts/"+str(pin["artifact_id"]), token)
        validate_scope(run, job, artifact, pins, pin)
        location = legacy.api_get(base+"artifacts/"+str(pin["artifact_id"])+"/zip", token, redirect=True)
        zip_path = args.download/"artifact.zip"
        download_artifact(location, zip_path, pin["zip_sha256"], pin["zip_bytes"])
        archive, recipe = legacy.zip_inputs(zip_path, pin, pins, args.download)
        extract_scope_members(zip_path, pin, args.download/"scope")
    else:
        raise ValueError("Unknown explicit native acquisition source; no fallback")
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
    state_args = {}
    if pins["scope"] == QUALIFIED_SCOPE:
        # Missing records/logs are errors; never silently downgrade P27 to old pool-only carry.
        state_args = {"state_record": load("state-migrations/qualification.json"),
                      "parsed_state_log": pool.test_result(
                          (scope/"state-migrations/native-state.log").read_text(), "state-migrations")}
    carry = validate_carry(pool_record, native_record, checkpoint, identity, contract,
                           native_input_hashes(contract), parsed_pool, parsed_native, **state_args)
    # Check all compiled pre-license output bytes captured by the real checkpoint.
    for name, record in checkpoint["output_files"].items():
        need(native.sha(args.package/name) == record["sha256"],
             "Compile checkpoint output changed: "+name)
    record = {"status": "acquired_exact_prior_native_scope_not_current_adapter_qualified",
              **provenance, "ready": False,
              "original_run": run, "original_job": job, "original_artifact": artifact,
              "current_source_sha256": native.source_identity(), "package": identity,
              "pins_sha256": native.sha(PINS), "source_archive_sha256": native.sha(args.source_archive),
              "carry": carry, "original_native_evidence_sha256": native.sha(scope/"native-qualification/qualification.json")}
    (args.evidence/"acquisition.json").write_bytes(json.dumps(record, indent=2).encode())
    # Current native22 and complete offline run are subsequent unchanged controllers.


if __name__ == "__main__":
    main()
