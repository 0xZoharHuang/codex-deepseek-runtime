"""QA-only preparation boundaries; no native provisioner or model is executed."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_acceptance_fixture", ROOT / "windows_acceptance_setup.py")
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_manifest(tmp_path, monkeypatch):
    parent = tmp_path / "admission"; parent.mkdir()
    queue = parent / "queue"; queue.mkdir()
    homes = tmp_path / "fixtures"; homes.mkdir()
    home = homes / "home"; home.mkdir()
    entry = tmp_path / "immutable-entry.exe"; entry.write_bytes(b"NONEXECUTABLE_ENTRY")
    verifier = tmp_path / "owned-verifier.py"; verifier.write_text("# frozen QA")
    native = tmp_path / "package/bin/codex.exe"; native.parent.mkdir(parents=True)
    files = [native, native.with_name("codex-windows-sandbox-setup.exe"),
             native.with_name("codex-command-runner.exe"),
             native.parent.parent / "codex-resources/codex-windows-sandbox-setup.exe",
             native.parent.parent / "codex-resources/codex-command-runner.exe",
             native.parent.parent / "codex-package.json"]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"NONEXECUTABLE_PACKAGE_FIXTURE")
    value = {
        "schema": 1, "run_id": "a" * 32, "queue_root": str(queue),
        "allowed_home_roots": [str(homes)], "native_binary": str(native),
        "native_sha256": sha(native), "native_files": {str(p): sha(p) for p in files},
        "installed_entry": str(entry), "installed_entry_sha256": sha(entry),
        "target_sid": "S-1-5-21-1-2-3-1001", "target_user": "FIXTURE\\fixture",
        "session_id": 1, "requester_executables": {},
        "verifier_sources": {str(verifier): sha(verifier)},
        "worker_timeout_seconds": 60, "request_timeout_seconds": 30, "max_homes": 2,
    }
    path = parent / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(qa, "IS_WINDOWS", True)
    monkeypatch.setattr(sys, "argv", [str(verifier)])
    return value, path, home, entry


def test_opt_in_is_disabled_without_a_manifest(tmp_path, monkeypatch):
    monkeypatch.delenv(qa.MANIFEST_ENV, raising=False)
    assert qa.configure_fixture_home(tmp_path, entry=tmp_path / "absent") is False
    assert qa.prepare_initialized_home(tmp_path, {}, entry=tmp_path / "absent") is None


@pytest.mark.parametrize("fault", ["digest", "field", "queue", "sidecar", "limit"])
def test_manifest_fails_closed_before_preparation(tmp_path, monkeypatch, fault):
    value, path, home, entry = fixture_manifest(tmp_path, monkeypatch)
    if fault == "field": value["unknown"] = True
    if fault == "queue": value["queue_root"] = str(tmp_path)
    if fault == "sidecar": value["native_files"] = {}
    if fault == "limit": value["max_homes"] = 1000
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        qa._read_manifest(path, "0" * 64 if fault == "digest" else sha(path))
    assert not list(Path(value["queue_root"]).glob("*.request.json"))


def test_only_declared_entry_and_tool_verifier_can_prepare(tmp_path, monkeypatch):
    value, path, home, entry = fixture_manifest(tmp_path, monkeypatch)
    monkeypatch.setattr(qa, "_configured_manifest", lambda: value)
    config = home / "config.toml"
    original = 'sandbox_mode="read-only"\napproval_policy="on-request"\n'
    config.write_text(original)
    assert qa.configure_fixture_home(tmp_path / "outside", entry=tmp_path / "official.exe") is False
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "read-only-query.py")])
    assert qa.configure_fixture_home(home, entry=entry) is False
    assert config.read_text() == original
    monkeypatch.setattr(sys, "argv", [next(iter(value["verifier_sources"]))])
    assert qa.configure_fixture_home(home, entry=entry)
    import tomllib
    parsed = tomllib.loads(config.read_text())
    assert parsed["sandbox_mode"] == "read-only" and parsed["approval_policy"] == "on-request"
    assert parsed["windows"]["sandbox"] == "elevated"
    with pytest.raises(ValueError, match="outside"):
        qa.configure_fixture_home(tmp_path, entry=entry)
    config.write_text(original + '[windows]\nsandbox="unelevated"\n')
    with pytest.raises(ValueError, match="explicit backend"):
        qa.configure_fixture_home(home, entry=entry)
    assert 'sandbox="unelevated"' in config.read_text()


@pytest.mark.parametrize("directory,name", [
    (".sandbox", "setup_marker.json"),
    (".sandbox", "sandbox_users.json"),
    (".sandbox-secrets", "namespace_owner.json"),
    (".sandbox-secrets", "sandbox_users.json"),
])
def test_existing_namespace_without_marker_is_not_new_ownership(tmp_path, directory, name):
    root = tmp_path / directory; root.mkdir()
    target = root / name; target.write_bytes(b"DO_NOT_ADOPT")
    with pytest.raises(ValueError, match="pre-existing"):
        qa._require_fresh_namespace(tmp_path)
    assert target.read_bytes() == b"DO_NOT_ADOPT"


@pytest.mark.parametrize("flags", [[], ["-I", "-X", "utf8"], ["-B", "-Xutf8"]])
def test_frozen_script_is_the_python_program_not_an_argument(tmp_path, flags):
    script = tmp_path / "verifier.py"; script.write_text("# fixture")
    assert qa._python_script(["python", *flags, str(script), "--output", "fixture"]) == str(script)
    for mode in ("-c", "-m"):
        with pytest.raises(ValueError, match="direct frozen"):
            qa._python_script(["python", mode, "untrusted", str(script)])


@pytest.mark.parametrize("failure", ["native", "ownership-receipt"])
def test_failed_attempt_blocks_alias_retry_and_remains_in_cleanup_journal(tmp_path, monkeypatch, failure):
    value, path, home, entry = fixture_manifest(tmp_path, monkeypatch)
    queue = Path(value["queue_root"])
    alias = Path(str(home).upper()) if os.name == "nt" else home / ".." / home.name
    for index, requested in enumerate((home, alias), 1):
        request_id = f"{index:032x}"
        (queue / (request_id + ".request.json")).write_text(json.dumps({
            "run_id": value["run_id"], "request_id": request_id, "action": "provision",
            "home": str(requested), "pid": 123, "created": 1.0,
        }))
    monkeypatch.setattr(qa, "_token", lambda pid: {"sid": value["target_sid"], "elevated": True, "session_id": 1})
    monkeypatch.setattr(qa, "_private_queue", lambda manifest: None)
    monkeypatch.setattr(qa, "_target_user", lambda manifest: None)
    monkeypatch.setattr(qa, "_validate_request", lambda manifest, request, identifier: (Path(request["home"]), {}))
    calls = []
    def fail(manifest, requested, output):
        calls.append(str(requested))
        if failure == "native":
            raise RuntimeError("injected native setup failure")
        return {"marker_sha256": "0" * 64, "exit_code": 0}
    monkeypatch.setattr(qa, "_provision", fail)
    write = qa._write_new
    def write_receipt(path, value):
        if failure == "ownership-receipt" and Path(path).name == "ownership.json":
            raise OSError("injected ownership receipt write failure")
        return write(path, value)
    monkeypatch.setattr(qa, "_write_new", write_receipt)
    monkeypatch.setattr(qa.time, "sleep", lambda seconds: (queue.parent / "stop.request").write_text("owned stop"))
    assert qa.worker(path, sha(path)) == 0
    assert calls == [str(home)]
    responses = [json.loads(p.read_text()) for p in sorted(queue.glob("*.response.json"))]
    assert [x["status"] for x in responses] == ["failed", "failed"]
    assert responses[1]["error_type"] == "ValueError"
    final = json.loads((queue.parent / "worker-finished.json").read_text())
    assert list(final["attempted"]) == final["namespace_cleanup_pending"]
    assert len(final["attempted"]) == 1 and not final["prepared"]
    attempts = list(queue.parent.glob("provision-*/attempt.json"))
    assert len(attempts) == 1 and json.loads(attempts[0].read_text())["namespace_cleanup_pending"]


@pytest.mark.parametrize("kind", ["natural", "natural_gone", "owned_failure", "uncertain_live", "uncertain_gone", "rejected", "observer_error", "timeout", "missing_birth"])
def test_private_high_safe_observer_signals_only_proven_owned(tmp_path, monkeypatch, kind):
    import ci.qualify_native as observer
    import codex_deepseek.processes as processes
    value, path, home, entry = fixture_manifest(tmp_path, monkeypatch)
    output = tmp_path / "output";output.mkdir()
    live = {2}
    signals = []
    parent = type("OwnPopen", (), {})()
    parent.pid = 1
    parent.returncode = None
    parent.poll_calls = 0
    parent.kills = 0
    def poll():
        parent.poll_calls += 1
        if kind in {"timeout", "missing_birth"}:
            return parent.returncode
        if parent.poll_calls > 1:
            parent.returncode = 9 if kind == "owned_failure" else 0
        return parent.returncode
    parent.poll = poll
    def kill():
        parent.kills += 1
        parent.returncode = -1
    parent.kill = kill
    def wait(timeout):
        assert timeout == 10
        if parent.returncode is None: parent.returncode = 0
        return parent.returncode
    parent.wait = wait
    argv = []
    def popen(command, **kwargs):
        argv.append(command)
        return parent
    monkeypatch.setattr(qa.subprocess, "Popen", popen)
    monkeypatch.setattr(processes, "identity", lambda pid: None if kind == "missing_birth" else "own:1")
    monkeypatch.setattr(processes, "matches", lambda pid, stamp: pid in live and stamp == ("own:2" if pid == 2 else "foreign:3"))
    def terminate(pid, stamp, timeout):
        assert pid == 2 and stamp == "own:2" and timeout == 10
        signals.append((pid, stamp));live.discard(pid)
    monkeypatch.setattr(processes, "terminate", terminate)
    def child(pid):
        assert pid == 2, "Never inspect or signal an unproved foreign candidate"
        if kind == "natural_gone":
            live.discard(pid)
            raise qa.psutil.NoSuchProcess(pid)
        class Child:
            def wait(self, timeout):
                assert timeout == 10;live.discard(pid)
        return Child()
    monkeypatch.setattr(qa.psutil, "Process", child)
    monkeypatch.setattr(qa.psutil, "pid_exists", lambda pid: pid in live)
    def observe(owned, uncertain, rejected, errors):
        if kind in {"natural", "natural_gone", "owned_failure", "timeout"}:
            owned[(2, "own:2")] = {"pid": 2, "birth": "own:2", "ownership": "stable_direct_parent_edge"}
        if kind in {"uncertain_live", "uncertain_gone"}:
            uncertain[(3, "foreign:3")] = {"pid": 3, "birth": "foreign:3", "reason": "parent_changed_during_collection"}
            if kind == "uncertain_live":live.add(3)
        if kind == "rejected":
            rejected[(3, "foreign:3")] = {"pid": 3, "birth": "foreign:3", "reason": "current_ppid_differs"}
            live.add(3)
        if kind == "observer_error":errors[(1, "own:1")] = {"pid": 1, "error_type": "AccessDenied"}
    monkeypatch.setattr(observer, "_observe_process_family", observe)
    monkeypatch.setattr(qa, "_ready", lambda manifest, home: {"ready": True})
    clock = iter([0, 31] if kind == "timeout" else [0, 0])
    monkeypatch.setattr(qa.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(qa.time, "sleep", lambda seconds: None)
    if kind in {"natural", "natural_gone", "uncertain_gone", "rejected"}:
        assert qa._provision(value, home, output)["ready"]
    else:
        with pytest.raises((RuntimeError, TimeoutError)):qa._provision(value, home, output)
    proof = json.loads((output / "process-cleanup.json").read_text())
    assert argv[0] == [value["native_binary"], "sandbox", "setup", "--elevated", "--user", value["target_user"], "--codex-home", str(home)]
    assert signals == ([(2, "own:2")] if kind in {"owned_failure", "timeout"} else [])
    assert parent.kills == (1 if kind in {"timeout", "missing_birth"} else 0)
    assert proof["unknown_live"] == ([3] if kind == "uncertain_live" else [])
    assert not proof["remaining"]
    assert bool(proof["observation_errors"]) == (kind == "observer_error")
