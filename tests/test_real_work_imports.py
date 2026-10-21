"""Protected tests must execute the changed project, never its copied baseline."""
import json
import os
from pathlib import Path
import subprocess
import sys


HELPER=Path(__file__).resolve().parents[1]/'real_work_imports.py'


def project(path,value,package='fixture_pkg'):
    (path/package).mkdir(parents=True)
    (path/package/'__init__.py').write_text('from .core import VALUE\n')
    (path/package/'core.py').write_text('VALUE='+repr(value)+'\n')


def invoke(tmp_path,mode,args,pythonpath,package='fixture_pkg'):
    receipt=tmp_path/'imports.json'
    env={**os.environ,'PYTHONPATH':str(pythonpath),'PYTHONOPTIMIZE':'','PYTEST_ADDOPTS':'','PYTEST_PLUGINS':''}
    result=subprocess.run([sys.executable,str(HELPER),'--package',package,'--root',str(tmp_path/'work'),
                           '--receipt',str(receipt),mode,*map(str,args)],cwd=tmp_path/'work',env=env,capture_output=True,text=True)
    return result,json.loads(receipt.read_text())


def test_protected_package_tests_use_changed_sources_without_preimport(tmp_path):
    work=tmp_path/'work';oracle=tmp_path/'oracle';project(work,'changed');project(oracle,'old')
    tests=oracle/'tests';tests.mkdir();(tests/'__init__.py').write_text('')
    (tests/'test_bug.py').write_text('import fixture_pkg\ndef test_production_fix():\n assert fixture_pkg.VALUE == "changed"\n')
    result,proof=invoke(tmp_path,'pytest',['-q',tests],work)
    assert result.returncode==0,result.stdout+result.stderr
    assert proof['passed'] and set(proof['modules'])=={'fixture_pkg','fixture_pkg.core'}
    assert all(Path(row['file']).is_relative_to(work) for row in proof['modules'].values())


def test_independent_script_cannot_succeed_using_the_old_package_or_one_foreign_submodule(tmp_path):
    work=tmp_path/'work';oracle=tmp_path/'oracle';project(work,'changed');project(oracle,'old')
    script=tmp_path/'driver.py';script.write_text('import fixture_pkg\nassert fixture_pkg.VALUE in {"changed", "old"}\n')
    result,proof=invoke(tmp_path,'script',[script],work)
    assert result.returncode==0 and proof['passed']
    result,proof=invoke(tmp_path,'script',[script],oracle)
    assert result.returncode!=0 and not proof['passed'] and set(proof['invalid_modules'])=={'fixture_pkg','fixture_pkg.core'}
    # Replacing just one submodule must not pass because the top-level package is local.
    script.write_text('import fixture_pkg,importlib.util,sys\ns=importlib.util.spec_from_file_location("fixture_pkg.foreign",'+repr(str(oracle/'fixture_pkg/core.py'))+')\nm=importlib.util.module_from_spec(s);s.loader.exec_module(m);sys.modules[s.name]=m\n')
    result,proof=invoke(tmp_path,'script',[script],work)
    assert result.returncode!=0 and proof['invalid_modules']==['fixture_pkg.foreign']


def test_legacy_jinja_test_resources_keep_module_names_without_shadowing_production(tmp_path):
    work=tmp_path/'work';oracle=tmp_path/'oracle';project(work,'changed','jinja2');project(oracle,'old','jinja2')
    tests=oracle/'tests';(tests/'res').mkdir(parents=True);(tests/'res/__init__.py').write_text('TOKEN=42\n')
    (tests/'test_utils.py').write_text('import jinja2,res\nclass Local: pass\ndef test_legacy_layout():\n assert Local.__module__ == "test_utils"\n assert res.TOKEN == 42\n assert jinja2.VALUE == "changed"\n')
    result,proof=invoke(tmp_path,'pytest',['-q',tests],work,'jinja2')
    assert result.returncode==0,result.stdout+result.stderr
    assert proof['passed'] and all(Path(row['file']).is_relative_to(work) for row in proof['modules'].values())
