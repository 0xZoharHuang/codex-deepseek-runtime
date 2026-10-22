#!/usr/bin/env python3
"""Read-only CDS local-installation preflight; optional offline help/version only.

Local editable metadata is an association/consistency check, not publisher
authentication, a source audit, full native-package verification, or connectivity.
This helper never reads runtime config, auth files, or credential stores.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePath
import re
import shutil
import subprocess
import sys

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_ID = re.compile(r"[0-9a-f]{20}\Z")
MAX_JSON_BYTES = 4 * 1024 * 1024
IDENTITY_FIELDS = ("source_id", "release_id", "release_environment",
                   "console_script_sha256", "native_package", "native_version", "model")


class Diagnostic(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise Diagnostic(code, message)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def default_root(*, windows=None, home=None, environ=None):
    windows = os.name == "nt" if windows is None else windows
    home = Path.home() if home is None else Path(home)
    environ = os.environ if environ is None else environ
    return (Path(environ.get("LOCALAPPDATA", str(home / "AppData/Local"))) / "codex-deepseek"
            if windows else home / ".local/share/codex-deepseek")


def declared_layout(root: PurePath, source_id, release_id, environment: PurePath, *, windows):
    """Pure path validation; also testable with PureWindowsPath on another OS."""
    require(isinstance(source_id, str) and SOURCE_ID.fullmatch(source_id),
            "invalid_metadata", "installation source_id is not an installer release identifier")
    require(isinstance(release_id, str) and re.fullmatch(re.escape(source_id) + r"(?:-repair-[0-9a-f]{10})?", release_id),
            "invalid_metadata", "release_id does not belong to source_id")
    require(root.is_absolute() and environment.is_absolute() and ".." not in environment.parts,
            "path_escape", "release_environment must be an absolute path without parent traversal")
    release = root / "releases" / release_id
    require(environment == release / "venv", "path_escape",
            "release_environment does not match the selected root and release_id")
    entry = environment / ("Scripts" if windows else "bin") / ("codex-deepseek.exe" if windows else "codex-deepseek")
    return release, entry


def contained(path, root, label):
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise Diagnostic("required_path_missing", label + " is missing") from exc
    require(resolved.is_relative_to(root), "path_escape", label + " resolves outside the selected installation root")
    return resolved


def read_json(path, root):
    require(path.is_file(), "metadata_missing", "A required installation metadata file is missing")
    require(not path.is_symlink(), "metadata_symlink", "Installation metadata must be an ordinary file")
    contained(path, root, "Metadata")
    require(path.stat().st_size <= MAX_JSON_BYTES, "invalid_metadata", "Installation metadata exceeds the supported size")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, ValueError) as exc:
        raise Diagnostic("invalid_metadata", "Installation metadata is not valid UTF-8 JSON") from exc
    require(isinstance(value, dict), "invalid_metadata", "Installation metadata must be a JSON object")
    return value


def inspect_one(entry, *, root=None, windows=None, fallback_root=None):
    windows = os.name == "nt" if windows is None else windows
    requested = Path(entry).expanduser().absolute()
    expected_name = "codex-deepseek.exe" if windows else "codex-deepseek"
    aliases = {expected_name, "cds.exe" if windows else "cds"}
    name = requested.name.casefold() if windows else requested.name
    require(name in aliases, "unrecognized_entry", "Only codex-deepseek or its cds symlink is recognized; vanilla codex is not CDS")
    require(requested.is_file(), "entry_missing", "Selected executable does not exist or is not a regular file")
    resolved = requested.resolve(strict=True)
    resolved_name = resolved.name.casefold() if windows else resolved.name
    require(resolved_name == expected_name, "unrecognized_entry", "The selected alias does not resolve to a canonical codex-deepseek launcher")
    require(os.access(resolved, os.X_OK), "entry_not_executable", "The selected launcher is not executable")

    environment = resolved.parent.parent
    adjacent = (resolved.parent.name.casefold() == ("scripts" if windows else "bin")
                and environment.name.casefold() == "venv"
                and environment.parent.parent.name.casefold() == "releases")
    if root is not None:
        root_path = Path(root).expanduser()
        require(root_path.is_dir(), "root_missing", "Explicit installation root is missing or is not a directory")
        explicit_root = root_path.resolve(strict=True)
    else:
        explicit_root = None
    if adjacent:
        selected_root = environment.parent.parent.parent.resolve(strict=True)
        require(explicit_root is None or explicit_root == selected_root, "root_conflict",
                "Explicit root conflicts with the selected immutable release")
        metadata_path = environment.parent / "installation.json"
        basis = "adjacent_release"
    else:
        root_path = explicit_root or Path(fallback_root or default_root(windows=windows)).expanduser()
        require(root_path.is_dir(), "root_missing", "Installation data root is missing; provide --root for a custom installation")
        selected_root = root_path.resolve(strict=True)
        metadata_path = selected_root / "installation.json"
        basis = "explicit_root" if explicit_root is not None else "default_root"
    metadata = read_json(metadata_path, selected_root)
    for field in IDENTITY_FIELDS:
        require(isinstance(metadata.get(field), str) and 0 < len(metadata[field]) <= 4096,
                "invalid_metadata", "A required installation identity field is missing or invalid")
    require(HEX64.fullmatch(metadata["console_script_sha256"]), "invalid_metadata", "Launcher digest is not SHA256")
    declared_environment = Path(metadata["release_environment"])
    release, immutable_entry = declared_layout(selected_root, metadata["source_id"], metadata["release_id"], declared_environment, windows=windows)
    contained(declared_environment, selected_root, "Release environment")
    release = contained(release, selected_root, "Release")
    immutable_entry = contained(immutable_entry, release, "Immutable launcher")
    require(immutable_entry.is_file(), "entry_missing", "The immutable release launcher is missing")
    sibling_path = release / "installation.json"
    sibling = metadata if sibling_path == metadata_path else read_json(sibling_path, selected_root)
    require(all(metadata.get(k) == sibling.get(k) for k in IDENTITY_FIELDS), "metadata_conflict",
            "Root and immutable release installation metadata disagree")
    native = read_json(release / "native-runtime.json", selected_root)
    require(native.get("distribution") == "codex-deepseek-runtime" and native.get("status") == "ready",
            "foreign_distribution", "Native manifest does not identify a ready codex-deepseek-runtime distribution")
    require(native.get("upstream_version") == metadata["native_version"], "metadata_conflict",
            "Installation and native manifest versions disagree")
    package = Path(metadata["native_package"])
    require(package.is_absolute() and ".." not in package.parts, "path_escape", "Native package must be an absolute in-root path")
    package = contained(package, selected_root / "native", "Native package")
    require(package.is_dir(), "native_package_missing", "Native package directory is missing")
    expected_hash = metadata["console_script_sha256"]
    require(digest(resolved) == expected_hash and digest(immutable_entry) == expected_hash,
            "launcher_mismatch", "Selected launcher or immutable launcher differs from local installation metadata")
    return {
        "entry": str(requested), "resolved_entry": str(resolved),
        "immutable_entry": str(immutable_entry), "root": str(selected_root),
        "metadata": str(metadata_path), "metadata_basis": basis,
        "source_id": metadata["source_id"], "native_version": metadata["native_version"],
        "model": metadata["model"], "launcher_sha256": expected_hash,
        "path_flavor": "windows" if windows else "posix",
        "checks": {"launcher_matches_local_metadata": True,
                   "selected_and_release_metadata_consistent": True,
                   "release_and_native_package_within_root": True,
                   "native_manifest_distribution_marker_matches": True},
    }


def discover():
    result = []
    for name in ("codex-deepseek", "cds"):
        found = shutil.which(name)
        if found and found not in result:
            result.append(found)
    require(result, "entry_not_found", "Neither codex-deepseek nor cds is on PATH; provide --entry explicitly")
    return result


def inspect(entry=None, *, root=None):
    entries = [entry] if entry is not None else discover()
    identities = [inspect_one(value, root=root) for value in entries]
    keys = {(x["root"], x["source_id"], x["launcher_sha256"]) for x in identities}
    require(len(keys) == 1, "entry_conflict", "PATH entries refer to different installations; select --entry explicitly")
    chosen = identities[0]
    chosen["discovered_entries"] = [x["entry"] for x in identities]
    return chosen


def offline_native(identity, action, timeout=15.0, *, environ=None):
    require(action in {"help", "version"}, "invalid_action", "Only offline native help or version is allowed")
    require(0 < timeout <= 60, "invalid_timeout", "Timeout must be greater than zero and at most 60 seconds")
    require(identity["path_flavor"] == ("windows" if os.name == "nt" else "posix"),
            "platform_mismatch", "A simulated path fixture cannot be executed as another OS")
    # Execute the associated immutable entry, avoiding a mutable activation alias.
    argv = [identity["immutable_entry"], "--" + action]
    parent_env = os.environ if environ is None else environ
    child_env = {name: parent_env[name] for name in parent_env
                 if not name.upper().startswith(("OPENAI_", "OPENROUTER_", "CODEX_"))
                 and name not in {"PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__"}}
    try:
        result = subprocess.run(argv, shell=False, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                env=child_env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise Diagnostic("offline_timeout", "Offline invocation exceeded its bounded timeout") from exc
    return {"action": action, "argv": argv, "exit_code": result.returncode,
            "stdout": result.stdout[:8192], "stderr": result.stderr[:8192],
            "output_truncated": len(result.stdout) > 8192 or len(result.stderr) > 8192}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", help="Explicit codex-deepseek executable or cds symlink")
    parser.add_argument("--root", help="Installation data root, not CODEX_HOME or a runtime profile")
    parser.add_argument("--offline-native", choices=("help", "version"))
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    report = {"ok": False, "scope": "local metadata and launcher consistency",
              "publisher_authentication": "not_performed", "full_native_package_verification": "not_performed",
              "source_hash_authenticity": "not_performed", "connection": "not_tested", "model_requests_initiated": 0}
    try:
        identity = inspect(args.entry, root=args.root)
        report["installation"] = identity
        if args.offline_native:
            report["offline_native"] = offline_native(identity, args.offline_native, args.timeout)
            require(report["offline_native"]["exit_code"] == 0, "offline_native_failed", "Offline native command returned a nonzero status")
        report["ok"] = True
    except Diagnostic as exc:
        report["diagnostic"] = {"code": exc.code, "message": str(exc)}
    except (OSError, RuntimeError, ValueError) as exc:
        # Do not echo arbitrary metadata contents or exception payloads.
        report["diagnostic"] = {"code": "filesystem_or_path_error", "message": "A required path could not be read or resolved", "error_type": type(exc).__name__}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
