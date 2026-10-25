"""Reuse one explicitly qualified native build; never rebuild or change model runtime."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com/"
RELEASE_TAG = "v0.6.2"
RECIPE_ASSET = "native-build-recipe-5c9e004f.json"
RELEASE_BASE = "https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/"+RELEASE_TAG
MAX_SOURCE_BYTES = 512 * 1024 * 1024


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unique_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def selected_pin(pins, target):
    if pins.get("schema") != 1 or pins.get("repository") != "0xZoharHuang/codex-deepseek-runtime":
        raise ValueError("Unknown qualified build repository/schema")
    if target not in {"x86_64-unknown-linux-gnu", "aarch64-apple-darwin"}:
        raise ValueError("Qualified build reuse is limited to Unix targets")
    if pins.get("source") not in {"actions-artifact","release-asset"}:
        raise ValueError("Unknown acquisition source; no automatic fallback")
    pin = pins["targets"][target]
    if sha(ROOT/"native/runtime-overlays.json") != pins["recipe_sha256"]:
        raise ValueError("Full current native recipe differs; reuse requires exact equality")
    manifest_path = ROOT/"native"/pin["manifest"]
    if sha(manifest_path) != pin["manifest_sha256"]:
        raise ValueError("Current target manifest differs from the reviewed candidate")
    manifest = unique_json(manifest_path.read_bytes())
    archive = manifest.get("archive", {})
    if (manifest.get("target") != target or manifest.get("distribution_version") != "0.6.2"
            or manifest.get("status") != "ready"
            or archive.get("sha256") != pin["archive_sha256"]
            or len(manifest.get("files", {})) != pin["file_count"]
            or manifest["files"].get("build-receipt.json") != pin["receipt_sha256"]):
        raise ValueError("Qualified target manifest is incomplete or selects another native build")
    return pin, manifest


def validate_run(value, pins):
    if (value.get("id") != pins["run_id"] or value.get("run_attempt") != pins["run_attempt"]
            or value.get("head_sha") != pins["producer_commit"]
            or value.get("path") != pins["workflow_path"]
            or value.get("status") != "completed" or value.get("conclusion") != "success"
            or value.get("repository", {}).get("id") != pins["repository_id"]
            or value.get("repository", {}).get("full_name") != pins["repository"]):
        raise ValueError("Original GitHub build is not the exact successful producer")


def validate_artifact(value, pins, pin):
    run = value.get("workflow_run", {})
    if (value.get("id") != pin["artifact_id"] or value.get("name") != pin["artifact_name"]
            or value.get("expired") is not False or value.get("size_in_bytes") != pin["zip_bytes"]
            or value.get("digest") != "sha256:"+pin["zip_sha256"]
            or run.get("id") != pins["run_id"] or run.get("head_sha") != pins["producer_commit"]
            or run.get("repository_id") != pins["repository_id"]
            or run.get("head_repository_id") != pins["repository_id"]):
        raise ValueError("Artifact digest, identity, origin or expiry differs")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def api_get(path, token, *, redirect=False):
    if not path.startswith("repos/0xZoharHuang/codex-deepseek-runtime/actions/"):
        raise ValueError("Unexpected GitHub API path")
    request = urllib.request.Request(API+path, headers={
        "Accept": "application/vnd.github+json", "Authorization": "Bearer "+token,
        "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "cds-qualified-native-reuse"})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=60) as response:
            if redirect:
                raise ValueError("Expected an official artifact redirect")
            raw = response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:
                raise ValueError("GitHub API metadata exceeds bound")
            return unique_json(raw)
    except urllib.error.HTTPError as error:
        if not redirect or error.code != 302:
            raise
        location = error.headers.get("Location", "")
        value = urllib.parse.urlsplit(location)
        if (value.scheme != "https" or not value.hostname or value.username or value.password
                or value.port not in (None,443)):
            raise ValueError("Artifact download location is not HTTPS")
        # A separate, unauthenticated request prevents forwarding the API token
        # to GitHub's short-lived storage URL. Do not record its signed query.
        return location


def download(url, path, expected_hash, *, exact_bytes=None, maximum=MAX_SOURCE_BYTES):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError("Download output must be fresh")
    limit = exact_bytes if exact_bytes is not None else maximum
    received = 0
    with urllib.request.urlopen(url, timeout=90) as source, path.open("xb") as target:
        while chunk := source.read(min(1024*1024, limit+1-received)):
            received += len(chunk)
            if received > limit:
                raise ValueError("Download exceeded byte bound")
            target.write(chunk)
    if (exact_bytes is not None and received != exact_bytes) or sha(path) != expected_hash:
        raise ValueError("Downloaded bytes differ from the reviewed input")
    return received


def zip_inputs(path, pin, pins, directory):
    if Path(path).stat().st_size != pin["zip_bytes"] or sha(path) != pin["zip_sha256"]:
        raise ValueError("Artifact archive checksum or size differs")
    expected = {"native-package.tar.gz":(pin["archive_bytes"],pin["archive_sha256"]),
                "runtime-overlays.json":(pins["recipe_bytes"],pins["recipe_sha256"]),
                "source-commit.txt":(41,None)}
    with zipfile.ZipFile(path) as source:
        names = source.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate artifact member")
        for name, (size, wanted) in expected.items():
            member = source.getinfo(name)
            if member.is_dir() or member.flag_bits & 1 or member.file_size != size:
                raise ValueError("Artifact member type/size differs")
            output = directory/name
            with source.open(member) as stream, output.open("xb") as target:
                shutil.copyfileobj(stream,target,1024*1024)
            if output.stat().st_size != size or (wanted and sha(output) != wanted):
                raise ValueError("Artifact member bytes differ")
        if (directory/"source-commit.txt").read_bytes() != (pins["producer_commit"]+"\n").encode():
            raise ValueError("Artifact source commit differs")
    return directory/"native-package.tar.gz", directory/"runtime-overlays.json"



def release_inputs(pins, pin, manifest):
    """The stable route accepts only the same v0.6.2 package and original recipe."""
    if pins.get("source") != "release-asset":
        raise ValueError("Release assets require an explicit acquisition source")
    target=manifest.get("target")
    if target not in {"x86_64-unknown-linux-gnu","aarch64-apple-darwin"}:
        raise ValueError("Unsupported stable reuse target")
    filename="codex-deepseek-native-0.6.2-"+target+".tar.gz"
    expected_url=RELEASE_BASE+"/"+filename
    archive=manifest.get("archive",{})
    if (manifest.get("distribution_version")!="0.6.2" or manifest.get("status")!="ready"
            or archive.get("filename")!=filename or archive.get("url")!=expected_url
            or archive.get("sha256")!=pin["archive_sha256"]):
        raise ValueError("Stable manifest must select the exact official v0.6.2 asset")
    return {"archive":{"url":expected_url,"sha256":pin["archive_sha256"],"bytes":pin["archive_bytes"]},
            "recipe":{"url":RELEASE_BASE+"/"+RECIPE_ASSET,"sha256":pins["recipe_sha256"],"bytes":pins["recipe_bytes"]}}


def acquire_inputs(pins, pin, manifest, directory):
    """Choose the declared source once; errors never select a different route."""
    mode=pins.get("source")
    if mode=="release-asset":
        inputs=release_inputs(pins,pin,manifest)
        archive=directory/"native-package.tar.gz";recipe=directory/"runtime-overlays.json"
        for item,path in [(inputs["archive"],archive),(inputs["recipe"],recipe)]:
            download(item["url"],path,item["sha256"],exact_bytes=item["bytes"])
        return archive,recipe,{"acquisition":"pinned_public_release_assets","release_tag":RELEASE_TAG,
                              "original_build_metadata_checked_live":False,"release_downloads":inputs}
    if mode!="actions-artifact":
        raise ValueError("Unknown acquisition source; no automatic fallback")
    token=os.environ.get("GITHUB_TOKEN","")
    if not token:
        raise ValueError("Explicit read-only Actions token is required")
    base="repos/"+pins["repository"]+"/actions/"
    run=api_get(base+"runs/"+str(pins["run_id"]),token);validate_run(run,pins)
    artifact=api_get(base+"artifacts/"+str(pin["artifact_id"]),token)
    validate_artifact(artifact,pins,pin)
    location=api_get(base+"artifacts/"+str(pin["artifact_id"])+"/zip",token,redirect=True)
    zip_path=directory/"artifact.zip"
    download(location,zip_path,pin["zip_sha256"],exact_bytes=pin["zip_bytes"])
    archive,recipe=zip_inputs(zip_path,pin,pins,directory)
    return archive,recipe,{"acquisition":"pinned_successful_GitHub_build_artifact",
                          "original_build_metadata_checked_live":True,"original_run":run,"original_artifact":artifact}


def main():
    parser=argparse.ArgumentParser()
    for name in ("download","package","evidence","source-archive"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(ROOT))
    from build_native import build_target,build_assets
    from ci.reuse_native import extract_exact
    from ci.qualify_native import package_identity
    if os.environ.get("RUNNER_ENVIRONMENT")!="github-hosted":
        raise ValueError("Acquisition requires a disposable GitHub-hosted runner")
    target=build_target()
    pins_path=ROOT/"ci/qualified-native-build.json"
    pins_hash=sha(pins_path);pins=unique_json(pins_path.read_bytes())
    pin,manifest=selected_pin(pins,target)
    paths=[getattr(args,n).resolve() for n in ("download","package","evidence","source_archive")]
    if any(p.exists() or p==ROOT or p.is_relative_to(ROOT) for p in paths):
        raise ValueError("Fresh acquisition paths outside checkout required")
    if any(a==b or a.is_relative_to(b) or b.is_relative_to(a)
           for i,a in enumerate(paths) for b in paths[i+1:]):
        raise ValueError("Acquisition outputs must be disjoint")
    args.download,args.package,args.evidence,args.source_archive=paths
    args.evidence.mkdir(parents=True);args.download.mkdir(parents=True)
    record={"status":"running","source_mode":pins["source"],
            "original_build":{"run_id":pins["run_id"],"producer_commit":pins["producer_commit"]},
            "target":target,"qualified_input_manifest_sha256":pins_hash,
            "native_builds":0,"model_requests":0,"product_release_ready":False}
    try:
        archive,recipe,provenance=acquire_inputs(pins,pin,manifest,args.download)
        record.update(provenance)
        files=extract_exact(archive,args.package,manifest)
        if files != manifest["files"]:
            raise ValueError("Extracted full package differs from current manifest")
        identity=package_identity(args.package,recipe)
        url,digest=build_assets(target)["source"]
        args.source_archive.parent.mkdir(parents=True,exist_ok=True)
        source_bytes=download(url,args.source_archive,digest)
        if sha(pins_path)!=pins_hash:
            raise ValueError("Qualified build selection changed during acquisition")
        selected_pin(pins,target)
        record.update(status="passed",package=identity,zip_sha256=pin["zip_sha256"],
                      archive_sha256=pin["archive_sha256"],recipe_sha256=sha(recipe),
                      archive_regular_files=len(files),source_archive_sha256=digest,
                      source_archive_bytes=source_bytes,original_build_receipt_preserved=True)
    except BaseException as error:
        record.update(status="failed",error_type=type(error).__name__,error=str(error))
        raise
    finally:
        (args.evidence/"acquisition.json").write_text(json.dumps(record,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
