"""Verify fixed GitHub build assets before running current source qualifications."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "0xZoharHuang/codex-deepseek-runtime"
RUN = 39899815305
HEAD = "9e5f4a5b4312b5036c8b13655273073df5c371aa"
ARTIFACTS = {
    "x86_64-unknown-linux-gnu": {
        "name": "cds-linux-x64-" + HEAD + "-1", "id": 12479937195,
        "github_digest": "sha256:4ef4dfb97ff8b37b7911bc5b4e8b712b6103cc22ceed9e071166ab93d20efc20",
        "manifest": "runtime-x86_64-unknown-linux-gnu.json",
        "archive_sha256": "3fe87429998f460406d91ca32aae0134ccc1bf5a95da3110735935c17dcb3cd5",
    },
    "aarch64-apple-darwin": {
        "name": "cds-macos-arm64-" + HEAD + "-1", "id": 12481979141,
        "github_digest": "sha256:b948a68683ca2774a631dc765153dc1731e8b7c0aa41b497d1f37c875d0fc523",
        "manifest": "runtime.json",
        "archive_sha256": "aef450933d716b0c9451d5a487eee59eb346c188c894f8fee1b8155c836052b0",
    },
}

def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def checked_members(archive, manifest, *, directories=None):
    """Validate every archive member before writing any path."""
    expected = manifest["files"]
    if not isinstance(expected, dict) or not expected:
        raise ValueError("No exact native file inventory")
    seen, members = {}, []
    with tarfile.open(archive, "r:gz") as stream:
        for item in stream:
            name = item.name
            if name == "." and item.isdir():
                if name in seen or item.mode & (stat.S_ISUID | stat.S_ISGID):
                    raise ValueError("Duplicate or privileged archive root")
                seen[name] = None
                continue
            if name.startswith("./"):
                name = name[2:]
            relative = PurePosixPath(name)
            if (not name or relative.is_absolute() or "\\" in name
                    or ":" in name or ".." in relative.parts or "." in relative.parts
                    or relative.as_posix() != name):
                raise ValueError("Unsafe archive member: " + name)
            if name in seen:
                raise ValueError("Duplicate archive member: " + name)
            if item.mode & (stat.S_ISUID | stat.S_ISGID):
                raise ValueError("Unexpected privileged archive mode")
            if item.isdir():
                structural_empty = (manifest.get("target") == "aarch64-apple-darwin"
                                    and name == "codex-resources")
                if not structural_empty and not any(n.startswith(name + "/") for n in expected):
                    raise ValueError("Unrecorded archive directory: " + name)
                seen[name] = None
                if directories is not None:
                    directories.append((name, item.mode & 0o777))
                continue
            if not item.isfile() or name not in expected:
                raise ValueError("Unrecorded or non-regular archive member: " + name)
            if item.mode & (stat.S_ISUID | stat.S_ISGID):
                raise ValueError("Unexpected privileged archive mode")
            with stream.extractfile(item) as data:
                digest = hashlib.file_digest(data, "sha256").hexdigest()
            if digest != expected[name]:
                raise ValueError("Native archive file bytes differ: " + name)
            seen[name] = digest
            members.append((name, item))
    if {n:h for n,h in seen.items() if h is not None} != expected:
        raise ValueError("Incomplete native archive inventory")
    return members

def extract_exact(archive, destination, manifest):
    if destination.exists() or destination.is_symlink():
        raise ValueError("Native destination must be fresh")
    expected_archive = manifest["archive"]["sha256"]
    if sha(archive) != expected_archive:
        raise ValueError("Archive checksum differs before extraction")
    directories = []
    members = checked_members(archive, manifest, directories=directories)
    destination.mkdir(parents=True)
    for name, mode in directories:
        destination.joinpath(*PurePosixPath(name).parts).mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as stream:
        for name, member in members:
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with stream.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)
            if sha(target) != manifest["files"][name]:
                raise ValueError("Extracted native bytes differ")
    for name, mode in sorted(directories, key=lambda item: len(PurePosixPath(item[0]).parts), reverse=True):
        destination.joinpath(*PurePosixPath(name).parts).chmod(mode)
    if sha(archive) != expected_archive:
        raise ValueError("Archive changed during extraction")
    return {p.relative_to(destination).as_posix():sha(p)
            for p in destination.rglob("*") if p.is_file()}

def verify_download_metadata(run, artifacts, target):
    selection = ARTIFACTS[target]
    if (run.get("id") != RUN or run.get("head_sha") != HEAD
            or run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("run_attempt") != 1
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("path") != ".github/workflows/native-platform-qualification.yml"):
        raise ValueError("Fixed GitHub build run identity/status differs")
    matches = [a for a in artifacts.get("artifacts", []) if a.get("name") == selection["name"]]
    if (len(matches) != 1 or matches[0].get("id") != selection["id"]
            or matches[0].get("expired") is not False
            or matches[0].get("digest") != selection["github_digest"]
            or matches[0].get("workflow_run", {}).get("id") != RUN
            or matches[0].get("workflow_run", {}).get("head_sha") != HEAD):
        raise ValueError("Fixed native artifact is missing, changed or expired; explicit rebuild required")
    return matches[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from build_native import build_target, build_assets
    from ci.qualify_native import package_identity
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise ValueError("Acquisition requires a disposable GitHub-hosted runner")
    target = build_target()
    selection = ARTIFACTS[target]
    for name in ("download", "package", "evidence", "source_archive"):
        setattr(args, name, getattr(args, name).resolve())
    roots = [args.download, args.package, args.evidence, args.source_archive]
    if any(p.exists() or p == ROOT or p.is_relative_to(ROOT) for p in roots):
        raise ValueError("Preserve previous outputs; fresh paths outside checkout required")
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for i, a in enumerate(roots) for b in roots[i+1:]):
        raise ValueError("Acquisition outputs must be disjoint")
    args.evidence.mkdir(parents=True)
    record = {"status": "running", "run_id": RUN, "head_sha": HEAD,
              "native_builds": 0, "model_calls": 0, "product_release_ready": False}
    try:
        args.download.mkdir(parents=True)
        def gh_api(endpoint, path):
            with path.open("wb") as output:
                subprocess.run(["gh", "api", "repos/" + REPOSITORY + endpoint],
                               check=True, stdout=output, timeout=90)
            return json.loads(path.read_bytes())
        run = gh_api("/actions/runs/" + str(RUN), args.evidence/"github-run.json")
        artifacts = gh_api("/actions/runs/" + str(RUN) + "/artifacts", args.evidence/"github-artifacts.json")
        artifact = verify_download_metadata(run, artifacts, target)
        subprocess.run(["gh", "run", "download", str(RUN), "--repo", REPOSITORY,
                        "--name", selection["name"], "--dir", str(args.download)],
                       check=True, timeout=600)
        if (args.download/"source-commit.txt").read_text().strip() != HEAD:
            raise ValueError("Downloaded provenance has a different source commit")
        manifest_path = ROOT/"native"/selection["manifest"]
        manifest_hash = sha(manifest_path)
        manifest = json.loads(manifest_path.read_bytes())
        if (manifest.get("target") != target or manifest.get("status") != "ready"
                or manifest["archive"]["sha256"] != selection["archive_sha256"]):
            raise ValueError("Current target manifest no longer selects this pinned artifact")
        archive = args.download/"native-package.tar.gz"
        if sha(archive) != selection["archive_sha256"]:
            raise ValueError("Downloaded package archive differs")
        files = extract_exact(archive, args.package, manifest)
        if files != manifest["files"]:
            raise ValueError("Extracted inventory differs from current target manifest")
        identity = package_identity(args.package, args.download/"runtime-overlays.json")
        url, digest = build_assets(target)["source"]
        args.source_archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=90) as source, args.source_archive.open("xb") as output:
            shutil.copyfileobj(source, output)
        if sha(args.source_archive) != digest or sha(manifest_path) != manifest_hash:
            raise ValueError("Pinned source archive or current manifest differs")
        record.update(status="passed", artifact_id=artifact["id"],
                      artifact_name=selection["name"],
                      github_artifact_digest_metadata=artifact["digest"],
                      archive_sha256=sha(archive), manifest_sha256=manifest_hash,
                      archive_regular_files=len(files), source_archive_sha256=digest,
                      original_build_receipt_preserved=True, package=identity)
    except BaseException as error:
        record.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        (args.evidence/"acquisition.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
