"""Negative controls for exact native pool qualification, without Cargo or a model."""
import hashlib
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("guardian_pool_ci", ROOT/"ci/qualify_guardian_pool.py")
pool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pool)


def success_log():
    return ("running 10 tests\n" + "".join("test "+name+" ... ok\n" for name in pool.EXPECTED_TESTS)
            + "test result: ok. 10 passed; 0 failed; 0 ignored; 0 measured; 118 filtered out; finished in 4.30s\n")


@pytest.mark.parametrize("bad", [None, "missing", "replacement", "duplicate", "ignored",
                                 "failed", "summary", "skip-return-ok", "extra-summary"])
def test_pool_requires_exact_executions_and_no_early_success(bad):
    log = success_log()
    first = pool.EXPECTED_TESTS[0]
    if bad == "missing": log = log.replace("test "+first+" ... ok\n", "")
    elif bad == "replacement": log = log.replace(first, "unreviewed::same_count")
    elif bad == "duplicate": log = log.replace(pool.EXPECTED_TESTS[1], first)
    elif bad == "ignored": log = log.replace("... ok", "... ignored", 1)
    elif bad == "failed": log = log.replace("... ok", "... FAILED", 1)
    elif bad == "summary": log = log.replace("0 failed", "1 failed")
    elif bad == "skip-return-ok":
        log += "Skipping test because it cannot execute when network is disabled in a Codex sandbox.\n"
    elif bad == "extra-summary": log += log.splitlines()[-1]+"\n"
    if bad is None:
        result = pool.test_result(log)
        assert result["executed"] == 10 and result["filtered_out_not_executed"] == 118
        assert result["tests"] == list(pool.EXPECTED_TESTS)
    else:
        with pytest.raises(ValueError): pool.test_result(log)


@pytest.mark.parametrize("bad", [None, "missing-source", "changed-source", "changed-diff",
                                 "second-tree", "unsafe-path", "unasserted-dependency", "extra-source"])
def test_pool_binds_to_one_actual_producer_source(tmp_path, bad):
    tree = tmp_path/"b-1234abcd"/"src"
    source = tree/"codex-rs"/"pool.rs"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"verified native pool")
    patch = tree.parent/"combined.patch"
    patch.write_bytes(b"verified combined source patch")
    digest = lambda data: hashlib.sha256(data).hexdigest()
    dependency = tree/"codex-rs/dependency.rs"
    dependency.write_bytes(b"original unmodified dependency")
    receipt = {"combined_patch_sha256": digest(patch.read_bytes()),
               "source_files": {"codex-rs/pool.rs": digest(source.read_bytes())}}
    complete = {"codex-rs/pool.rs": digest(source.read_bytes()),
                "codex-rs/dependency.rs": digest(dependency.read_bytes())}
    contract = {"links": {}, "final": complete, "before": complete,
                "target": "x86_64-unknown-linux-gnu",
                "unchanged_protected": {"codex-rs/dependency.rs": complete["codex-rs/dependency.rs"]}}
    # A successful before snapshot cannot hide later changes outside receipt.source_files.
    before = pool.producer_source(tmp_path, receipt, contract)[1]
    if bad == "missing-source": source.unlink()
    elif bad == "changed-source": source.write_bytes(b"unbuilt change")
    elif bad == "changed-diff": patch.write_bytes(b"unbuilt diff")
    elif bad == "second-tree": (tmp_path/"b-5678abcd"/"src").mkdir(parents=True)
    elif bad == "unsafe-path": receipt["source_files"] = {"../outside": "0"*64}
    elif bad == "unasserted-dependency": dependency.write_bytes(b"changed after producer snapshot")
    elif bad == "extra-source": (tree/"codex-rs/extra.rs").write_bytes(b"not in official contract")
    if bad is None:
        actual, proof = pool.producer_source(tmp_path, receipt, contract)
        assert actual == tree and proof["source_files"] == receipt["source_files"]
        assert proof == before and proof["full_source_file_count"] == 2
        assert "codex-rs/dependency.rs" in proof["full_source_inventory"]
    else:
        with pytest.raises((ValueError, FileNotFoundError)):
            pool.producer_source(tmp_path, receipt, contract)

