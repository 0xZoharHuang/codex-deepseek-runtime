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


# BEGIN PINNED BUILD-TIME NOTICE HELPERS
def _redirected(path):
    path = Path(path)
    return path.is_symlink() or bool(
        getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400)


# Cargo crates may vendor notices below arbitrary paths. Directory walks never
# follow redirects; only notice-file aliases are resolved, inside an admitted
# crate or the exact Cargo-pinned Git checkout that owns that crate.
NOTICE_TOKENS = ("LICENSE", "NOTICE", "COPYING", "COPYRIGHT")
NESTED_NOTICE_NAME = re.compile(r"^(?:licen[cs]e|notice|copying|copyright)(?:[._-].*)?$", re.IGNORECASE)
NOTICE_FOLDERS = (("licenses",), ("LICENSES",), ("third_party", "licenses"), ("third-party", "licenses"))
MAX_NOTICE_SCAN_ENTRIES = 100_000
MAX_NOTICE_SCAN_DEPTH = 32
MAX_CRATE_NOTICE_FILES = 4_096
MAX_CRATE_NOTICE_BYTES = 64 * 1024 * 1024
MAX_NOTICE_ALIAS_HOPS = 16


def _git(root, *arguments, executable="git"):
    try:
        result = subprocess.run([executable, "-C", str(root), *arguments],
                                check=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        raise ValueError("Git notice identity command failed at " + str(root) + ": " + " ".join(arguments)) from error
    return result.stdout


def _plain_parents(path):
    require(not any(_redirected(p) for p in Path(path).parents),
            "Crate notice parent is redirected: " + str(path))


def _notice_name(path, source):
    relative = path.relative_to(source)
    return (len(relative.parts) == 1 and any(token in path.name.upper() for token in NOTICE_TOKENS)
            or any(relative.parts[:len(prefix)] == prefix for prefix in NOTICE_FOLDERS)
            or bool(NESTED_NOTICE_NAME.fullmatch(path.name)))


def source_context(package, *, git="git"):
    """Cargo Git identity admits a shared root; arbitrary parent directories do not."""
    source = Path(os.path.abspath(package["manifest_path"])).parent
    require(source.is_dir(), "Crate source directory is missing: " + str(source))
    require(not any(_redirected(p) for p in (source, *source.parents)),
            "Crate source path is redirected: " + str(source))
    context = {"source": source, "root": source, "kind": "crate", "tree": {}, "index": {}, "git_executable": git}
    if package.get("license_file"):
        declared_path = Path(package["license_file"])
        context["declared_notice"] = Path(os.path.abspath(declared_path if declared_path.is_absolute() else source / declared_path))
    declared = package.get("source", "")
    if not declared.startswith("git+"):
        return context
    match = re.fullmatch(r"git\+[^#\x00\r\n]+#([0-9a-f]{40})", declared)
    require(match is not None, "Cargo Git source needs an exact full revision: " + declared)
    revision = match[1]
    query_revision = re.search(r"[?&]rev=([0-9a-f]{40})(?:[&#]|$)", declared)
    require(query_revision is None or query_revision[1] == revision,
            "Cargo Git source revision query differs from its locked commit: " + declared)
    root = Path(os.path.abspath(_git(source, "rev-parse", "--show-toplevel", executable=git).decode().strip()))
    require(source.is_relative_to(root), "Crate is outside its Git checkout: " + str(source))
    require(not any(_redirected(p) for p in (root, *root.parents)),
            "Git source root is redirected: " + str(root))
    require(_git(root, "rev-parse", "HEAD", executable=git).decode().strip() == revision,
            "Git checkout HEAD differs from Cargo source revision: " + str(source))
    tree_raw = _git(root, "ls-tree", "-r", "-z", revision, executable=git)
    index_raw = _git(root, "ls-files", "--stage", "-z", executable=git)
    require(len(tree_raw) <= 16 * 1024 * 1024 and len(index_raw) <= 16 * 1024 * 1024,
            "Git notice inventory exceeds bounded bytes")
    tree, index = {}, {}
    for raw, destination, is_index in [(tree_raw, tree, False), (index_raw, index, True)]:
        entries = raw.rstrip(b"\0").split(b"\0") if raw else []
        require(len(entries) <= MAX_NOTICE_SCAN_ENTRIES, "Git notice inventory exceeds bounded entries")
        for entry in entries:
            prefix, filename = entry.split(b"\t", 1)
            fields = prefix.split()
            name = filename.decode("utf-8")
            require("\\" not in name and not Path(name).is_absolute()
                    and ".." not in Path(name).parts, "Unsafe Git notice path: " + name)
            if is_index:
                require(fields[2] == b"0", "Git notice index contains unresolved stages: " + name)
                value = (fields[0].decode(), fields[1].decode())
            else:
                value = (fields[0].decode(), fields[2].decode())
            require(name not in destination, "Duplicate Git notice path: " + name)
            destination[name] = value
    folded = {}
    for name in tree:
        folded.setdefault(name.casefold(), []).append(name)
    context.update(root=root, kind="pinned_git_checkout", revision=revision,
                   declared=declared, tree=tree, index=index, folded=folded)
    return context


def _git_entry(context, path):
    relative = path.relative_to(context["root"]).as_posix()
    if os.name == "nt":
        matches = context["folded"].get(relative.casefold(), [])
        require(len(matches) == 1, "Missing or ambiguous Windows Git notice path: " + relative)
        relative = matches[0]
    require(relative in context["tree"], "Notice is not tracked at the Cargo revision: " + relative)
    value = context["tree"][relative]
    require(context["index"].get(relative) == value,
            "Git notice index differs from the Cargo revision: " + relative)
    return relative, value


def _resolve_notice(path, source, context):
    path = Path(os.path.abspath(path))
    shared_declaration = (context["kind"] == "pinned_git_checkout"
                          and path == context.get("declared_notice")
                          and path.is_relative_to(context["root"]))
    require(path.is_relative_to(source) or shared_declaration,
            "Crate notice escapes its source: " + str(path))
    root, seen, chain = context["root"], set(), []
    current = path
    for _ in range(MAX_NOTICE_ALIAS_HOPS + 1):
        require(current.is_relative_to(root), "Crate notice alias escapes its source scope: " + str(current))
        if context.get("allowed_roots"):
            require(any(current.is_relative_to(admitted) for admitted in context["allowed_roots"]),
                    "Crate notice escapes declared source/cache: " + str(current))
        require(current not in seen, "Crate notice alias cycle: " + str(current))
        seen.add(current); _plain_parents(current)
        relative = current.relative_to(root).as_posix()
        git_name, entry = _git_entry(context, current) if context["kind"] == "pinned_git_checkout" else (relative, None)
        actual_link = current.is_symlink()
        git_link = entry is not None and entry[0] == "120000"
        info = current.lstat()
        require(not _redirected(current) or actual_link,
                "Crate notice contains a redirected non-symlink entry: " + str(current))
        if not actual_link and not git_link:
            require(stat.S_ISREG(info.st_mode), "Crate notice is not a regular file: " + str(current))
            if entry is not None:
                require(entry[0] in {"100644", "100755"}, "Invalid Git notice file mode: " + git_name)
            return current, chain, (git_name, entry)
        require(len(chain) < MAX_NOTICE_ALIAS_HOPS, "Crate notice alias chain exceeds bounded hops")
        if git_link:
            target_bytes = _git(root, "cat-file", "blob", entry[1], executable=context["git_executable"])
            require(len(target_bytes) <= 4096, "Git notice alias target exceeds bounded bytes")
            target = target_bytes.decode("utf-8")
            if actual_link:
                require(current.readlink() == Path(target),
                        "Git notice symlink differs from the pinned target: " + str(current))
            else:
                require(stat.S_ISREG(info.st_mode) and current.read_bytes() == target_bytes,
                        "Materialized Git notice alias differs from its pinned blob: " + str(current))
        else:
            require(context["kind"] == "crate", "Untracked Git notice alias: " + str(current))
            target = str(current.readlink())
        target_path = Path(target)
        require(target and not target_path.is_absolute() and not target_path.drive
                and not any(ch in target for ch in "\0\r\n"),
                "Crate notice alias target must be a relative file path: " + str(current))
        chain.append({"source": git_name, "target": target,
                      "representation": "symlink" if actual_link else "git_materialized",
                      **({"git_mode": "120000", "git_blob": entry[1]} if git_link else {})})
        current = Path(os.path.abspath(current.parent / target_path))
    raise ValueError("Crate notice alias chain exceeds bounded hops")


def notice_candidates(source, context=None):
    """No-follow walk; validate aliases before admitting their logical notice path."""
    source = Path(os.path.abspath(source))
    context = context or {"source": source, "root": source, "kind": "crate", "tree": {}, "index": {}}
    require(source.is_dir(), "Crate source directory is missing: " + str(source))
    require(not any(_redirected(p) for p in (source, *source.parents)),
            "Crate source path is redirected: " + str(source))
    pending, candidates, visited = [source], set(), 0
    while pending:
        folder = pending.pop()
        require(len(folder.relative_to(source).parts) <= MAX_NOTICE_SCAN_DEPTH,
                "Crate notice scan exceeds the bounded directory depth")
        with os.scandir(folder) as entries:
            for entry in entries:
                visited += 1
                require(visited <= MAX_NOTICE_SCAN_ENTRIES, "Crate notice scan exceeds the bounded entry count")
                path = Path(entry.path)
                require(path.is_relative_to(source), "Crate notice scan escapes its source: " + str(path))
                logical_notice = _notice_name(path, source) or path == context.get("declared_notice")
                git_alias = False
                if context["kind"] == "pinned_git_checkout":
                    relative = path.relative_to(context["root"]).as_posix()
                    matches = context["folded"].get(relative.casefold(), []) if os.name == "nt" else [relative]
                    git_alias = any(context["tree"].get(name, ("", ""))[0] == "120000" for name in matches)
                if entry.is_symlink() or _redirected(path) or git_alias:
                    require(logical_notice, "Crate notice scan contains a redirected entry: " + str(path))
                    _resolve_notice(path, source, context)
                    candidates.add(path)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                else:
                    require(entry.is_file(follow_symlinks=False),
                            "Crate notice scan contains a non-regular entry: " + str(path))
                    if logical_notice:
                        candidates.add(path)
                require(len(candidates) <= MAX_CRATE_NOTICE_FILES,
                        "Crate notice count exceeds the bounded file count")
    return candidates


def _regular_notice_bytes(path, scope):
    require(not any(_redirected(p) for p in (path, *path.parents)),
            "Crate notice path is redirected: " + str(path))
    require(path.is_relative_to(scope), "Crate notice escapes its source: " + str(path))
    before = path.stat()
    require(stat.S_ISREG(before.st_mode), "Crate notice is not a regular file: " + str(path))
    require(before.st_size <= 16 * 1024 * 1024, "Crate notice exceeds the bounded file size")
    with path.open("rb") as stream:
        content = stream.read(16 * 1024 * 1024 + 1)
        opened = os.fstat(stream.fileno())
    after = path.stat()
    fields = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink)
    require(fields(before) == fields(opened) == fields(after) and len(content) == before.st_size,
            "Crate notice changed during copy: " + str(path))
    require(content[:2] != b"MZ" and content[:4] not in (b"\x7fELF", b"PK\x03\x04"),
            "Compiled/container bytes are not a native source notice")
    return content


