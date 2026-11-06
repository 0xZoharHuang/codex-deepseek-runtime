"""Qualify a normal default-manifest install on a disposable hosted runner."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import tomllib

SOURCE = Path(__file__).resolve().parents[1]

def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

def validate_installed_origins(origins, release, source=SOURCE):
    expected = tomllib.loads((Path(source)/"pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not isinstance(expected, str) or not expected.strip():
        raise ValueError("Source distribution version must be explicit")
    root = (Path(release)/"venv").resolve()
    if (origins["version"] != expected or len(origins["modules"]) != 32
            or not all(Path(row["origin"]).resolve().is_relative_to(root)
                       for row in origins["modules"])):
        raise ValueError("Installed distribution version or module origins differ")
    return expected

def isolated_environment(home):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CODEX_", "CDS_", "OPENAI_", "OPENROUTER_",
                                "ANTHROPIC_", "GITHUB_", "ACTIONS_", "OTEL_", "PYTHON"))
           and k not in {"GH_TOKEN", "GITHUB_TOKEN", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}}
    env.update(CODEX_DEEPSEEK_HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               OTEL_SDK_DISABLED="true")
    return env


def ipc_worker(binding_path):
    import psutil
    from codex_deepseek.processes import identity, matches
    binding = json.loads(Path(binding_path).read_bytes())
    home, output = Path(binding["home"]), Path(binding["output"])
    env = isolated_environment(home)
    env["OPENROUTER_API_KEY"] = "SYNTHETIC-NONFUNCTIONAL-RELEASE-IPC"
    record = {"status": "running", "model_requests": 0, "synthetic_credential_only": True}
    peer = None
    try:
        with (output/"ipc.stderr").open("wb") as stderr:
            factory = subprocess.Popen
            if sys.platform == "win32":
                sys.path.insert(0, str(SOURCE))
                from ci.windows_stdio_probe import TracedPopen
                factory = TracedPopen
            peer = factory([binding["entry"], "app-server"], cwd=binding["cwd"],
                                    env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=stderr, text=True, encoding="utf-8", bufsize=1)
            record.update(pid=peer.pid, birth=identity(peer.pid))
            answers = queue.Queue()
            def read():
                try:
                    for line in peer.stdout:
                        answers.put(json.loads(line))
                except BaseException as error:
                    answers.put({"reader_error": type(error).__name__})
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            peer.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "public_release_install", "version": "1"},
                           "capabilities": {"experimentalApi": True}}}) + "\n")
            peer.stdin.flush()
            deadline = time.monotonic() + 45
            while True:
                answer = answers.get(timeout=max(.1, deadline-time.monotonic()))
                if answer.get("id") == 1:
                    break
                if "reader_error" in answer or time.monotonic() >= deadline:
                    raise RuntimeError("Native initialize did not complete")
            if "error" in answer or Path(answer["result"]["codexHome"]).resolve() != home.resolve():
                raise RuntimeError("Native initialize error or home mismatch")
            record["initialize"] = answer["result"]
            peer.stdin.write('{"jsonrpc":"2.0","method":"initialized"}\n')
            peer.stdin.flush()
            family = []
            for process in [psutil.Process(peer.pid), *psutil.Process(peer.pid).children(recursive=True)]:
                birth = identity(process.pid)
                if birth is not None:
                    family.append({"pid": process.pid, "birth": birth})
            peer.stdin.close()
            record["eof_exit"] = peer.wait(timeout=30)
            reader.join(timeout=10)
            if record["eof_exit"] or reader.is_alive():
                record["reader_alive_at_eof"] = reader.is_alive()
                try:
                    from ci.windows_stdio_probe import writer_holders
                    record["handle_diagnostics"] = writer_holders(peer, family)
                except BaseException as diagnostic_error:
                    record["handle_diagnostics"] = {"error": repr(diagnostic_error)}
                raise RuntimeError("Normal native EOF did not finish")
            deadline = time.monotonic() + 180
            while any(matches(row["pid"], row["birth"]) for row in family) and time.monotonic() < deadline:
                time.sleep(1)
            record["remaining"] = [r for r in family if matches(r["pid"], r["birth"])]
            if record["remaining"]:
                raise RuntimeError("Owned runtime/provider failed normal idle retirement")
        import sqlite3
        with sqlite3.connect((home/"provider-state/adapter.sqlite").as_uri()+"?mode=ro", uri=True) as db:
            count = db.execute("SELECT count(*) FROM receipts").fetchone()[0]
        if count:
            raise RuntimeError("The IPC-only probe unexpectedly made model requests")
        record.update(status="passed_normal_wrapper_ipc", model_receipts=count)
    except BaseException as error:
        record.update(status="failed", error=repr(error), traceback=traceback.format_exc())
    finally:
        if peer is not None and peer.poll() is None:
            try:
                peer.stdin.close()
                peer.wait(timeout=20)
            except BaseException:
                # Exact Popen handle only; the outer observer owns descendant cleanup.
                peer.terminate()
                peer.wait(timeout=10)
        write(output/"ipc.json", record)
    return 0 if record["status"] == "passed_normal_wrapper_ipc" else 1

WINDOWS_PUBLIC_REPO = "https://github.com/0xZoharHuang/codex-deepseek-runtime"


def windows_public_refs():
    tag = os.environ["RELEASE_TAG"]
    archive_tag = os.environ.get("RELEASE_ARCHIVE_TAG") or tag
    version = r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?"
    if not all(re.fullmatch(version, v) for v in (tag, archive_tag)):
        raise ValueError("Explicit version tags are required")
    for name, length in (("RELEASE_COMMIT", 40), ("RELEASE_MANIFEST_SHA256", 64),
                         ("RELEASE_ARCHIVE_SHA256", 64), ("RELEASE_SOURCE_ID", 20)):
        if not re.fullmatch(fr"[0-9a-f]{{{length}}}", os.environ[name]):
            raise ValueError(f"Invalid exact pin for {name}")
    branch = os.environ.get("RELEASE_CANDIDATE_BRANCH", "")
    if branch:
        ref = "refs/heads/" + branch
        if not re.fullmatch(r"codex/[a-zA-Z0-9._/-]+", branch):
            raise ValueError("Candidate requires an explicit codex/ branch")
        subprocess.run(["git", "check-ref-format", ref], check=True, timeout=10)
        return ref, "refs/remotes/origin/reviewed-candidate", archive_tag
    ref = "refs/tags/" + tag
    return ref, ref, archive_tag


def check_direct_windows_runner():
    import urllib.request

    if (os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted" or sys.platform != "win32"
            or os.environ.get("GITHUB_REPOSITORY") != "0xZoharHuang/codex-deepseek-runtime"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise ValueError("Use the trusted main workflow on a disposable hosted Windows runner")
    if {"http", "https", "all"} & urllib.request.getproxies().keys():
        raise ValueError("Direct consumer gate requires a runner without a proxy")


def prepare_windows_public_source(source):
    check_direct_windows_runner()
    remote_ref, local_ref, _ = windows_public_refs()
    env = isolated_environment(source)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if source.parent != Path(os.environ["RUNNER_TEMP"]).resolve() or source.exists():
        raise ValueError("Fetch requires a fresh dedicated checkout in RUNNER_TEMP")
    def git(*args):
        return subprocess.check_output(["git", "-c", "credential.helper=", "-C", str(source),
                                        *args], env=env, text=True, timeout=180).strip()
    source.mkdir()
    git("init", "--quiet")
    git("config", "core.autocrlf", "false")
    git("remote", "add", "origin", WINDOWS_PUBLIC_REPO + ".git")
    git("fetch", "--depth=1", "--no-tags", "origin", f"{remote_ref}:{local_ref}")
    if git("rev-parse", local_ref + "^{commit}") != os.environ["RELEASE_COMMIT"]:
        raise ValueError("Fetched reference differs from the reviewed commit")
    git("checkout", "--detach", os.environ["RELEASE_COMMIT"])
    # Verify before the workflow can consume this checkout's dependency lock.
    windows_public_pin(source, env)

def windows_public_pin(source, env):
    tag, commit = os.environ["RELEASE_TAG"], os.environ["RELEASE_COMMIT"]
    source_ref, local_ref, archive_tag = windows_public_refs()
    repo = WINDOWS_PUBLIC_REPO
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], env=env,
                                       text=True, timeout=30).strip()
    if (git("remote", "get-url", "origin") != repo + ".git" or git("rev-parse", "HEAD") != commit
            or git("rev-parse", local_ref + "^{commit}") != commit or git("status", "--porcelain")):
        raise ValueError("Public checkout differs from the pinned release")
    manifest_path = source / "native/runtime-x86_64-pc-windows-msvc.json"
    if sha(manifest_path) != os.environ["RELEASE_MANIFEST_SHA256"]:
        raise ValueError("Public Windows manifest hash differs")
    manifest = json.loads(manifest_path.read_bytes())
    archive = manifest["archive"]
    if (manifest["status"] != "ready" or manifest["target"] != "x86_64-pc-windows-msvc"
            or tag != "v" + manifest["distribution_version"]
            or archive["sha256"] != os.environ["RELEASE_ARCHIVE_SHA256"]
            or archive["url"] != f"{repo}/releases/download/{archive_tag}/{archive['filename']}"):
        raise ValueError("Default manifest must identify the pinned public asset")
    candidate = source_ref.startswith("refs/heads/")
    return manifest, {"source_kind": "candidate_branch" if candidate else "published_tag",
                      "source_ref": source_ref, "release_tag": tag,
                      "public_tag": None if candidate else tag, "public_commit": commit,
                      "archive_tag": archive_tag, "archive_url": archive["url"],
                      "verifier_commit": os.environ.get("GITHUB_SHA"),
                      "manifest_sha256": sha(manifest_path), "direct_transport": True}


def windows_installed_identity(source, release, root, installation, manifest):
    # Use the tagged installer's complete physical package verifier, including
    # reparse, hardlink, extra-file and missing-file checks.
    spec = importlib.util.spec_from_file_location("public_install", source / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    package = Path(installation["native_package"]).resolve()
    descriptor = manifest["archive"]
    archive = root / "downloads" / descriptor["sha256"][:24] / descriptor["filename"]
    if (not package.is_relative_to(root / "native") or sha(archive) != descriptor["sha256"]
            or json.loads((release / "native-runtime.json").read_bytes()) != manifest
            or installation["source_id"] != os.environ["RELEASE_SOURCE_ID"]
            or installation["native_recipe_id"] != manifest["native_recipe_id"]
            or installation["native_archive_sha256"] != descriptor["sha256"]
            or sha(installer.console_entry(release / "venv")) != installation["console_script_sha256"]):
        raise ValueError("Installed bytes or their identities differ from the public release")
    files = installer.native_package_files(package, manifest)
    return {"native_files": len(files), "native_cli_sha256": files["bin/codex.exe"],
            "native_recipe_id": manifest["native_recipe_id"], "archive_bytes": archive.stat().st_size,
            "downloaded_archive_sha256": descriptor["sha256"]}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--prepare-windows-public-source", type=Path)
    parser.add_argument("--public-windows-source", type=Path)
    parser.add_argument("--ipc-worker", type=Path)
    args = parser.parse_args()
    if args.prepare_windows_public_source:
        prepare_windows_public_source(args.prepare_windows_public_source.resolve())
        return 0
    if args.ipc_worker:
        return ipc_worker(args.ipc_worker)
    windows = args.public_windows_source is not None
    source = args.public_windows_source.resolve(strict=True) if windows else SOURCE
    if (os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or sys.platform not in ({"win32"} if windows else {"darwin", "linux"})):
        raise ValueError("Use a disposable GitHub-hosted runner for the selected platform")
    root, evidence = args.root.resolve(), args.evidence.resolve()
    if (root.exists() or evidence.exists() or root == evidence
            or root.is_relative_to(evidence) or evidence.is_relative_to(root)
            or any(p.is_relative_to(s) or s.is_relative_to(p) for p in (root, evidence) for s in (SOURCE, source))):
        raise ValueError("Fresh disjoint roots outside the source are required")
    evidence.mkdir(parents=True)
    sys.path.insert(0, str(SOURCE))
    from ci.qualify_native import run_observed, package_identity
    record = {"status": "running", "platform": platform.platform(),
              "root": str(root), "normal_manifest_override": False,
              "native_archive_override": False, "model_requests": 0,
              "native_builds": 0, "processes": []}
    def run(label, argv, env, timeout):
        with (evidence/(label+".log")).open("wb") as log:
            result = run_observed(argv, cwd=source, env=env, log=log, timeout=timeout)
        record["processes"].append({"stage": label, **result})
        write(evidence/"qualification.json", record)
        if not result["passed"]:
            raise RuntimeError(label+" or owned-process cleanup failed")
    try:
        env = isolated_environment(root/"codex-home")
        if windows:
            check_direct_windows_runner()
            manifest, pin = windows_public_pin(source, env)
            record.update(pin)
        run("install", [sys.executable, "-I", "-B", str(source/"install.py"),
                        "--root", str(root), "--no-activate"], env, 900)
        releases = [p for p in (root/"releases").iterdir() if (p/"installation.json").is_file()]
        if len(releases) != 1:
            raise ValueError("Expected exactly one fresh normal installation")
        release = releases[0]
        installation = json.loads((release/"installation.json").read_bytes())
        package = Path(installation["native_package"])
        if windows:
            identity = windows_installed_identity(source, release, root, installation, manifest)
            entry, python = release/"venv/Scripts/codex-deepseek.exe", release/"venv/Scripts/python.exe"
        else:
            archived = SOURCE/"native/runtime-overlays-pre-interactive-bootstrap-p35.json"
            identity = package_identity(package, archived)
            entry, python = release/"venv/bin/codex-deepseek", release/"venv/bin/python"
        run("version", [str(entry), "--version"], env, 45)
        origin_code = (
            "import pathlib,importlib.util,hashlib,json,codex_deepseek;"
            "source=pathlib.Path("+repr(str(source/"src"))+");rows=[]\n"
            "for path in sorted((source/'codex_deepseek').rglob('*.py')):\n"
            " parts=list(path.relative_to(source).with_suffix('').parts)\n"
            " if parts[-1]=='__init__':parts=parts[:-1]\n"
            " name='.'.join(parts);spec=importlib.util.find_spec(name);actual=pathlib.Path(spec.origin)\n"
            " expected=hashlib.sha256(path.read_bytes()).hexdigest();assert hashlib.sha256(actual.read_bytes()).hexdigest()==expected\n"
            " rows.append({'module':name,'origin':str(actual),'sha256':expected})\n"
            "print(json.dumps({'version':codex_deepseek.__version__,'modules':rows}))")
        origin = subprocess.run([str(python), "-I", "-B", "-c", origin_code], env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=45)
        (evidence/"origins.stdout").write_text(origin.stdout, encoding="utf-8")
        (evidence/"origins.stderr").write_text(origin.stderr, encoding="utf-8")
        if origin.returncode:
            raise RuntimeError("Installed module origin verification failed")
        origins = json.loads(origin.stdout)
        distribution_version = validate_installed_origins(origins, release, source)
        binding = {"home": str(root/"codex-home"), "entry": str(entry),
                   "cwd": str(source), "output": str(evidence)}
        write(evidence/"ipc-binding.json", binding)
        run("normal-ipc", [str(python), "-I", "-B", str(Path(__file__).resolve()),
                          "--ipc-worker", str(evidence/"ipc-binding.json")], env, 300)
        ipc = json.loads((evidence/"ipc.json").read_bytes())
        if ipc["status"] != "passed_normal_wrapper_ipc":
            raise RuntimeError("Normal wrapper IPC probe failed")
        if windows and (ipc["initialize"].get("platformOs") != "windows"
                        or manifest["upstream_version"] not in ipc["initialize"]["userAgent"]
                        or manifest["upstream_version"] not in (evidence/"version.log").read_text(encoding="utf-8")):
            raise RuntimeError("Installed CLI/native app-server version or platform mismatch")
        status = ("passed_reviewed_candidate_install" if record.get("source_kind") == "candidate_branch"
                  else "passed_normal_public_install")
        record.update(status=status, package=identity,
                      native_package=str(package), installation_sha256=sha(release/"installation.json"),
                      source_id=installation["source_id"],
                      native_archive_sha256=installation["native_archive_sha256"],
                      installed_modules=32, distribution_version=distribution_version, no_activate=True,
                      normal_eof_exit=ipc["eof_exit"], model_receipts=0)
    except BaseException as error:
        record.update(status="failed", error=repr(error), traceback=traceback.format_exc())
        raise
    finally:
        record["finished_at"] = time.time()
        write(evidence/"qualification.json", record)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