@pytest.mark.parametrize("bad", [None, "missing", "wrong-resource", "bad-digest",
                                 "package-bytes", "cache-bytes"])
def test_linux_pool_uses_the_exact_embedded_bwrap_digest(tmp_path, bad):
    package, work = tmp_path/"package", tmp_path/"work"
    resource, cached = package/"codex-resources/bwrap", work/"target/release/bwrap"
    resource.parent.mkdir(parents=True); cached.parent.mkdir(parents=True)
    resource.write_bytes(b"actual stripped sandbox"); cached.write_bytes(resource.read_bytes())
    digest = hashlib.sha256(resource.read_bytes()).hexdigest()
    receipt = {"bwrap": {"resource": "codex-resources/bwrap", "embedded_sha256": digest}}
    if bad == "missing": receipt.clear()
    elif bad == "wrong-resource": receipt["bwrap"]["resource"] = "../other"
    elif bad == "bad-digest": receipt["bwrap"]["embedded_sha256"] = "unknown"
    elif bad == "package-bytes": resource.write_bytes(b"not shipped")
    elif bad == "cache-bytes": cached.write_bytes(b"not built")
    if bad is None:
        assert pool.bundled_bwrap_environment(package, work, receipt, "x86_64-unknown-linux-gnu") == {"CODEX_BWRAP_SHA256": digest}
    else:
        with pytest.raises(ValueError):
            pool.bundled_bwrap_environment(package, work, receipt, "x86_64-unknown-linux-gnu")


def test_nonlinux_pool_does_not_inherit_or_invent_bwrap_digest(tmp_path):
    assert pool.bundled_bwrap_environment(tmp_path, tmp_path, {}, "aarch64-apple-darwin") == {}
    with pytest.raises(ValueError):
        pool.bundled_bwrap_environment(tmp_path, tmp_path, {"bwrap": {}}, "aarch64-apple-darwin")

def test_native_inventory_retains_known_archive_license_link_bytes(tmp_path):
    tree=tmp_path/"b-1234abcd"/"src"
    source=tree/"codex-rs/pool.rs"; source.parent.mkdir(parents=True)
    source.write_bytes(b"guarded pool")
    copying=tree/"codex-rs/vendor/bubblewrap/COPYING"
    copying.parent.mkdir(parents=True);copying.write_bytes(b"official license")
    alias=copying.with_name("LICENSE");alias.write_bytes(copying.read_bytes())
    patch=tree.parent/"combined.patch";patch.write_bytes(b"combined")
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    pool_name="codex-rs/pool.rs"
    license_name="codex-rs/vendor/bubblewrap/COPYING"
    before={pool_name:digest(source),license_name:digest(copying)}
    contract={"target":"x86_64-pc-windows-msvc","before":before,"final":before,
              "links":{"codex-rs/vendor/bubblewrap/LICENSE":"COPYING"},
              "unchanged_protected":{}}
    receipt={"combined_patch_sha256":digest(patch),"source_files":{pool_name:digest(source)}}
    _,proof=pool.producer_source(tmp_path,receipt,contract)
    assert proof["full_source_file_count"]==3
    assert proof["full_source_inventory"]["codex-rs/vendor/bubblewrap/LICENSE"]==digest(copying)
    alias.write_bytes(b"altered archive license")
    with pytest.raises(ValueError,match="Complete native source inventory differs"):
        pool.producer_source(tmp_path,receipt,contract)


def state_success_log():
    return ("running 22 tests\n" + "".join("test " + name + " ... ok\n" for name in pool.STATE_TESTS)
            + "test result: ok. 22 passed; 0 failed; 0 ignored; 0 measured; 201 filtered out; finished in 1.00s\n")


@pytest.mark.parametrize("bad", [None, "missing", "replacement", "duplicate", "ignored",
                                 "failed", "summary", "skip-return-ok", "extra-summary",
                                 "empty-filter", "old-eleven-only", "guardian-only", "extra-test"])
