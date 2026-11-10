"""Controller controls stay independent of mutable work and final model reports."""
import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

import acceptance_assertions as checks
import verify_real_work as verifier
from install import python_entry


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


@pytest.fixture
def work_controls(tmp_path, monkeypatch):
    root = tmp_path / "fixtures" / "jinja"
    (root / "work").mkdir(parents=True)
    (root / "oracle" / "tests").mkdir(parents=True)
    (root / "work" / "module.py").write_text("VALUE = 1\n")
    (root / "oracle" / "tests" / "test_original.py").write_text("def test_original(): pass\n")
    for name in checks.real_work_input_names("jinja"):
        (root / name).write_text("frozen input " + name)
    tools_root = tmp_path / "tools"
    selected = tools_root / "jinja" / "tools311"
    launcher = python_entry(selected);launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"synthetic launcher image")
    cfg = selected / "pyvenv.cfg"
    base = tmp_path / "python-base" / "python.exe";base.parent.mkdir()
    base.write_bytes(b"synthetic base image")
    cfg.write_text("home = " + str(base.parent) + "\nexecutable = " + str(base) + "\n")
    dependency = selected / "library.py";dependency.write_text("DEPENDENCY = 1\n")
    tools_map = {str(path.relative_to(selected).as_posix()): _sha(path)
                 for path in (launcher, cfg, dependency)}
    tools = {"same_bytes": True, "paths": {"selected": {
        "root": str(selected), "python_sha256": _sha(launcher), "files_sha256": tools_map}}}
    work_map = {"module.py": _sha(root / "work" / "module.py")}
    oracle_map = {"tests/test_original.py": _sha(root / "oracle" / "tests" / "test_original.py")}
    sources = {"workspace": work_map, "upstream-heldout": {
        **{"work/" + key: value for key, value in work_map.items()},
        **{"oracle/" + key: value for key, value in oracle_map.items()}}}
    xml_root = tmp_path / "original-xml";xml_root.mkdir()
    baselines, originals = {}, {}
    for suite in sources:
        xml = xml_root / (suite + ".xml")
        xml.write_text('<testsuites><testsuite tests="2">'
            '<testcase classname="tests.original" name="test_one"/>'
            '<testcase classname="tests.original" name="test_two"/>'
            '</testsuite></testsuites>')
        baselines[suite] = checks.pytest_inventory_baseline(
            checks.pytest_execution_evidence(xml), suite=suite,
            source_sha256=_canonical(sources[suite]), tools_sha256=_canonical(tools_map))
        originals[suite] = {"path": str(xml), "sha256": _sha(xml)}
    controls = tmp_path / "controls"
    file = controls / "jinja" / "qualification-controls.json";file.parent.mkdir(parents=True)
    value = {"schema_version": 1, "case": "jinja", "pytest_baselines": baselines,
        "original_xml": originals, "source_files_sha256": sources,
        "input_source_sha256": {key: _canonical(files) for key, files in sources.items()},
        "oracle_files_sha256": oracle_map,
        "fixture_input_sha256": checks.real_work_input_hashes(root, "jinja"),
        "tools_files_sha256": tools_map, "tools_sha256": _canonical(tools_map),
        "interpreter_binding": {
            "launcher": {"path": str(launcher.resolve()), "sha256": _sha(launcher)},
            "pyvenv_cfg": {"path": str(cfg), "sha256": _sha(cfg)},
            "base_executable": {"path": str(base), "sha256": _sha(base)}}}
    monkeypatch.setattr(verifier, "CONTROLS", controls)
    monkeypatch.setattr(verifier, "TOOLS_ROOT", tools_root)
    monkeypatch.setattr(verifier, "RUNTIME_HOME", tmp_path / "runtime-home")
    monkeypatch.setattr(verifier, "workspace_path", lambda case: case / "work")

    def freeze(updated=value):
        file.write_text(json.dumps(updated, sort_keys=True))
        monkeypatch.setenv("CODEX_DEEPSEEK_QA_CONTROLS_SHA256_JINJA", _sha(file))
    freeze()
    return {"root": root, "controls": controls, "file": file, "value": value,
            "tools": tools, "freeze": freeze, "dependency": dependency}


def test_original_controller_controls_load_with_both_full_inventories(work_controls):
    data = work_controls
    value = verifier.load_work_controls(data["root"], "jinja", data["tools"])
    assert set(value["pytest_baselines"]) == {"workspace", "upstream-heldout"}
    assert all(len(baseline["required_cases"]) == 2 for baseline in value["pytest_baselines"].values())


