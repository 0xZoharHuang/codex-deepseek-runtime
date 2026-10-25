"""Real isolated pytest entry keeps QA imports separate from the installed adapter."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ci import qualify_native as qualification


@pytest.mark.parametrize("collect_only", [False, True])
def test_isolated_pytest_admits_only_trusted_helpers_and_keeps_installed_adapter(tmp_path, collect_only):
    cwd = tmp_path / "untrusted cwd"
    injected = tmp_path / "untrusted pythonpath"
    tests = tmp_path / "probe"
    for folder in (cwd, injected, tests):
        folder.mkdir()
    for folder in (cwd, injected):
        for package in ("ci", "codex_deepseek"):
            module = folder / package
            module.mkdir()
            (module / "__init__.py").write_text(
                "raise RuntimeError('untrusted import must never execute')\n",
                encoding="utf-8",
            )
    env = dict(os.environ)
    env.update(PYTHONPATH=os.pathsep.join([str(injected), str(qualification.ROOT / "src")]),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(name, None)
    installed = subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import codex_deepseek,json;print(json.dumps(codex_deepseek.__file__))"],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert installed.returncode == 0, installed.stderr
    expected = Path(json.loads(installed.stdout)).resolve()
    assert not expected.is_relative_to(qualification.ROOT / "src")
    receipt = tmp_path / "provenance.json"
    probe = tests / "test_import_provenance.py"
    probe.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "import codex_deepseek\nimport ci.qualify_native as helper\n"
        "observed={'isolated':sys.flags.isolated,'adapter':str(Path(codex_deepseek.__file__).resolve()),"
        "'helper':str(Path(helper.__file__).resolve()),'path':sys.path}\n"
        f"assert observed['isolated']==1\nassert observed['adapter']=={str(expected)!r}\n"
        f"assert observed['helper']=={str((qualification.ROOT/'ci/qualify_native.py').resolve())!r}\n"
        f"assert {str(qualification.ROOT/'src')!r} not in sys.path\n"
        f"assert {str(cwd)!r} not in sys.path and {str(injected)!r} not in sys.path\n"
        f"Path({str(receipt)!r}).write_text(json.dumps(observed),encoding='utf-8')\n"
        "def test_real_isolated_imports():\n    assert observed['isolated']==1\n",
        encoding="utf-8",
    )
    command = qualification.pytest_command(
        "-q", "-p", "no:cacheprovider", "-c", os.devnull, str(probe),
        *(["--collect-only"] if collect_only else []),
    )
    assert command[:4] == [sys.executable, "-I", "-B", "-c"]
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(receipt.read_text(encoding="utf-8"))
    assert observed["adapter"] == str(expected)
    assert observed["isolated"] == 1
    if collect_only:
        assert "1 test collected" in result.stdout
    else:
        assert "1 passed" in result.stdout
