"""Preserve the Windows-only v0.6.14 boundary and admit exact installer-only successors."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("system,machine,filename", [
    ("Linux", "x86_64", "runtime-x86_64-unknown-linux-gnu.json"),
    ("Darwin", "arm64", "runtime.json"),
])
@pytest.mark.parametrize("entry", ["main", "install"])
def test_unix_candidate_stops_before_any_install_write_or_download(tmp_path, monkeypatch, system, machine, filename, entry):
    spec = importlib.util.spec_from_file_location("unix_candidate_install", ROOT / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setattr(installer.platform, "system", lambda: system)
    monkeypatch.setattr(installer.platform, "machine", lambda: machine)
    root = tmp_path / "untouched-installation"
    historical = ROOT / "native/rollback/0.6.14" / filename
    monkeypatch.setattr(sys, "argv", ["install.py", "--root", str(root), "--native-manifest", str(historical)])
    writes = []
    def unexpected(*args, **kwargs):
        writes.append((args, kwargs))
        raise AssertionError("Unqualified Unix source must fail before writes or downloads")
    for name in ("native_storage", "private_directory", "native_package", "source_identity", "activate_windows"):
        monkeypatch.setattr(installer, name, unexpected)
    monkeypatch.setattr(installer.urllib.request, "urlopen", unexpected)
    monkeypatch.setattr(installer.subprocess, "run", unexpected)
    with pytest.raises(SystemExit, match="^The native release manifest is not ready; finish and verify the release build first$"):
        installer.main() if entry == "main" else installer.install(root, native_manifest_path=historical)
    assert not writes and not root.exists()
    manifest = json.loads(historical.read_bytes())
    previous = json.loads((ROOT / "native/rollback/0.6.13" / filename).read_bytes())
    assert manifest["status"] == "pending" and manifest["distribution_version"] == "0.6.14"
    assert previous["status"] == "ready" and previous["distribution_version"] == "0.6.13"
    for field in ("archive", "files", "native_recipe_id", "patch_sha256"):
        assert manifest[field] == previous[field]
    assert manifest["qualification"]["source_tag"] == "v0.6.13"



@pytest.mark.parametrize("system,machine,filename,previous_version", [
    ("Windows", "AMD64", "runtime-x86_64-pc-windows-msvc.json", "0.6.14"),
    ("Linux", "x86_64", "runtime-x86_64-unknown-linux-gnu.json", "0.6.13"),
    ("Darwin", "arm64", "runtime.json", "0.6.13"),
])
def test_installer_only_release_selects_entire_qualified_native_component(monkeypatch, system, machine, filename, previous_version):
    spec = importlib.util.spec_from_file_location("candidate_install", ROOT / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setattr(installer.platform, "system", lambda: system)
    monkeypatch.setattr(installer.platform, "machine", lambda: machine)
    previous = json.loads((ROOT / "native/rollback" / previous_version / filename).read_bytes())
    selected = installer.runtime_manifest()
    assert previous["status"] == "ready" and previous["distribution_version"] == previous_version
    assert selected == dict(previous, distribution_version="0.6.15")
    assert selected["native_recipe_id"] == installer.expected_native_recipe(selected["target"])
