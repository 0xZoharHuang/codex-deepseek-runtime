"""P40 changes the Windows recipe while retaining the exact P39 Unix targets."""
import copy
import json
from pathlib import Path

import pytest

from test_native_managed_metadata_recipe import WIN, build

ROOT = Path(__file__).resolve().parents[1]
P39 = ROOT / "native/runtime-overlays-pre-atomic-plugin-git-p39.json"
P40 = "windows-curated-plugin-git-atomic-spawn-0164.patch"


def test_p40_is_one_windows_overlay_after_the_frozen_p39_recipe():
    assert build.sha(P39) == "567baa9d7506be39aa5a3363bbda02b897a0fd50185705e26df444ee11490c92"
    old = json.loads(P39.read_bytes())
    new = build.runtime_recipe()
    assert {**new, "overlays": new["overlays"][:-1]} == old
    leaf = new["overlays"][-1]
    assert leaf["file"] == P40 and leaf["sha256"] == build.sha(ROOT / "native" / P40)
    assert set(leaf["changes_by_target"]) == {WIN}
    assert build.native_recipe_identity(WIN, old) != build.native_recipe_identity(WIN, new)
    for target in set(build.PLATFORM_ASSETS) - {WIN}:
        assert build.native_recipe_identity(target, old) == build.native_recipe_identity(target, new)
        assert build.overlay_source_hashes(target, old) == build.overlay_source_hashes(target, new)
    paths = {row["path"]: row for row in leaf["changes_by_target"][WIN]}
    assert set(paths) == {
        "codex-rs/core-plugins/src/git_lifetime_windows_tests.rs",
        "codex-rs/core-plugins/src/startup_sync.rs",
        "codex-rs/utils/pty/Cargo.toml",
        "codex-rs/utils/pty/src/win/background_output.rs",
        "codex-rs/utils/pty/src/win/mod.rs",
        "codex-rs/utils/pty/src/win/procthreadattr.rs",
        "codex-rs/utils/pty/src/win/psuedocon.rs",
    }
    assert [p for p, row in paths.items() if row["before_sha256"] is None] == [
        "codex-rs/utils/pty/src/win/background_output.rs"
    ]
    old_paths = build.overlay_source_hashes(WIN, old)
    assert all(paths[p]["before_sha256"] == old_paths[p] for p in paths.keys() & old_paths.keys())


@pytest.mark.parametrize("drift", ["omitted", "wrong_order", "unix"])
def test_p40_contract_rejects_recipe_drift(tmp_path, monkeypatch, drift):
    recipe = copy.deepcopy(build.runtime_recipe())
    if drift == "omitted":
        recipe["overlays"].pop()
    elif drift == "wrong_order":
        recipe["overlays"][-2:] = reversed(recipe["overlays"][-2:])
    else:
        targets = recipe["overlays"][-1]["changes_by_target"]
        targets["x86_64-unknown-linux-gnu"] = targets[WIN]
    (tmp_path / "native").mkdir()
    (tmp_path / "native/runtime-overlays.json").write_text(json.dumps(recipe), encoding="utf-8")
    monkeypatch.setattr(build, "SOURCE", tmp_path)
    with pytest.raises(ValueError):
        build.runtime_recipe()