def test_state_suite_requires_all_exact_twenty_two_native_executions(bad):
    log = state_success_log()
    first = pool.STATE_TESTS[0]
    if bad == "missing": log = log.replace("test " + first + " ... ok\n", "")
    elif bad == "replacement": log = log.replace(first, "migrations::tests::unreviewed_same_count")
    elif bad == "duplicate": log = log.replace(pool.STATE_TESTS[1], first)
    elif bad == "ignored": log = log.replace("... ok", "... ignored", 1)
    elif bad == "failed": log = log.replace("... ok", "... FAILED", 1)
    elif bad == "summary": log = log.replace("0 failed", "1 failed")
    elif bad == "skip-return-ok": log += "Skipping test because it cannot execute when network is disabled\n"
    elif bad == "extra-summary": log += log.splitlines()[-1] + "\n"
    elif bad == "empty-filter":
        log = "running 0 tests\ntest result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 223 filtered out; finished in 0.00s\n"
    elif bad == "old-eleven-only":
        log = ("".join("test " + name + " ... ok\n" for name in pool.STATE_TESTS[:11])
               + "test result: ok. 11 passed; 0 failed; 0 ignored; 0 measured; 212 filtered out; finished in 1.00s\n")
    elif bad == "guardian-only": log = success_log()
    elif bad == "missing-legacy":
        log = log.replace("test " + pool.CAPACITY_LEGACY_TESTS[0] + " ... ok\n", "")
    elif bad in {"missing-image-budget", "missing-cancel"}:
        name = pool.CAPACITY_FRESH_TESTS[-2 if bad == "missing-image-budget" else -1]
        log = log.replace("test " + name + " ... ok\n", "")
    elif bad == "extra-test": log += "test migrations::tests::unreviewed_extra ... ok\n"
    if bad is None:
        result = pool.test_result(log, "state-migrations")
        assert result == {"executed": 22, "failed": 0, "ignored": 0,
                          "filtered_out_not_executed": 201, "tests": list(pool.STATE_TESTS)}
        assert len({name for name in pool.STATE_TESTS if name.startswith("migrations::tests::eol_")}) == 11
    else:
        with pytest.raises(ValueError): pool.test_result(log, "state-migrations")


def test_native_suite_is_explicit_and_default_guardian_remains_strict():
    assert pool.test_result(success_log())["executed"] == 10
    with pytest.raises(ValueError): pool.test_result(state_success_log())
    with pytest.raises(ValueError): pool.test_result(success_log(), "unreviewed-suite")


@pytest.mark.parametrize("suite", ["guardian-pool", "state-migrations", "capacity-core"])
@pytest.mark.parametrize("bad", [None, "process", "source-after", "package-after",
                                 "producer-after", "compiler", "successful-process-wrong-log", "nonzero-exit"])
