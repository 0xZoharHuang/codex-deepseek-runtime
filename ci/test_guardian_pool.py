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


@pytest.mark.parametrize("suite", ["guardian-pool", "state-migrations"])
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
    if suite == "state-migrations": argv += ["--suite", suite]
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
    observed = []

    def run_observed(command, *, cwd, env, log, timeout):
        selected = pool.suite_spec(suite)
        assert command == [*cargo, "test", "--locked", "--release", "-p", selected["crate"],
                           "--lib", selected["filter"], "--", "--test-threads=2", "--show-output"]
        assert cwd == tree/"codex-rs" and timeout == 7200
        assert {key: env[key] for key in pool.PROFILE_KEYS} == profile
        assert env["CARGO_TARGET_DIR"] == str(work/"target")
        assert "OPENROUTER_API_KEY" not in env
        actual_log = state_success_log() if suite == "state-migrations" else success_log()
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
