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
    env['CODEX_DEEPSEEK_TEST_COLLECTION_PATH']=str(tmp_path/'probe-collection.json')
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

    # The full public controller must enforce the same installed-only import path.
    import run_acceptance
    offline=run_acceptance.installed_offline_command(sys.executable,
        "-q","-p","no:cacheprovider","-c",os.devnull,str(probe),
        *(["--collect-only"] if collect_only else []))
    assert offline[:4]==[sys.executable,"-I","-B","-c"]
    checked=subprocess.run(offline,cwd=cwd,env=env,capture_output=True,text=True,
                           encoding="utf-8",timeout=30)
    assert checked.returncode==0,checked.stdout+checked.stderr
    assert json.loads(receipt.read_text(encoding="utf-8"))["adapter"]==str(expected)
    origin_proof=json.loads((tmp_path/'offline-installed-origins.json').read_text(encoding='utf-8'))
    assert origin_proof['passed'] and origin_proof['isolated']==1
    assert all(Path(value).is_relative_to(expected.parent) for value in origin_proof['origins'].values())
    if not collect_only:
        foreign=tmp_path/"foreign product";foreign.mkdir()
        (foreign/"outside.py").write_text("raise AssertionError('foreign product executed')\n",encoding="utf-8")
        probe.write_text(
            "import pytest,codex_deepseek\n"
            "def test_lazy_foreign_product_is_rejected_before_execution():\n"
            f"    codex_deepseek.__path__.insert(0,{str(foreign)!r})\n"
            "    try:\n"
            "        with pytest.raises(RuntimeError,match='Product import outside immutable installation'):\n"
            "            __import__('codex_deepseek.outside')\n"
            "    finally:\n"
            f"        codex_deepseek.__path__.remove({str(foreign)!r})\n",encoding="utf-8")
        denied=subprocess.run(offline,cwd=cwd,env=env,capture_output=True,text=True,
                              encoding="utf-8",timeout=30)
        assert denied.returncode==0,denied.stdout+denied.stderr
        rejected=json.loads((tmp_path/'offline-installed-origins.json').read_text(encoding='utf-8'))
        assert rejected['passed'] and rejected['rejected_before_execution']==['codex_deepseek.outside']
