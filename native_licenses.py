"""Retain original root and vendored notices, declared attribution and SPDX texts."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import platform
from pathlib import Path
import re
import stat
import subprocess

SOURCE = Path(__file__).resolve().parent


def require(ok, message):
    if not ok:
        raise ValueError(message)


def _redirected(path):
    path = Path(path)
    return path.is_symlink() or bool(
        getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400)


# Cargo crates may vendor C libraries below arbitrary paths (for example
# curl/COPYING and aws-lc/third_party/fiat/LICENSE). Scan names throughout the
# crate, retaining the legacy root and dedicated notice-directory behavior.
NOTICE_TOKENS = ("LICENSE", "NOTICE", "COPYING", "COPYRIGHT")
NESTED_NOTICE_NAME = re.compile(r"^(?:licen[cs]e|notice|copying|copyright)(?:[._-].*)?$", re.IGNORECASE)
NOTICE_FOLDERS = (("licenses",), ("LICENSES",), ("third_party", "licenses"), ("third-party", "licenses"))
MAX_NOTICE_SCAN_ENTRIES = 100_000
MAX_NOTICE_SCAN_DEPTH = 32
MAX_CRATE_NOTICE_FILES = 4_096
MAX_CRATE_NOTICE_BYTES = 64 * 1024 * 1024


def notice_candidates(source):
    """Bounded, no-follow source walk; never silently truncate vendored notices."""
    source = Path(source).absolute()
    require(source.is_dir(), "Crate source directory is missing")
    require(not any(_redirected(p) for p in (source, *source.parents)), "Crate source path is redirected")
    pending = [source]
    candidates = set()
    visited = 0
    while pending:
        folder = pending.pop()
        require(len(folder.relative_to(source).parts) <= MAX_NOTICE_SCAN_DEPTH,
                "Crate notice scan exceeds the bounded directory depth")
        with os.scandir(folder) as entries:
            for entry in entries:
                visited += 1
                require(visited <= MAX_NOTICE_SCAN_ENTRIES, "Crate notice scan exceeds the bounded entry count")
                path = Path(entry.path)
                junction = entry.is_junction() if hasattr(entry, "is_junction") else (
                    hasattr(path, "is_junction") and path.is_junction())
                require(not entry.is_symlink() and not junction
                        and not (getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400),
                        "Crate notice scan contains a redirected entry")
                # scandir supplies direct children of an already admitted directory.
                # Reject reparses before descent; lexical containment then avoids a
                # separate filesystem resolution of every vendored source file.
                require(path.is_relative_to(source), "Crate notice scan escapes its source")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    continue
                require(entry.is_file(follow_symlinks=False), "Crate notice scan contains a non-regular entry")
                relative = path.relative_to(source)
                root_notice = len(relative.parts) == 1 and any(token in path.name.upper() for token in NOTICE_TOKENS)
                dedicated_notice = any(relative.parts[:len(prefix)] == prefix for prefix in NOTICE_FOLDERS)
                if root_notice or dedicated_notice or NESTED_NOTICE_NAME.fullmatch(path.name):
                    candidates.add(path)
                    require(len(candidates) <= MAX_CRATE_NOTICE_FILES,
                            "Crate notice count exceeds the bounded file count")
    return candidates


def _notice_bytes(path, source):
    path = Path(path)
    require(not any(_redirected(p) for p in (path, *path.parents)),
            "Crate notice path is redirected")
    require(path.resolve().is_relative_to(source.resolve()), "Crate notice escapes its source")
    before = path.stat()
    require(stat.S_ISREG(before.st_mode), "Crate notice is not a regular file")
    require(before.st_size <= 16 * 1024 * 1024, "Crate notice exceeds the bounded file size")
    with path.open("rb") as stream:
        content = stream.read(16 * 1024 * 1024 + 1)
        opened = os.fstat(stream.fileno())
    after = path.stat()
    fields = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink)
    require(fields(before) == fields(opened) == fields(after) and len(content) == before.st_size,
            "Crate notice changed during copy")
    require(content[:2] != b"MZ" and content[:4] not in (b"\x7fELF", b"PK\x03\x04"),
            "Compiled/container bytes are not a native source notice")
    return content


def collect(manifest, output, *, metadata=None, target=None):
    platform_target = target or {
        ("Darwin", "arm64"): "aarch64-apple-darwin",
        ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
        ("Windows", "AMD64"): "x86_64-pc-windows-msvc",
    }[(platform.system(), platform.machine())]
    output = Path(output)
    if metadata is None:
        spec = importlib.util.spec_from_file_location("cds_license_build_recipe", SOURCE / "build_native.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        toolchain_pin = build.runtime_recipe()["build_toolchain"]
        result = subprocess.run(
            ["cargo", "+" + toolchain_pin, "metadata", "--locked", "--format-version", "1",
             "--filter-platform", platform_target, "--manifest-path", str(manifest)],
            check=True, capture_output=True, text=True, encoding="utf-8")
        metadata = json.loads(result.stdout)
    packages = sorted((p for p in metadata["packages"] if p.get("source")), key=lambda p: p["id"])
    # Output cannot be below a scanned source, including on a first invocation.
    # Reject before writing SPDX files so the collector never modifies its inputs.
    for package in packages:
        source = Path(package["manifest_path"]).parent
        require(not output.resolve().is_relative_to(source.resolve()),
                "License output cannot be inside crate source")
    output.mkdir(parents=True, exist_ok=True)
    license_data = json.loads((SOURCE / "native/license-data.json").read_text(encoding="utf-8"))
    for row in license_data["texts"]:
        path = SOURCE / "licenses/spdx" / (row["id"] + ".txt")
        content = path.read_bytes()
        require(hashlib.sha256(content).hexdigest() == row["sha256"], "SPDX source integrity mismatch")
        destination = output / "spdx" / path.name
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(content)
    entries = []
    for package in packages:
        manifest_path = Path(package["manifest_path"]).absolute()
        source = manifest_path.parent
        manifest_bytes = _notice_bytes(manifest_path, source)
        key = package["name"] + "-" + package["version"] + "-" + hashlib.sha256(package["id"].encode()).hexdigest()[:8]
        require(Path(key).name == key and "/" not in key and "\\" not in key, "Unsafe crate identity")
        target_dir = output / "crates" / key
        candidates = notice_candidates(source)
        if package.get("license_file"):
            path = Path(package["license_file"])
            candidates.add(path if path.is_absolute() else source / path)
        fallback = not candidates
        if fallback:
            candidates.update(path for path in source.glob("README*") if path.is_file())
        require(len(candidates) <= MAX_CRATE_NOTICE_FILES, "Crate notice count exceeds the bounded file count")
        target_dir.mkdir(parents=True)
        notices, originals = [], []
        notice_bytes = 0
        for index, path in enumerate(sorted(candidates)):
            content = _notice_bytes(path, source)
            notice_bytes += len(content)
            require(notice_bytes <= MAX_CRATE_NOTICE_BYTES, "Crate notices exceed the bounded aggregate size")
            name = str(index) + "-" + path.name
            destination = target_dir / name
            destination.write_bytes(content)
            distributed = destination.relative_to(output).as_posix()
            notices.append(distributed)
            originals.append({"source_file": path.relative_to(source).as_posix(),
                              "distributed_file": distributed,
                              "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)})
        declarations = {
            "name": package["name"], "version": package["version"], "source": package["source"],
            "repository": package.get("repository"), "authors": package.get("authors", []),
            "declared_license": package.get("license"), "upstream_notice_files": notices,
            "upstream_notice_sources": originals, "README_fallback": fallback,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        }
        (target_dir / "attribution.json").write_text(json.dumps(declarations, indent=2), encoding="utf-8", newline="\n")
        entries.append(declarations)
    index = {"scope": "Superset of native workspace external dependencies resolved for " + platform_target
             + "; includes Code Mode Host and build/test dependencies",
             "spdx_source": license_data, "packages": entries}
    (output / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8", newline="\n")
    return len(entries)
