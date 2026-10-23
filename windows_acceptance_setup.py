"""Opt-in QA preparation for native Windows sandbox fixtures.

This is an acceptance controller, never imported by the installed adapter. A
Limited verifier requests only ProvisionOnly for its own declared fixture home;
an explicitly started, bounded High worker executes the pinned native command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import psutil

MANIFEST_ENV = "CODEX_DEEPSEEK_TEST_WINDOWS_PREPARE_MANIFEST"
HASH_ENV = "CODEX_DEEPSEEK_TEST_WINDOWS_PREPARE_SHA256"
IS_WINDOWS = os.name == "nt"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read_manifest(path, expected):
    path = Path(path).absolute()
    if path.is_symlink() or path.is_junction() or path.stat().st_nlink != 1:
        raise ValueError("Acceptance manifest must be a regular unlinked file")
    if not re.fullmatch(r"[0-9a-f]{64}", expected or "") or digest(path) != expected:
        raise ValueError("Acceptance preparation manifest identity differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema", "run_id", "queue_root", "allowed_home_roots",
                "native_binary", "native_sha256", "native_files", "installed_entry",
                "installed_entry_sha256", "target_sid", "target_user",
                "session_id", "requester_executables", "verifier_sources",
                "worker_timeout_seconds", "request_timeout_seconds", "max_homes"}
    if set(value) != required or value["schema"] != 1:
        raise ValueError("Unknown acceptance preparation manifest")
    if not re.fullmatch(r"[0-9a-f]{32}", value["run_id"]):
        raise ValueError("Invalid acceptance run identity")
    queue = Path(value["queue_root"])
    if queue != path.parent / "queue" or queue.resolve(strict=True) != queue:
        raise ValueError("Acceptance queue is not the owned physical sibling")
    native = Path(value["native_binary"])
    package = native.parent.parent
    required_files = {str(native), str(native.with_name("codex-windows-sandbox-setup.exe")),
                      str(native.with_name("codex-command-runner.exe")),
                      str(package / "codex-resources/codex-windows-sandbox-setup.exe"),
                      str(package / "codex-resources/codex-command-runner.exe"),
                      str(package / "codex-package.json")}
    if (not required_files <= set(value["native_files"])
            or value["native_files"].get(str(native)) != value["native_sha256"]
            or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in value["native_files"].values())):
        raise ValueError("Native executable, resource sidecars and layout must all be pinned")
    if not (30 <= value["request_timeout_seconds"] <= 300
            and 60 <= value["worker_timeout_seconds"] <= 21600
            and 1 <= value["max_homes"] <= 128):
        raise ValueError("Acceptance preparation bounds are invalid")
    return value


def _configured_manifest():
    path = os.environ.get(MANIFEST_ENV)
    if not path:
        return None
    if not IS_WINDOWS:
        raise ValueError("Windows acceptance preparation requested on another OS")
    return _read_manifest(path, os.environ.get(HASH_ENV, ""))


def _home(manifest, home):
    home = Path(home).absolute()
    roots = [Path(x).absolute() for x in manifest["allowed_home_roots"]]
    if not any(home != root and home.is_relative_to(root) for root in roots):
        raise ValueError("Fixture home is outside this acceptance run")
    if not home.is_dir() or home.resolve(strict=True) != home:
        raise ValueError("Fixture home must be a physical path")
    for path in (home, *home.parents):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Fixture home has a reparse ancestor")
    return home


def managed_entry(manifest, entry):
    path = Path(entry).resolve()
    if path != Path(manifest["installed_entry"]).resolve():
        return False
    if digest(path) != manifest["installed_entry_sha256"]:
        raise ValueError("Immutable acceptance entry changed")
    script = str(Path(sys.argv[0]).resolve())
    expected = manifest["verifier_sources"].get(script)
    if expected is None:
        return False  # This frozen verifier does not exercise sandboxed tools.
    if digest(script) != expected:
        raise ValueError("Acceptance verifier changed after preparation admission")
    return True


def configure_fixture_home(home, *, entry, explicit=False):
    manifest = _configured_manifest()
    if manifest is None or not managed_entry(manifest, entry):
        return False
    home = _home(manifest, home)
    import tomlkit
    path = home / "config.toml"
    if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
        raise ValueError("Fixture configuration is linked")
    document = tomlkit.parse(path.read_text(encoding="utf-8") if path.exists() else "")
    backend = document.get("windows", {}).get("sandbox")
    if backend is not None and backend != "elevated":
        raise ValueError("This elevated acceptance run cannot silently replace an explicit backend")
    if backend is None:
        if explicit:
            raise ValueError("Explicit runtime home must already declare its Windows backend")
        if "windows" not in document:
            document["windows"] = tomlkit.table()
        document["windows"]["sandbox"] = "elevated"
        path.write_bytes(tomlkit.dumps(document).encode("utf-8"))
    return True


def _write_new(path, value):
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise ValueError("Preserving existing acceptance receipt")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def prepare_initialized_home(home, initialized, *, entry):
    manifest = _configured_manifest()
    if manifest is None or not managed_entry(manifest, entry):
        return None
    home = _home(manifest, home)
    actual = initialized.get("codexHome")
    if not isinstance(actual, str) or Path(actual).resolve() != home:
        raise ValueError("Native initialized home differs from the requested fixture")
    request_id = uuid.uuid4().hex
    queue = Path(manifest["queue_root"])
    request = {"run_id": manifest["run_id"], "request_id": request_id,
               "action": "provision", "home": str(home), "pid": os.getpid(),
               "created": psutil.Process().create_time()}
    _write_new(queue / (request_id + ".request.json"), request)
    response = queue / (request_id + ".response.json")
    deadline = time.monotonic() + manifest["request_timeout_seconds"] + 30
    while time.monotonic() < deadline:
        if response.exists():
            value = json.loads(response.read_text(encoding="utf-8"))
            if any(value.get(k) != request[k] for k in ("run_id", "request_id", "home")):
                raise ValueError("Sandbox preparation response belongs to another fixture")
            if value.get("status") != "provisioned":
                raise RuntimeError("Native fixture preparation failed: " + value.get("error_type", "unknown"))
            return value
        time.sleep(0.1)
    raise TimeoutError("Explicit native fixture preparation did not finish; no fallback")


def prepare_terminal_fixture(root, entry):
    if _configured_manifest() is None:
        return
    # Reuse the native initialize handshake without starting a task or model turn.
    from verify_live import Peer
    peer = Peer(Path(root), command=[str(entry)])
    peer.close()


def _token(pid):
    import win32api
    import win32con
    import win32ts
    import win32security
    process = win32api.OpenProcess(0x1000, False, pid)
    token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
    try:
        return {
            "sid": win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]),
            "elevated": bool(win32security.GetTokenInformation(token, win32security.TokenElevation)),
            "session_id": win32ts.ProcessIdToSessionId(pid),
        }
    finally:
        token.Close()
        process.Close()


def _python_script(command):
    values = list(command[1:])
    while values:
        first = values.pop(0)
        if first in {"-I", "-B", "-u", "-E", "-s"}:
            continue
        if first == "-X":
            if not values or values.pop(0) not in {"utf8", "utf8=1"}:
                raise ValueError("Unexpected interpreter option")
            continue
        if first in {"-Xutf8", "-Xutf8=1"}:
            continue
        if first.startswith("-"):
            raise ValueError("A direct frozen verifier script is required")
        return str(Path(first).resolve(strict=True))
    raise ValueError("Requester has no verifier script")


def _target_user(manifest):
    import socket
    import win32net
    import win32security
    value = manifest["target_user"]
    parts = value.split("\\")
    if len(parts) != 2 or parts[0].casefold() != socket.gethostname().casefold():
        raise ValueError("Native setup target must be the explicit local machine user")
    account = win32net.NetUserGetInfo(None, parts[1], 0)
    sid, domain, kind = win32security.LookupAccountName(None, value)
    if (kind != win32security.SidTypeUser or account["name"].casefold() != parts[1].casefold()
            or win32security.ConvertSidToStringSid(sid) != manifest["target_sid"]):
        raise ValueError("Native setup target user differs from the approved SID")


def _validate_request(manifest, request, request_id):
    if set(request) != {"run_id", "request_id", "action", "home", "pid", "created"}:
        raise ValueError("Unknown sandbox preparation request fields")
    if (request["run_id"] != manifest["run_id"] or request["request_id"] != request_id
            or request["action"] != "provision"):
        raise ValueError("Sandbox preparation request identity differs")
    home = _home(manifest, request["home"])
    process = psutil.Process(request["pid"])
    if abs(process.create_time() - request["created"]) >= 0.001:
        raise ValueError("Requester process identity expired")
    identity = _token(process.pid)
    if (identity["sid"] != manifest["target_sid"] or identity["elevated"]
            or identity["session_id"] != manifest["session_id"]):
        raise ValueError("Requester must be the declared Limited interactive identity")
    executable = str(Path(process.exe()).resolve())
    expected = manifest["requester_executables"].get(executable)
    if not expected or digest(executable) != expected:
        raise ValueError("Requester interpreter differs from the frozen QA environment")
    command = process.cmdline()
    script = _python_script(command)
    expected_script = manifest["verifier_sources"].get(script)
    if not expected_script or not Path(script).is_file() or digest(script) != expected_script:
        raise ValueError("Requester is not executing a declared frozen acceptance verifier")
    import tomllib
    config = home / "config.toml"
    policy = home / "runtime-policy/config.toml"
    _home(manifest, policy.parent)
    if (not policy.is_file() or config.is_symlink() or policy.is_symlink()
            or config.stat().st_nlink != 1 or policy.stat().st_nlink != 1):
        raise ValueError("Native wrapper policy is missing or linked")
    if tomllib.loads(config.read_text(encoding="utf-8")).get("windows", {}).get("sandbox") != "elevated":
        raise ValueError("Fixture Windows backend was not explicitly configured")
    return home, identity


def _provision(manifest, home, output):
    native = Path(manifest["native_binary"])
    if digest(native) != manifest["native_sha256"]:
        raise ValueError("Frozen native setup executable changed")
    for name, expected in manifest["native_files"].items():
        if digest(name) != expected:
            raise ValueError("Frozen native setup/runner sidecar changed")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CODEX_", "CDS_", "OPENAI_", "OPENROUTER_", "OTEL_", "PYTHON", "GITHUB_", "ACTIONS_"))
           and k not in {"GH_TOKEN", "GITHUB_TOKEN"}}
    env.update(CODEX_HOME=str(home), CODEX_DEEPSEEK_POLICY_HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    command = [str(native), "sandbox", "setup", "--elevated", "--user",
               manifest["target_user"], "--codex-home", str(home)]
    descendants = {}
    with (output / "native.stdout").open("wb") as stdout, (output / "native.stderr").open("wb") as stderr:
        process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        native_pid = process.pid
        deadline = time.monotonic() + manifest["request_timeout_seconds"]
        failed = True
        try:
            while process.poll() is None:
                try:
                    children = psutil.Process(native_pid).children(recursive=True)
                except psutil.NoSuchProcess:
                    children = []
                for child in children:
                    try:
                        descendants[child.pid] = child.create_time()
                    except psutil.NoSuchProcess:
                        pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("Native ProvisionOnly exceeded the acceptance bound")
                time.sleep(0.1)
            if process.returncode:
                raise RuntimeError("Native ProvisionOnly exited " + str(process.returncode))
            for pid, birth in descendants.items():
                try:
                    child = psutil.Process(pid)
                    if abs(child.create_time() - birth) < 0.001:
                        child.wait(timeout=10)
                except psutil.NoSuchProcess:
                    pass
            failed = False
        finally:
            cleanup_errors = []
            for pid, birth in descendants.items():
                try:
                    child = psutil.Process(pid)
                    if abs(child.create_time() - birth) < 0.001 and failed:
                        child.kill()
                        child.wait(timeout=10)
                except psutil.NoSuchProcess:
                    pass
                except Exception as error:
                    cleanup_errors.append({"pid": pid, "error_type": type(error).__name__})
            try:
                if process.poll() is None:
                    process.kill()
            except Exception as error:
                cleanup_errors.append({"pid": native_pid, "error_type": type(error).__name__})
            try:
                process.wait(timeout=10)
            except Exception as error:
                cleanup_errors.append({"pid": native_pid, "error_type": type(error).__name__})
            remaining = []
            for pid, birth in descendants.items():
                try:
                    child = psutil.Process(pid)
                    if abs(child.create_time() - birth) < 0.001:
                        remaining.append(pid)
                except psutil.NoSuchProcess:
                    pass
                except Exception as error:
                    cleanup_errors.append({"pid": pid, "error_type": type(error).__name__})
            _write_new(output / "process-cleanup.json",
                       {"native_pid": native_pid, "native_exited": process.poll() is not None,
                        "observed_descendants": descendants, "remaining": remaining,
                        "errors": cleanup_errors})
            if cleanup_errors or remaining or process.poll() is None:
                raise RuntimeError("Owned native setup process cleanup is incomplete")
    return {"native_pid": native_pid, "exit_code": process.returncode,
            "native_sha256": manifest["native_sha256"], **_ready(manifest, home)}


def _ready(manifest, home):
    import win32net
    import win32security
    import socket
    marker = home / ".sandbox/setup_marker.json"
    owner_path = home / ".sandbox-secrets/namespace_owner.json"
    for path in (marker, owner_path):
        if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
            raise RuntimeError("Native readiness or ownership metadata missing/linked")
    marker_value = json.loads(marker.read_text(encoding="utf-8"))
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    namespace = owner.get("namespace")
    if (owner.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{64}", namespace or "")
            or owner.get("owner_sid") != manifest["target_sid"]
            or os.path.normcase(owner.get("home", "")) != os.path.normcase(str(home))
            or marker_value.get("version") != 7 or marker_value.get("namespace") != namespace
            or marker_value.get("offline_username") != owner.get("offline")
            or marker_value.get("online_username") != owner.get("online")
            or owner.get("group") != "CodexDeepSeek-" + namespace
            or home.stat().st_dev != owner.get("volume")
            or home.stat().st_ino != int.from_bytes(bytes(owner.get("file_id", [])), "little")):
        raise RuntimeError("Native readiness identity differs from the requested home")
    accounts = {}
    for key in ("offline", "online"):
        data = win32net.NetUserGetInfo(None, owner[key], 0)
        sid, domain, kind = win32security.LookupAccountName(
            None, socket.gethostname() + "\\" + owner[key])
        if kind != win32security.SidTypeUser or data["name"].casefold() != owner[key].casefold():
            raise RuntimeError("Native sandbox principal is not the recorded local user")
        accounts[key] = win32security.ConvertSidToStringSid(sid)
    group_sid, domain, kind = win32security.LookupAccountName(
        None, socket.gethostname() + "\\" + owner["group"])
    if kind != win32security.SidTypeAlias:
        raise RuntimeError("Native namespace group is not a local alias")
    return {"marker_sha256": digest(marker), "namespace": namespace,
            "owner_record_sha256": digest(owner_path), "account_sids": accounts,
            "group_sid": win32security.ConvertSidToStringSid(group_sid)}


def _require_fresh_namespace(home):
    for directory in (home / ".sandbox", home / ".sandbox-secrets"):
        if directory.is_symlink() or directory.is_junction():
            raise ValueError("Sandbox state directory is a reparse point")
        for name in ("setup_marker.json", "sandbox_users.json", "namespace_owner.json"):
            if os.path.lexists(directory / name):
                raise ValueError("Refusing to enroll pre-existing namespace state as a new fixture")


def _private_queue(manifest):
    import win32security
    queue = Path(manifest["queue_root"])
    allowed = {manifest["target_sid"], "S-1-5-18", "S-1-5-32-544"}
    for path in (queue, queue.parent):
        sd = win32security.GetNamedSecurityInfo(
            str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION)
        if not sd.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED:
            raise ValueError("Acceptance queue ACL must be protected")
        acl = sd.GetSecurityDescriptorDacl()
        if acl is None:
            raise ValueError("Acceptance queue cannot have a null DACL")
        for i in range(acl.GetAceCount()):
            ace = acl.GetAce(i)
            if (ace[0][0] != win32security.ACCESS_ALLOWED_ACE_TYPE
                    or win32security.ConvertSidToStringSid(ace[2]) not in allowed):
                raise ValueError("Acceptance queue grants another principal access")



def worker(path, expected):
    if not IS_WINDOWS:
        raise RuntimeError("Windows-only acceptance worker")
    manifest = _read_manifest(path, expected)
    identity = _token(os.getpid())
    if (identity["sid"] != manifest["target_sid"] or not identity["elevated"]
            or identity["session_id"] != manifest["session_id"]):
        raise RuntimeError("Preparation worker must be the declared High interactive identity")
    queue = Path(manifest["queue_root"])
    _private_queue(manifest)
    _target_user(manifest)
    _write_new(queue.parent / "worker-started.json",
               {"pid": os.getpid(), "created": psutil.Process().create_time(),
                "identity": identity, "run_id": manifest["run_id"], "manifest_sha256": expected})
    prepared = {}
    attempted = {}
    deadline = time.monotonic() + manifest["worker_timeout_seconds"]
    while time.monotonic() < deadline and not (queue.parent / "stop.request").exists():
        for path in sorted(queue.glob("*.request.json")):
            request_id = path.name.removesuffix(".request.json")
            if not re.fullmatch(r"[0-9a-f]{32}", request_id):
                continue
            response = queue / (request_id + ".response.json")
            if response.exists():
                continue
            value = {"run_id": manifest["run_id"], "request_id": request_id, "status": "failed"}
            try:
                if path.is_symlink() or path.stat().st_nlink != 1 or path.stat().st_size > 8192:
                    raise ValueError("Request is linked or too large")
                request = json.loads(path.read_text(encoding="utf-8"))
                value["home"] = request.get("home")
                home, requester = _validate_request(manifest, request, request_id)
                key = os.path.normcase(str(home.resolve(strict=True)))
                marker = home / ".sandbox/setup_marker.json"
                if key in prepared:
                    ready = _ready(manifest, home)
                    if any(ready[name] != prepared[key][name] for name in ready if name != "marker_sha256"):
                        raise ValueError("Prepared namespace changed; explicit reconciliation required")
                    proof = {**prepared[key], **ready}
                    prepared[key] = proof
                    value["reused_this_run"] = True
                else:
                    if key in attempted:
                        raise ValueError("Previous preparation failed; explicit reconciliation required")
                    if len(attempted) >= manifest["max_homes"]:
                        raise ValueError("Acceptance home limit reached")
                    _require_fresh_namespace(home)
                    output = queue.parent / ("provision-" + request_id)
                    output.mkdir()
                    attempted[key] = {"request_id": request_id, "output": str(output)}
                    _write_new(output / "attempt.json", {"home": key, "run_id": manifest["run_id"], "native_sha256": manifest["native_sha256"], "namespace_cleanup_pending": True})
                    proof = _provision(manifest, home, output)
                    _write_new(output / "ownership.json",
                               {"home": key, "requester": requester, "run_id": manifest["run_id"], **proof})
                    prepared[key] = proof
                value.update(status="provisioned", **proof)
            except Exception as error:
                value.update(error_type=type(error).__name__, error=str(error))
            _write_new(response, value)
        time.sleep(0.1)
    _write_new(queue.parent / "worker-finished.json",
               {"run_id": manifest["run_id"], "prepared": prepared,
                "reason": "stop_requested" if (queue.parent / "stop.request").exists() else "deadline",
                "attempted": attempted, "namespace_cleanup_pending": list(attempted)})
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    arguments = parser.parse_args()
    raise SystemExit(worker(arguments.worker_manifest, arguments.manifest_sha256))