def test_controls_require_external_prelaunch_pin(work_controls, monkeypatch):
    data = work_controls
    monkeypatch.delenv("CODEX_DEEPSEEK_QA_CONTROLS_SHA256_JINJA")
    with pytest.raises(ValueError, match="prelaunch SHA"):
        verifier.load_work_controls(data["root"], "jinja", data["tools"])


def test_original_xml_tampering_cannot_redefine_the_baseline(work_controls):
    data = work_controls
    xml = Path(data["value"]["original_xml"]["workspace"]["path"])
    xml.write_text('<testsuites><testsuite tests="1"><testcase classname="tests.original" name="test_one"/></testsuite></testsuites>')
    with pytest.raises(ValueError, match="pytest inventory"):
        verifier.load_work_controls(data["root"], "jinja", data["tools"])


def test_self_consistent_one_test_inventory_cannot_shrink_the_original_xml(work_controls):
    data = work_controls;value = copy.deepcopy(data["value"])
    baseline = value["pytest_baselines"]["workspace"]
    baseline["required_cases"] = baseline["required_cases"][:1]
    baseline["sha256"] = checks._inventory_digest({key: val for key, val in baseline.items() if key != "sha256"})
    data["freeze"](value)
    with pytest.raises(ValueError, match="pytest inventory"):
        verifier.load_work_controls(data["root"], "jinja", data["tools"])


def test_tools_claiming_same_bytes_still_need_the_full_original_manifest(work_controls):
    data = work_controls;tools = copy.deepcopy(data["tools"])
    data["dependency"].write_text("DEPENDENCY = 'changed'\n")
    tools["paths"]["selected"]["files_sha256"]["library.py"] = _sha(data["dependency"])
    with pytest.raises(ValueError, match="Selected interpreter"):
        verifier.load_work_controls(data["root"], "jinja", tools)


def test_initial_bad_source_is_rejected_but_a_legitimate_saved_patch_is_allowed(work_controls):
    data = work_controls
    (data["root"] / "work" / "module.py").write_text("VALUE = 'patched'\n")
    with pytest.raises(ValueError, match="Initial task source"):
        verifier.load_work_controls(data["root"], "jinja", data["tools"])
    assert verifier.load_work_controls(data["root"], "jinja", data["tools"], initial=False)


def test_saved_workflow_cannot_adopt_a_changed_oracle_as_its_new_baseline(work_controls):
    data = work_controls
    (data["root"] / "oracle" / "tests" / "test_original.py").write_text("def test_original(): return\n")
    with pytest.raises(ValueError, match="Independent oracle"):
        verifier.load_work_controls(data["root"], "jinja", data["tools"], initial=False)


@pytest.mark.parametrize("initial", [True, False])
def test_frozen_dependency_and_driver_inputs_are_always_bound(work_controls, initial):
    data = work_controls
    (data["root"] / "dependencies.lock").write_text("changed before this turn\n")
    with pytest.raises(ValueError, match="Fixture drivers"):
        verifier.load_work_controls(data["root"], "jinja", data["tools"], initial=initial)


@pytest.mark.parametrize("location", ["physical-work", "runtime-home"])
def test_controls_cannot_live_in_external_writable_work_or_runtime_home(work_controls, monkeypatch, tmp_path, location):
    data = work_controls
    if location == "physical-work":
        physical = tmp_path / "physical-work";physical.mkdir()
        monkeypatch.setattr(verifier, "workspace_path", lambda case: physical)
    else:
        physical = verifier.RUNTIME_HOME;physical.mkdir()
    bad_controls = physical / "controls"
    shutil.copytree(data["controls"], bad_controls)
    monkeypatch.setattr(verifier, "CONTROLS", bad_controls)
    with pytest.raises(ValueError, match="outside"):
        verifier.load_work_controls(data["root"], "jinja", data["tools"], initial=False)


@pytest.mark.parametrize("record", ["pyvenv_cfg", "base_executable"])
def test_venv_configuration_and_actual_base_image_are_early_pinned(work_controls, record):
    data = work_controls
    Path(data["value"]["interpreter_binding"][record]["path"]).write_bytes(b"changed interpreter support")
    with pytest.raises(ValueError, match="Pinned interpreter"):
        verifier.load_work_controls(data["root"], "jinja", data["tools"], initial=False)
