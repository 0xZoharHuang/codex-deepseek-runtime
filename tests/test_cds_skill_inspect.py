"""Hermetic CDS preflight tests; Windows layouts are not Windows execution proof."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PureWindowsPath
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from unittest import mock

import pytest


HELPER = Path(__file__).resolve().parents[1] / "skills/cds/scripts/inspect_cds.py"
SPEC = importlib.util.spec_from_file_location("cds_skill_inspect_under_test", HELPER)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

WINDOWS = os.name == "nt"
ENTRY_NAME = "codex-deepseek.exe" if WINDOWS else "codex-deepseek"
ALIAS_NAME = "cds.exe" if WINDOWS else "cds"


@pytest.fixture
def short_temp():
    # Avoid inheriting pytest's long test-name paths on Windows.
    with tempfile.TemporaryDirectory(prefix="cds-test-") as directory:
        yield Path(directory).resolve()


class Installation:
    """Editable local metadata fixture, not authenticated release provenance.

    A Windows fixture has the right names/layout but deliberately contains no
    executable PE binary. Only mocked subprocess tests may use it for execution.
    """
    def __init__(self, parent, name="root with spaces", source_id="a" * 20, body=None):
        self.root = parent / name
        self.release = self.root / "releases" / source_id
        self.environment = self.release / "venv"
        self.entry = self.environment / ("Scripts" if WINDOWS else "bin") / ENTRY_NAME
        self.entry.parent.mkdir(parents=True)
        self.marker = parent / (name.replace(" ", "_") + "-executed")
        if WINDOWS:
            self.entry.write_bytes(b"SIMULATED_CDS_EXE_LAYOUT_DO_NOT_EXECUTE\n")
        else:
            payload = self.release / "fixture_payload.py"
            payload.write_text(body or (
                "import json,os,sys\nfrom pathlib import Path\n"
                f"Path({str(self.marker)!r}).write_text('executed')\n"
                "print(json.dumps({'argv':sys.argv[1:]}))\n"
            ), encoding="utf-8")
            self.entry.write_text("#!/bin/sh\nexec " + shlex.join([
                sys.executable, "-I", str(payload)
            ]) + ' "$@"\n', encoding="utf-8")
        self.entry.chmod(0o755)
        self.package = self.root / "native" / ("b" * 24)
        self.package.mkdir(parents=True)
        self.metadata = {
            "source_id": source_id, "release_id": source_id,
            "release_environment": str(self.environment),
            "console_script_sha256": hashlib.sha256(self.entry.read_bytes()).hexdigest(),
            "native_package": str(self.package), "native_version": "0.164.0-alpha.7",
            "model": "deepseek-example", "unrelated_secret_field": "DO_NOT_PRINT_THIS_SENTINEL",
        }
        self.write_metadata()
        self.manifest = {"distribution": "codex-deepseek-runtime", "status": "ready",
                         "upstream_version": "0.164.0-alpha.7"}
        self.write_manifest()

    def write_metadata(self):
        data = json.dumps(self.metadata)
        for path in [self.release / "installation.json", self.root / "installation.json"]:
            path.write_text(data, encoding="utf-8")

    def write_manifest(self):
        (self.release / "native-runtime.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def activated_copy(self, directory):
        directory.mkdir(parents=True)
        destination = directory / ENTRY_NAME
        shutil.copy2(self.entry, destination)
        return destination


def symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if WINDOWS and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks permission to create this symlink fixture")
        raise
    except NotImplementedError:
        pytest.skip("This Python/filesystem does not support symlink fixtures")


def run_main(arguments):
    output = io.StringIO()
    with redirect_stdout(output):
        code = probe.main(arguments)
    return code, json.loads(output.getvalue()), output.getvalue()


def assert_rejected(arguments, expected_code):
    with mock.patch.object(probe.subprocess, "run", side_effect=AssertionError("An unrecognized entry was executed")) as execute:
        code, result, _ = run_main([*arguments, "--offline-native", "version"])
    execute.assert_not_called()
    assert code == 2
    assert result["diagnostic"]["code"] == expected_code


def test_inspection_does_not_execute_or_disclose_unselected_metadata(short_temp):
    installed = Installation(short_temp)
    with mock.patch.object(probe.subprocess, "run") as execute:
        code, result, raw = run_main(["--entry", str(installed.entry)])
    execute.assert_not_called()
    assert code == 0
    assert result["connection"] == "not_tested"
    assert result["publisher_authentication"] == "not_performed"
    assert result["source_hash_authenticity"] == "not_performed"
    assert result["full_native_package_verification"] == "not_performed"
    assert "unrelated_secret_field" not in raw and "DO_NOT_PRINT_THIS_SENTINEL" not in raw


def test_canonical_activation_copy_matches_immutable_entry(short_temp):
    installed = Installation(short_temp)
    activated = installed.activated_copy(short_temp / "User bin with spaces")
    identity = probe.inspect_one(activated, root=installed.root)
    assert Path(identity["immutable_entry"]) == installed.entry
    assert Path(identity["resolved_entry"]) == activated
    assert identity["launcher_sha256"] == installed.metadata["console_script_sha256"]


def test_relative_cds_symlink_matches_canonical_activation_copy(short_temp):
    installed = Installation(short_temp)
    activated = installed.activated_copy(short_temp / "bin")
    alias = activated.parent / ALIAS_NAME
    symlink(alias, activated.name)
    identity = probe.inspect_one(alias, root=installed.root)
    assert Path(identity["immutable_entry"]) == installed.entry
    assert Path(identity["resolved_entry"]) == activated


def test_windows_pure_path_case_and_escape_rules():
    root = PureWindowsPath(r"C:\Users\Example User\AppData\Local\codex-deepseek")
    environment = root / "releases" / ("a" * 20) / "venv"
    _, entry = probe.declared_layout(root, "a" * 20, "a" * 20,
                                     PureWindowsPath(str(environment).upper()), windows=True)
    assert entry.name == "codex-deepseek.exe" and entry.parent.name == "Scripts"
    for invalid in [PureWindowsPath(r"C:relative\venv"), PureWindowsPath(r"D:\foreign\venv"),
                    environment / ".." / "venv"]:
        with pytest.raises(probe.Diagnostic) as error:
            probe.declared_layout(root, "a" * 20, "a" * 20, invalid, windows=True)
        assert error.value.code == "path_escape"


@pytest.mark.parametrize("name", [ALIAS_NAME, "codex.exe" if WINDOWS else "codex"])
def test_foreign_cds_and_vanilla_codex_are_not_executed(short_temp, name):
    installed = Installation(short_temp)
    foreign = short_temp / name
    shutil.copy2(installed.entry, foreign)
    assert_rejected(["--entry", str(foreign), "--root", str(installed.root)], "unrecognized_entry")
    assert not installed.marker.exists()


def test_foreign_canonical_name_with_wrong_hash_is_not_executed(short_temp):
    installed = Installation(short_temp)
    foreign = installed.activated_copy(short_temp / "foreign")
    with foreign.open("ab") as stream:
        stream.write(b"foreign replacement\n")
    assert_rejected(["--entry", str(foreign), "--root", str(installed.root)], "launcher_mismatch")


def test_incomplete_and_foreign_metadata_do_not_execute(short_temp):
    installed = Installation(short_temp)
    (installed.release / "installation.json").write_text(json.dumps({
        "console_script_sha256": installed.metadata["console_script_sha256"]
    }), encoding="utf-8")
    assert_rejected(["--entry", str(installed.entry)], "invalid_metadata")
    installed.write_metadata()
    installed.manifest["distribution"] = "vanilla-codex"
    installed.write_manifest()
    assert_rejected(["--entry", str(installed.entry)], "foreign_distribution")


def test_root_metadata_conflict_and_explicit_root_conflict(short_temp):
    installed = Installation(short_temp)
    activated = installed.activated_copy(short_temp / "bin")
    (installed.root / "installation.json").write_text(json.dumps({
        **installed.metadata, "model": "conflicting-model"
    }), encoding="utf-8")
    assert_rejected(["--entry", str(activated), "--root", str(installed.root)], "metadata_conflict")
    other_root = short_temp / "other-root"
    other_root.mkdir()
    assert_rejected(["--entry", str(installed.entry), "--root", str(other_root)], "root_conflict")


def test_declared_directory_escapes_are_not_executed(short_temp):
    installed = Installation(short_temp)
    installed.metadata["release_environment"] = str(short_temp / "outside/venv")
    installed.write_metadata()
    assert_rejected(["--entry", str(installed.entry)], "path_escape")
    installed.metadata["release_environment"] = str(installed.environment)
    outside = short_temp / "outside-native"
    outside.mkdir()
    installed.metadata["native_package"] = str(outside)
    installed.write_metadata()
    assert_rejected(["--entry", str(installed.entry)], "path_escape")


def test_native_package_symlink_cannot_escape_root(short_temp):
    installed = Installation(short_temp)
    outside = short_temp / "outside-native"
    outside.mkdir()
    alias = installed.root / "native/escape"
    symlink(alias, outside, directory=True)
    installed.metadata["native_package"] = str(alias)
    installed.write_metadata()
    assert_rejected(["--entry", str(installed.entry)], "path_escape")


def test_metadata_symlink_is_not_followed(short_temp):
    installed = Installation(short_temp)
    metadata = installed.release / "installation.json"
    metadata.unlink()
    target = short_temp / "not-installation-metadata.json"
    target.write_text("DO_NOT_READ_AS_INSTALLATION_METADATA", encoding="utf-8")
    symlink(metadata, target)
    assert_rejected(["--entry", str(installed.entry)], "metadata_symlink")


def test_discovered_conflict_and_missing_entries_do_not_execute(short_temp):
    first = Installation(short_temp, name="first")
    second = Installation(short_temp, name="second", source_id="b" * 20)
    with mock.patch.object(probe, "discover", return_value=[str(first.entry), str(second.entry)]):
        assert_rejected([], "entry_conflict")
    with mock.patch.object(probe.shutil, "which", return_value=None):
        assert_rejected([], "entry_not_found")
    assert_rejected(["--entry", str(short_temp / ENTRY_NAME)], "entry_missing")


def test_mocked_subprocess_preserves_space_path_argv_and_child_environment(short_temp):
    installed = Installation(short_temp)
    identity = probe.inspect_one(installed.entry)
    env = {"HOME": str(short_temp), "PATH": os.defpath,
           "OPENROUTER_API_KEY": "NONFUNCTIONAL_TEST_SENTINEL", "CDS_TEST": "kept"}
    before = dict(env)
    completed = subprocess.CompletedProcess([], 0, "offline help\n", "")
    with mock.patch.object(probe.subprocess, "run", return_value=completed) as execute:
        result = probe.offline_native(identity, "help", timeout=3, environ=env)
    args, kwargs = execute.call_args
    assert args == ([str(installed.entry), "--help"],)
    assert kwargs["shell"] is False and kwargs["timeout"] == 3
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["env"] == {"HOME": str(short_temp), "PATH": os.defpath, "CDS_TEST": "kept"}
    assert env == before and result["exit_code"] == 0


def test_offline_timeout_and_action_whitelist_without_executing_fixture(short_temp):
    installed = Installation(short_temp)
    identity = probe.inspect_one(installed.entry)
    timeout = subprocess.TimeoutExpired([str(installed.entry), "--version"], 0.1)
    with mock.patch.object(probe.subprocess, "run", side_effect=timeout) as execute:
        with pytest.raises(probe.Diagnostic) as error:
            probe.offline_native(identity, "version", timeout=0.1, environ={})
    assert error.value.code == "offline_timeout" and execute.call_args.kwargs["timeout"] == 0.1
    with mock.patch.object(probe.subprocess, "run") as execute:
        with pytest.raises(probe.Diagnostic) as error:
            probe.offline_native(identity, "doctor")
        assert error.value.code == "invalid_action"
        execute.assert_not_called()


@pytest.mark.skipif(WINDOWS, reason="POSIX shell/shebang fixture; real Windows native execution is a separate acceptance check")
def test_posix_actual_space_path_and_exact_version_argument(short_temp):
    installed = Installation(short_temp)
    result = probe.offline_native(probe.inspect_one(installed.entry), "version", environ={"PATH": os.defpath})
    assert result["exit_code"] == 0
    assert json.loads(result["stdout"]) == {"argv": ["--version"]}
    assert installed.marker.read_text(encoding="utf-8") == "executed"


@pytest.mark.skipif(WINDOWS, reason="POSIX shell/shebang timeout fixture; not Windows process execution evidence")
def test_posix_actual_offline_timeout_is_bounded(short_temp):
    installed = Installation(short_temp, body="import time\ntime.sleep(3)\n")
    started = time.monotonic()
    with pytest.raises(probe.Diagnostic) as error:
        probe.offline_native(probe.inspect_one(installed.entry), "version", timeout=0.1, environ={"PATH": os.defpath})
    assert error.value.code == "offline_timeout"
    assert time.monotonic() - started < 2


def test_default_windows_root_uses_supplied_localappdata(short_temp):
    localappdata = short_temp / "User LocalAppData with spaces"
    assert probe.default_root(windows=True, home=short_temp, environ={"LOCALAPPDATA": str(localappdata)}) == localappdata / "codex-deepseek"
