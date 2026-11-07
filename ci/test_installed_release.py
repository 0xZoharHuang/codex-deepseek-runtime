"""The normal installer gate must follow the release, not its original version."""
import pytest
from pathlib import Path
import importlib.util
import hashlib

spec = importlib.util.spec_from_file_location("installed_release_gate", Path(__file__).with_name("qualify_installed_release.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
validate_installed_origins = gate.validate_installed_origins


def fixture(tmp_path, version, names=("__init__", "transport")):
    source = tmp_path/"source"
    source.mkdir()
    (source/"pyproject.toml").write_text('[project]\nversion = "'+version+'"\n', encoding="utf-8")
    release = tmp_path/"release"
    (release/"venv").mkdir(parents=True)
    source_package = source/"src/codex_deepseek"
    installed_package = release/"venv/codex_deepseek"
    source_package.mkdir(parents=True)
    installed_package.mkdir()
    modules = []
    for name in names:
        filename = name + ".py"
        data = f"# {name}\n".encode()
        (source_package/filename).write_bytes(data)
        origin = installed_package/filename
        origin.write_bytes(data)
        module = "codex_deepseek" if name == "__init__" else f"codex_deepseek.{name}"
        modules.append({"module": module, "origin": str(origin),
                        "sha256": hashlib.sha256(data).hexdigest()})
    origins = {"version": version, "modules": modules}
    return source, release, origins


@pytest.mark.parametrize("version", ["0.6.12", "0.6.13", "0.99.7"])
def test_origin_gate_tracks_explicit_source_release(tmp_path, version):
    source, release, origins = fixture(tmp_path, version, ("__init__", "transport", "image_selection"))
    assert validate_installed_origins(origins, release, source) == version
    origins["version"] = "0.1.0"
    with pytest.raises(ValueError, match="version or module origins"):
        validate_installed_origins(origins, release, source)


@pytest.mark.parametrize("problem", ["missing_module", "outside", "parent_escape", "duplicate", "substituted", "changed_bytes"])
def test_origin_gate_preserves_complete_in_installation_boundary(tmp_path, problem):
    source, release, origins = fixture(tmp_path, "0.6.13")
    if problem == "missing_module":
        origins["modules"].pop()
    elif problem == "outside":
        origins["modules"][0]["origin"] = str(tmp_path/"foreign.py")
    elif problem == "parent_escape":
        origins["modules"][0]["origin"] = str(release/"venv"/".."/"foreign.py")
    elif problem == "duplicate":
        origins["modules"][1] = origins["modules"][0].copy()
    elif problem == "substituted":
        origins["modules"][1]["module"] = "codex_deepseek.other"
    else:
        Path(origins["modules"][1]["origin"]).write_bytes(b"# changed\n")
    with pytest.raises(ValueError, match="version or module origins"):
        validate_installed_origins(origins, release, source)


@pytest.mark.parametrize("candidate", [False, True])
def test_public_windows_source_and_archive_pins(tmp_path, monkeypatch, candidate):
    import hashlib
    import json
    import subprocess

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "--quiet")
    git("config", "core.autocrlf", "false")
    git("remote", "add", "origin", gate.WINDOWS_PUBLIC_REPO + ".git")
    tag, archive_tag = ("v0.7.0", "v0.6.14") if candidate else ("v0.6.14", "v0.6.14")
    branch = "codex/test-reviewed-source"
    archive = {"filename": "native.tar.gz", "sha256": "a" * 64,
               "url": f"{gate.WINDOWS_PUBLIC_REPO}/releases/download/{archive_tag}/native.tar.gz"}
    manifest = {"status": "ready", "target": "x86_64-pc-windows-msvc",
                "distribution_version": tag[1:], "archive": archive}
    path = tmp_path / "native/runtime-x86_64-pc-windows-msvc.json"
    path.parent.mkdir()
    path.write_bytes(json.dumps(manifest).encode())
    git("add", "--", "native")
    git("-c", "user.email=ci@example.invalid", "-c", "user.name=CI", "commit", "-qm", "fixture")
    commit = git("rev-parse", "HEAD")
    if candidate:
        git("update-ref", "refs/remotes/origin/reviewed-candidate", commit)
    else:
        git("tag", tag, commit)
    for name, value in {
        "RELEASE_TAG": tag, "RELEASE_ARCHIVE_TAG": archive_tag if candidate else "",
        "RELEASE_CANDIDATE_BRANCH": branch if candidate else "",
        "RELEASE_COMMIT": commit, "RELEASE_SOURCE_ID": "a" * 20,
        "RELEASE_ARCHIVE_SHA256": archive["sha256"],
        "RELEASE_MANIFEST_SHA256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }.items():
        monkeypatch.setenv(name, value)
    selected, proof = gate.windows_public_pin(tmp_path, gate.isolated_environment(tmp_path))
    assert selected == manifest
    assert proof["public_tag"] == (None if candidate else tag)
    assert proof["source_ref"] == ("refs/heads/" + branch if candidate else "refs/tags/" + tag)
    assert proof["archive_tag"] == archive_tag
    monkeypatch.setenv("RELEASE_COMMIT", "f" * 40)
    with pytest.raises(ValueError, match="checkout differs"):
        gate.windows_public_pin(tmp_path, gate.isolated_environment(tmp_path))
    monkeypatch.setenv("RELEASE_COMMIT", commit)
    monkeypatch.setenv("RELEASE_ARCHIVE_TAG", "v0.6.12")
    with pytest.raises(ValueError, match="pinned public asset"):
        gate.windows_public_pin(tmp_path, gate.isolated_environment(tmp_path))
    monkeypatch.setenv("RELEASE_CANDIDATE_BRANCH", "HEAD")
    with pytest.raises(ValueError, match="explicit codex/ branch"):
        gate.windows_public_refs()
