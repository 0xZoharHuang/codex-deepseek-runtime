"""Test-only hosted Windows 2025 gate for an exact reviewed P40 release candidate."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib
import traceback
import urllib.request

REPO = "0xZoharHuang/codex-deepseek-runtime"
REF = "refs/heads/codex/windows-p40-installed-gate-r605"
ARCHIVE_SHA = "d9b8d73184bfac47842505c8e540d132ebb564d443b0ad55722d7583a8bde418"
ARCHIVE_SIZE = 204367729
NATIVE_SHA = "8c84247eef4fbd757acbedae4c8a0c331b45643a3403a416887f59bc2c19fd0c"
IPC_SHA = "40bc7b97f9c114b1ffc40c6134c337e7b0bf5f8470177d7d07b8497fe47713dd"
OBSERVER_SHA = "13a771b041aaadcdd7d37ba129bf9e82f2dff73b57abfbbc5f0812ac17c62deb"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    import hashlib
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def clean_environment():
    prefixes = ("CODEX_", "CDS_", "OPENAI_", "OPENROUTER_", "ANTHROPIC_", "GITHUB_",
                "GH_", "ACTIONS_", "OTEL_", "PYTHON", "PIP_")
    return {key: value for key, value in os.environ.items() if
            not key.upper().startswith(prefixes) and
            not any(word in key.upper() for word in ("TOKEN", "PASSWORD", "SECRET")) and
            key.upper() not in {"VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}}


def pins():
    names = (("RELEASE_COMMIT", 40), ("RELEASE_MANIFEST_SHA256", 64), ("RELEASE_SOURCE_ID", 20))
    result = {}
    for name, length in names:
        value = os.environ[name]
        require(re.fullmatch(fr"[0-9a-f]{{{length}}}", value), "Invalid exact release pin: " + name)
        result[name] = value
    return result


def prepare(source, values):
    require(not source.exists(), "A fresh independent reviewed source checkout is required")
    source.mkdir()
    env = clean_environment()
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    def git(*args):
        return subprocess.check_output(["git", "-c", "credential.helper=", "-C", str(source), *args],
                                       env=env, timeout=120).decode("utf8").strip()
    commit = values["RELEASE_COMMIT"]
    git("init", "--quiet")
    git("config", "core.autocrlf", "false")
    git("remote", "add", "origin", "https://github.com/" + REPO + ".git")
    git("fetch", "--depth=1", "--no-tags", "origin", commit)
    git("checkout", "--detach", commit)
    require(git("rev-parse", "HEAD") == commit and not git("status", "--porcelain=v1", "-uall"),
            "Reviewed source checkout differs")
    return verify_source(source, values)


def verify_source(source, values):
    script = source / "ci/qualify_installed_release.py"
    require(sha(script) == IPC_SHA and sha(source/"ci/qualify_native.py") == OBSERVER_SHA,
            "Original strict IPC worker or process observer changed")
    manifest = source/"native/runtime-x86_64-pc-windows-msvc.json"
    require(sha(manifest) == values["RELEASE_MANIFEST_SHA256"], "Default Windows manifest differs")
    value = json.loads(manifest.read_bytes())
    require(value["status"] == "ready" and value["target"] == "x86_64-pc-windows-msvc"
            and value["distribution_version"] == "0.7.1" and value["archive"]["sha256"] == ARCHIVE_SHA
            and value["files"]["bin/codex.exe"] == NATIVE_SHA,
            "Reviewed default Windows manifest does not identify P40")
    version = tomllib.loads((source/"pyproject.toml").read_text(encoding="utf8"))["project"]["version"]
    require(version == "0.7.1", "Reviewed source version differs")
    return value


def download(archive):
    asset = os.environ["P40_ASSET_ID"]
    require(re.fullmatch(r"[1-9][0-9]+", asset) and bool(os.environ.get("GH_TOKEN")),
            "Explicit release asset and read-only token required")
    partial = archive.with_suffix(".part")
    require(not archive.exists(), "Use a fresh native archive path")
    with partial.open("xb") as out:
        result = subprocess.run(["gh", "api", "-H", "Accept: application/octet-stream",
                                 f"/repos/{REPO}/releases/assets/{asset}"],
                                stdout=out, stderr=subprocess.PIPE, timeout=120)
    require(result.returncode == 0, "Draft asset download failed; inspect action status")
    require(partial.stat().st_size == ARCHIVE_SIZE and sha(partial) == ARCHIVE_SHA,
            "Asset body is not the exact verified P40 archive")
    partial.rename(archive)


def qualify(source, archive, root, evidence, values):
    require(not root.exists() and not evidence.exists(), "Fresh install and evidence roots required")
    manifest = verify_source(source, values)
    require(archive.stat().st_size == ARCHIVE_SIZE and sha(archive) == ARCHIVE_SHA,
            "Local native archive differs at install time")
    env = clean_environment()
    sys.path[:0] = [str(source), str(source/"src")]
    from ci.qualify_native import run_observed
    qa = load("reviewed_installed_gate", source/"ci/qualify_installed_release.py")
    install = load("reviewed_installer", source/"install.py")
    source_id, files = install.source_identity()
    require(source_id == values["RELEASE_SOURCE_ID"], "Source ID differs from the reviewed release")
    evidence.mkdir()
    home = root/"codex-home"
    env.update(CODEX_DEEPSEEK_HOME=str(home), PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", OTEL_SDK_DISABLED="true", PIP_CONFIG_FILE=os.devnull)
    record = {"status":"running","scope":"Windows 2025 P40 no-model native IPC gate",
              "source_commit":values["RELEASE_COMMIT"],"source_id":source_id,
              "manifest_sha256":values["RELEASE_MANIFEST_SHA256"],"archive_sha256":ARCHIVE_SHA,
              "archive_bytes":ARCHIVE_SIZE,"native_archive_local_override":True,
              "native_manifest_override":False,"no_activate":True,"processes":[]}
    def run(stage, args, timeout):
        with (evidence/(stage+".log")).open("xb") as log:
            result = run_observed(args, cwd=source, env=env, log=log, timeout=timeout)
        record["processes"].append({"stage":stage, **result})
        qa.write(evidence/"qualification.json", record)
        require(result["passed"] and not result["forced_cleanup"], stage+" or owned natural retirement failed")
        return result
    try:
        run("install", [sys.executable,"-I","-B",str(source/"install.py"),"--root",str(root),
                        "--native-archive",str(archive),"--no-activate"], 900)
        release, = [p for p in (root/"releases").iterdir() if (p/"installation.json").is_file()]
        receipt = release/"installation.json"
        installation = json.loads(receipt.read_bytes())
        require(installation["source_sha256"] == files, "Installed release source inventory differs")
        identity = qa.windows_installed_identity(source, release, root, installation, manifest)
        entry = release/"venv/Scripts/codex-deepseek.exe"
        python = release/"venv/Scripts/python.exe"
        run("version", [str(entry),"--version"], 45)
        code = """import hashlib, importlib.util, json, pathlib, sys, codex_deepseek
