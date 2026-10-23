"""Qualify changed native transport behavior without any provider credential.

This runs the real, freshly built CLI and HTTP helper against owned loopback
fixtures. It does not certify live-model quality or whole-product readiness.
"""
from __future__ import annotations

import argparse
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
EXPECTED_CASES = 15


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_identity(package: Path) -> dict:
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
    if receipt.get("overlay_recipe_sha256") != sha(ROOT / "native/runtime-overlays.json"):
        raise ValueError("Full recipe file differs from the build receipt")
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
    if os.name == "nt":
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
    suffix = ".exe" if os.name == "nt" else ""
    cli = package / ("bin/codex" + suffix)
    helper = package / ("bin/codex-http-transport" + suffix)
    if not cli.is_file() or not helper.is_file():
        raise ValueError("Native CLI or transport helper is missing")
    return {
        "target": target,
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
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
        identity = package_identity(package)
        record["package"] = identity
        record["adapter"] = adapter_identity()
        source_before = source_identity()
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("CODEX_", "CDS_", "OPENAI_", "OPENROUTER_", "OTEL_", "PYTEST_", "ACTIONS_", "GITHUB_"))
            and key not in {"GH_TOKEN", "GITHUB_TOKEN", "PYTHONPATH", "PYTHONHOME"}
        }
        env.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "CODEX_DEEPSEEK_TEST_CLI_BINARY": identity["cli"],
            "CODEX_DEEPSEEK_TEST_HTTP_BINARY": identity["helper"],
            "CDS_RETRY_EVIDENCE_DIR": str(evidence / "retry"),
        })
        command = [
            sys.executable, "-I", "-m", "pytest",
            "-p", "pytest_asyncio.plugin",
            *SUITES, "-q", "-p", "no:cacheprovider",
            "--basetemp", str(work / "pytest"),
            "--junitxml", str(evidence / "native.xml"),
        ]
        collection_command = [
            sys.executable, "-I", "-m", "pytest", "-p", "pytest_asyncio.plugin",
            *SUITES, "-q", "--collect-only", "-p", "no:cacheprovider",
        ]
        collected = subprocess.run(
            collection_command, cwd=ROOT, env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120,
        )
        (evidence / "collected.log").write_text(
            collected.stdout + collected.stderr, encoding="utf-8",
        )
        collected_names = [
            line.split("::", 1)[1].strip() for line in collected.stdout.splitlines()
            if any(line.startswith(suite + "::") for suite in SUITES)
        ]
        if (collected.returncode or len(collected_names) != EXPECTED_CASES
                or len(set(collected_names)) != EXPECTED_CASES):
            raise RuntimeError("Native case collection is incomplete or ambiguous")
        record["collected_case_names"] = collected_names
        record["command"] = command
        save()
        with (evidence / "pytest.log").open("wb") as log:
            completed = subprocess.run(
                command, cwd=ROOT, env=env, stdout=log,
                stderr=subprocess.STDOUT, timeout=1200,
            )
        record["pytest_exit_code"] = completed.returncode
        if completed.returncode != 0:
            raise RuntimeError("Native qualification failed; inspect pytest.log")
        record["junit"] = junit_result(evidence / "native.xml", collected_names)
        record["package_after"] = package_identity(package)
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
            shutil.rmtree(work)
            record["owned_fixture_directory_removed"] = not work.exists()
        except OSError as error:
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
