"""Migration EOL source admission, never a claim that an old binary was rebuilt."""
import copy
import hashlib
import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("migration_eol_build", ROOT / "build_native.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
PATCH = "migration-checksum-eol-0164.patch"
ARCHIVED = ROOT / "native/runtime-overlays-0.6.8-29ffae4e.json"
PATHS = {
    "codex-rs/state/src/migrations.rs",
    "codex-rs/state/src/sqlite.rs",
    "codex-rs/state/src/migrations_tests.rs",
}


def test_shared_eol_source_promotion_preserves_exact_068_recipe_and_assets():
    raw = ARCHIVED.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "29ffae4eeae3733592626ea1cc0c8154867006af4e761f61cb3f03e17dac9fca"
    old = json.loads(raw)
    assert raw == (ROOT / "native/guardian-provider-transport-0164-candidate.json").read_bytes()
    current = build.runtime_recipe()
    # Later source admissions append after the exact historical Guardian prefix.
    # Preserve the whole old recipe and require the complete reviewed suffix.
    assert len(old["overlays"]) == 26
    prefix_count = len(old["overlays"])
    assert current["overlays"][:prefix_count] == old["overlays"]
    suffix = current["overlays"][prefix_count:]
    assert [item["file"] for item in suffix] == [
        PATCH, "restored-call-capacity-0164.patch", "capacity-cancel-owned-commit-0164.patch",
        "windows-host-boundaries-0164.patch", "capacity-historical-typed-projection-0164.patch",
        "windows-service-station-bootstrap-0164.patch", "capacity-historical-text-projection-0164.patch",
        "capacity-irreducible-pair-fixture-0164.patch",
        "capacity-token-budget-old-gap-continuation-0164.patch",
        "windows-interactive-runner-bootstrap-0164.patch",
        "windows-ancestor-metadata-synchronize-0164.patch",
        "windows-managed-metadata-provision-0164.patch",
        "windows-managed-admin-maintenance-0164.patch",
        "windows-curated-plugin-git-atomic-spawn-0164.patch",
        "windows-plugin-git-atomic-complete-0164.patch",
    ]
    for target in build.PLATFORM_ASSETS:
        known = {row["path"]: row["after_sha256"]
                 for item in old["overlays"]
                 for row in item["changes_by_target"].get(target, [])}
        for item in suffix:
            if item["file"] in {"windows-host-boundaries-0164.patch", "windows-service-station-bootstrap-0164.patch",
                                "windows-interactive-runner-bootstrap-0164.patch", "windows-ancestor-metadata-synchronize-0164.patch",
                                "windows-managed-metadata-provision-0164.patch", "windows-managed-admin-maintenance-0164.patch",
                                "windows-curated-plugin-git-atomic-spawn-0164.patch",
                                "windows-plugin-git-atomic-complete-0164.patch"}:
                assert set(item["changes_by_target"]) == {"x86_64-pc-windows-msvc"}
                if target != "x86_64-pc-windows-msvc":
                    continue
            else:
                assert set(item["changes_by_target"]) == set(build.PLATFORM_ASSETS)
                first = item["changes_by_target"][next(iter(build.PLATFORM_ASSETS))]
                assert item["changes_by_target"][target] == first
            for row in item["changes_by_target"][target]:
                if row["path"] in known:
                    assert row["before_sha256"] == known[row["path"]]
                elif row["before_sha256"] is None:
                    assert row["path"] not in known
                known[row["path"]] = row["after_sha256"]
    assert {key: value for key, value in current.items() if key != "overlays"} == {
        key: value for key, value in old.items() if key != "overlays"
    }
    leaf = suffix[0]
    assert leaf["file"] == PATCH
    patch = ROOT / "native" / PATCH
    assert build.sha(patch) == leaf["sha256"]
    assert set(leaf["changes_by_target"]) == set(build.PLATFORM_ASSETS)
    patch_paths = {line[6:] for line in patch.read_text(encoding="utf-8").splitlines() if line.startswith("+++ b/")}
    assert patch_paths == PATHS
    targets = list(build.PLATFORM_ASSETS)
    assert all(leaf["changes_by_target"][target] == leaf["changes_by_target"][targets[0]] for target in targets)
    for target in targets:
        assert {row["path"] for row in leaf["changes_by_target"][target]} == PATHS
        assert all(row["before_sha256"] != row["after_sha256"] for row in leaf["changes_by_target"][target])
        name = "runtime.json" if target == "aarch64-apple-darwin" else "runtime-" + target + ".json"
        historical = json.loads((ROOT / "native/rollback/0.6.8" / name).read_bytes())
        assert historical["distribution_version"] == "0.6.8" and historical["status"] == "ready"
        assert historical["native_recipe_id"] == build.native_recipe_identity(target, old)
        assert historical["native_recipe_id"] != build.native_recipe_identity(target, current)


@pytest.mark.parametrize("problem", ["omit", "reorder", "duplicate", "missing_target", "unknown_overlay"])
def test_active_eol_recipe_cannot_silently_drop_or_retarget_compatibility(tmp_path, monkeypatch, problem):
    current = copy.deepcopy(build.runtime_recipe())
    index = next(i for i,item in enumerate(current["overlays"]) if item["file"] == PATCH)
    if problem == "omit":
        current["overlays"].pop(index)
    elif problem == "reorder":
        current["overlays"][index-1:index+1] = reversed(current["overlays"][index-1:index+1])
    elif problem == "duplicate":
        current["overlays"].append(copy.deepcopy(current["overlays"][index]))
    elif problem == "missing_target":
        current["overlays"][index]["changes_by_target"].pop("x86_64-unknown-linux-gnu")
    else:
        current["overlays"][index]["file"] = "unreviewed-migration.patch"
    source = tmp_path / "source"
    (source / "native").mkdir(parents=True)
    (source / "native/runtime-overlays.json").write_bytes(json.dumps(current).encode())
    monkeypatch.setattr(build, "SOURCE", source)
    with pytest.raises(ValueError):
        build.runtime_recipe()


@pytest.mark.parametrize("target", list(build.PLATFORM_ASSETS))
def test_published_068_binary_cannot_be_qualified_as_eol_fixed_package(tmp_path, monkeypatch, target):
    qualification_spec = importlib.util.spec_from_file_location("migration_old_package_qualification", ROOT / "ci/qualify_native.py")
    native = importlib.util.module_from_spec(qualification_spec)
    qualification_spec.loader.exec_module(native)
    name = "runtime.json" if target == "aarch64-apple-darwin" else "runtime-" + target + ".json"
    old = json.loads((ROOT / "native/rollback/0.6.8" / name).read_bytes())
    current_recipe_id = build.native_recipe_identity(target)
    assert old["native_recipe_id"] != current_recipe_id
    # A test-only same-distribution-version relabel must still fail the actual
    # installer's recipe guard. The immutable original archive/manifest is untouched.
    installer_spec = importlib.util.spec_from_file_location("migration_old_manifest_installer", ROOT / "install.py")
    installer = importlib.util.module_from_spec(installer_spec)
    installer_spec.loader.exec_module(installer)
    current_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    same_version = {**old, "distribution_version": current_version}
    assert same_version["distribution_version"] == current_version
    assert same_version["native_recipe_id"] == old["native_recipe_id"]
    manifest_path = tmp_path / "same-version-negative-fixture.json"
    manifest_path.write_bytes(json.dumps(same_version).encode())
    system, machine = {
        "aarch64-apple-darwin": ("Darwin", "arm64"),
        "x86_64-unknown-linux-gnu": ("Linux", "x86_64"),
        "x86_64-pc-windows-msvc": ("Windows", "amd64"),
    }[target]
    monkeypatch.setattr(installer.platform, "system", lambda: system)
    monkeypatch.setattr(installer.platform, "machine", lambda: machine)
    with pytest.raises(SystemExit) as rejected_manifest:
        installer.runtime_manifest(manifest_path)
    assert str(rejected_manifest.value) == (
        "The " + target + " native manifest is not qualified for this source/toolchain/patch combination. "
        "Existing immutable stable releases remain usable; finish target qualification before installing this candidate."
    )
    package = tmp_path / "published-068-package"
    package.mkdir()
    (package / "codex-package.json").write_bytes(json.dumps({
        "target": target, "recipeId": old["native_recipe_id"],
        "version": build.VERSION, "upstreamCommit": build.COMMIT,
    }).encode())
    (package / "build-receipt.json").write_bytes(b"{}")
    monkeypatch.setitem(sys.modules, "build_native", build)
    monkeypatch.setattr(build, "build_target", lambda: target)
    with pytest.raises(ValueError) as rejected_package:
        native.package_identity(package)
    assert str(rejected_package.value) == "Package target or recipe does not match this host and checkout"
