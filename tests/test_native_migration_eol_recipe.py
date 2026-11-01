"""Migration EOL source admission, never a claim that an old binary was rebuilt."""
import copy
import hashlib
import importlib.util
import json
import sys
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
    assert current["overlays"][:-1] == old["overlays"]
    assert {key: value for key, value in current.items() if key != "overlays"} == {
        key: value for key, value in old.items() if key != "overlays"
    }
    leaf = current["overlays"][-1]
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
        historical = json.loads((ROOT / "native" / name).read_bytes())
        assert historical["distribution_version"] == "0.6.8" and historical["status"] == "ready"
        assert historical["native_recipe_id"] == build.native_recipe_identity(target, old)
        assert historical["native_recipe_id"] != build.native_recipe_identity(target, current)


@pytest.mark.parametrize("problem", ["omit", "reorder", "duplicate", "missing_target", "unknown_overlay"])
def test_active_eol_recipe_cannot_silently_drop_or_retarget_compatibility(tmp_path, monkeypatch, problem):
    current = copy.deepcopy(build.runtime_recipe())
    if problem == "omit":
        current["overlays"].pop()
    elif problem == "reorder":
        current["overlays"][-2:] = reversed(current["overlays"][-2:])
    elif problem == "duplicate":
        current["overlays"].append(copy.deepcopy(current["overlays"][-1]))
    elif problem == "missing_target":
        current["overlays"][-1]["changes_by_target"].pop("x86_64-unknown-linux-gnu")
    else:
        current["overlays"][-1]["file"] = "unreviewed-migration.patch"
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
    old = json.loads((ROOT / "native" / name).read_bytes())
    package = tmp_path / "published-068-package"
    package.mkdir()
    (package / "codex-package.json").write_bytes(json.dumps({"target": target, "recipeId": old["native_recipe_id"]}).encode())
    (package / "build-receipt.json").write_bytes(b"{}")
    monkeypatch.setitem(sys.modules, "build_native", build)
    monkeypatch.setattr(build, "build_target", lambda: target)
    with pytest.raises(ValueError, match="Package target or recipe"):
        native.package_identity(package)
