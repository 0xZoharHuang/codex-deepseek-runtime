"""Build-time attribution preserves original vendored notices without following aliases."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "cds_native_licenses_test", Path(__file__).resolve().parents[1] / "native_licenses.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def fixture(root, notices):
    root.mkdir()
    manifest = root / "Cargo.toml"
    manifest.write_text('[package]\nname="fixture"\nversion="1.0.0"\n', encoding="utf-8")
    for name, content in notices.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    package = {"id": "registry+https://example.invalid#fixture@1.0.0",
               "name": "fixture", "version": "1.0.0", "source": "registry+fixture",
               "manifest_path": str(manifest), "license": "MIT", "authors": ["fixture"]}
    return package


def collect(tmp_path, package):
    output = tmp_path / "distribution"
    collector.collect(None, output, metadata={"packages": [package]},
                      target="x86_64-pc-windows-msvc")
    item = json.loads((output / "index.json").read_text())["packages"][0]
    return output, item


def test_original_vendored_notices_survive_wrapper_and_unrelated_binary(tmp_path):
    notices = {"LICENSE": b"wrapper summary",
               "vendor/curl/COPYING": b"Original curl copyright and conditions",
               "aws-lc/LICENSE": b"Original AWS-LC composite",
               "aws-lc/third_party/fiat/LICENSE": b"Original fiat authors URL and MIT notice",
               "vendor/third/LICENCE.txt": b"Original British-spelling notice",
               "library-NOTICE.txt": b"Legacy broad root filename",
               "licenses/arbitrary-name.txt": b"Legacy dedicated directory attribution"}
    package = fixture(tmp_path / "crate", {**notices, "vendor/library.so": b"\x7fELFbinary"})
    output, item = collect(tmp_path, package)
    by_source = {row["source_file"]: row for row in item["upstream_notice_sources"]}
    assert set(by_source) == set(notices)
    assert item["README_fallback"] is False
    assert set(item["upstream_notice_files"]) == {row["distributed_file"] for row in by_source.values()}
    import hashlib
    for source, original in notices.items():
        row = by_source[source]
        assert (output / row["distributed_file"]).read_bytes() == original
        assert row["sha256"] == hashlib.sha256(original).hexdigest()
        assert row["size"] == len(original)


@pytest.mark.parametrize("folder", ["licenses", "LICENSES", "third_party/licenses", "third-party/licenses"])
def test_legacy_dedicated_notice_folder_keeps_arbitrary_attribution_names(tmp_path, folder):
    package = fixture(tmp_path / "crate", {folder + "/AUTHORS.txt": b"Original author credits"})
    output, item = collect(tmp_path, package)
    assert len(item["upstream_notice_sources"]) == 1
    row = item["upstream_notice_sources"][0]
    assert row["source_file"] == folder + "/AUTHORS.txt"
    assert (output / row["distributed_file"]).read_bytes() == b"Original author credits"


def test_declared_custom_license_and_readme_fallback_remain_supported(tmp_path):
    package = fixture(tmp_path / "crate", {"legal/custom.txt": b"Declared custom original",
                                         "README.md": b"Readme is not chosen instead"})
    package["license_file"] = "legal/custom.txt"
    output, item = collect(tmp_path, package)
    assert [row["source_file"] for row in item["upstream_notice_sources"]] == ["legal/custom.txt"]
    assert item["README_fallback"] is False
    package = fixture(tmp_path / "fallback-crate", {"README.md": b"Original README copyright"})
    # Preserve the collector's create-new per-crate destination contract.
    output2 = tmp_path / "fallback-output"
    collector.collect(None, output2, metadata={"packages": [package]}, target="x86_64-pc-windows-msvc")
    item2 = json.loads((output2 / "index.json").read_text())["packages"][0]
    assert item2["README_fallback"] is True
    assert (output2 / item2["upstream_notice_files"][0]).read_bytes() == b"Original README copyright"


@pytest.mark.parametrize("magic", [b"MZ", b"\x7fELF", b"PK\x03\x04"])
def test_named_nested_binary_is_rejected_instead_of_published(tmp_path, magic):
    package = fixture(tmp_path / "crate", {"vendor/LICENSE.txt": magic + b"not a notice"})
    with pytest.raises(ValueError, match="Compiled/container"):
        collect(tmp_path, package)


@pytest.mark.parametrize("bound", ["entries", "depth", "files", "aggregate"])
def test_scan_limits_fail_explicitly_without_silent_truncation(tmp_path, monkeypatch, bound):
    package = fixture(tmp_path / "crate", {"a/b/LICENSE": b"first notice",
                                         "a/b/COPYING": b"second notice",
                                         "a/b/NOTICE": b"third notice"})
    settings = {"entries": ("MAX_NOTICE_SCAN_ENTRIES", 2),
                "depth": ("MAX_NOTICE_SCAN_DEPTH", 1),
                "files": ("MAX_CRATE_NOTICE_FILES", 2),
                "aggregate": ("MAX_CRATE_NOTICE_BYTES", 5)}
    monkeypatch.setattr(collector, *settings[bound])
    with pytest.raises(ValueError, match="bounded"):
        collect(tmp_path, package)


def test_large_notice_is_rejected_before_reading_it(tmp_path):
    package = fixture(tmp_path / "crate", {})
    with (Path(package["manifest_path"]).parent / "LICENSE").open("wb") as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="bounded file size"):
        collect(tmp_path, package)


def test_declared_escape_and_missing_notice_are_not_silently_ignored(tmp_path):
    package = fixture(tmp_path / "crate", {"README.md": b"Do not hide declared license failure"})
    outside = tmp_path / "outside-license.txt"
    outside.write_bytes(b"OUTSIDE_SOURCE_MARKER")
    package["license_file"] = "../outside-license.txt"
    with pytest.raises(ValueError, match="escapes its source"):
        collect(tmp_path, package)
    package["license_file"] = "missing.txt"
    with pytest.raises(FileNotFoundError):
        collector.collect(None, tmp_path / "missing-output", metadata={"packages": [package]},
                          target="x86_64-pc-windows-msvc")


def test_output_inside_source_is_rejected_before_any_write(tmp_path):
    package = fixture(tmp_path / "crate", {"LICENSE": b"Original"})
    output = Path(package["manifest_path"]).parent / "generated"
    with pytest.raises(ValueError, match="inside crate source"):
        collector.collect(None, output, metadata={"packages": [package]}, target="x86_64-pc-windows-msvc")
    assert not output.exists()


def test_reparse_attribute_is_rejected_without_python312_junction_helpers(monkeypatch):
    original = Path.stat
    def marked(path, *, follow_symlinks=True):
        if follow_symlinks is False:
            return SimpleNamespace(st_file_attributes=0x400, st_mode=0o100644)
        return original(path)
    monkeypatch.setattr(Path, "stat", marked)
    assert collector._redirected(Path("not-a-real-path"))


def test_redirected_vendor_directory_never_exports_outside_crate(tmp_path):
    package = fixture(tmp_path / "crate", {"LICENSE": b"Wrapper"})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "LICENSE").write_bytes(b"OUTSIDE_SOURCE_MARKER")
    link = Path(package["manifest_path"]).parent / "vendor"
    if os.name == "nt":
        command = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
                                 capture_output=True)
        assert command.returncode == 0, command.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="redirected"):
            collect(tmp_path, package)
        assert not any(path.is_file() and b"OUTSIDE_SOURCE_MARKER" in path.read_bytes()
                       for path in (tmp_path / "distribution").rglob("*"))
    finally:
        # Remove only the exact owned link; never recurse into its target.
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
    assert (outside / "LICENSE").read_bytes() == b"OUTSIDE_SOURCE_MARKER"
