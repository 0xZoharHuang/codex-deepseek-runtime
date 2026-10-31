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
