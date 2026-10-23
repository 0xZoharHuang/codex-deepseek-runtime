import hashlib
import json
from pathlib import Path
import pytest
import verify_real_work as work


@pytest.mark.parametrize('fault',[None,'stale-home','corrupt-entry','foreign-package','invalid-release'])
def test_pinned_entry_acceptance_binds_actual_installed_release(tmp_path,monkeypatch,fault):
    home=tmp_path/'home';entrydir=home/'provider-state/cli-path/owned';entrydir.mkdir(parents=True)
    release=tmp_path/'releases/frozen';python=release/'venv/Scripts/python.exe';python.parent.mkdir(parents=True);python.write_bytes(b'qualified-python-fixture')
    package=release/'venv/Lib/site-packages';package.mkdir(parents=True)
    (release/'installation.json').write_text(json.dumps({'adapter_source_sha256':{'src/example.py':'hash'},'model_catalog_sha256':'catalog'}))
    (release/'native-runtime.json').write_text('{}')
    monkeypatch.setattr(work,'python_entry',lambda environment:Path(environment)/'Scripts/python.exe')
    validations=[]
    def valid(selected,sources,catalog,native):
        validations.append((selected,sources,catalog,native));return fault!='invalid-release'
    monkeypatch.setattr(work,'valid_release',valid)
    files={}
    for name in ['codex.exe','codex-deepseek.exe']:
        (entrydir/name).write_bytes(name.encode());files[name]=hashlib.sha256((entrydir/name).read_bytes()).hexdigest()
    recipe={'home':str(tmp_path/'other' if fault=='stale-home' else home),'interpreter':str(python),'package_root':str(tmp_path/'foreign' if fault=='foreign-package' else package)}
    (entrydir/'manifest.json').write_text(json.dumps({'recipe':recipe,'files':files}))
    if fault=='corrupt-entry':(entrydir/'codex-deepseek.exe').write_bytes(b'changed launcher')
    if fault is None:
        assert work.qualified_entry_release(entrydir/'codex-deepseek.exe',home)==release
        assert len(validations)==1
    else:
        with pytest.raises(ValueError):work.qualified_entry_release(entrydir/'codex-deepseek.exe',home)
        assert bool(validations)==(fault=='invalid-release')