source=pathlib.Path(sys.argv[1]);rows=[]
for p in sorted((source/'codex_deepseek').rglob('*.py')):
 parts=list(p.relative_to(source).with_suffix('').parts)
 name='.'.join(parts[:-1] if parts[-1]=='__init__' else parts)
 actual=pathlib.Path(importlib.util.find_spec(name).origin)
 rows.append({'module':name,'origin':str(actual),'sha256':hashlib.sha256(actual.read_bytes()).hexdigest()})
print(json.dumps({'version':codex_deepseek.__version__,'modules':rows}))
"""
        run("origins", [str(python),"-I","-B","-c",code,str(source/"src")], 45)
        origins = json.loads((evidence/"origins.log").read_text(encoding="utf8"))
        require(len(origins["modules"]) == 33 and qa.validate_installed_origins(origins, release, source) == "0.7.1",
                "Installed 33 module origins differ")
        binding = evidence/"ipc-binding.json"
        qa.write(binding, {"home":str(home),"entry":str(entry),"cwd":str(source),"output":str(evidence)})
        ipc_run = run("normal-ipc", [str(python),"-I","-B",str(source/"ci/qualify_installed_release.py"),
                                     "--ipc-worker",str(binding)], 300)
        ipc = json.loads((evidence/"ipc.json").read_bytes())
        version = manifest["upstream_version"]
        require(ipc["status"] == "passed_normal_wrapper_ipc" and ipc["eof_exit"] == 0
                and ipc["remaining"] == [] and ipc["model_receipts"] == 0
                and ipc["initialize"]["platformOs"] == "windows"
                and version in ipc["initialize"]["userAgent"] and version in (evidence/"version.log").read_text(encoding="utf8"),
                "Normal native initialization, EOF, reader or idle retirement failed")
        actual = Path(installation["native_package"])/"bin/codex.exe"
        require(sha(actual) == identity["native_cli_sha256"] == manifest["files"]["bin/codex.exe"],
                "Actual native executable identity differs")
        backends = [row for row in ipc_run["observed"] if row.get("exe")
                    and Path(row["exe"]).resolve() == actual.resolve() and "app-server" in row.get("argv", [])]
        require(backends, "No admitted native app-server executable was observed")
        record.update(status="passed_reviewed_windows2025_p40_installed_ipc",
                      installation_sha256=sha(receipt),identity=identity,installed_modules=33,
                      native_backends=[{"pid":row["pid"],"birth":row["birth"]}for row in backends],
                      model_receipts=0,normal_eof_exit=0,strict_ipc_worker_sha256=IPC_SHA,
                      strict_observer_sha256=OBSERVER_SHA)
    except BaseException as error:
        record.update(status="failed", error=repr(error), traceback=traceback.format_exc())
        raise
    finally:
        record["finished_at"]=time.time()
        qa.write(evidence/"qualification.json",record)


def check_hosted_runner(environment, platform_name, version, proxy_kinds):
    required = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Windows", "RUNNER_ARCH": "X64",
                "GITHUB_REPOSITORY": REPO, "GITHUB_REF": REF}
    require(platform_name == "win32" and all(environment.get(k) == v for k, v in required.items()),
            "Use the dedicated disposable hosted Windows gate")
    version_tuple = (version.major, version.minor, version.build)
    require(version_tuple == (10, 0, 26100) and version.product_type == 3,
            f"Windows Server 2025 build 26100 required; observed {version_tuple}, product type {version.product_type}")
    require(not proxy_kinds, "Direct hosted gate cannot use configured HTTP/HTTPS/all proxies")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare","download","qualify"))
    phase=parser.parse_args().phase
    version = sys.getwindowsversion() if sys.platform == "win32" else None
    proxy_kinds = sorted({"http", "https", "all"} & urllib.request.getproxies().keys())
    if version is not None:
        # ImageOS changes with the tool image. Record it without using it to
        # admit the runner. Proxy names are safe; proxy URLs are not logged.
        print(json.dumps({"windows_build": version.build, "product_type": version.product_type,
                          "runner_environment": os.environ.get("RUNNER_ENVIRONMENT"),
                          "image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion"),
                          "proxy_kinds": proxy_kinds}), flush=True)
    check_hosted_runner(os.environ, sys.platform, version, proxy_kinds)
    temp=Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    source=temp/"cds-p40-reviewed";archive=temp/"cds-p40-verified.tar.gz"
    values=pins()
    if phase=="prepare":prepare(source,values)
    elif phase=="download":download(archive)
    else:qualify(source,archive,temp/"cds-p40-install",temp/"cds-p40-evidence",values)


if __name__=="__main__":
    main()
