"""Qualify a normal default-manifest install on a disposable hosted Unix runner."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import queue
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
            peer = subprocess.Popen([binding["entry"], "app-server"], cwd=binding["cwd"],
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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--ipc-worker", type=Path)
    args = parser.parse_args()
    if args.ipc_worker:
        return ipc_worker(args.ipc_worker)
    if (os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or sys.platform not in {"darwin", "linux"}):
        raise ValueError("Use a disposable GitHub-hosted Unix runner")
    root, evidence = args.root.resolve(), args.evidence.resolve()
    if (root.exists() or evidence.exists() or root == evidence
            or root.is_relative_to(evidence) or evidence.is_relative_to(root)
            or root.is_relative_to(SOURCE) or evidence.is_relative_to(SOURCE)):
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
            result = run_observed(argv, cwd=SOURCE, env=env, log=log, timeout=timeout)
        record["processes"].append({"stage": label, **result})
        write(evidence/"qualification.json", record)
        if not result["passed"]:
            raise RuntimeError(label+" or owned-process cleanup failed")
    try:
        env = isolated_environment(root/"codex-home")
        run("install", [sys.executable, "-I", "-B", str(SOURCE/"install.py"),
                        "--root", str(root), "--no-activate"], env, 900)
        releases = [p for p in (root/"releases").iterdir() if (p/"installation.json").is_file()]
        if len(releases) != 1:
            raise ValueError("Expected exactly one fresh normal installation")
        release = releases[0]
        installation = json.loads((release/"installation.json").read_bytes())
        package = Path(installation["native_package"])
        archived = SOURCE/"native/runtime-overlays-pre-interactive-bootstrap-p35.json"
        identity = package_identity(package, archived)
        entry, python = release/"venv/bin/codex-deepseek", release/"venv/bin/python"
        run("version", [str(entry), "--version"], env, 45)
        origin_code = (
            "import pathlib,importlib.util,hashlib,json,codex_deepseek;"
            "source=pathlib.Path("+repr(str(SOURCE/"src"))+");rows=[]\n"
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
        distribution_version = validate_installed_origins(origins, release)
        binding = {"home": str(root/"codex-home"), "entry": str(entry),
                   "cwd": str(SOURCE), "output": str(evidence)}
        write(evidence/"ipc-binding.json", binding)
        run("normal-ipc", [str(python), "-I", "-B", str(Path(__file__).resolve()),
                          "--ipc-worker", str(evidence/"ipc-binding.json")], env, 300)
        ipc = json.loads((evidence/"ipc.json").read_bytes())
        if ipc["status"] != "passed_normal_wrapper_ipc":
            raise RuntimeError("Normal wrapper IPC probe failed")
        record.update(status="passed_normal_public_install", package=identity,
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
