"""Qualify Guardian or State tests on the fresh producer's actual source/cache.

No model, credential, installed runtime or native package is modified.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ci import qualify_native as native
import verify_native_source as sources

PREFIX = "async_scorer::sampler::connection_pool::"
EXPECTED_TESTS = (
    PREFIX + "cancellation_tests::cancellation_preserves_unused_sockets_and_backpressured_completion",
    PREFIX + "cancellation_tests::cancelled_classifiers_interrupt_and_reuse_only_completed_streams",
    PREFIX + "tests::cold_pool_uses_http_during_open_timeout_then_recovers_after_cooldown",
    PREFIX + "tests::cooldown_preserves_healthy_sockets_and_both_transports_share_capacity",
    PREFIX + "tests::http_cache_preserves_no_constraint_redirect_policy_and_configured_headers",
    PREFIX + "tests::provider_without_websockets_scores_over_http_without_opening_pool",
    PREFIX + "tests::replenishes_before_last_idle_socket_is_leased",
    PREFIX + "tests::stalled_handshake_does_not_delay_other_warm_sockets",
    PREFIX + "tests::stalled_http_headers_exhaust_the_sampling_retry_budget",
    PREFIX + "tests::supersession_closes_http_before_headers_and_while_draining_the_body",
)
STATE_TESTS = (
    "migrations::tests::guardian_metadata_cleanup_preserves_custom_names_and_titles",
    "migrations::tests::thread_section_migration_preserves_legacy_pin_compatibility",
    "migrations::tests::thread_attachment_migration_preserves_existing_data",
    "migrations::tests::thread_section_order_migration_backfills_stably",
    "migrations::tests::thread_item_update_ordinals_allow_older_writers",
    "migrations::tests::realtime_items_preserve_older_thread_history_writers",
    "migrations::tests::agent_job_tables_are_dropped_when_upgrading",
    "migrations::tests::recency_migration_backfills_and_seeds_old_binary_inserts",
    "migrations::tests::repairs_recency_migration_that_was_applied_as_version_38",
    "migrations::tests::repair_recency_migration_succeeds_while_another_connection_holds_writer_slot",
    "migrations::tests::writable_pool_reads_do_not_wait_for_an_existing_writer",
    "migrations::tests::eol_fresh_platform_six_stores_match_canonical_representation",
    "migrations::tests::eol_existing_lf_six_stores_keep_ledger_userdata_and_old_reader",
    "migrations::tests::eol_old_lf_prefix_pending_stays_lf_for_old_reader",
    "migrations::tests::eol_unknown_hash_dirty_and_changed_sql_are_strict",
    "migrations::tests::eol_legacy_38_to_39_repair_accepts_both_precise_forms",
    "migrations::tests::eol_sql_and_checksum_choose_same_literal_representation_and_rollback",
    "migrations::tests::eol_missing_lazy_stores_inherit_lf_primary_for_old_p6_rollback",
    "migrations::tests::eol_existing_auxiliary_ledger_does_not_depend_on_primary_health",
    "migrations::tests::eol_crlf_prefix_pending_stays_crlf_on_every_platform",
    "migrations::tests::eol_primary_proven_style_inherited_by_missing_and_empty_aux_stores",
    "migrations::tests::eol_empty_or_mixed_ledgers_prove_no_single_style_without_rewriting",
)


def suite_spec(suite: str) -> dict:
    if suite == "guardian-pool":
        return {"tests": EXPECTED_TESTS, "crate": "codex-guardian-v2",
                "filter": PREFIX.removesuffix("::"), "log": "native-pool.log",
                "scope": "Guardian false/true provider pool, HTTP scoring and cancellation",
                "passed": "passed_native_guardian_pool_scope"}
    if suite == "state-migrations":
        return {"tests": STATE_TESTS, "crate": "codex-state",
                "filter": "migrations::tests::", "log": "native-state.log",
                "scope": "State migration exact EOL forms, six stores, strict ledgers and old-reader compatibility",
                "passed": "passed_native_state_migrations_scope"}
    raise ValueError("Unknown native test suite")


PROFILE_KEYS = (
    "CARGO_INCREMENTAL", "CARGO_BUILD_JOBS", "CARGO_PROFILE_RELEASE_DEBUG",
    "CARGO_PROFILE_RELEASE_STRIP", "CARGO_PROFILE_RELEASE_LTO",
    "CARGO_PROFILE_RELEASE_CODEGEN_UNITS",
)


def test_result(log: str, suite: str = "guardian-pool") -> dict:
    expected = suite_spec(suite)["tests"]
    # Upstream network skip macros return Ok, so Rust's passed count is insufficient.
    if re.search(r"skipping test|cannot execute when network is disabled", log, re.I):
        raise ValueError("A native fixture returned through an upstream skip macro")
    cases = re.findall(r"^test (\S+) \.\.\. (ok|FAILED|ignored)\s*$", log, re.M)
    if Counter(name for name, _ in cases) != Counter(expected):
        raise ValueError("Native suite execution differs from the exact declared tests")
    if any(state != "ok" for _, state in cases):
        raise ValueError("Native suite contains a failed or ignored fixture")
    summaries = re.findall(
        r"^test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; "
        r"(\d+) measured; (\d+) filtered out; finished in [^\n]+$", log, re.M)
    if len(summaries) != 1 or summaries[0][:5] != ("ok", str(len(expected)), "0", "0", "0"):
        raise ValueError("Native suite summary is missing or unsuccessful")
    return {"executed": len(cases), "failed": 0, "ignored": 0,
            "filtered_out_not_executed": int(summaries[0][5]),
            "tests": [name for name, _ in cases]}


def producer_source(work: Path, receipt: dict, contract: dict) -> tuple[Path, dict]:
    trees = [p / "src" for p in work.iterdir()
             if re.fullmatch(r"b-[0-9a-f]{8}", p.name) and p.is_dir()]
    if len(trees) != 1 or trees[0].is_symlink() or not trees[0].is_dir():
        raise ValueError("Expected one freshly admitted producer source tree")
    tree = trees[0]
    patch = tree.parent / "combined.patch"
    if patch.is_symlink() or native.sha(patch) != receipt["combined_patch_sha256"]:
        raise ValueError("Producer source diff does not match its package receipt")
    asserted_sources = receipt["source_files"]
    if not isinstance(asserted_sources, dict) or not asserted_sources:
        raise ValueError("Producer has no asserted source files")
    for name, digest in asserted_sources.items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError("Unsafe producer source assertion")
        source = tree / name
        if source.is_symlink() or native.sha(source) != digest:
            raise ValueError("Producer source file differs: " + name)
    inventory, auxiliary = sources.checked_source_inventory(tree, contract)
    return tree, {"tree": str(tree.resolve()), "combined_patch_sha256": native.sha(patch),
                  "source_files": {name: native.sha(tree / name) for name in sorted(asserted_sources)},
                  "full_source_inventory": inventory,
                  "full_source_file_count": len(inventory),
                  "full_source_inventory_sha256": sources.inventory_digest(inventory),
                  "auxiliary_sources": auxiliary, "upstream_links": contract["links"]}



def bundled_bwrap_environment(package: Path, work: Path, receipt: dict, target: str) -> dict:
    if target != "x86_64-unknown-linux-gnu":
        if "bwrap" in receipt:
            raise ValueError("Unexpected bundled Linux sandbox on another target")
        return {}
    item = receipt.get("bwrap", {})
    digest = item.get("embedded_sha256", "")
    if item.get("resource") != "codex-resources/bwrap" or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Linux producer lacks an exact bundled sandbox identity")
    for path in (package / item["resource"], work / "target/release/bwrap"):
        if path.is_symlink() or native.sha(path) != digest:
            raise ValueError("Linux bundled sandbox bytes differ from the producer")
    return {"CODEX_BWRAP_SHA256": digest}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--build-work", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--suite", choices=("guardian-pool", "state-migrations"),
                        default="guardian-pool")
    args = parser.parse_args()
    selected = suite_spec(args.suite)
    if sys.platform not in {"linux", "darwin"}:
        raise ValueError("This CI entry point qualifies only hosted Unix native producers")
    package, work = args.package.resolve(strict=True), args.build_work.resolve(strict=True)
    evidence = args.evidence.resolve()
    paths = [package, work, evidence, ROOT]
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError("Source, package, build cache and fresh evidence must be separate")
    if evidence.exists():
        raise ValueError("Preserve existing native qualification evidence")
    evidence.mkdir(parents=True)
    record = {"status": "running", "started_at": time.time(),
              "scope": selected["scope"], "suite": args.suite,
              "real_model_requests": 0, "provider_credentials_read": False,
              "product_release_ready": False}
    try:
        before = native.package_identity(package)
        source_before = native.source_identity()
        receipt = json.loads((package / "build-receipt.json").read_bytes())
        archives = list((work / "downloads").glob("source-*"))
        if len(archives) != 1 or archives[0].is_symlink():
            raise ValueError("Expected one exact official source archive from this producer")
        archive = archives[0]
        contract = sources.source_contract(before["target"], archive)
        tree, source_proof = producer_source(work, receipt, contract)
        from build_native import compiler_commands
        _, cargo, _ = compiler_commands(receipt["build_toolchain_pin"])
        if receipt["cargo_command"][:len(cargo)] != cargo:
            raise ValueError("CI test compiler differs from the actual native producer")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CODEX_", "CDS_", "OPENAI_", "OPENROUTER_",
                                    "ANTHROPIC_", "OTEL_", "PYTEST_", "GITHUB_", "ACTIONS_"))
               and k not in {"GH_TOKEN", "GITHUB_TOKEN", "PYTHONPATH", "PYTHONHOME"}}
        profile = receipt["environment"]
        if set(profile) != set(PROFILE_KEYS):
            raise ValueError("Native producer release profile is not completely declared")
        env.update(profile, CARGO_TARGET_DIR=str(work / "target"))
        env.update(bundled_bwrap_environment(package, work, receipt, before["target"]))
        command = [*cargo, "test", "--locked", "--release", "-p", selected["crate"],
                   "--lib", selected["filter"], "--",
                   "--test-threads=2", "--show-output"]
        record.update(package=before, source_sha256=source_before, producer=source_proof,
                      command=command, environment={k: env[k] for k in PROFILE_KEYS},
                      upstream_source_archive={"path": str(archive), "sha256": native.sha(archive)},
                      cargo_target_dir=env["CARGO_TARGET_DIR"],
                      bundled_sandbox_environment={k: v for k, v in env.items() if k=="CODEX_BWRAP_SHA256"})
        log_path = evidence / selected["log"]
        with log_path.open("wb") as log:
            process = native.run_observed(command, cwd=tree / "codex-rs", env=env,
                                          log=log, timeout=7200)
        record["processes"] = process
        record["log_sha256"] = native.sha(log_path)
        if not process["passed"] or process.get("returncode") != 0:
            raise RuntimeError("Native tests or owned-process cleanup failed")
        record["tests"] = test_result(log_path.read_text(encoding="utf-8"), args.suite)
        if native.package_identity(package) != before or native.source_identity() != source_before:
            raise ValueError("Package or checkout changed during native pool qualification")
        if native.sha(archive) != record["upstream_source_archive"]["sha256"]:
            raise ValueError("Official source archive changed during native pool qualification")
        if producer_source(work, receipt, contract)[1] != source_proof:
            raise ValueError("Producer source changed during native pool qualification")
        record["status"] = selected["passed"]
    except BaseException as error:
        record.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        record["finished_at"] = time.time()
        (evidence / "qualification.json").write_bytes(json.dumps(record, indent=2).encode())
    print(json.dumps({"status": record["status"], "evidence": str(evidence)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