def notice_content(path, source, context=None):
    source = Path(os.path.abspath(source))
    context = context or {"source": source, "root": source, "kind": "crate", "tree": {}, "index": {}}
    resolved, chain, git_entry = _resolve_notice(path, source, context)
    content = _regular_notice_bytes(resolved, context["root"])
    if context["kind"] == "pinned_git_checkout":
        require(content == _git(context["root"], "cat-file", "blob", git_entry[1][1], executable=context["git_executable"]),
                "Git notice content differs from the Cargo revision: " + str(resolved))
    require((resolved, chain, git_entry) == _resolve_notice(path, source, context),
            "Crate notice alias changed during copy: " + str(path))
    provenance = None
    shared_declaration = not Path(os.path.abspath(path)).is_relative_to(source)
    if chain or shared_declaration:
        provenance = {"kind": context["kind"], "resolved_source_file": git_entry[0],
                      "alias_chain": chain}
        if shared_declaration:
            provenance["declaration"] = "Cargo license_file"
        if context["kind"] == "pinned_git_checkout":
            provenance.update(git_source=context["declared"], git_revision=context["revision"])
    return content, provenance


def _notice_bytes(path, source, context=None):
    return notice_content(path, source, context)[0]
# END PINNED BUILD-TIME NOTICE HELPERS

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
        context = source_context(package)
        manifest_bytes = _notice_bytes(manifest_path, source, context)
        key = package["name"] + "-" + package["version"] + "-" + hashlib.sha256(package["id"].encode()).hexdigest()[:8]
        require(Path(key).name == key and "/" not in key and "\\" not in key, "Unsafe crate identity")
        target_dir = output / "crates" / key
        try:
            candidates = notice_candidates(source, context)
        except ValueError as error:
            raise ValueError(f"Crate {package['name']}@{package['version']} ({package['source']}) root={source}: {error}") from error
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
            try:
                content, resolution = notice_content(path, source, context)
            except ValueError as error:
                raise ValueError(f"Crate {package['name']}@{package['version']} notice={path}: {error}") from error
            notice_bytes += len(content)
            require(notice_bytes <= MAX_CRATE_NOTICE_BYTES, "Crate notices exceed the bounded aggregate size")
            name = str(index) + "-" + path.name
            destination = target_dir / name
            destination.write_bytes(content)
            distributed = destination.relative_to(output).as_posix()
            notices.append(distributed)
            originals.append({"source_file": Path(os.path.relpath(path, source)).as_posix(),
                              "distributed_file": distributed,
                              "sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
                              **({"source_resolution": resolution} if resolution else {})})
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
