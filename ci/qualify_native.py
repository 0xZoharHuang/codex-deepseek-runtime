"""Qualify changed native transport behavior without any provider credential.

This runs the real, identity-verified CLI and HTTP helper against owned loopback
fixtures. It does not certify live-model quality or whole-product readiness.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITES = ("tests/test_native_raw_http.py", "tests/test_native_payload_retry.py")
EXPECTED_NODE_IDS = (
    'tests/test_native_raw_http.py::test_native_raw_large_multipart_files_delete_exact_wire_and_warm_reuse',
    'tests/test_native_raw_http.py::test_native_raw_errors_preserve_status_headers_body_without_replay[413]',
    'tests/test_native_raw_http.py::test_native_raw_errors_preserve_status_headers_body_without_replay[429]',
    'tests/test_native_raw_http.py::test_native_raw_errors_preserve_status_headers_body_without_replay[500]',
    'tests/test_native_raw_http.py::test_native_raw_gate_denies_auth_origin_metadata_and_non_files_delete',
    'tests/test_native_raw_http.py::test_native_raw_tls_rejection_and_no_proxy',
    'tests/test_native_raw_http.py::test_native_raw_cancel_model_before_upstream_headers',
    'tests/test_native_raw_http.py::test_native_upload_cancel_before_headers_preserves_unknown_without_replay',
    'tests/test_native_payload_retry.py::test_real_cli_retry_classification[413-None-1]',
    'tests/test_native_payload_retry.py::test_real_cli_retry_classification[413-1-2]',
    'tests/test_native_payload_retry.py::test_real_cli_retry_classification[503-None-2]',
    'tests/test_native_payload_retry.py::test_real_cli_retry_classification[429-1-2]',
    'tests/test_native_payload_retry.py::test_real_cli_compaction_413_uses_same_native_retry_semantics[None]',
    'tests/test_native_payload_retry.py::test_real_cli_compaction_413_uses_same_native_retry_semantics[1]',
    'tests/test_native_payload_retry.py::test_real_cli_local_operator_budget_413_is_not_replayed',
    'tests/test_native_payload_retry.py::test_real_cli_ordinary_capacity_prefix_preserves_unconsumed_tool_pair[summary]',
    'tests/test_native_payload_retry.py::test_real_cli_ordinary_capacity_prefix_preserves_unconsumed_tool_pair[token-budget-local-reset]',
    'tests/test_native_payload_retry.py::test_real_cli_cold_capacity_checkpoint_qualifies_rebuilt_envelopes[summary]',
    'tests/test_native_payload_retry.py::test_real_cli_cold_capacity_checkpoint_qualifies_rebuilt_envelopes[token-budget-local-reset]',
    'tests/test_native_payload_retry.py::test_real_cli_direct_image_metadata_cold_capacity[summary]',
    'tests/test_native_payload_retry.py::test_real_cli_direct_image_metadata_cold_capacity[token-budget-local-reset]',
)
EXPECTED_CASES = len(EXPECTED_NODE_IDS)  # Eight raw HTTP cases and thirteen payload/recovery cases.


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()



def recipe_scope_identity(receipt: dict, recipe: dict, target: str,
                          archived_recipe: Path | None) -> dict:
    """Validate an original build recipe without rewriting its receipt.

    Reuse permits only Windows-specific recipe changes on Linux/macOS.
    Native target/source/file checks below remain mandatory.
    """
    from build_native import native_recipe_identity
    current_sha = sha(ROOT / "native/runtime-overlays.json")
    original_sha = receipt.get("overlay_recipe_sha256")
    if archived_recipe is None:
        if original_sha != current_sha:
            raise ValueError("Full recipe file differs from the build receipt")
        return {"mode": "exact_full_recipe", "original_sha256": original_sha,
                "current_sha256": current_sha}
    if target not in {"x86_64-unknown-linux-gnu", "aarch64-apple-darwin"}:
        raise ValueError("Archived native reuse is limited to the qualified Unix targets")
    if archived_recipe.is_symlink() or sha(archived_recipe) != original_sha:
        raise ValueError("Archived recipe bytes do not match the original receipt")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate archived recipe member")
            result[key] = value
        return result
    original = json.loads(archived_recipe.read_bytes(), object_pairs_hook=unique)
    def without_windows(value):
        value = json.loads(json.dumps(value))
        value["required_base_inputs"].pop("x86_64-pc-windows-msvc")
        overlays = []
        for item in value["overlays"]:
            item["changes_by_target"].pop("x86_64-pc-windows-msvc", None)
            if item["changes_by_target"]:
                overlays.append(item)
        value["overlays"] = overlays
        return value
    if without_windows(original) != without_windows(recipe):
        raise ValueError("Archived recipe differs outside the Windows-only scope")
    if native_recipe_identity(target, original) != native_recipe_identity(target, recipe):
        raise ValueError("Archived and current target recipe identities differ")
    return {"mode": "original_archive_target_equivalence",
            "original_sha256": original_sha, "current_sha256": current_sha,
            "archived_recipe": str(archived_recipe.resolve()),
            "only_windows_recipe_changes": original_sha != current_sha,
            "target_recipe_id": native_recipe_identity(target, recipe)}


def package_identity(package: Path, archived_recipe: Path | None = None) -> dict:
    sys.path.insert(0, str(ROOT))
    from build_native import (COMMIT, VERSION, build_target, build_assets,
                              native_recipe_identity, runtime_recipe, overlay_source_hashes)
    target = build_target()
    metadata = json.loads((package / "codex-package.json").read_text())
    receipt = json.loads((package / "build-receipt.json").read_text())
    recipe = runtime_recipe()
    expected = native_recipe_identity(target, recipe)
    if metadata.get("target") != target or metadata.get("recipeId") != expected:
        raise ValueError("Package target or recipe does not match this host and checkout")
    if receipt.get("native_recipe_id") != expected or receipt.get("upstream_commit") != COMMIT:
        raise ValueError("Build receipt does not match the pinned source recipe")
    if (receipt.get("build_toolchain_pin") != recipe["build_toolchain"]
            or not str(receipt.get("rustc", "")).startswith("rustc " + recipe["build_toolchain"] + " ")):
        raise ValueError("Compiler pin differs from this checkout")
    recipe_validation = recipe_scope_identity(receipt, recipe, target, archived_recipe)
    expected_patches = list(recipe["required_base_inputs"][target]) + [
        {"file": item["file"], "sha256": item["sha256"]}
        for item in recipe["overlays"] if target in item["changes_by_target"]
    ]
    if receipt.get("patch_inputs") != expected_patches or metadata.get("patchInputs") != expected_patches:
        raise ValueError("Native patch stack differs from the fixed recipe")
    expected_archives = {
        name: {"url": url, "sha256": digest}
        for name, (url, digest) in build_assets(target).items()
    }
    if receipt.get("archives") != expected_archives:
        raise ValueError("Native upstream/resource archives differ from the fixed inputs")
    expected_sources = {
        item["path"]: item["fork_sha256"]
        for item in json.loads((ROOT / "native/source-changes.json").read_text())["files"]
    }
    if target.endswith("windows-msvc"):
        expected_sources.update({
            item["path"]: item["fork_sha256"]
            for item in json.loads((ROOT / "native/windows-source-changes.json").read_text())["files"]
        })
    expected_sources.update(overlay_source_hashes(target, recipe))
    if receipt.get("source_files") != expected_sources:
        raise ValueError("Patched source bytes differ from the fixed inputs")
    if metadata.get("upstreamCommit") != COMMIT or metadata.get("version") != VERSION:
        raise ValueError("Native metadata has the wrong upstream baseline")
    if not (re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("patchSha256", "")))
            and metadata.get("patchSha256") == receipt.get("patch_sha256")
            == receipt.get("combined_patch_sha256")):
        raise ValueError("Native source diff identities disagree")
    recorded = receipt.get("files")
    if not isinstance(recorded, dict) or not recorded:
        raise ValueError("Build receipt has no file inventory")
    actual = set()
    for path in package.rglob("*"):
        if path.is_symlink():
            raise ValueError("Native package must not contain symlinks")
        if path.is_file():
            actual.add(path.relative_to(package).as_posix())
    if actual != set(recorded) | {"build-receipt.json"}:
        raise ValueError("Native package file inventory differs from the receipt")
    for name, expected_hash in recorded.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("Invalid path in the native build receipt")
        path = package.joinpath(*relative.parts)
        if not path.is_file() or sha(path) != expected_hash:
            raise ValueError("Native package byte mismatch: " + name)
    if archived_recipe is not None:
        manifest_path = ROOT / "native" / (
            "runtime.json" if target == "aarch64-apple-darwin"
            else "runtime-x86_64-unknown-linux-gnu.json"
        )
        manifest = json.loads(manifest_path.read_bytes())
        files = {name: sha(package / name) for name in actual}
        if (manifest.get("status") != "ready" or manifest.get("target") != target
                or manifest.get("native_recipe_id") != expected
                or manifest.get("upstream_commit") != COMMIT
                or manifest.get("patch_sha256") != metadata["patchSha256"]
                or manifest.get("files") != files):
            raise ValueError("Reused package differs from the complete current target manifest")
        recipe_validation["native_manifest_sha256"] = sha(manifest_path)
    suffix = ".exe" if target.endswith("windows-msvc") else ""
    cli = package / ("bin/codex" + suffix)
    helper = package / ("bin/codex-http-transport" + suffix)
    if not cli.is_file() or not helper.is_file():
        raise ValueError("Native CLI or transport helper is missing")
    return {
        "target": target,
        "recipe_validation": recipe_validation,
        "recipe_id": expected,
        "upstream_commit": COMMIT,
        "compiler": receipt["rustc"],
        "cli": str(cli),
        "cli_sha256": sha(cli),
        "helper": str(helper),
        "helper_sha256": sha(helper),
        "build_receipt_sha256": sha(package / "build-receipt.json"),
        "files": len(recorded),
    }



def validate_collected_nodes(stdout: str) -> list[str]:
    """A same-count deletion/substitution must not qualify a different native suite."""
    node_ids = [line.strip() for line in stdout.splitlines()
                if re.match(r"^[^ ]+[.]py::", line.strip())]
    if Counter(node_ids) != Counter(EXPECTED_NODE_IDS):
        raise ValueError("Native collection differs from the declared node identities")
    return node_ids


def junit_result(path: Path, expected_names: list[str] | None = None) -> dict:
    root = ET.fromstring(path.read_bytes())
    cases = list(root.iter("testcase"))
    rejected = []
    for case in cases:
        for state in ("failure", "error", "skipped"):
            if case.find(state) is not None:
                rejected.append({"case": case.get("name"), "state": state})
    if len(cases) != EXPECTED_CASES or rejected:
        raise ValueError(json.dumps({
            "expected_cases": EXPECTED_CASES,
            "observed_cases": len(cases),
            "rejected": rejected,
        }))
    declared_keys = Counter((suite.removesuffix(".py").replace("/", "."), name)
                            for suite, name in (node.split("::", 1) for node in EXPECTED_NODE_IDS))
    observed_keys = Counter((case.get("classname", ""), case.get("name", "")) for case in cases)
    if observed_keys != declared_keys:
        raise ValueError("Executed native case identities differ from the declared node identities")
    names = [case.get("name", "") for case in cases]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate native test case identities")
    if expected_names is not None and set(names) != set(expected_names):
        raise ValueError("Executed cases differ from the independent collection")
    return {"cases": len(cases), "failed": 0, "skipped": 0, "names": names}



def adapter_identity() -> dict:
    import codex_deepseek
    from importlib.metadata import version
    import tomllib
    installed = Path(codex_deepseek.__file__).resolve().parent
    source = ROOT / "src/codex_deepseek"
    expected = {path.relative_to(source).as_posix(): sha(path)
                for path in source.rglob("*.py")}
    actual = {path.relative_to(installed).as_posix(): sha(path)
              for path in installed.rglob("*.py")}
    if actual != expected:
        raise ValueError("Installed Python adapter differs from this checkout")
    wanted_version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    if version("codex-deepseek-runtime") != wanted_version:
        raise ValueError("Installed Python distribution version differs from this checkout")
    return {
        "directory": str(installed),
        "version": wanted_version,
        "source_sha256": expected,
    }


def source_identity() -> dict:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True, stdout=subprocess.PIPE,
    )
    names = result.stdout.decode().split("\0")
    # Include the controller itself even during its initial uncommitted validation.
    paths = {name for name in names if name}
    paths.add("ci/qualify_native.py")
    return {name: sha(ROOT / name) for name in sorted(paths) if (ROOT / name).is_file()}



def run_observed(command, *, cwd, env, log, timeout):
    """Own only this test process and observed PID/kernel identities."""
    import psutil
    from codex_deepseek.processes import identity, matches, terminate
    seen, unknown = {}, set()
    report = {"timed_out": False, "forced_cleanup": [], "remaining_before_cleanup": [],
              "remaining_after_cleanup": [], "unknown_live": []}
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    started = time.monotonic()
    try:
        birth = identity(process.pid)
        if birth is None:
            unknown.add(process.pid)
            raise RuntimeError("Created test process has no stable identity")
        seen[(process.pid, birth)] = {"pid": process.pid, "birth": birth}
        while process.poll() is None:
            for pid, stamp in list(seen):
                if not matches(pid, stamp):
                    continue
                try:
                    for child in psutil.Process(pid).children(recursive=True):
                        stamp = identity(child.pid)
                        if stamp is None:
                            unknown.add(child.pid)
                        else:
                            seen[(child.pid, stamp)] = {"pid": child.pid, "birth": stamp}
                except psutil.NoSuchProcess:
                    pass
            if time.monotonic() - started > timeout:
                report["timed_out"] = True
                break
            time.sleep(.1)
    except BaseException as error:
        report.update(error_type=type(error).__name__, error=str(error))
    finally:
        if process.poll() is None:
            # This Popen still owns the original process handle; no PID-name kill.
            try:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(type(error).__name__)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(matches(pid, stamp) for pid, stamp in seen):
            time.sleep(.05)
        left = [row for (pid, stamp), row in seen.items() if matches(pid, stamp)]
        report["remaining_before_cleanup"] = left
        for row in left:
            try:
                terminate(row["pid"], row["birth"], timeout=3)
                report["forced_cleanup"].append(row)
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(type(error).__name__)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(matches(pid, stamp) for pid, stamp in seen):
            time.sleep(.05)
        report["remaining_after_cleanup"] = [
            row for (pid, stamp), row in seen.items() if matches(pid, stamp)
        ]
        report["unknown_live"] = sorted(pid for pid in unknown if psutil.pid_exists(pid))
        report["observed"] = list(seen.values())
        report["returncode"] = process.returncode
        report["seconds"] = time.monotonic() - started
    report["passed"] = (report["returncode"] == 0 and not report["timed_out"]
                        and not report.get("error") and not report["remaining_before_cleanup"]
                        and not report["remaining_after_cleanup"] and not report["unknown_live"]
                        and not report.get("cleanup_errors"))
    return report

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--archived-recipe", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    evidence = args.evidence.resolve()
    work = args.work.resolve()
    for path in (evidence, work):
        if path.exists():
            raise ValueError("Preserve existing evidence/work; choose a fresh directory")
        if path == ROOT or path.is_relative_to(ROOT / "src"):
            raise ValueError("Qualification outputs must not replace project sources")
        if path == package or path.is_relative_to(package) or package.is_relative_to(path):
            raise ValueError("Qualification outputs must be separate from the native package")
    if work.is_relative_to(evidence) or evidence.is_relative_to(work):
        raise ValueError("Private test fixtures must not be included in uploaded evidence")
    evidence.mkdir(parents=True)
    work.mkdir(parents=True)
    record = {
        "started_at": time.time(),
        "platform": platform.platform(),
        "python": sys.version,
        "runner_image": {key: os.environ.get(key) for key in ("ImageOS", "ImageVersion")},
        "scope": "real native CLI/helper with synthetic loopback HTTP/TLS fixtures",
        "real_model_requests": 0,
        "provider_credentials_read": False,
        "product_release_ready": False,
        "status": "running",
    }
    result_path = evidence / "qualification.json"
    def save():
        result_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    save()
    try:
        identity = package_identity(package, args.archived_recipe)
        record["package"] = identity
        record["adapter"] = adapter_identity()
        source_before = source_identity()
        record["source_sha256"] = source_before
        record["native_builds"] = 0 if args.archived_recipe is not None else None
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("CODEX_", "CDS_", "OPENAI_", "OPENROUTER_", "OTEL_", "PYTEST_", "ACTIONS_", "GITHUB_"))
            and key not in {"GH_TOKEN", "GITHUB_TOKEN", "PYTHONPATH", "PYTHONHOME"}
        }
        for name in ("home", "tmp"):
            (work / name).mkdir(mode=0o700)
        env.update({
            "HOME": str(work / "home"),
            "TMPDIR": str(work / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "CODEX_DEEPSEEK_TEST_CLI_BINARY": identity["cli"],
            "CODEX_DEEPSEEK_TEST_HTTP_BINARY": identity["helper"],
            "CDS_RETRY_EVIDENCE_DIR": str(evidence / "retry"),
        })
        command = [
            sys.executable, "-I", "-B", "-m", "pytest",
            "-p", "pytest_asyncio.plugin", "-W", "error",
            "-o", "asyncio_default_fixture_loop_scope=function",
            *SUITES, "-q", "-p", "no:cacheprovider",
            "--basetemp", str(work / "pytest"),
            "--junitxml", str(evidence / "native.xml"),
        ]
        collection_command = [
            sys.executable, "-I", "-B", "-m", "pytest", "-p", "pytest_asyncio.plugin",
            "-W", "error", "-o", "asyncio_default_fixture_loop_scope=function",
            *SUITES, "-q", "--collect-only", "-p", "no:cacheprovider",
        ]
        collected = subprocess.run(
            collection_command, cwd=ROOT, env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120,
        )
        (evidence / "collected.log").write_text(
            collected.stdout + collected.stderr, encoding="utf-8",
        )
        if collected.returncode:
            raise RuntimeError("Native case collection failed")
        collected_ids = validate_collected_nodes(collected.stdout)
        collected_names = [node.split("::", 1)[1] for node in collected_ids]
        record["collected_node_ids"] = collected_ids
        record["collected_case_names"] = collected_names
        record["command"] = command
        save()
        with (evidence / "pytest.log").open("wb") as log:
            process_proof = run_observed(command, cwd=ROOT, env=env, log=log, timeout=1200)
        record["processes"] = process_proof
        record["pytest_exit_code"] = process_proof["returncode"]
        if not process_proof["passed"]:
            raise RuntimeError("Native qualification or owned process cleanup failed; inspect pytest.log")
        record["junit"] = junit_result(evidence / "native.xml", collected_names)
        record["package_after"] = package_identity(package, args.archived_recipe)
        if record["package_after"] != identity:
            raise RuntimeError("Native package changed during qualification")
        if adapter_identity() != record["adapter"]:
            raise RuntimeError("Installed Python adapter changed during qualification")
        if source_identity() != source_before:
            raise RuntimeError("Tracked source bytes changed during qualification")
        record["source_unchanged"] = True
        record["status"] = "passed_native_transport_scope"
    except BaseException as error:
        record["status"] = "failed"
        record["error_type"] = type(error).__name__
        record["error"] = str(error)
        raise
    finally:
        record["finished_at"] = time.time()
        # This directory is created by this invocation and contains only synthetic
        # fixture material. Never remove package, source, user homes, or evidence.
        try:
            processes = record.get("processes", {})
            if processes.get("remaining_after_cleanup") or processes.get("unknown_live") or processes.get("error"):
                raise RuntimeError("Preserve fixture paths while an owned process is unresolved")
            shutil.rmtree(work)
            record["owned_fixture_directory_removed"] = not work.exists()
        except Exception as error:
            record["owned_fixture_directory_removed"] = False
            record["cleanup_error"] = type(error).__name__
            record["status"] = "failed"
        save()
    if not record.get("owned_fixture_directory_removed"):
        raise RuntimeError("Owned fixture cleanup did not complete")
    print(json.dumps({"status": record["status"], "evidence": str(result_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