def test_suite_entry_uses_verified_producer_and_rejects_execution_or_identity_drift(
        tmp_path, monkeypatch, suite, bad):
    # Exercise the real CLI controller with the existing process observer replaced
    # by an inert receipt writer: no Cargo, model or network is executed here.
    import json
    import sys
    import build_native

    package, work, checkout, evidence = (tmp_path/name for name in ("package", "work", "checkout", "evidence"))
    for path in (package, work, checkout): path.mkdir()
    tree = work/"b-1234abcd/src"
    (tree/"codex-rs").mkdir(parents=True)
    archive = work/"downloads/source-official"
    archive.parent.mkdir()
    archive.write_bytes(b"fixed official archive")
    profile = {key: "0" for key in pool.PROFILE_KEYS}
    profile["CARGO_BUILD_JOBS"] = "1"
    cargo = ["cargo", "+1.99.1"]
    receipt = {"build_toolchain_pin": "1.99.1", "cargo_command": cargo + ["build"],
               "environment": profile}
    (package/"build-receipt.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(pool, "ROOT", checkout)
    monkeypatch.setattr(sys, "platform", "darwin")
    argv = ["qualify_guardian_pool.py", "--package", str(package), "--build-work", str(work),
            "--evidence", str(evidence)]
    # The original Guardian invocation has no --suite argument.
    if suite != "guardian-pool": argv += ["--suite", suite]
    monkeypatch.setattr(sys, "argv", argv)
    counts = {"package": 0, "source": 0, "producer": 0}
    identity = {"target": "aarch64-apple-darwin", "physical_binary_sha256": "fixture"}

    def package_identity(path):
        assert path == package
        counts["package"] += 1
        return dict(identity, drift=True) if bad == "package-after" and counts["package"] > 1 else dict(identity)

    def source_identity():
        counts["source"] += 1
        return "changed" if bad == "source-after" and counts["source"] > 1 else "checkout-fingerprint"

    def producer_source(actual_work, actual_receipt, contract):
        assert actual_work == work and actual_receipt == receipt and contract == {"fixture": "verified"}
        counts["producer"] += 1
        return tree, {"full_source_inventory_sha256": "changed" if bad == "producer-after" and counts["producer"] > 1 else "complete-source-fingerprint"}

    monkeypatch.setattr(pool.native, "package_identity", package_identity)
    monkeypatch.setattr(pool.native, "source_identity", source_identity)
    monkeypatch.setattr(pool, "producer_source", producer_source)
    monkeypatch.setattr(pool.sources, "source_contract", lambda target, actual: {"fixture": "verified"})
    monkeypatch.setattr(build_native, "compiler_commands", lambda pin: (
        ["rustc", "+" + pin], ["different-cargo"] if bad == "compiler" else cargo, {}))
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-fixture-not-a-credential")
    monkeypatch.setenv("RUST_MIN_STACK", "2097152")
    observed = []

    def run_observed(command, *, cwd, env, log, timeout):
        selected = pool.suite_spec(suite)
        selection = (["--", "--exact", *selected["tests"]] if suite == "capacity-core"
                     else [selected["filter"], "--"])
        config = (["--config", "profile.release.package.codex-core.opt-level=0"]
                  if suite == "capacity-core" else [])
        assert command == [*cargo, *config, "test", "--locked", "--release", "-p", selected["crate"],
                           "--lib", *selection, "--test-threads=2", "--show-output"]
        if suite == "capacity-core":
            assert command.count("--exact") == 1
            assert selected["filter"] is None and selected["exact"] is True
            assert command[command.index("--exact") + 1:-2] == list(pool.CAPACITY_TESTS)
        assert cwd == tree/"codex-rs" and timeout == 7200
        assert {key: env[key] for key in pool.PROFILE_KEYS} == profile
        assert env["CARGO_TARGET_DIR"] == str(work/"target")
        assert "OPENROUTER_API_KEY" not in env
        assert env["RUST_MIN_STACK"] == ("8388608" if suite == "capacity-core" else "2097152")
        assert __import__("os").environ["RUST_MIN_STACK"] == "2097152"
        assert "--nocapture" not in command and command.count("--show-output") == 1
        actual_log = (state_success_log() if suite == "state-migrations" else
                      capacity_success_log() if suite == "capacity-core" else success_log())
        if bad == "successful-process-wrong-log": actual_log = "running 0 tests\n"
        log.write(actual_log.encode())
        observed.append(command)
        return {"passed": bad != "process", "returncode": 1 if bad in {"process", "nonzero-exit"} else 0}

    monkeypatch.setattr(pool.native, "run_observed", run_observed)
    if bad is None:
        assert pool.main() == 0
    else:
        with pytest.raises((ValueError, RuntimeError)): pool.main()
    result = json.loads((evidence/"qualification.json").read_text())
    selected = pool.suite_spec(suite)
    assert result["suite"] == suite and result["scope"] == selected["scope"]
    assert result["status"] == (selected["passed"] if bad is None else "failed")
    assert result["product_release_ready"] is False and result["real_model_requests"] == 0
    if bad == "compiler": assert observed == []
    else: assert len(observed) == 1 and (evidence/selected["log"]).is_file()
    if bad is None:
        assert counts == {"package": 2, "source": 2, "producer": 2}
        assert result["tests"]["executed"] == len(selected["tests"])
        if suite == "capacity-core":
            override = result["test_profile_override"]
            assert override["base_profile"] == "release"
            assert override["inline_cargo_config"] == ["profile.release.package.codex-core.opt-level=0"]
            assert override["package_overrides"] == {"codex-core": {"opt-level": 0}}
            assert override["applies_to"] == "all codex-core targets in this test dependency graph, including normal libraries"
            assert override["production_optimization_qualified"] is False
            assert override["performance_claim"] is False
            assert result["test_process_settings"] == {
                "environment": {"RUST_MIN_STACK": "8388608"},
                "libtest_output": "--show-output",
                "scope": "capacity-core test invocation only; no production environment or source change",
            }
        else:
            assert "test_profile_override" not in result
            assert "test_process_settings" not in result


def capacity_success_log():
    return ("running 67 tests\n" + "".join("test " + name + " ... ok\n" for name in pool.CAPACITY_TESTS)
            + "test result: ok. 67 passed; 0 failed; 0 ignored; 0 measured; 3200 filtered out; finished in 0.01s\n")


@pytest.mark.parametrize("bad", [None, "missing", "replacement", "duplicate", "ignored",
                                 "failed", "summary", "skip-return-ok", "extra-summary",
                                 "empty-filter", "old-six-only", "new-eight-only", "fresh-eleven-only",
                                 "previous-fourteen-only", "previous-twenty-three-only",
                                 "legacy-only", "previous-thirty-one-only", "missing-legacy",
                                 "missing-image-budget", "missing-cancel", "extra-test",
                                  "previous-thirty-three-only", "new-eleven-only", "previous-sixty-only", "text-four-only", "previous-sixty-four-only", "reset-continuation-only"])
def test_capacity_suite_requires_all_named_new_and_existing_native_mechanisms(bad):
    log = capacity_success_log()
    first = pool.CAPACITY_TESTS[0]
    if bad == "missing": log = log.replace("test " + first + " ... ok\n", "")
    elif bad == "replacement": log = log.replace(first, "compact::tests::unreviewed_same_count")
    elif bad == "duplicate": log = log.replace(pool.CAPACITY_TESTS[1], first)
    elif bad == "ignored": log = log.replace("... ok", "... ignored", 1)
    elif bad == "failed": log = log.replace("... ok", "... FAILED", 1)
    elif bad == "summary": log = log.replace("0 failed", "1 failed")
    elif bad == "skip-return-ok": log += "Skipping test because it cannot execute when network is disabled\n"
    elif bad == "extra-summary": log += log.splitlines()[-1] + "\n"
    elif bad == "empty-filter":
        log = "running 0 tests\ntest result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 3214 filtered out; finished in 0.00s\n"
    elif bad in {"old-six-only", "new-eight-only", "fresh-eleven-only", "previous-fourteen-only",
                 "previous-twenty-three-only", "previous-thirty-one-only", "legacy-only"}:
        cases = {"old-six-only": pool.CAPACITY_PAIR_TESTS,
                 "new-eight-only": pool.CAPACITY_NEW_TESTS,
                 "fresh-eleven-only": pool.CAPACITY_FRESH_TESTS,
                 "previous-fourteen-only": pool.CAPACITY_NEW_TESTS + pool.CAPACITY_PAIR_TESTS,
                 "previous-twenty-three-only": pool.CAPACITY_NEW_TESTS + pool.CAPACITY_FRESH_TESTS[:9] + pool.CAPACITY_PAIR_TESTS,
                 "previous-thirty-one-only": pool.CAPACITY_NEW_TESTS + pool.CAPACITY_FRESH_TESTS[:9] + pool.CAPACITY_PAIR_TESTS + pool.CAPACITY_LEGACY_TESTS,
                 "legacy-only": pool.CAPACITY_LEGACY_TESTS}[bad]
        log = ("".join("test " + name + " ... ok\n" for name in cases)
               + f"test result: ok. {len(cases)} passed; 0 failed; 0 ignored; 0 measured; 3200 filtered out; finished in 0.01s\n")
    elif bad in {"previous-thirty-three-only", "new-eleven-only"}:
        cases = (pool.CAPACITY_PREVIOUS_TESTS if bad == "previous-thirty-three-only"
                 else pool.CAPACITY_CANCELLATION_TESTS)
        log = ("".join("test " + name + " ... ok\n" for name in cases)
               + f"test result: ok. {len(cases)} passed; 0 failed; 0 ignored; 0 measured; 3200 filtered out; finished in 0.01s\n")
    elif bad in {"previous-sixty-only", "text-four-only"}:
        cases = pool.CAPACITY_TESTS[:60] if bad == "previous-sixty-only" else pool.CAPACITY_TEXT_PROJECTION_TESTS
        log = ("".join("test " + name + " ... ok\n" for name in cases)
               + f"test result: ok. {len(cases)} passed; 0 failed; 0 ignored; 0 measured; 3200 filtered out; finished in 0.01s\n")
    elif bad in {"previous-sixty-four-only", "reset-continuation-only"}:
        cases = pool.CAPACITY_TESTS[:64] if bad == "previous-sixty-four-only" else pool.CAPACITY_RESET_CONTINUATION_TESTS
        log = ("".join("test " + name + " ... ok\n" for name in cases)
               + f"test result: ok. {len(cases)} passed; 0 failed; 0 ignored; 0 measured; 3200 filtered out; finished in 0.01s\n")
    elif bad == "missing-legacy":
        log = log.replace("test " + pool.CAPACITY_LEGACY_TESTS[0] + " ... ok\n", "")
    elif bad in {"missing-image-budget", "missing-cancel"}:
        name = pool.CAPACITY_FRESH_TESTS[-2 if bad == "missing-image-budget" else -1]
        log = log.replace("test " + name + " ... ok\n", "")
    elif bad == "extra-test": log += "test compact::tests::request_capacity_unreviewed ... ok\n"
    if bad is None:
        result = pool.test_result(log, "capacity-core")
        assert result["executed"] == 67 and result["failed"] == result["ignored"] == 0
        assert result["tests"] == list(pool.CAPACITY_TESTS)
        assert len(pool.CAPACITY_NEW_TESTS) == 8 and len(pool.CAPACITY_PAIR_TESTS) == 6
        assert len(pool.CAPACITY_FRESH_TESTS) == 11
        assert len(pool.CAPACITY_LEGACY_TESTS) == 8
        assert len(set(pool.CAPACITY_TESTS)) == 67
        assert set(pool.CAPACITY_LEGACY_TESTS).isdisjoint(
            pool.CAPACITY_NEW_TESTS + pool.CAPACITY_FRESH_TESTS + pool.CAPACITY_PAIR_TESTS)
        assert set(pool.CAPACITY_NEW_TESTS).isdisjoint(pool.CAPACITY_PAIR_TESTS)
        assert set(pool.CAPACITY_FRESH_TESTS).isdisjoint(pool.CAPACITY_NEW_TESTS + pool.CAPACITY_PAIR_TESTS)
    else:
        with pytest.raises(ValueError): pool.test_result(log, "capacity-core")


def test_fresh_ci_requires_capacity_core_in_addition_to_prior_native_gates():
    workflow = (ROOT/".github/workflows/native-platform-qualification.yml").read_text(encoding="utf-8")
    step = "Qualify actual restored-call and byte-prefix capacity"
    assert workflow.count("- name: " + step) == 1
    assert workflow.count("--suite capacity-core") == 1
    assert workflow.index("- name: " + step) < workflow.index("- name: Run real native payload and recovery fixtures")
    assert "--suite state-migrations" in workflow
    assert "--evidence \"$RUNNER_TEMP/cds-ci-artifact/guardian-pool\"" in workflow
    assert "--evidence \"$RUNNER_TEMP/cds-ci-artifact/capacity-core\"" in workflow
    selected = pool.suite_spec("capacity-core")
    assert selected["crate"] == "codex-core" and selected["filter"] is None
    assert selected["exact"] is True
    assert selected["passed"] == "passed_native_capacity_core_scope"


@pytest.mark.parametrize("bad", [None, "missing", "global", "all-packages", "config-file",
                                  "wrong-package", "wrong-level", "extra-override", "duplicate"])
def test_capacity_profile_override_is_one_inline_package_setting_not_a_global_change(bad, monkeypatch):
    # Parsing this command must never create a Cargo config file or alter source.
    def no_write(*args, **kwargs):
        raise AssertionError("A suite profile override must stay inside the command line")
    monkeypatch.setattr(Path, "write_text", no_write)
    monkeypatch.setattr(Path, "write_bytes", no_write)
    setting = "profile.release.package.codex-core.opt-level=0"
    config = ["--config", setting]
    if bad == "missing": config = []
    elif bad == "global": config = ["--config", "profile.release.opt-level=0"]
    elif bad == "all-packages": config = ["--config", 'profile.release.package."*".opt-level=0']
    elif bad == "config-file": config = ["--config", "C:/unreviewed-global-config.toml"]
    elif bad == "wrong-package": config = ["--config", "profile.release.package.codex-state.opt-level=0"]
    elif bad == "wrong-level": config = ["--config", "profile.release.package.codex-core.opt-level=1"]
    elif bad == "extra-override": config += ["--config", "profile.release.package.codex-core.debug-assertions=true"]
    elif bad == "duplicate": config *= 2
    command = ["cargo", "+1.99.1", *config, "test", "--locked", "--release", "-p", "codex-core", "--lib",
               "--", "--exact", *pool.CAPACITY_TESTS, "--test-threads=2", "--show-output"]
    if bad is None:
        profile = pool.validated_test_profile(command, "capacity-core")
        assert profile["package_overrides"] == {"codex-core": {"opt-level": 0}}
        assert profile["inline_cargo_config"] == [setting]
        assert profile["production_optimization_qualified"] is profile["performance_claim"] is False
    else:
        with pytest.raises(ValueError): pool.validated_test_profile(command, "capacity-core")


@pytest.mark.parametrize("suite", ["guardian-pool", "state-migrations"])
def test_other_suites_cannot_inherit_the_core_test_package_override(suite):
    command = ["cargo", "+1.99.1", "test", "--release", "-p", pool.suite_spec(suite)["crate"], "--lib", "--"]
    assert pool.validated_test_profile(command, suite) is None
    for setting in ("profile.release.package.codex-core.opt-level=0", "profile.release.opt-level=0"):
        with pytest.raises(ValueError):
            pool.validated_test_profile([*command[:2], "--config", setting, *command[2:]], suite)


@pytest.mark.parametrize("bad", [None, "interleaved-nocapture", "skip-in-success-output", "duplicate-in-success-output"])
def test_capacity_show_output_preserves_stdout_and_strict_native_case_gate(bad):
    log = capacity_success_log()
    first = pool.CAPACITY_TESTS[0]
    captured = ("\nsuccesses:\n\n---- " + first + " stdout ----\n"
                + "NATIVE_CAPACITY_MOCK_PAYLOAD synthetic fixture payload\n")
    if bad == "interleaved-nocapture":
        log = log.replace("test " + first + " ... ok\n",
                          "test " + first + " ... NATIVE_CAPACITY_MOCK_PAYLOAD\nok\n")
    elif bad == "skip-in-success-output": captured += "Skipping test because network is disabled\n"
    elif bad == "duplicate-in-success-output": captured += "test " + first + " ... ok\n"
    if bad is None:
        assert pool.test_result(log + captured, "capacity-core")["executed"] == 67
    else:
        with pytest.raises(ValueError): pool.test_result(log + captured, "capacity-core")



def test_capacity_exact60_preserves_previous44_and_matches_actual_listed_additions():
    previous = (pool.CAPACITY_NEW_TESTS + pool.CAPACITY_FRESH_TESTS
                + pool.CAPACITY_PAIR_TESTS + pool.CAPACITY_LEGACY_TESTS)
    assert len(previous) == 33
    assert pool.CAPACITY_PREVIOUS_TESTS == previous
    assert pool.CAPACITY_TESTS[:33] == previous
    reviewed = (
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
    assert len(reviewed) == 11 and len(set(reviewed)) == 11
    assert pool.CAPACITY_CANCELLATION_TESTS == reviewed
    assert pool.CAPACITY_TESTS[33:44] == reviewed
    assert len(reviewed[:4]) == 4 and all(name.startswith("compact::tests::") for name in reviewed[:4])
    assert len(reviewed[4:]) == 7 and all(name.startswith("session::tests::capacity_commit_tests::") for name in reviewed[4:])
    assert set(previous).isdisjoint(reviewed)
    assert pool.CAPACITY_TESTS[:44] == previous + reviewed
    assert len(pool.CAPACITY_TYPED_PROJECTION_TESTS) == 16
    assert pool.CAPACITY_TESTS[44:60] == pool.CAPACITY_TYPED_PROJECTION_TESTS
    assert set(pool.CAPACITY_TESTS[:44]).isdisjoint(pool.CAPACITY_TYPED_PROJECTION_TESTS)


@pytest.mark.parametrize("name", pool.CAPACITY_CANCELLATION_TESTS + pool.CAPACITY_TYPED_PROJECTION_TESTS + pool.CAPACITY_TEXT_PROJECTION_TESTS + pool.CAPACITY_RESET_CONTINUATION_TESTS)
@pytest.mark.parametrize("problem", ["missing", "duplicate", "ignored", "failed"])
def test_every_new_capacity_case_is_required_once_and_cannot_be_ignored(name, problem):
    log = capacity_success_log()
    line = "test " + name + " ... ok\n"
    assert line in log
    if problem == "missing": log = log.replace(line, "", 1)
    elif problem == "duplicate": log += line
    elif problem == "ignored": log = log.replace(line, "test " + name + " ... ignored\n", 1)
    else: log = log.replace(line, "test " + name + " ... FAILED\n", 1)
    with pytest.raises(ValueError):
        pool.test_result(log, "capacity-core")


def test_capacity_exact64_preserves_previous60_and_actual_listed_text_cases():
    previous = (pool.CAPACITY_PREVIOUS_TESTS + pool.CAPACITY_CANCELLATION_TESTS
                + pool.CAPACITY_TYPED_PROJECTION_TESTS)
    assert len(previous) == 60
    assert pool.CAPACITY_TESTS[:60] == previous
    reviewed = (
        'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::single_historical_text_512k_bytes_budget_256k',
        'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::confirmed_current_turn_unicode_text_budget_256k',
        'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::real_token_cancelled_after_first_text_byte_range_summary',
        'compact::tests::historical_typed_projection_tests::historical_typed_projection_reads_all_parts_before_one_durable_install::later_text_byte_range_upstream_failure_installs_nothing',
    )
    assert len(reviewed) == len(set(reviewed)) == 4
    assert pool.CAPACITY_TEXT_PROJECTION_TESTS == reviewed
    assert pool.CAPACITY_TESTS[60:64] == reviewed
    assert set(previous).isdisjoint(reviewed)


def test_capacity_exact67_preserves_previous64_and_actual_reset_continuation_cases():
    previous = (pool.CAPACITY_PREVIOUS_TESTS + pool.CAPACITY_CANCELLATION_TESTS
                + pool.CAPACITY_TYPED_PROJECTION_TESTS + pool.CAPACITY_TEXT_PROJECTION_TESTS)
    assert len(previous) == 64
    assert pool.CAPACITY_TESTS[:64] == previous
    reviewed = (
        'session::capacity_reset_continuation::tests::local_reset_with_old_gap_keeps_native_hook_and_writer_contract::post_native_hook_plan_retains_exact_old_assistant_and_current_suffix',
        'session::capacity_reset_continuation::tests::local_reset_with_old_gap_keeps_native_hook_and_writer_contract::stopped_native_hook_changes_no_history_window_checkpoint_or_model',
        'session::capacity_reset_continuation::tests::real_sampling_reset_then_413_fresh_old_gap_then_ordinary_preserves_current',
    )
    assert len(reviewed) == len(set(reviewed)) == 3
    assert pool.CAPACITY_RESET_CONTINUATION_TESTS == reviewed
    assert pool.CAPACITY_TESTS[64:] == reviewed
    assert set(previous).isdisjoint(reviewed)
