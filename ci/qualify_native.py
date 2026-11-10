"""Qualify changed native transport behavior without any provider credential.

This runs the real, identity-verified CLI and HTTP helper against owned loopback
fixtures. It does not certify live-model quality or whole-product readiness.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITES = ("tests/test_native_raw_http.py", "tests/test_native_payload_retry.py")
EXPECTED_NODE_IDS = (
    'tests/test_native_raw_http.py::test_native_raw_large_multipart_files_delete_exact_wire_and_warm_reuse',
    'tests/test_native_raw_http.py::test_native_raw_errors_preserve_status_headers_body_without_replay[413]',
    'tests/test_native_raw_http.py::test_native_raw_errors_preserve_status_headers_body_without_replay[429]',
    'tests/test_native_raw_http.py::test_native_raw_errors_preserve_status_headers_body_without_replay[500]',
    'tests/test_native_raw_http.py::test_native_raw_gate_denies_auth_origin_metadata_and_non_files_delete',
    'tests/test_native_raw_http.py::test_native_raw_tls_rejection_and_no_proxy',
    'tests/test_native_raw_http.py::test_native_raw_cancel_model_before_upstream_headers',
    'tests/test_native_raw_http.py::test_native_upload_cancel_before_headers_preserves_unknown_without_replay',
    'tests/test_native_payload_retry.py::test_real_cli_retry_classification[413-None-1]',
    'tests/test_native_payload_retry.py::test_real_cli_retry_classification[413-1-2]',
    'tests/test_native_payload_retry.py::test_real_cli_retry_classification[503-None-2]',
    'tests/test_native_payload_retry.py::test_real_cli_retry_classification[429-1-2]',
    'tests/test_native_payload_retry.py::test_real_cli_compaction_413_uses_same_native_retry_semantics[None]',
    'tests/test_native_payload_retry.py::test_real_cli_compaction_413_uses_same_native_retry_semantics[1]',
    'tests/test_native_payload_retry.py::test_real_cli_local_operator_budget_413_is_not_replayed',
    'tests/test_native_payload_retry.py::test_real_cli_ordinary_capacity_prefix_preserves_unconsumed_tool_pair[summary]',
    'tests/test_native_payload_retry.py::test_real_cli_ordinary_capacity_prefix_preserves_unconsumed_tool_pair[token-budget-local-reset]',
    'tests/test_native_payload_retry.py::test_real_cli_cold_capacity_checkpoint_qualifies_rebuilt_envelopes[summary]',
    'tests/test_native_payload_retry.py::test_real_cli_cold_capacity_checkpoint_qualifies_rebuilt_envelopes[token-budget-local-reset]',
    'tests/test_native_payload_retry.py::test_real_cli_direct_image_metadata_cold_capacity[summary]',
    'tests/test_native_payload_retry.py::test_real_cli_direct_image_metadata_cold_capacity[token-budget-local-reset]',
    'tests/test_native_payload_retry.py::test_real_cli_recovered_parallel_metadata_checkpoint_survives_second_cold_capacity',
)
EXPECTED_CASES = len(EXPECTED_NODE_IDS)  # Eight raw HTTP cases and fourteen payload/recovery cases.


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()



def recipe_scope_identity(receipt: dict, recipe: dict, target: str,
                          archived_recipe: Path | None) -> dict:
    """Validate an original build recipe without rewriting its receipt.

    Reuse permits only Windows-specific recipe changes on Linux/macOS.
    Native target/source/file checks below remain mandatory.
    """
    from build_native import native_recipe_identity
    current_sha = sha(ROOT / "native/runtime-overlays.json")
    original_sha = receipt.get("overlay_recipe_sha256")
    if archived_recipe is None:
        if original_sha != current_sha:
            raise ValueError("Full recipe file differs from the build receipt")
        return {"mode": "exact_full_recipe", "original_sha256": original_sha,
                "current_sha256": current_sha}
    if target not in {"x86_64-unknown-linux-gnu", "aarch64-apple-darwin"}:
        raise ValueError("Archived native reuse is limited to the qualified Unix targets")
    if archived_recipe.is_symlink() or sha(archived_recipe) != original_sha:
        raise ValueError("Archived recipe bytes do not match the original receipt")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate archived recipe member")
            result[key] = value
        return result
    original = json.loads(archived_recipe.read_bytes(), object_pairs_hook=unique)
    def without_windows(value):
        value = json.loads(json.dumps(value))
        value["required_base_inputs"].pop("x86_64-pc-windows-msvc")
        overlays = []
        for item in value["overlays"]:
            item["changes_by_target"].pop("x86_64-pc-windows-msvc", None)
            if item["changes_by_target"]:
                overlays.append(item)
        value["overlays"] = overlays
        return value
    if without_windows(original) != without_windows(recipe):
        raise ValueError("Archived recipe differs outside the Windows-only scope")
    if native_recipe_identity(target, original) != native_recipe_identity(target, recipe):
        raise ValueError("Archived and current target recipe identities differ")
    return {"mode": "original_archive_target_equivalence",
            "original_sha256": original_sha, "current_sha256": current_sha,
            "archived_recipe": str(archived_recipe.resolve()),
            "only_windows_recipe_changes": original_sha != current_sha,
            "target_recipe_id": native_recipe_identity(target, recipe)}


def package_identity(package: Path, archived_recipe: Path | None = None) -> dict:
    sys.path.insert(0, str(ROOT))
    from build_native import (COMMIT, VERSION, build_target, build_assets,
                              native_recipe_identity, runtime_recipe, overlay_source_hashes)
    target = build_target()
    metadata = json.loads((package / "codex-package.json").read_text())
    receipt = json.loads((package / "build-receipt.json").read_text())
    recipe = runtime_recipe()
    expected = native_recipe_identity(target, recipe)
    if metadata.get("target") != target or metadata.get("recipeId") != expected:
        raise ValueError("Package target or recipe does not match this host and checkout")
    if receipt.get("native_recipe_id") != expected or receipt.get("upstream_commit") != COMMIT:
        raise ValueError("Build receipt does not match the pinned source recipe")
    if (receipt.get("build_toolchain_pin") != recipe["build_toolchain"]
            or not str(receipt.get("rustc", "")).startswith("rustc " + recipe["build_toolchain"] + " ")):
        raise ValueError("Compiler pin differs from this checkout")
    recipe_validation = recipe_scope_identity(receipt, recipe, target, archived_recipe)
    expected_patches = list(recipe["required_base_inputs"][target]) + [
        {"file": item["file"], "sha256": item["sha256"]}
        for item in recipe["overlays"] if target in item["changes_by_target"]
    ]
    if receipt.get("patch_inputs") != expected_patches or metadata.get("patchInputs") != expected_patches:
        raise ValueError("Native patch stack differs from the fixed recipe")
    expected_archives = {
        name: {"url": url, "sha256": digest}
        for name, (url, digest) in build_assets(target).items()
    }
    if receipt.get("archives") != expected_archives:
        raise ValueError("Native upstream/resource archives differ from the fixed inputs")
    expected_sources = {
        item["path"]: item["fork_sha256"]
        for item in json.loads((ROOT / "native/source-changes.json").read_text())["files"]
    }
    if target.endswith("windows-msvc"):
        expected_sources.update({
            item["path"]: item["fork_sha256"]
            for item in json.loads((ROOT / "native/windows-source-changes.json").read_text())["files"]
        })
    expected_sources.update(overlay_source_hashes(target, recipe))
    if receipt.get("source_files") != expected_sources:
        raise ValueError("Patched source bytes differ from the fixed inputs")
    if metadata.get("upstreamCommit") != COMMIT or metadata.get("version") != VERSION:
        raise ValueError("Native metadata has the wrong upstream baseline")
    if not (re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("patchSha256", "")))
            and metadata.get("patchSha256") == receipt.get("patch_sha256")
            == receipt.get("combined_patch_sha256")):
        raise ValueError("Native source diff identities disagree")
    recorded = receipt.get("files")
    if not isinstance(recorded, dict) or not recorded:
        raise ValueError("Build receipt has no file inventory")
    actual = set()
    for path in package.rglob("*"):
        if path.is_symlink():
            raise ValueError("Native package must not contain symlinks")
        if path.is_file():
            actual.add(path.relative_to(package).as_posix())
    if actual != set(recorded) | {"build-receipt.json"}:
        raise ValueError("Native package file inventory differs from the receipt")
    for name, expected_hash in recorded.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("Invalid path in the native build receipt")
        path = package.joinpath(*relative.parts)
        if not path.is_file() or sha(path) != expected_hash:
            raise ValueError("Native package byte mismatch: " + name)
    if archived_recipe is not None:
        manifest_path = ROOT / "native" / (
            "runtime.json" if target == "aarch64-apple-darwin"
            else "runtime-x86_64-unknown-linux-gnu.json"
        )
        manifest = json.loads(manifest_path.read_bytes())
        files = {name: sha(package / name) for name in actual}
        if (manifest.get("status") != "ready" or manifest.get("target") != target
                or manifest.get("native_recipe_id") != expected
                or manifest.get("upstream_commit") != COMMIT
                or manifest.get("patch_sha256") != metadata["patchSha256"]
                or manifest.get("files") != files):
            raise ValueError("Reused package differs from the complete current target manifest")
        recipe_validation["native_manifest_sha256"] = sha(manifest_path)
    suffix = ".exe" if target.endswith("windows-msvc") else ""
    cli = package / ("bin/codex" + suffix)
    helper = package / ("bin/codex-http-transport" + suffix)
    if not cli.is_file() or not helper.is_file():
        raise ValueError("Native CLI or transport helper is missing")
    return {
        "target": target,
        "recipe_validation": recipe_validation,
        "recipe_id": expected,
        "upstream_commit": COMMIT,
        "compiler": receipt["rustc"],
        "cli": str(cli),
        "cli_sha256": sha(cli),
        "helper": str(helper),
        "helper_sha256": sha(helper),
        "build_receipt_sha256": sha(package / "build-receipt.json"),
        "files": len(recorded),
    }



def pytest_command(*arguments: str) -> list[str]:
    # -I ignores cwd/PYTHONPATH. Admit only this trusted repository's QA helpers;
    # never add ROOT/src, which would replace the identity-checked installed adapter.
    bootstrap = ("import sys;sys.path.insert(0," + repr(str(ROOT)) + ");"
                 "import pytest;raise SystemExit(pytest.main(sys.argv[1:]))")
    return [sys.executable, "-I", "-B", "-c", bootstrap, *arguments]


def validate_collected_nodes(stdout: str) -> list[str]:
    """A same-count deletion/substitution must not qualify a different native suite."""
    node_ids = [line.strip() for line in stdout.splitlines()
                if re.match(r"^[^ ]+[.]py::", line.strip())]
    if Counter(node_ids) != Counter(EXPECTED_NODE_IDS):
        raise ValueError("Native collection differs from the declared node identities")
    return node_ids


def junit_result(path: Path, expected_names: list[str] | None = None) -> dict:
    root = ET.fromstring(path.read_bytes())
    cases = list(root.iter("testcase"))
    rejected = []
    for case in cases:
        for state in ("failure", "error", "skipped"):
            if case.find(state) is not None:
                rejected.append({"case": case.get("name"), "state": state})
    if len(cases) != EXPECTED_CASES or rejected:
        raise ValueError(json.dumps({
            "expected_cases": EXPECTED_CASES,
            "observed_cases": len(cases),
            "rejected": rejected,
        }))
    declared_keys = Counter((suite.removesuffix(".py").replace("/", "."), name)
                            for suite, name in (node.split("::", 1) for node in EXPECTED_NODE_IDS))
    observed_keys = Counter((case.get("classname", ""), case.get("name", "")) for case in cases)
    if observed_keys != declared_keys:
        raise ValueError("Executed native case identities differ from the declared node identities")
    names = [case.get("name", "") for case in cases]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate native test case identities")
    if expected_names is not None and set(names) != set(expected_names):
        raise ValueError("Executed cases differ from the independent collection")
    return {"cases": len(cases), "failed": 0, "skipped": 0, "names": names}



def adapter_identity() -> dict:
    import codex_deepseek
    from importlib.metadata import version
    import tomllib
    installed = Path(codex_deepseek.__file__).resolve().parent
    source = ROOT / "src/codex_deepseek"
    expected = {path.relative_to(source).as_posix(): sha(path)
                for path in source.rglob("*.py")}
    actual = {path.relative_to(installed).as_posix(): sha(path)
              for path in installed.rglob("*.py")}
    if actual != expected:
        raise ValueError("Installed Python adapter differs from this checkout")
    wanted_version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    if version("codex-deepseek-runtime") != wanted_version:
        raise ValueError("Installed Python distribution version differs from this checkout")
    return {
        "directory": str(installed),
        "version": wanted_version,
        "source_sha256": expected,
    }


def source_identity() -> dict:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True, stdout=subprocess.PIPE,
    )
    names = result.stdout.decode().split("\0")
    # Include the controller itself even during its initial uncommitted validation.
    paths = {name for name in names if name}
    paths.add("ci/qualify_native.py")
    return {name: sha(ROOT / name) for name in sorted(paths) if (ROOT / name).is_file()}



def _process_snapshot(pid, parent_map=None):
    """Read identity and edge fields only; an unproven PID is not diagnostic scope."""
    import psutil
    from codex_deepseek.processes import identity
    row = {"pid": pid, "birth": identity(pid), "ppid": None, "creation_time": None}
    if row["birth"] is None:
        row.update(stable=False, birth_after=None)
        return row
    try:
        process = psutil.Process(pid)
        row.update(creation_time=process.create_time(),
                   ppid=process.ppid() if parent_map is None else parent_map[pid])
    except (psutil.Error, OSError, KeyError) as error:
        row["metadata_error"] = type(error).__name__
    row["birth_after"] = identity(pid)
    row["stable"] = (row["birth_after"] == row["birth"]
                     and type(row["creation_time"]) in (int, float)
                     and row["creation_time"] >= 0 and type(row["ppid"]) is int)
    return row


def _proven_process_diagnostics(row):
    """Read text only for an owned identity; discard it if that instance changes."""
    import psutil
    from codex_deepseek.processes import identity
    first = identity(row["pid"])
    if first != row["birth"]:
        return None, first, first
    values = {}
    try:
        process = psutil.Process(row["pid"])
        for key, method in (("name", "name"), ("exe", "exe"), ("argv", "cmdline")):
            try:
                values[key] = getattr(process, method)()
            except (psutil.Error, OSError) as error:
                values[key] = None
                values.setdefault("diagnostic_errors", {})[key] = type(error).__name__
    except (psutil.Error, OSError):
        return None, first, identity(row["pid"])
    last = identity(row["pid"])
    return (values if last == row["birth"] else None), first, last


def _observer_parent_map():
    """Use the pinned Windows psutil batch API; never collect process text here."""
    import psutil
    if sys.platform != "win32":
        return None
    reader = getattr(getattr(psutil, "_psplatform", None), "ppid_map", None)
    return reader() if callable(reader) else None


def _observer_family_batch(seen):
    """Bracket identity-only candidates with two Windows PID/PPID snapshots.

    Windows parent IDs are immutable for a process lifetime. The second map
    still must confirm each edge, and admission keeps the original per-PID
    kernel birth and parent birth checks. No foreign argv/exe is inspected.
    """
    before_map = _observer_parent_map()
    if before_map is None:
        return None
    reverse = {}
    for pid, parent in before_map.items():
        reverse.setdefault(parent, []).append(pid)
    snapshots, candidates = {}, {}
    pending = list(seen)
    visited = set()
    while pending:
        pid, birth = pending.pop()
        if (pid, birth) in visited:
            continue
        visited.add((pid, birth))
        if pid not in snapshots:
            snapshots[pid] = _process_snapshot(pid, before_map)
        first = snapshots[pid]
        if not first["stable"] or first["birth"] != birth:
            continue
        children = []
        for child_pid in reverse.get(pid, ()):
            if child_pid not in snapshots:
                snapshots[child_pid] = _process_snapshot(child_pid, before_map)
            child = snapshots[child_pid]
            children.append(child)
            if (child["stable"] and child["pid"] != pid
                    and child["ppid"] == pid
                    and child["creation_time"] >= first["creation_time"]):
                pending.append((child["pid"], child["birth"]))
        candidates[(pid, birth)] = children
    after_map = _observer_parent_map()
    if after_map is None:
        raise RuntimeError("Windows parent map disappeared during one observation")
    return snapshots, candidates, after_map


def _observe_process_family(seen, uncertain, rejected, errors):
    """Admit only individually proven direct-parent edges, never a PPID tree."""
    import psutil
    from codex_deepseek.processes import identity, matches
    stack = list(seen)
    visited = set()

    def remember(container, row, reason, parent):
        value = {**row, "reason": reason, "parent_pid": parent["pid"],
                 "parent_birth": parent["birth"]}
        key = (value["pid"], value.get("birth"), reason,
               parent["pid"], parent["birth"])
        container[key] = value

    try:
        batch = _observer_family_batch(seen)
    except (psutil.Error, OSError, RuntimeError) as error:
        # Unknown discovery is a qualification error, never permission to signal.
        errors[("parent_maps",)] = {"reason": "process_parent_maps_unavailable",
                                    "error_type": type(error).__name__}
        return
    if batch is not None:
        snapshots, candidate_rows, after_map = batch
        # Retain live descendants of a parent which vanished/reused between the
        # two snapshots as uncertain, even if that parent was not yet admitted.
        for parent_key, children in candidate_rows.items():
            parent = snapshots[parent_key[0]]
            if identity(parent["pid"]) != parent["birth"]:
                for child in children:
                    remember(uncertain, {**child,
                             "parent_birth_before": parent["birth"],
                             "parent_birth_after": identity(parent["pid"])},
                             "parent_changed_during_collection", parent)

    while stack:
        key = stack.pop()
        if key in visited:
            continue
        visited.add(key)
        parent = seen[key]
        if not matches(parent["pid"], parent["birth"]):
            if batch is not None:
                for child in candidate_rows.get(key, ()):
                    remember(uncertain, {**child,
                             "parent_birth_before": parent["birth"],
                             "parent_birth_after": identity(parent["pid"])},
                             "parent_changed_during_collection", parent)
            continue
        first = snapshots.get(parent["pid"]) if batch is not None else _process_snapshot(parent["pid"])
        if first is None:
            continue
        if not first["stable"] or first["birth"] != parent["birth"]:
            if first.get("metadata_error") and matches(parent["pid"], parent["birth"]):
                remember(errors, first, "parent_metadata_unavailable", parent)
            continue
        if batch is not None:
            candidates = candidate_rows.get(key, [])
            last = _process_snapshot(parent["pid"], after_map)
        else:
            try:
                children = psutil.Process(parent["pid"]).children(recursive=False)
            except (psutil.Error, OSError) as error:
                # A vanished parent has no stable sample; never infer its descendants.
                if matches(parent["pid"], parent["birth"]):
                    remember(errors, first, "children_unavailable:" + type(error).__name__, parent)
                continue
            candidates = [_process_snapshot(child.pid) for child in children]
            last = _process_snapshot(parent["pid"])
        parent_stable = (last["stable"] and last["birth"] == parent["birth"]
                         and last["creation_time"] == first["creation_time"])
        for child in candidates:
            if not child["stable"]:
                remember(uncertain, child, "child_identity_or_metadata_changed", parent)
                if (child.get("metadata_error") and child.get("birth")
                        and child.get("birth_after") == child["birth"]):
                    remember(errors, child, "child_metadata_unavailable", parent)
                continue
            if not parent_stable:
                remember(uncertain, {**child, "parent_birth_before": first["birth"],
                                     "parent_birth_after": last.get("birth"),
                                     "parent_after_stable": last["stable"]},
                         "parent_changed_during_collection", parent)
                continue
            if (child["pid"] == parent["pid"]
                    or child["ppid"] != parent["pid"]
                    or (batch is not None and after_map.get(child["pid"]) != parent["pid"])
                    or child["creation_time"] < first["creation_time"]):
                remember(rejected, child, "not_a_current_direct_child", parent)
                continue
            # Recheck the exact edge immediately before extending the owned set.
            if (not matches(parent["pid"], parent["birth"])
                    or identity(child["pid"]) != child["birth"]):
                remember(uncertain, child, "edge_changed_before_admission", parent)
                continue
            child_key = (child["pid"], child["birth"])
            if child_key not in seen:
                seen[child_key] = {**child, "parent_pid": parent["pid"],
                                   "parent_birth": parent["birth"],
                                   "parent_creation_time": first["creation_time"],
                                   "ownership": "stable_direct_parent_edge"}
                values, diagnostic_first, diagnostic_last = _proven_process_diagnostics(seen[child_key])
                if values is not None:
                    seen[child_key].update(values)
                else:
                    remember(uncertain, {**child, "birth_after": diagnostic_last,
                                         "diagnostic_birth_before": diagnostic_first},
                             "identity_changed_during_owned_diagnostics", parent)
                stack.append(child_key)
        # The parent is also an already-proven identity, or the created Popen.
        if parent_stable:
            seen[key].update({name: first.get(name) for name in ("ppid", "creation_time")})
            values, diagnostic_first, diagnostic_last = _proven_process_diagnostics(parent)
            if values is not None:
                seen[key].update(values)
            else:
                remember(uncertain, {**first, "birth_after": diagnostic_last,
                                     "diagnostic_birth_before": diagnostic_first},
                         "identity_changed_during_owned_diagnostics", parent)


class _WindowsProcessEvidence:
    """Optional QA snapshot from already-admitted process instances only."""
    def __init__(self,path):
        if sys.platform!='win32':raise ValueError('Retained OS exit evidence requires Windows')
        import ctypes
        from ctypes import wintypes
        self.path=Path(path);self.handles={};self.attempted=set();self.exits={}
        self.unretained={};self.errors={};self.hashes={};self.last_write=0
        self.ctypes=ctypes;self.wintypes=wintypes
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        self.kernel.OpenProcess.restype=wintypes.HANDLE
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE];self.kernel.CloseHandle.restype=wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype=wintypes.DWORD
        self.kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetExitCodeProcess.restype=wintypes.BOOL
        self.kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        self.kernel.GetProcessTimes.restype=wintypes.BOOL

    def birth(self,handle):
        times=[self.wintypes.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(handle,*map(self.ctypes.byref,times)):return None
        return 'windows:'+str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)

    def error(self,key,reason):
        self.errors[(key,reason)]={'pid':key[0],'birth':key[1],'reason':reason,
                                  'winerror':self.ctypes.get_last_error()}

    def sample(self,seen,*,force=False):
        for key,row in list(seen.items()):
            if key not in self.attempted:
                self.attempted.add(key)
                handle=self.kernel.OpenProcess(0x1000|0x100000,False,row['pid'])
                if not handle:
                    self.unretained[key]={'pid':key[0],'birth':key[1],'reason':'owned_handle_unavailable',
                        'winerror':self.ctypes.get_last_error()}
                elif self.birth(handle)!=key[1]:
                    self.kernel.CloseHandle(handle)
                    self.unretained[key]={'pid':key[0],'birth':key[1],'reason':'opened_instance_birth_mismatch'}
                else:self.handles[key]=handle
            # This text was obtained only after the existing admission proof.
            if isinstance(row.get('exe'),str) and row['exe'] and 'sha256' not in row:
                try:
                    image=Path(row['exe']);first=image.stat()
                    signature=(str(image),first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns)
                    if signature not in self.hashes:
                        digest=sha(image);last=image.stat()
                        if (first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns)!=(last.st_dev,last.st_ino,last.st_size,last.st_mtime_ns):
                            raise OSError('image changed while hashing')
                        self.hashes[signature]=digest
                    row['sha256']=self.hashes[signature]
                except (OSError,ValueError,TypeError) as error:
                    self.unretained[(key,'image')]={'pid':key[0],'birth':key[1],
                        'reason':'owned_image_hash_unavailable','error_type':type(error).__name__}
        for key,handle in list(self.handles.items()):
            if key in self.exits:continue
            first=self.birth(handle)
            if first!=key[1]:self.error(key,'retained_handle_birth_mismatch');continue
            status=self.kernel.WaitForSingleObject(handle,0)
            if status==0:
                code=self.wintypes.DWORD()
                if not self.kernel.GetExitCodeProcess(handle,self.ctypes.byref(code)):
                    self.error(key,'get_retained_exit_failed');continue
                if self.birth(handle)!=key[1]:self.error(key,'retained_exit_birth_mismatch');continue
                self.exits[key]={'pid':key[0],'birth':key[1],'exit_code':int(code.value)}
            elif status!=258:self.error(key,'retained_wait_failed')
        if force or time.monotonic()-self.last_write>=.2:self.publish(seen)

    def publish(self,seen):
        value={'observed':list(seen.values()),'exits':list(self.exits.values()),
               'exit_evidence_source':'retained_os_process_handles',
               'unretained':list(self.unretained.values()),'errors':list(self.errors.values())}
        temporary=self.path.with_name(self.path.name+'.tmp-'+str(os.getpid()))
        try:
            with temporary.open('w',encoding='utf-8',newline='\n') as stream:
                json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            for attempt in range(4):
                try:os.replace(temporary,self.path);break
                except PermissionError:
                    if attempt==3:raise
                    time.sleep(.005)
            self.last_write=time.monotonic()
        except (OSError,ValueError,TypeError) as error:
            self.errors[(None,'snapshot_write')]={'reason':'snapshot_write_failed','error_type':type(error).__name__}
            try:temporary.unlink(missing_ok=True)
            except OSError:pass

    def close(self):
        for key,handle in self.handles.items():
            if not self.kernel.CloseHandle(handle):self.error(key,'retained_close_failed')
        self.handles.clear()


def run_observed(command, *, cwd, env, log, timeout, process_evidence_path=None):
    """Own only this test process and observed PID/kernel identities."""
    import psutil
    from codex_deepseek.processes import identity, matches, terminate
    evidence=_WindowsProcessEvidence(process_evidence_path) if process_evidence_path is not None else None
    seen, unknown = {}, set()
    uncertain, rejected, observation_errors = {}, {}, {}
    report = {"timed_out": False, "forced_cleanup": [], "remaining_before_cleanup": [],
              "remaining_after_cleanup": [], "unknown_live": []}
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    started = time.monotonic()
    try:
        birth = identity(process.pid)
        if birth is None:
            unknown.add(process.pid)
            raise RuntimeError("Created test process has no stable identity")
        seen[(process.pid, birth)] = {"pid": process.pid, "birth": birth, "ownership": "created_Popen_handle"}
        if evidence is not None:evidence.sample(seen,force=True)
        while process.poll() is None:
            _observe_process_family(seen, uncertain, rejected, observation_errors)
            if evidence is not None:evidence.sample(seen)
            if time.monotonic() - started > timeout:
                report["timed_out"] = True
                break
            time.sleep(.1)
    except BaseException as error:
        report.update(error_type=type(error).__name__, error=str(error))
    finally:
        try:
            if process.poll() is None:
                # This Popen still owns the original process handle; no PID-name kill.
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                except Exception as error:
                    report.setdefault("cleanup_errors", []).append(type(error).__name__)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(matches(pid, stamp) for pid, stamp in seen):
                time.sleep(.05)
            left = [row for (pid, stamp), row in seen.items() if matches(pid, stamp)]
            report["remaining_before_cleanup"] = left
            for row in left:
                try:
                    terminate(row["pid"], row["birth"], timeout=3)
                    report["forced_cleanup"].append(row)
                except Exception as error:
                    report.setdefault("cleanup_errors", []).append(type(error).__name__)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(matches(pid, stamp) for pid, stamp in seen):
                time.sleep(.05)
            report["remaining_after_cleanup"] = [
                row for (pid, stamp), row in seen.items() if matches(pid, stamp)
            ]
            # Uncertain association is never promoted to ownership or signalled.
            uncertain_live = set()
            # A missing identity is not exit evidence when the PID remains live.
            # Never signal an unproven instance, including a previously owned PID.
            if evidence is not None:evidence.sample(seen,force=True)
            for row in seen.values():
                if evidence is not None and (row["pid"],row["birth"]) in evidence.exits:continue
                if identity(row["pid"]) is None and psutil.pid_exists(row["pid"]):
                    uncertain_live.add(row["pid"])
            for row in uncertain.values():
                stamp = row.get("birth_after") or row.get("birth")
                if evidence is not None and stamp and (row["pid"],stamp) in evidence.exits:continue
                current = identity(row["pid"])
                if ((stamp and current == stamp)
                        or ((not stamp or current is None) and psutil.pid_exists(row["pid"]))):
                    uncertain_live.add(row["pid"])
            report["unknown_live"] = sorted(uncertain_live | {pid for pid in unknown if psutil.pid_exists(pid)})
            report["uncertain_observations"] = list(uncertain.values())
            report["rejected_processes"] = list(rejected.values())
            report["observation_errors"] = list(observation_errors.values())
            report["observed"] = list(seen.values())
            report["returncode"] = process.returncode
            report["seconds"] = time.monotonic() - started
            if evidence is not None:
                evidence.sample(seen,force=True)
                report["process_evidence_path"]=str(evidence.path)
                report["exits"]=list(evidence.exits.values())
                report["exit_evidence_source"]="retained_os_process_handles"
        finally:
            if evidence is not None:
                evidence.close()
                report["process_evidence_errors"]=list(evidence.errors.values())
    report["passed"] = (report["returncode"] == 0 and not report["timed_out"]
                        and not report.get("error") and not report["remaining_before_cleanup"]
                        and not report["remaining_after_cleanup"] and not report["unknown_live"]
                        and not report.get("cleanup_errors") and not report["observation_errors"]
                        and not report.get("process_evidence_errors"))
    return report

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--archived-recipe", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    evidence = args.evidence.resolve()
    work = args.work.resolve()
    for path in (evidence, work):
        if path.exists():
            raise ValueError("Preserve existing evidence/work; choose a fresh directory")
        if path == ROOT or path.is_relative_to(ROOT / "src"):
            raise ValueError("Qualification outputs must not replace project sources")
        if path == package or path.is_relative_to(package) or package.is_relative_to(path):
            raise ValueError("Qualification outputs must be separate from the native package")
    if work.is_relative_to(evidence) or evidence.is_relative_to(work):
        raise ValueError("Private test fixtures must not be included in uploaded evidence")
    evidence.mkdir(parents=True)
    work.mkdir(parents=True)
    record = {
        "started_at": time.time(),
        "platform": platform.platform(),
        "python": sys.version,
        "runner_image": {key: os.environ.get(key) for key in ("ImageOS", "ImageVersion")},
        "scope": "real native CLI/helper with synthetic loopback HTTP/TLS fixtures",
        "real_model_requests": 0,
        "provider_credentials_read": False,
        "product_release_ready": False,
        "status": "running",
    }
    result_path = evidence / "qualification.json"
    def save():
        result_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    save()
    try:
        identity = package_identity(package, args.archived_recipe)
        record["package"] = identity
        record["adapter"] = adapter_identity()
        source_before = source_identity()
        record["source_sha256"] = source_before
        record["native_builds"] = 0 if args.archived_recipe is not None else None
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("CODEX_", "CDS_", "OPENAI_", "OPENROUTER_", "OTEL_", "PYTEST_", "ACTIONS_", "GITHUB_"))
            and key not in {"GH_TOKEN", "GITHUB_TOKEN", "PYTHONPATH", "PYTHONHOME"}
        }
        for name in ("home", "tmp"):
            (work / name).mkdir(mode=0o700)
        env.update({
            "HOME": str(work / "home"),
            "TMPDIR": str(work / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "CODEX_DEEPSEEK_TEST_CLI_BINARY": identity["cli"],
            "CODEX_DEEPSEEK_TEST_HTTP_BINARY": identity["helper"],
            "CDS_RETRY_EVIDENCE_DIR": str(evidence / "retry"),
        })
        command = pytest_command(
            "-p", "pytest_asyncio.plugin", "-W", "error",
            "-o", "asyncio_default_fixture_loop_scope=function",
            *SUITES, "-q", "-p", "no:cacheprovider",
            "--basetemp", str(work / "pytest"),
            "--junitxml", str(evidence / "native.xml"),
        )
        collection_command = pytest_command(
            "-p", "pytest_asyncio.plugin",
            "-W", "error", "-o", "asyncio_default_fixture_loop_scope=function",
            *SUITES, "-q", "--collect-only", "-p", "no:cacheprovider",
        )
        collected = subprocess.run(
            collection_command, cwd=ROOT, env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120,
        )
        (evidence / "collected.log").write_text(
            collected.stdout + collected.stderr, encoding="utf-8",
        )
        if collected.returncode:
            raise RuntimeError("Native case collection failed")
        collected_ids = validate_collected_nodes(collected.stdout)
        collected_names = [node.split("::", 1)[1] for node in collected_ids]
        record["collected_node_ids"] = collected_ids
        record["collected_case_names"] = collected_names
        record["command"] = command
        save()
        with (evidence / "pytest.log").open("wb") as log:
            process_proof = run_observed(command, cwd=ROOT, env=env, log=log, timeout=1200)
        record["processes"] = process_proof
        record["pytest_exit_code"] = process_proof["returncode"]
        if not process_proof["passed"]:
            raise RuntimeError("Native qualification or owned process cleanup failed; inspect pytest.log")
        record["junit"] = junit_result(evidence / "native.xml", collected_names)
        record["package_after"] = package_identity(package, args.archived_recipe)
        if record["package_after"] != identity:
            raise RuntimeError("Native package changed during qualification")
        if adapter_identity() != record["adapter"]:
            raise RuntimeError("Installed Python adapter changed during qualification")
        if source_identity() != source_before:
            raise RuntimeError("Tracked source bytes changed during qualification")
        record["source_unchanged"] = True
        record["status"] = "passed_native_transport_scope"
    except BaseException as error:
        record["status"] = "failed"
        record["error_type"] = type(error).__name__
        record["error"] = str(error)
        raise
    finally:
        record["finished_at"] = time.time()
        # This directory is created by this invocation and contains only synthetic
        # fixture material. Never remove package, source, user homes, or evidence.
        try:
            processes = record.get("processes", {})
            if processes.get("remaining_after_cleanup") or processes.get("unknown_live") or processes.get("error"):
                raise RuntimeError("Preserve fixture paths while an owned process is unresolved")
            shutil.rmtree(work)
            record["owned_fixture_directory_removed"] = not work.exists()
        except Exception as error:
            record["owned_fixture_directory_removed"] = False
            record["cleanup_error"] = type(error).__name__
            record["status"] = "failed"
        save()
    if not record.get("owned_fixture_directory_removed"):
        raise RuntimeError("Owned fixture cleanup did not complete")
    print(json.dumps({"status": record["status"], "evidence": str(result_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
