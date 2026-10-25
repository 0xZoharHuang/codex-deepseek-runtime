"""Qualify the complete current offline collection, including the separately qualified declared native partition."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ci import qualify_native as qualification


def collection(path):
    data = json.loads(path.read_bytes())
    if data.get("deselected") != 0 or not data.get("items"):
        raise ValueError("Offline collection is empty or deselected cases")
    ids = [x["nodeid"] for x in data["items"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate collected test identities")
    return data["items"]


def validate_partition(all_items, native, cases, own_items, allowed):
    native_items = [x for x in all_items if any(
        x["nodeid"].startswith(suite + "::") for suite in qualification.SUITES)]
    if (len(native_items) != qualification.EXPECTED_CASES
            or {x["name"] for x in native_items} != set(native["names"])):
        raise ValueError("Native cases differ from the complete collection partition")
    expected = [x for x in all_items if x not in native_items]
    wanted = Counter((x["classname"], x["name"]) for x in expected)
    executed = Counter((x.get("classname"), x.get("name")) for x in cases)
    if (executed != wanted
            or Counter((x["classname"], x["name"]) for x in own_items) != wanted):
        raise ValueError("Offline JUnit differs from independently collected partition")
    if any(x.find("failure") is not None or x.find("error") is not None for x in cases):
        raise ValueError("Offline JUnit reports failures")
    skips = [{"classname": x.get("classname"), "name": x.get("name"),
              "message": x.find("skipped").get("message")}
             for x in cases if x.find("skipped") is not None]
    key = lambda rows: Counter(tuple(x[k] for k in ("classname", "name", "message")) for x in rows)
    if key(skips) != key(allowed):
        raise ValueError("Platform skips differ from individually declared expected cases")
    return {"complete_collection": len(all_items), "native_cases": len(native_items),
            "adapter_cases": len(cases), "skipped": skips, "deselected": 0,
            "every_collected_case_executed_once": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--archived-recipe", type=Path,
                        help="Exact original recipe for validated reuse; omit only for a fresh current-recipe package")
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--native-evidence", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform not in {"linux", "darwin"}:
        raise ValueError("This complete offline controller is for the two qualified Unix targets")
    args.package = args.package.resolve(strict=True)
    args.native_evidence = args.native_evidence.resolve(strict=True)
    args.evidence = args.evidence.resolve()
    args.work = args.work.resolve()
    paths = [args.package, args.native_evidence, args.evidence, args.work]
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for i, a in enumerate(paths) for b in paths[i+1:]):
        raise ValueError("Package, previous evidence, new evidence and fixtures must be separate")
    for path in (args.evidence, args.work):
        if path.exists() or path == ROOT or path.is_relative_to(ROOT):
            raise ValueError("Fresh output roots outside the checkout are required")
    args.evidence.mkdir(parents=True)
    args.work.mkdir(parents=True)
    record = {"status": "running", "started_at": time.time(),
              "scope": "Complete current offline collection; declared native partition separately executed",
              "real_model_requests": 0, "provider_credentials_read": False,
              "native_builds": 0, "product_release_ready": False}
    try:
        for name in ("home", "tmp"):
            (args.work / name).mkdir(mode=0o700)
        identity = qualification.package_identity(args.package, args.archived_recipe)
        source_before = qualification.source_identity()
        adapter_before = qualification.adapter_identity()
        record.update(package=identity, adapter=adapter_before, source_sha256=source_before)
        from build_native import build_assets
        if qualification.sha(args.source_archive) != build_assets(identity["target"])["source"][1]:
            raise ValueError("Pinned offline source archive differs")
        native_path = args.native_evidence / "qualification.json"
        native_hash = qualification.sha(native_path)
        native = json.loads(native_path.read_bytes())
        actual_junit = qualification.junit_result(
            args.native_evidence / "native.xml", native.get("collected_case_names"))
        if (native.get("status") != "passed_native_transport_scope"
                or native.get("package") != identity
                or native.get("adapter") != adapter_before
                or native.get("source_sha256") != source_before
                or native.get("junit") != actual_junit):
            raise ValueError("Native execution is not bound to the same current source/package")
        record.update(native_evidence_sha256=native_hash, native_junit=actual_junit)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CODEX_", "CDS_", "OPENAI_", "OPENROUTER_", "ANTHROPIC_",
                                    "OTEL_", "PYTEST_", "ACTIONS_", "GITHUB_", "PYTHON"))
               and k not in {"GH_TOKEN", "GITHUB_TOKEN", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}}
        env.update(HOME=str(args.work/"home"), TMPDIR=str(args.work/"tmp"),
                   PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                   CODEX_DEEPSEEK_TEST_HTTP_BINARY=identity["helper"],
                   CODEX_DEEPSEEK_TEST_CLI_BINARY=identity["cli"],
                   CODEX_DEEPSEEK_TEST_CATALOG=str(ROOT/"catalog.json"),
                   CODEX_DEEPSEEK_TEST_SOURCE_ARCHIVE=str(args.source_archive),
                   CODEX_DEEPSEEK_TEST_SANDBOX_MODE="workspace-write",
                   CODEX_DEEPSEEK_TEST_EVIDENCE_ROOT=str(args.evidence/"tests"))
        bootstrap = ("import pathlib,sys;sys.path.insert(0,str(pathlib.Path.cwd()));"
                     "import pytest;raise SystemExit(pytest.main(sys.argv[1:]))")
        base = [sys.executable, "-I", "-B", "-c", bootstrap, "-p", "run_acceptance",
                "-p", "pytest_asyncio.plugin", "-p", "no:cacheprovider", "-q",
                "-W", "error", "-o", "asyncio_default_fixture_loop_scope=function"]
        env["CODEX_DEEPSEEK_TEST_COLLECTION_PATH"] = str(args.evidence/"all-collection.json")
        with (args.evidence/"collection.log").open("wb") as log:
            collected = qualification.run_observed(
                [*base, "tests", "--collect-only"], cwd=ROOT, env=env, log=log, timeout=180)
        record["collection_processes"] = collected
        if not collected["passed"]:
            raise RuntimeError("Complete current offline collection failed")
        all_items = collection(args.evidence/"all-collection.json")
        env["CODEX_DEEPSEEK_TEST_COLLECTION_PATH"] = str(args.evidence/"adapter-collection.json")
        command = [*base, "tests", *["--ignore="+suite for suite in qualification.SUITES],
                   "--basetemp", str(args.work/"pytest"),
                   "--junitxml", str(args.evidence/"adapter.xml")]
        record["command"] = command
        with (args.evidence/"pytest.log").open("wb") as log:
            process = qualification.run_observed(command, cwd=ROOT, env=env, log=log, timeout=1200)
        record["processes"] = process
        if not process["passed"]:
            raise RuntimeError("Offline tests or owned-process cleanup failed; raw output retained")
        skips = json.loads((ROOT/"ci/offline-platform-skips.json").read_bytes())
        record.update(validate_partition(
            all_items, actual_junit, list(ET.parse(args.evidence/"adapter.xml").iter("testcase")),
            collection(args.evidence/"adapter-collection.json"), skips["common"] + skips[sys.platform]))
        if qualification.package_identity(args.package, args.archived_recipe) != identity:
            raise ValueError("Native package changed")
        if qualification.adapter_identity() != adapter_before or qualification.source_identity() != source_before:
            raise ValueError("Python adapter or checkout changed")
        if qualification.sha(native_path) != native_hash:
            raise ValueError("Native qualification evidence changed")
        if qualification.sha(args.source_archive) != build_assets(identity["target"])["source"][1]:
            raise ValueError("Pinned source archive changed")
        record["status"] = "passed_complete_offline_scope"
    except BaseException as error:
        record.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        try:
            proofs = [record.get(name, {}) for name in ("processes", "collection_processes")]
            if any(p.get("remaining_after_cleanup") or p.get("unknown_live") or p.get("error") for p in proofs):
                raise RuntimeError("Preserve fixture paths while a test process is unresolved")
            shutil.rmtree(args.work)
            record["owned_work_removed"] = not args.work.exists()
        except Exception as error:
            record.update(status="failed", cleanup_error=type(error).__name__, owned_work_removed=False)
        record["finished_at"] = time.time()
        (args.evidence/"qualification.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    if not record.get("owned_work_removed"):
        raise RuntimeError("Owned fixture cleanup did not complete")


if __name__ == "__main__":
    main()
