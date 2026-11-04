"""Qualify native mechanism tests on the fresh producer's actual source/cache.

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


# The new restored-call/byte-prefix cases must run alongside the existing
# crossing/unfinished-pair guards. Exact names reject empty or partial filters.
HISTORY_PREFIX = "context_manager::history::tests::"
CAPACITY_NEW_TESTS = (
    *(HISTORY_PREFIX + "request_capacity_restored_calls_use_native_aborted_outputs_without_mutating_live_history::" + kind
      for kind in ("function", "custom", "local_shell", "tool_search")),
    HISTORY_PREFIX + "request_capacity_restored_custom_call_output_id_is_stable_across_independent_rebuilds",
    "compact::tests::request_capacity_byte_prefix_bounds_dense_data_instead_of_counting_small_notes",
    "compact::tests::request_capacity_byte_prefix_dominant_first_input_does_not_retry_every_trailing_note",
    "compact::tests::request_capacity_byte_prefix_requires_new_real_input_after_the_previous_summary",
)
CAPACITY_PAIR_TESTS = (
    *(HISTORY_PREFIX + "request_capacity_prefix_protects_crossing_and_unfinished_native_pairs::" + kind
      for kind in ("function_pair", "custom_pair", "local_shell_function_output_pair", "client_tool_search_pair")),
    HISTORY_PREFIX + "request_capacity_prefix_rechecks_pairs_after_moving_boundary_back",
    HISTORY_PREFIX + "request_capacity_prefix_keeps_native_standalone_server_tool_search_output",
)
# Exact expanded test-case labels follow test-case-core3.3.1 escape_test_name.
# Qualification still requires actual producer Rust executions of every name.
CAPACITY_FRESH_TESTS = (
    HISTORY_PREFIX + "request_capacity_fresh_summary_keeps_current_turn_and_completes_only_restored_old_pairs",
    HISTORY_PREFIX + "request_capacity_fresh_summary_requires_current_turn_and_stops_before_ambiguous_input",
    HISTORY_PREFIX + "request_capacity_fresh_summary_does_not_cross_pair_ending_in_current_turn",
    *("compact::tests::request_capacity_fresh_summary_recovers_old_cp6_without_manual_or_ordinary_warmup::" + label
      for label in ("old_cp6_direct_fresh_summary",
                    "byte_bounded_subset_preserves_all_unsent_history",
                    "completed_without_summary_cannot_install",
                    "failed_upstream_cannot_install",
                    "changed_native_lifetime_cannot_install",
                    "changed_auth_owner_cannot_install",
                    "seven_image_budget_six_requires_whole_transaction",
                    "cancelled_after_partial_response_cannot_install")),
)
# Existing real native capacity mechanisms are part of the same release gate.
# --exact selects each declared node, including expanded HTTP-classification cases.
CAPACITY_LEGACY_TESTS = (
    "compact::tests::byte_capacity_compaction_preserves_unconsumed_suffix_and_fences_epoch",
    "compact::tests::irreducible_capacity_failure_keeps_history_and_failed_attempt_fence",
    "compact::tests::capacity_token_budget_reset_preserves_pending_and_concurrent_suffix_without_model",
    "compact::tests::capacity_token_budget_without_confirmed_cursor_preserves_input",
    "compact::tests::capacity_token_budget_stale_prefix_returns_original_http413",
    *("compact::tests::manual_partition_does_not_treat_other_http_errors_as_byte_capacity::" + label
      for label in ("unauthorized", "rate_limited", "unavailable")),
)
# Additions verified once each in the actual 7f851e producer libtest --list.
# List SHA256: 5495ba3bb2de03e04185ce8066fe073bb3c41ff78502073cf55fceafe7f38a96
# Producer receipt SHA256: 499d67e3d7aff90a04aa03e195a3c448f20f7bd544d3d65b9d05035b3f6d0537
# Discovery qualifies exact names only; actual execution must still pass below.
CAPACITY_CANCELLATION_TESTS = (
    'compact::tests::capacity_installer_rechecks_real_cancel_token_after_waiting_for_native_state_lock',
    'compact::tests::capacity_token_budget_already_cancelled_helper_preserves_native_state_without_model',
    'compact::tests::capacity_token_cancel_prevents_summary_install_without_dropping_future::cancel_and_release_final_summary_inside_native_grace',
    'compact::tests::capacity_token_cancel_prevents_summary_install_without_dropping_future::stopped_helper_emits_no_request',
    'session::tests::capacity_commit_tests::capacity_complete_cancel_before_guard_keeps_real_window_history_and_writer_unchanged',
    'session::tests::capacity_commit_tests::capacity_owned_commit_survives_native_force_abort_and_orders_real_writer::after_add_ack_withheld_with_native_interruption_marker',
    'session::tests::capacity_commit_tests::capacity_owned_commit_survives_native_force_abort_and_orders_real_writer::after_add_ack_withheld_without_native_interruption_marker',
    'session::tests::capacity_commit_tests::capacity_owned_commit_survives_native_force_abort_and_orders_real_writer::before_add_with_native_interruption_marker',
    'session::tests::capacity_commit_tests::capacity_owned_commit_survives_native_force_abort_and_orders_real_writer::before_add_without_native_interruption_marker',
    'session::tests::capacity_commit_tests::capacity_owned_commit_survives_native_force_abort_and_orders_real_writer::shutdown_admission_closes_before_add_finishes',
    'session::tests::capacity_commit_tests::capacity_owned_commit_survives_native_force_abort_and_orders_real_writer::shutdown_admission_closes_while_append_ack_withheld',
)
CAPACITY_PREVIOUS_TESTS = CAPACITY_NEW_TESTS + CAPACITY_FRESH_TESTS + CAPACITY_PAIR_TESTS + CAPACITY_LEGACY_TESTS
# Typed projection names are from the actual producer libtest --list.
# List SHA256: 642d1361efdd3594e4ea6ef98fcf2d7d2d346a7ac717bd98911c656d2ed4a7b7
# Discovery fixes selection only; every actual Rust execution is required.
CAPACITY_TYPED_PROJECTION_TESTS = (
    'compact::tests::historical_typed_projection_tests::historical_projection_message_keeps_positional_metadata_and_media',
    'compact::tests::historical_typed_projection_tests::historical_projection_refuses_unattributed_current_or_incomplete_items::_current_expects',
    'compact::tests::historical_typed_projection_tests::historical_projection_refuses_unattributed_current_or_incomplete_items::_encrypted_expects',
    'compact::tests::historical_typed_projection_tests::historical_projection_refuses_unattributed_current_or_incomplete_items::_in_progress_expects',
    'compact::tests::historical_typed_projection_tests::historical_projection_refuses_unattributed_current_or_incomplete_items::_incomplete_expects',
    'compact::tests::historical_typed_projection_tests::historical_projection_refuses_unattributed_current_or_incomplete_items::_missing_output_expects',
    'compact::tests::historical_typed_projection_tests::historical_projection_refuses_unattributed_current_or_incomplete_items::_unknown_expects',
    'compact::tests::historical_typed_projection_tests::historical_projection_rejects_extra_suffix_or_changed_native_instruction',
    'compact::tests::historical_typed_projection_tests::historical_projection_requires_fresh_summary_exact_parts_and_carry_chain',
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::exact_completed_pair_seven_images_budget_six',
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::interleaved_parallel_pairs_three_plus_four_and_empty_output',
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::later_completed_without_fresh_summary_installs_nothing',
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::later_typed_part_upstream_failure_installs_nothing',
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::native_function_output_seven_images_budget_six',
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::one_historical_message_seven_images_and_positional_metadata',
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::real_token_cancelled_after_first_typed_part_summary',
)
# Text names were individually confirmed once in actual sealed libtest --list.
# List SHA256: d4dda3ee1660772dd99ee250e479e211253c61d4a4bb9b98f2f34894e005771c
# Listing selects cases; only actual Rust execution satisfies the native gate.
CAPACITY_TEXT_PROJECTION_TESTS = (
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::single_historical_text_512k_bytes_budget_256k',
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::confirmed_current_turn_unicode_text_budget_256k',
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::real_token_cancelled_after_first_text_byte_range_summary',
    'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::later_text_byte_range_upstream_failure_installs_nothing',
)
# Actual libtest listing confirms each new reset continuation name once.
# Listing SHA256: 20a36e396376b800daafc5f85fde1f776e6650b27f93b4a3b75c86316f395030
# Runtime qualification still requires every selected native execution.
CAPACITY_RESET_CONTINUATION_TESTS = (
    'session::capacity_reset_continuation::tests::local_reset_with_old_gap_keeps_native_hook_and_writer_contract::post_native_hook_plan_retains_exact_old_assistant_and_current_suffix',
    'session::capacity_reset_continuation::tests::local_reset_with_old_gap_keeps_native_hook_and_writer_contract::stopped_native_hook_changes_no_history_window_checkpoint_or_model',
    'session::capacity_reset_continuation::tests::real_sampling_reset_then_413_fresh_old_gap_then_ordinary_preserves_current',
)
CAPACITY_TESTS = (CAPACITY_PREVIOUS_TESTS + CAPACITY_CANCELLATION_TESTS
                  + CAPACITY_TYPED_PROJECTION_TESTS + CAPACITY_TEXT_PROJECTION_TESTS
                  + CAPACITY_RESET_CONTINUATION_TESTS)


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
    if suite == "capacity-core":
        return {"tests": CAPACITY_TESTS, "crate": "codex-core",
                "filter": None, "exact": True, "log": "native-capacity.log",
                "scope": "Restored calls, fresh and legacy capacity transactions, native token cancellation, owned writer checkpoint ordering, lossless UTF-8 historical text views, one-turn reset old-gap continuation and native hooks, epoch fences, token-budget reset and live pair guards",
                "passed": "passed_native_capacity_core_scope"}
    raise ValueError("Unknown native test suite")


def validated_test_profile(command: list[str], suite: str) -> dict | None:
    """Allow one invocation-local core test override, never a global/file config.

    This changes every codex-core target compiled in this test graph, including
    normal libraries, not only the libtest target. Packaged production binaries
    keep their producer profile and remain covered by the identity guard.
    """
    suite_spec(suite)
    cargo_args = command[:command.index("--")] if "--" in command else command
    configs = []
    for index, arg in enumerate(cargo_args):
        if arg == "--config":
            if index + 1 == len(cargo_args):
                raise ValueError("Missing native test profile config value")
            configs.append(cargo_args[index + 1])
        elif arg.startswith("--config="):
            configs.append(arg.partition("=")[2])
    expected = (["profile.release.package.codex-core.opt-level=0"]
                if suite == "capacity-core" else [])
    if configs != expected:
        raise ValueError("Native test profile differs from the exact suite override")
    if cargo_args.count("--release") != 1 or any(
            arg == "--profile" or arg.startswith("--profile=") for arg in cargo_args):
        raise ValueError("Native tests must retain the producer release base profile")
    if suite != "capacity-core":
        return None
    return {"base_profile": "release", "inline_cargo_config": configs,
            "package_overrides": {"codex-core": {"opt-level": 0}},
            "applies_to": "all codex-core targets in this test dependency graph, including normal libraries",
            "other_settings": "inherited producer release settings; debug assertions, overflow checks, features and test assertions are not overridden",
            "production_optimization_qualified": False, "performance_claim": False}


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
    parser.add_argument("--suite", choices=("guardian-pool", "state-migrations", "capacity-core"),
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
        selection = (["--", "--exact", *selected["tests"]] if selected.get("exact")
                     else [selected["filter"], "--"])
        inline_config = (["--config", "profile.release.package.codex-core.opt-level=0"]
                         if args.suite == "capacity-core" else [])
        command = [*cargo, *inline_config, "test", "--locked", "--release", "-p", selected["crate"],
                   "--lib", *selection, "--test-threads=2", "--show-output"]
        test_profile = validated_test_profile(command, args.suite)
        record.update(package=before, source_sha256=source_before, producer=source_proof,
                      command=command, environment={k: env[k] for k in PROFILE_KEYS},
                      upstream_source_archive={"path": str(archive), "sha256": native.sha(archive)},
                      cargo_target_dir=env["CARGO_TARGET_DIR"],
                      bundled_sandbox_environment={k: v for k, v in env.items() if k=="CODEX_BWRAP_SHA256"})
        if test_profile is not None:
            record["test_profile_override"] = test_profile
            # Deep history fixtures need the native Windows executable's 8 MiB
            # stack reserve on libtest's spawned test threads too. Keep this
            # inside the capacity-core invocation, never the producer profile.
            env["RUST_MIN_STACK"] = "8388608"
            record["test_process_settings"] = {
                "environment": {"RUST_MIN_STACK": env["RUST_MIN_STACK"]},
                "libtest_output": "--show-output",
                "scope": "capacity-core test invocation only; no production environment or source change",
            }
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
