"""Preserve bounded CI package/checkpoint artifacts, never a Cargo target cache."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
import shutil
import tarfile

MAX_PACKAGE_BYTES = 2 * 1024**3
MAX_METADATA_BYTES = 64 * 1024**2


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _plain_path(path):
    path = Path(os.path.abspath(path))
    for node in (path, *path.parents):
        if node.exists() or node.is_symlink():
            info = node.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Retention path is redirected: " + str(node))
    return path


def retain(package, work, evidence):
    package, work, evidence = map(_plain_path, (package, work, evidence))
    if package == work or package.is_relative_to(work) or work.is_relative_to(package):
        raise ValueError("Package and build work must remain separate")
    if (evidence == package or evidence.is_relative_to(package) or package.is_relative_to(evidence)
            or evidence == work or evidence.is_relative_to(work) or work.is_relative_to(evidence)):
        raise ValueError("Evidence must not be inside package or build work")
    evidence.mkdir(parents=True, exist_ok=True)
    scope_path = evidence/"retained-build-scope.json"
    if scope_path.exists() or scope_path.is_symlink():
        raise ValueError("Preserve original retained scope evidence")
    proof = {"scope": "Compiled/package data preservation only; not qualification",
             "ready": False, "package": None, "metadata": []}
    if package.exists():
        files = []
        for path in package.rglob("*"):
            _plain_path(path)
            if path.is_file():
                files.append(path)
        size = sum(path.stat().st_size for path in files)
        if size > MAX_PACKAGE_BYTES:
            raise ValueError("Package retention exceeds bounded bytes; do not archive target")
        qualified = (package/"build-receipt.json").is_file()
        name = "native-package.tar.gz" if qualified else "native-package-unqualified.tar.gz"
        destination = evidence/name
        if destination.exists():
            raise ValueError("Preserve original retained artifact")
        with destination.open("xb") as stream:
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                archive.add(package, arcname=".", recursive=True)
        proof["package"] = {"artifact": name, "bytes": destination.stat().st_size,
                            "sha256": sha(destination), "source_bytes": size,
                            "final_build_receipt_present": qualified,
                            "qualified_by_retention": False}
    total = 0
    for build in sorted(work.glob("b-*")) if work.exists() else []:
        for name in ("compile-checkpoint.json", "dependency-metadata.json",
                     "dependency-metadata-command.json", "dependency-metadata.stderr", "combined.patch"):
            source = build/name
            if not source.exists():
                continue
            _plain_path(source)
            if not source.is_file():
                raise ValueError("Build checkpoint is redirected or not regular")
            total += source.stat().st_size
            if total > MAX_METADATA_BYTES:
                raise ValueError("Build checkpoint retention exceeds bounded bytes")
            target = evidence/"compile-checkpoints"/build.name/name
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if sha(source) != sha(target):
                raise ValueError("Build checkpoint changed during retention")
            proof["metadata"].append({"file": target.relative_to(evidence).as_posix(),
                                      "bytes": target.stat().st_size, "sha256": sha(target)})
    with scope_path.open("xb") as stream:
        stream.write(json.dumps(proof, indent=2).encode())
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    retain(args.package, args.work, args.evidence)
