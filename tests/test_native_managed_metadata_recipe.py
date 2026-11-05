"""P38 source admission; native behavior is qualified by the separate Windows tests."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("managed_metadata_recipe", ROOT / "build_native.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
WIN = "x86_64-pc-windows-msvc"
PATCH = "windows-managed-metadata-provision-0164.patch"


def test_p38_preserves_p37_and_every_unix_source_identity():
    raw = (ROOT / "native/runtime-overlays-pre-managed-metadata-p37.json").read_bytes()
    previous = json.loads(raw)
    active = build.runtime_recipe()
    matches = [i for i, row in enumerate(active["overlays"]) if row["file"] == PATCH]
    assert matches == [37]
    current = {**active, "overlays": active["overlays"][:38]}
    historical_p38 = ROOT / "native/runtime-overlays-pre-managed-admin-p38.json"
    assert build.sha(historical_p38) == "a929e63d2db90670c19f8b45998ca33a8ac53a6f24adec19ddad6cf748160eed"
    assert current == json.loads(historical_p38.read_bytes())
    assert len(previous["overlays"]) == 37 and len(current["overlays"]) == 38
    assert {**current, "overlays": current["overlays"][:-1]} == previous
    leaf = current["overlays"][-1]
    assert leaf["file"] == PATCH and set(leaf["changes_by_target"]) == {WIN}
    assert build.sha(ROOT / "native" / PATCH) == leaf["sha256"]
    assert {row["path"] for row in leaf["changes_by_target"][WIN]} == {
        "codex-rs/windows-sandbox-rs/src/" + name for name in (
            "setup_provisioning.rs", "ancestor_metadata.rs",
            "setup_provisioning/sandbox_users.rs", "setup_provisioning/sandbox_users_tests.rs",
            "elevated/runner_client.rs")}
    for target in set(build.PLATFORM_ASSETS) - {WIN}:
        assert build.native_recipe_identity(target, current) == build.native_recipe_identity(target, previous)
        assert build.overlay_source_hashes(target, current) == build.overlay_source_hashes(target, previous)
    assert build.native_recipe_identity(WIN, current) != build.native_recipe_identity(WIN, previous)


@pytest.mark.parametrize("problem", ["omit", "reorder", "retarget", "extra_unix_target"])
def test_p38_admission_rejects_omission_or_unreviewed_platform_changes(tmp_path, monkeypatch, problem):
    recipe = copy.deepcopy(build.runtime_recipe())
    index = next(i for i, row in enumerate(recipe["overlays"]) if row["file"] == PATCH)
    if problem == "omit":
        recipe["overlays"].pop(index)
    elif problem == "reorder":
        recipe["overlays"][index-1:index+1] = reversed(recipe["overlays"][index-1:index+1])
    elif problem == "retarget":
        leaf = recipe["overlays"][index]
        leaf["changes_by_target"]["x86_64-unknown-linux-gnu"] = leaf["changes_by_target"].pop(WIN)
    else:
        leaf = recipe["overlays"][index]
        leaf["changes_by_target"]["x86_64-unknown-linux-gnu"] = copy.deepcopy(leaf["changes_by_target"][WIN])
    (tmp_path / "native").mkdir()
    (tmp_path / "native/runtime-overlays.json").write_text(json.dumps(recipe), encoding="utf-8")
    monkeypatch.setattr(build, "SOURCE", tmp_path)
    with pytest.raises(ValueError):
        build.runtime_recipe()
