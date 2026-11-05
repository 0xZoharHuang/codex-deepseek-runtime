"""P39 source/package admission. Runtime behavior needs the Windows native and App-independent CLI evidence."""
import copy
import json
from pathlib import Path

import pytest

from test_native_managed_metadata_recipe import WIN, build

ROOT = Path(__file__).resolve().parents[1]
P38 = "windows-managed-metadata-provision-0164.patch"
P39 = "windows-managed-admin-maintenance-0164.patch"
P39_SHA = "ba237f557e83847a07823cbc31ee5ea4c637de33da77d591b0b86e2ed7c2620d"
P39_RECIPE = "03a66332e89736c56894aae267cc5cb0541636251792b81d517ce4593ef0b81a"
P38_RECIPE = "b635d73ef2350648716954b69178c42ea0117322aa7aa1ce1a0aa28c707bff92"


def test_p39_is_the_only_change_after_exact_p38_recipe_and_preserves_unix_inputs():
    historical = ROOT / "native/runtime-overlays-pre-managed-admin-p38.json"
    assert build.sha(historical) == "a929e63d2db90670c19f8b45998ca33a8ac53a6f24adec19ddad6cf748160eed"
    previous = json.loads(historical.read_bytes())
    current = build.runtime_recipe()
    assert len(previous["overlays"]) == 38 and len(current["overlays"]) == 39
    assert previous["overlays"][-1]["file"] == P38
    assert {**current, "overlays": current["overlays"][:-1]} == previous
    assert build.native_recipe_identity(WIN, previous) == P38_RECIPE
    assert build.native_recipe_identity(WIN, current) == P39_RECIPE
    leaf = current["overlays"][-1]
    assert leaf["file"] == P39 and leaf["sha256"] == P39_SHA == build.sha(ROOT / "native" / P39)
    assert set(leaf["changes_by_target"]) == {WIN}
    expected = {"no_reparse_dir.rs", "acl_lock.rs", "token.rs", "setup_provisioning.rs", "setup_provisioning/managed_bin_acl.rs"}
    assert {row["path"] for row in leaf["changes_by_target"][WIN]} == {"codex-rs/windows-sandbox-rs/src/" + name for name in expected}
    before = build.overlay_source_hashes(WIN,previous)
    after = build.overlay_source_hashes(WIN,current)
    for row in leaf["changes_by_target"][WIN]:
        if row["path"] in before:
            assert row["before_sha256"] == before[row["path"]]
        assert after[row["path"]] == row["after_sha256"]
    new = [row["path"] for row in leaf["changes_by_target"][WIN] if row["before_sha256"] is None]
    assert new == ["codex-rs/windows-sandbox-rs/src/setup_provisioning/managed_bin_acl.rs"]
    for target in set(build.PLATFORM_ASSETS) - {WIN}:
        assert build.native_recipe_identity(target,current) == build.native_recipe_identity(target,previous)
        assert build.overlay_source_hashes(target,current) == build.overlay_source_hashes(target,previous)
        assert current["required_base_inputs"][target] == previous["required_base_inputs"][target]


@pytest.mark.parametrize("problem", ["omit", "reorder", "duplicate", "retarget", "extra_unix_target"])
def test_p39_wrong_order_or_platform_is_rejected_by_real_reader(tmp_path, monkeypatch, problem):
    recipe = copy.deepcopy(build.runtime_recipe())
    leaf = recipe["overlays"][-1]
    if problem == "omit":
        recipe["overlays"].pop()
    elif problem == "reorder":
        recipe["overlays"][-2:] = reversed(recipe["overlays"][-2:])
    elif problem == "duplicate":
        recipe["overlays"].append(copy.deepcopy(leaf))
    elif problem == "retarget":
        leaf["changes_by_target"]["x86_64-unknown-linux-gnu"] = leaf["changes_by_target"].pop(WIN)
    else:
        leaf["changes_by_target"]["aarch64-apple-darwin"] = copy.deepcopy(leaf["changes_by_target"][WIN])
    (tmp_path / "native").mkdir()
    (tmp_path / "native/runtime-overlays.json").write_text(json.dumps(recipe),encoding="utf-8")
    monkeypatch.setattr(build,"SOURCE",tmp_path)
    with pytest.raises(ValueError):
        build.runtime_recipe()
