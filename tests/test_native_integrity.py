"""Runtime checks all loadable artifacts; installation also checks legal text."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from codex_deepseek import cli


@pytest.fixture
def native_package(tmp_path,monkeypatch):
    root=tmp_path/'runtime';release=root/'releases/fixture';package=root/'native/fixture'
    release.mkdir(parents=True);package.mkdir(parents=True)
    monkeypatch.setattr(cli.sys,'prefix',str(release/'venv'))
    monkeypatch.setattr(cli,'data_root',lambda:root)
    files={name+cli.EXE:b'fixture executable' for name in ['bin/codex','bin/codex-code-mode-host',
        'bin/codex-http-transport','codex-path/rg']}
    if cli.WINDOWS:
        files.update({name+cli.EXE:b'fixture helper' for name in ['bin/codex-windows-sandbox-setup',
            'bin/codex-command-runner','bin/codex-windows-managed-deny-probe','bin/codex-windows-sandbox-service',
            'codex-resources/codex-windows-sandbox-setup','codex-resources/codex-command-runner']})
    if cli.sys.platform.startswith('linux'):files['codex-resources/bwrap']=b'fixture sandbox'
    (package/'codex-resources').mkdir(exist_ok=True)
    files['codex-package.json']=b'{"patchSha256":"fixture-patch"}'
    files['build-receipt.json']=b'{"fixture":true}'
    files['licenses/dependency/NOTICE.txt']=b'Original legal notice'
    for name,data in files.items():
        p=package/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(0o755)
    manifest={'status':'ready','distribution':'codex-deepseek-runtime','upstream_version':cli.NATIVE_VERSION,
        'patch_sha256':'fixture-patch','files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    (release/'native-runtime.json').write_text(json.dumps(manifest))
    (release/'installation.json').write_text(json.dumps({'native_package':str(package)}))
    return package,release,manifest


def test_startup_does_not_open_legal_text_but_installer_still_verifies_it(native_package,monkeypatch):
    package,release,manifest=native_package
    notice=package/'licenses/dependency/NOTICE.txt';original_open=Path.open;opened=[]
    def tracked(path,*args,**kwargs):
        opened.append(path)
        if path==notice:raise AssertionError('Startup reopened non-executable legal text')
        return original_open(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'open',tracked)
        assert cli.native()==package/'bin'/('codex'+cli.EXE)
    assert all(package/name in opened for name in manifest['files'] if not name.startswith('licenses/'))
    spec=importlib.util.spec_from_file_location('native_integrity_installer',Path(__file__).resolve().parents[1]/'install.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    assert installer.valid_native(package,manifest)
    notice.write_text('Damaged notice')
    assert not installer.valid_native(package,manifest)


@pytest.mark.parametrize('artifact',['bin/codex','bin/codex-code-mode-host','bin/codex-http-transport','codex-path/rg','build-receipt.json'])
def test_executable_and_metadata_tampering_still_fails(native_package,artifact):
    package,_,_=native_package
    if not artifact.endswith('.json'):artifact+=cli.EXE
    (package/artifact).write_bytes(b'TAMPERED')
    with pytest.raises(RuntimeError,match='Verified dedicated native runtime is unavailable'):cli.native()


def test_runtime_requires_every_binary_required_by_the_installer(native_package):
    _,release,manifest=native_package
    spec=importlib.util.spec_from_file_location('native_required_installer',Path(__file__).resolve().parents[1]/'install.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    for name in installer.required_binaries():
        changed={**manifest,'files':{key:value for key,value in manifest['files'].items() if key!=name}}
        (release/'native-runtime.json').write_text(json.dumps(changed))
        with pytest.raises(RuntimeError,match='Verified dedicated native runtime is unavailable'):cli.native()


@pytest.mark.parametrize('name',['licenses/../bin/codex','licenses/../../outside','/licenses/absolute','licenses\\escape','licenses/C:escape'])
def test_legal_text_exemption_cannot_hide_unsafe_manifest_paths(native_package,name):
    _,release,manifest=native_package;manifest['files'][name]='0'*64
    (release/'native-runtime.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError,match='Verified dedicated native runtime is unavailable'):cli.native()


def test_empty_timeout_error_is_named():
    # The CLI owns its process-level event loop. Exercise that boundary in a
    # child instead of replacing pytest-asyncio's policy loop via asyncio.run.
    import os
    import subprocess
    import sys
    source = ('from codex_deepseek import cli\n'
              'async def fail(_arguments): raise TimeoutError()\n'
              'cli.run=fail\n'
              'cli.sys.argv=["codex-deepseek","exec","fixture"]\n'
              'cli.main()\n')
    environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src')}
    result = subprocess.run([sys.executable, '-B', '-W', 'error', '-c', source],
                            env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr == 'codex-deepseek: TimeoutError\n'


@pytest.fixture
def isolated_run(native_package,monkeypatch):
    """Run the real entry dispatcher and integrity check without services or APIs."""
    from types import SimpleNamespace
    package,_,_=native_package
    binary=package/'bin'/('codex'+cli.EXE)
    checked=[];consumers=[];launches=[];stores=[]
    original_native=cli.native
    def verify():
        checked.append(True)
        return original_native()
    class Store:
        def __init__(self,path):self.path=path;self.owner='fixture-owner';self.closed=False;stores.append(self)
        def cleanup_native(self):pass
        def close(self):self.closed=True
    class Service:
        def __init__(self,home,catalog,*,native_binary,owner):
            assert stores and owner==stores[-1].owner
            consumers.append(native_binary)
        async def __aenter__(self):return SimpleNamespace(port=1234,token='fixture-token')
        async def __aexit__(self,*args):pass
    class Process:
        returncode=None
        async def wait(self):self.returncode=0;return 0
    async def launch(*arguments,**kwargs):launches.append(arguments);return Process()
    async def doctor(key,gateway,store,home,*,native_binary):consumers.append(native_binary);return 0
    def environment(home,token,native_binary,**kwargs):consumers.append(native_binary);return {}
    monkeypatch.delenv('CODEX_DEEPSEEK_HOME',raising=False)
    monkeypatch.delenv('CODEX_DEEPSEEK_TRACE_DIR',raising=False)
    monkeypatch.setattr(cli,'native',verify)
    monkeypatch.setattr(cli,'Store',Store)
    monkeypatch.setattr(cli,'ProviderService',Service)
    monkeypatch.setattr(cli,'check_home_ipc',lambda *args:None)
    monkeypatch.setattr(cli,'check_attachment',lambda *args:None)
    monkeypatch.setattr(cli,'bind_remote',lambda arguments,_:arguments)
    monkeypatch.setattr(cli,'check_daemon_package',lambda arguments,home,native_binary:consumers.append(native_binary))
    monkeypatch.setattr(cli,'migrate_searches',lambda *args:None)
    monkeypatch.setattr(cli,'native_environment',environment)
    monkeypatch.setattr(cli,'credential',lambda:'NONFUNCTIONAL_FIXTURE')
    monkeypatch.setattr(cli,'doctor',doctor)
    monkeypatch.setattr(cli,'identity',lambda pid:'fixture-identity')
    monkeypatch.setattr(cli,'install_signals',lambda loop,handlers:lambda:None)
    monkeypatch.setattr('codex_deepseek.web_config.ensure_browser',lambda home:None)
    monkeypatch.setattr(cli.asyncio,'create_subprocess_exec',launch)
    return SimpleNamespace(binary=binary,checked=checked,consumers=consumers,launches=launches,stores=stores)


@pytest.mark.parametrize('arguments',[
    ['--version'],['app-server','daemon','stop'],['app-server','daemon','version'],
    ['exec','fixture'],['runtime','status'],['runtime','doctor'],
])
async def test_each_run_verifies_native_once_and_shares_only_that_verified_path(isolated_run,arguments):
    assert await cli.run(arguments)==0
    assert len(isolated_run.checked)==1
    assert isolated_run.consumers and all(path==isolated_run.binary for path in isolated_run.consumers)
    assert all(store.closed for store in isolated_run.stores)


async def test_integrity_result_is_never_cached_across_runs(isolated_run):
    assert await cli.run(['--version'])==0
    isolated_run.binary.write_bytes(b'TAMPERED_AFTER_FIRST_RUN')
    with pytest.raises(RuntimeError,match='Verified dedicated native runtime is unavailable'):
        await cli.run(['--version'])
    assert len(isolated_run.checked)==2 and len(isolated_run.launches)==1
