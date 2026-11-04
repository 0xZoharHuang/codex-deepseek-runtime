"""The normal installer gate must follow the release, not its original version."""
import pytest
from pathlib import Path
import importlib.util

spec = importlib.util.spec_from_file_location("installed_release_gate", Path(__file__).with_name("qualify_installed_release.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
validate_installed_origins = gate.validate_installed_origins


def fixture(tmp_path, version):
    source = tmp_path/"source"
    source.mkdir()
    (source/"pyproject.toml").write_text('[project]\nversion = "'+version+'"\n', encoding="utf-8")
    release = tmp_path/"release"
    (release/"venv").mkdir(parents=True)
    origins = {"version": version, "modules": [{"origin": str(release/"venv"/(str(i)+".py"))} for i in range(32)]}
    return source, release, origins


@pytest.mark.parametrize("version", ["0.6.12", "0.6.13", "0.99.7"])
def test_origin_gate_tracks_explicit_source_release(tmp_path, version):
    source, release, origins = fixture(tmp_path, version)
    assert validate_installed_origins(origins, release, source) == version
    origins["version"] = "0.1.0"
    with pytest.raises(ValueError, match="version or module origins"):
        validate_installed_origins(origins, release, source)


@pytest.mark.parametrize("problem", ["missing_module", "outside", "parent_escape"])
def test_origin_gate_preserves_complete_in_installation_boundary(tmp_path, problem):
    source, release, origins = fixture(tmp_path, "0.6.13")
    if problem == "missing_module":
        origins["modules"].pop()
    elif problem == "outside":
        origins["modules"][0]["origin"] = str(tmp_path/"foreign.py")
    else:
        origins["modules"][0]["origin"] = str(release/"venv"/".."/"foreign.py")
    with pytest.raises(ValueError, match="version or module origins"):
        validate_installed_origins(origins, release, source)
