"""P41 closes the Windows plugin Git boundary without changing Unix recipes."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from test_native_managed_metadata_recipe import WIN, build

ROOT = Path(__file__).resolve().parents[1]
P41 = "windows-plugin-git-atomic-complete-0164.patch"


def test_p41_is_a_windows_only_extension_of_the_exact_p40_recipe():
    current = build.runtime_recipe()
    previous = {**current, "overlays": current["overlays"][:-1]}
    previous_bytes = (json.dumps(previous, indent=2) + "\n").encode("utf-8")
    assert hashlib.sha256(previous_bytes).hexdigest() == "49305fd55484e9d724b1fda10a81f459c98f3b094eb052e27cdbae4d7c02518e"
    assert build.native_recipe_identity(WIN, previous) == "4da3c0573311969b4fea3911d2c055aa5b2305e0d76e938083554d1fe58447f2"
    leaf = current["overlays"][-1]
    assert leaf["file"] == P41 and leaf["sha256"] == build.sha(ROOT / "native" / P41)
    assert set(leaf["changes_by_target"]) == {WIN}
    assert build.native_recipe_identity(WIN, current) != build.native_recipe_identity(WIN, previous)
    assert {row["path"] for row in leaf["changes_by_target"][WIN]} == {
        "codex-rs/core-plugins/src/git_policy.rs",
        "codex-rs/core-plugins/src/loader.rs",
        "codex-rs/core-plugins/src/marketplace_add/install.rs",
        "codex-rs/core-plugins/src/marketplace_upgrade/git.rs",
        "codex-rs/core-plugins/src/startup_sync.rs",
        "codex-rs/utils/pty/src/win/background_output.rs",
    }
    for target in set(build.PLATFORM_ASSETS) - {WIN}:
        assert build.native_recipe_identity(target, current) == build.native_recipe_identity(target, previous)
        assert build.overlay_source_hashes(target, current) == build.overlay_source_hashes(target, previous)


@pytest.mark.parametrize("drift", ["omitted", "reordered", "retargeted"])
def test_p41_rejects_omission_order_or_nonwindows_target(tmp_path, monkeypatch, drift):
    recipe = copy.deepcopy(build.runtime_recipe())
    if drift == "omitted":
        recipe["overlays"].pop()
    elif drift == "reordered":
        recipe["overlays"][-2:] = reversed(recipe["overlays"][-2:])
    else:
        targets = recipe["overlays"][-1]["changes_by_target"]
        targets["x86_64-unknown-linux-gnu"] = targets[WIN]
    (tmp_path / "native").mkdir()
    (tmp_path / "native/runtime-overlays.json").write_text(json.dumps(recipe), encoding="utf-8")
    monkeypatch.setattr(build, "SOURCE", tmp_path)
    with pytest.raises(ValueError):
        build.runtime_recipe()
