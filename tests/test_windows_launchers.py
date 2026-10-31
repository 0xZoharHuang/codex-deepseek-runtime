"""Real Windows executable launchers preserve the native process boundary."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest

from codex_deepseek.windows_launchers import launcher_recipe, make_launchers, profile_launchers

WINDOWS = pytest.mark.skipif(os.name != 'nt', reason='Exercises Windows PE executable launchers')


def package_at(root):
    package=root/'codex_deepseek';package.mkdir(parents=True)
    (package/'__init__.py').write_text('',encoding='utf-8')
    (package/'cli.py').write_text('''import json, os, sys
if '--wait' in sys.argv:
    print('READY', flush=True)
    sys.stdin.readline()
print(json.dumps({'args':sys.argv[1:], 'home':os.environ.get('CODEX_DEEPSEEK_HOME'),
                  'path':os.environ['PATH'], 'isolated':sys.flags.isolated,
                  'utf8':sys.flags.utf8_mode, 'package':__file__}, ensure_ascii=False))
if '--exit-23' in sys.argv: raise SystemExit(23)
''',encoding='utf-8')
    return root


@WINDOWS
def test_exe_preserves_special_paths_long_environment_arguments_and_import_isolation(tmp_path):
    special=tmp_path/'中文 & literal%PATH% ! space';special.mkdir()
    environment=special/'解释器 & % !'
    venv.EnvBuilder(with_pip=False).create(environment)
    interpreter=environment/'Scripts/python.exe'
    package=package_at(special/'真包 & % !')
    home=special/'用户 & % !'
    directory=profile_launchers(home,interpreter=interpreter,package_root=package)
    hostile=tmp_path/'untrusted';hostile.mkdir()
    injected=hostile/'codex_deepseek';injected.mkdir()
    (injected/'__init__.py').write_text('raise AssertionError("IMPORT_INJECTION")',encoding='utf-8')
    path=';'.join([str(directory),*[str(special/('entry'+str(i)+'x'*80)) for i in range(100)]])
    assert len(path)>8191
    env={**os.environ,'PATH':path,'PYTHONPATH':str(hostile),'CODEX_DEEPSEEK_HOME':'untrusted-profile'}
    arguments=['中文 空格','literal&value','%PATH%','!token!','quote"inside','tail\\','', '--exit-23']
    for name in ['codex','codex-deepseek']:
        exe=shutil.which(name,path=path);assert exe and Path(exe).samefile(directory/(name+'.exe'))
        result=subprocess.run([exe,*arguments],cwd=hostile,env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
        assert result.returncode==23,result.stderr
        value=json.loads(result.stdout)
        assert value=={'args':arguments,'home':str(home.resolve()),'path':path,'isolated':1,
                       'utf8':1,'package':str(package/'codex_deepseek/cli.py')}


@WINDOWS
def test_profile_recipe_does_not_replace_running_exe_and_repairs_immutably(tmp_path):
    home=tmp_path/'home';package=package_at(tmp_path/'one')
    first=profile_launchers(home,package_root=package)
    digest=hashlib.sha256((first/'codex.exe').read_bytes()).hexdigest()
    process=subprocess.Popen([str(first/'codex.exe'),'--wait'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,text=True,encoding='utf-8')
    try:
        assert process.stdout.readline().strip()=='READY'
        assert profile_launchers(home,package_root=package)==first
        second=profile_launchers(home,package_root=package_at(tmp_path/'two'))
        assert second!=first and process.poll() is None
        assert hashlib.sha256((first/'codex.exe').read_bytes()).hexdigest()==digest
        process.communicate('\n',timeout=10)
        assert process.returncode==0
    finally:
        if process.poll() is None:process.kill();process.wait()
    (first/'codex-deepseek.exe').write_bytes(b'corruption')
    repaired=profile_launchers(home,package_root=package)
    assert repaired!=first and (first/'codex-deepseek.exe').read_bytes()==b'corruption'
    assert profile_launchers(home,package_root=package)==repaired


def installer_module():
    spec=importlib.util.spec_from_file_location('windows_launcher_installer',Path(__file__).parents[1]/'install.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def release_at(root,name,payload):
    release=root/'releases'/name;entry=release/'venv/Scripts/codex-deepseek.exe'
    entry.parent.mkdir(parents=True);entry.write_bytes(payload)
    (release/'installation.json').write_text(json.dumps({'release_environment':str(release/'venv'),
        'console_script_sha256':hashlib.sha256(payload).hexdigest()}),encoding='utf-8')
    return release


def test_activation_preserves_unrelated_files_and_verifies_own_release_hash(tmp_path,monkeypatch):
    installer=installer_module();monkeypatch.setattr(installer,'console_entry',lambda path:Path(path)/'Scripts/codex-deepseek.exe')
    root=tmp_path/'runtime';release=release_at(root,'one',b'own first entry');bin_dir=tmp_path/'bin';bin_dir.mkdir()
    cmd=bin_dir/'codex-deepseek.cmd';cmd.write_bytes(b'user owned command')
    with pytest.raises(SystemExit,match='unrelated'):installer.activate_windows(root,release,bin_dir)
    assert cmd.read_bytes()==b'user owned command' and not (bin_dir/'codex-deepseek.exe').exists()
    cmd.unlink();launcher=installer.activate_windows(root,release,bin_dir)
    assert launcher.read_bytes()==b'own first entry'
    import base64
    prefix='@rem codex-deepseek managed '+base64.b64encode(os.fsencode(root.resolve())).decode()+'\n'
    cmd.write_text(prefix+'unrelated command with a copied marker\n',encoding='utf-8')
    with pytest.raises(SystemExit,match='unrelated'):installer.activate_windows(root,release,bin_dir)
    assert launcher.read_bytes()==b'own first entry'
    legacy=prefix+'@echo off\nsetlocal DisableDelayedExpansion\nchcp 65001 >nul\n"'+str(release/'venv/Scripts/codex-deepseek.exe').replace('%','%%')+'" %*\n'
    cmd.write_text(legacy,encoding='utf-8')
    next_release=release_at(root,'two',b'own second entry')
    assert installer.activate_windows(root,next_release,bin_dir)==launcher
    assert launcher.read_bytes()==b'own second entry' and not cmd.exists()
    launcher.write_bytes(b'unrelated exe')
    before=(root/'installation.json').read_bytes()
    with pytest.raises(SystemExit,match='unrelated'):installer.activate_windows(root,release,bin_dir)
    assert launcher.read_bytes()==b'unrelated exe' and (root/'installation.json').read_bytes()==before


@WINDOWS
def test_locked_activation_fails_atomically_and_preserves_current_receipt(tmp_path):
    import ctypes
    from ctypes import wintypes
    installer=installer_module();root=tmp_path/'runtime'
    release=release_at(root,'one',b'first entry');following=release_at(root,'two',b'second entry')
    launcher=installer.activate_windows(root,release,tmp_path/'bin');before=(root/'installation.json').read_bytes()
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    short=launcher.with_name('cds.exe')
    shutil.copy2(launcher,short)
    # A lock on either entry, or on the final receipt, must roll back all files.
    for locked in [launcher,short,root/'installation.json']:
        handle=kernel.CreateFileW(str(locked),0x80000000,1,None,3,0,None)
        assert handle!=ctypes.c_void_p(-1).value
        try:
            with pytest.raises(OSError):installer.activate_windows(root,following,tmp_path/'bin')
            assert launcher.read_bytes()==short.read_bytes()==b'first entry'
            assert (root/'installation.json').read_bytes()==before
        finally:kernel.CloseHandle(handle)
        assert not list(launcher.parent.glob('.codex-deepseek-*'))
        assert not list(root.glob('.codex-deepseek-*'))
    installer.activate_windows(root,following,tmp_path/'bin')
    assert launcher.read_bytes()==short.read_bytes()==b'second entry'



def test_activation_updates_only_existing_verified_short_entry(tmp_path,monkeypatch):
    installer=installer_module()
    monkeypatch.setattr(installer,'console_entry',lambda path:Path(path)/'Scripts/codex-deepseek.exe')
    for short_payload in [b'old managed entry',b'new managed entry']:
        case=tmp_path/('old' if short_payload.startswith(b'old') else 'target')
        root=case/'runtime';bin_dir=case/'bin'
        first=release_at(root,'one',b'old managed entry')
        following=release_at(root,'two',b'new managed entry')
        primary=installer.activate_windows(root,first,bin_dir)
        short=bin_dir/'cds.exe'
        assert not short.exists()  # Installing CDS does not claim a new global alias.
        short.write_bytes(short_payload)
        installer.activate_windows(root,following,bin_dir)
        assert primary.read_bytes()==short.read_bytes()==b'new managed entry'
        assert (root/'installation.json').read_bytes()==(following/'installation.json').read_bytes()


def test_activation_preserves_unknown_or_redirected_short_entry(tmp_path,monkeypatch):
    installer=installer_module()
    monkeypatch.setattr(installer,'console_entry',lambda path:Path(path)/'Scripts/codex-deepseek.exe')
    root=tmp_path/'runtime';bin_dir=tmp_path/'bin'
    first=release_at(root,'one',b'old managed entry')
    following=release_at(root,'two',b'new managed entry')
    primary=installer.activate_windows(root,first,bin_dir)
    short=bin_dir/'cds.exe';short.write_bytes(b'user owned command')
    before=(root/'installation.json').read_bytes()
    with pytest.raises(SystemExit,match='unrelated launcher'):
        installer.activate_windows(root,following,bin_dir)
    assert primary.read_bytes()==b'old managed entry'
    assert short.read_bytes()==b'user owned command' and (root/'installation.json').read_bytes()==before
    # Exercise the path API's redirect result without requiring OS symlink privileges.
    short.write_bytes(b'old managed entry')
    original=Path.is_symlink
    monkeypatch.setattr(Path,'is_symlink',lambda path: path==short or original(path))
    with pytest.raises(SystemExit,match='unrelated launcher'):
        installer.activate_windows(root,following,bin_dir)
    assert primary.read_bytes()==short.read_bytes()==b'old managed entry'
    assert (root/'installation.json').read_bytes()==before


def test_activation_rolls_back_both_entries_and_legacy_when_receipt_publish_fails(tmp_path,monkeypatch):
    import base64
    installer=installer_module()
    monkeypatch.setattr(installer,'console_entry',lambda path:Path(path)/'Scripts/codex-deepseek.exe')
    root=tmp_path/'runtime';bin_dir=tmp_path/'bin'
    first=release_at(root,'one',b'old managed entry')
    following=release_at(root,'two',b'new managed entry')
    primary=installer.activate_windows(root,first,bin_dir)
    short=bin_dir/'cds.exe';shutil.copy2(primary,short)
    legacy=bin_dir/'codex-deepseek.cmd'
    prefix='@rem codex-deepseek managed '+base64.b64encode(os.fsencode(root.resolve())).decode()+'\n'
    legacy.write_text(prefix+'@echo off\nsetlocal DisableDelayedExpansion\nchcp 65001 >nul\n"'
                      +str(first/'venv/Scripts/codex-deepseek.exe').replace('%','%%')+'" %*\n',encoding='utf-8')
    expected={path:path.read_bytes() for path in [primary,short,legacy,root/'installation.json']}
    replace=installer.replace_file
    def fail_receipt(source,target):
        if target==root/'installation.json':raise PermissionError('injected final receipt publish failure')
        return replace(source,target)
    monkeypatch.setattr(installer,'replace_file',fail_receipt)
    with pytest.raises(PermissionError,match='receipt publish failure'):
        installer.activate_windows(root,following,bin_dir)
    assert {path:path.read_bytes() for path in expected}==expected
    assert not list(bin_dir.glob('.codex-deepseek-*')) and not list(root.glob('.codex-deepseek-*'))
    monkeypatch.setattr(installer,'replace_file',replace)
    unlink=Path.unlink
    def fail_legacy_unlink(path,*args,**kwargs):
        if path==legacy:raise PermissionError('injected legacy removal failure')
        return unlink(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',fail_legacy_unlink)
    with pytest.raises(PermissionError,match='legacy removal failure'):
        installer.activate_windows(root,following,bin_dir)
    assert {path:path.read_bytes() for path in expected}==expected
    assert not list(bin_dir.glob('.codex-deepseek-*')) and not list(root.glob('.codex-deepseek-*'))


def test_activation_rejects_changed_source_before_replacing_either_entry(tmp_path,monkeypatch):
    installer=installer_module()
    monkeypatch.setattr(installer,'console_entry',lambda path:Path(path)/'Scripts/codex-deepseek.exe')
    root=tmp_path/'runtime';bin_dir=tmp_path/'bin'
    first=release_at(root,'one',b'old managed entry')
    following=release_at(root,'two',b'new managed entry')
    primary=installer.activate_windows(root,first,bin_dir)
    short=bin_dir/'cds.exe';shutil.copy2(primary,short)
    expected={path:path.read_bytes() for path in [primary,short,root/'installation.json']}
    source=following/'venv/Scripts/codex-deepseek.exe'
    copy=installer.shutil.copy2
    def change_source_then_copy(origin,target):
        if origin==source:source.write_bytes(b'changed after verified source digest')
        return copy(origin,target)
    monkeypatch.setattr(installer.shutil,'copy2',change_source_then_copy)
    with pytest.raises(SystemExit,match='checksum mismatch'):
        installer.activate_windows(root,following,bin_dir)
    assert {path:path.read_bytes() for path in expected}==expected
    assert not list(bin_dir.glob('.codex-deepseek-*')) and not list(root.glob('.codex-deepseek-*'))


def test_activation_reports_incomplete_rollback_and_retains_recovery_files(tmp_path,monkeypatch):
    installer=installer_module()
    monkeypatch.setattr(installer,'console_entry',lambda path:Path(path)/'Scripts/codex-deepseek.exe')
    root=tmp_path/'runtime';bin_dir=tmp_path/'bin'
    first=release_at(root,'one',b'old managed entry')
    following=release_at(root,'two',b'new managed entry')
    primary=installer.activate_windows(root,first,bin_dir)
    short=bin_dir/'cds.exe';shutil.copy2(primary,short)
    receipt=(root/'installation.json').read_bytes()
    replace=installer.replace_file
    def unavailable_short_and_restore(source,target):
        if target==short:raise PermissionError('injected short publish failure')
        if target==primary and Path(source).read_bytes()==b'old managed entry':
            raise PermissionError('injected restore failure')
        return replace(source,target)
    monkeypatch.setattr(installer,'replace_file',unavailable_short_and_restore)
    with pytest.raises(RuntimeError,match='rollback incomplete; retained recovery copies') as failure:
        installer.activate_windows(root,following,bin_dir)
    assert isinstance(failure.value.__cause__,PermissionError)
    assert primary.read_bytes()==b'new managed entry' and short.read_bytes()==b'old managed entry'
    assert (root/'installation.json').read_bytes()==receipt
    backups=[path for directory in [root,bin_dir] for path in directory.glob('.codex-deepseek-*')]
    assert any(path.read_bytes()==b'old managed entry' for path in backups)
    assert any(path.read_bytes()==receipt for path in backups)
    assert all(str(path) in str(failure.value) for path in backups)


def test_activation_preserves_foreign_file_changed_after_staging_and_rolls_back(tmp_path,monkeypatch):
    import base64
    installer=installer_module()
    monkeypatch.setattr(installer,'console_entry',lambda path:Path(path)/'Scripts/codex-deepseek.exe')
    copy=installer.shutil.copy2
    for name in ['codex-deepseek.exe','cds.exe','installation.json','codex-deepseek.cmd']:
        case=tmp_path/name.replace('.','-');root=case/'runtime';bin_dir=case/'bin'
        first=release_at(root,'one',b'old managed entry')
        following=release_at(root,'two',b'new managed entry')
        primary=installer.activate_windows(root,first,bin_dir)
        short=bin_dir/'cds.exe';shutil.copy2(primary,short)
        receipt=root/'installation.json';legacy=bin_dir/'codex-deepseek.cmd'
        if name==legacy.name:
            prefix='@rem codex-deepseek managed '+base64.b64encode(os.fsencode(root.resolve())).decode()+'\n'
            legacy.write_text(prefix+'@echo off\nsetlocal DisableDelayedExpansion\nchcp 65001 >nul\n"'
                              +str(first/'venv/Scripts/codex-deepseek.exe').replace('%','%%')+'" %*\n',encoding='utf-8')
        victim=receipt if name==receipt.name else bin_dir/name
        expected={path:path.read_bytes() for path in [primary,short,receipt,*([legacy] if legacy.exists() else [])]}
        final_backup_source=legacy if legacy.exists() else receipt
        injected=[]
        def change_after_final_backup(origin,target):
            result=copy(origin,target)
            if origin==final_backup_source and not injected:
                victim.write_bytes(b'foreign concurrent file');injected.append(str(victim))
            return result
        with monkeypatch.context() as patch:
            patch.setattr(installer.shutil,'copy2',change_after_final_backup)
            with pytest.raises(SystemExit,match='preserving concurrent file'):
                installer.activate_windows(root,following,bin_dir)
        assert injected==[str(victim)]
        for path,payload in expected.items():
            assert path.read_bytes()==(b'foreign concurrent file' if path==victim else payload)
        assert not list(bin_dir.glob('.codex-deepseek-*')) and not list(root.glob('.codex-deepseek-*'))


def test_activation_preserves_real_owned_relative_link_and_rejects_replaced_reference(tmp_path,monkeypatch):
    installer=installer_module()
    monkeypatch.setattr(installer,'console_entry',lambda path:Path(path)/'Scripts/codex-deepseek.exe')
    root=tmp_path/'runtime';bin_dir=tmp_path/'bin'
    first=release_at(root,'one',b'old managed entry')
    following=release_at(root,'two',b'new managed entry')
    third=release_at(root,'three',b'third managed entry')
    primary=installer.activate_windows(root,first,bin_dir)
    short=bin_dir/'cds.exe';short.symlink_to(primary.name)
    original=short.lstat();raw=os.readlink(short)
    installer.activate_windows(root,following,bin_dir)
    assert short.is_symlink() and os.readlink(short)==raw==primary.name
    assert (short.lstat().st_dev,short.lstat().st_ino)==(original.st_dev,original.st_ino)
    assert short.read_bytes()==primary.read_bytes()==b'new managed entry'
    receipt=root/'installation.json';before=receipt.read_bytes()
    replace=installer.replace_file
    def fail_receipt(source,target):
        if target==receipt:raise PermissionError('injected linked-entry receipt failure')
        return replace(source,target)
    with monkeypatch.context() as patch:
        patch.setattr(installer,'replace_file',fail_receipt)
        with pytest.raises(PermissionError,match='linked-entry receipt failure'):
            installer.activate_windows(root,third,bin_dir)
    assert receipt.read_bytes()==before and short.read_bytes()==primary.read_bytes()==b'new managed entry'
    assert (short.lstat().st_dev,short.lstat().st_ino)==(original.st_dev,original.st_ino)
    # A different link object with the same spelling and resolved target is not the admitted reference.
    held=bin_dir/'held-original-short';copy=installer.shutil.copy2;changed=[]
    def change_link_after_backups(source,target):
        result=copy(source,target)
        if source==receipt and not changed:
            short.rename(held);short.symlink_to(primary.name);changed.append(True)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(installer.shutil,'copy2',change_link_after_backups)
        with pytest.raises(SystemExit,match='short reference changed'):
            installer.activate_windows(root,third,bin_dir)
    assert changed and (short.lstat().st_dev,short.lstat().st_ino)!=(original.st_dev,original.st_ino)
    assert short.read_bytes()==primary.read_bytes()==b'new managed entry' and receipt.read_bytes()==before
    assert os.readlink(held)==raw
    # Matching target bytes do not admit a redirect to another entry.
    foreign=bin_dir/'foreign.exe';foreign.write_bytes(b'new managed entry')
    short.unlink();short.symlink_to(foreign.name)
    with pytest.raises(SystemExit,match='unrelated launcher'):
        installer.activate_windows(root,third,bin_dir)
    assert short.is_symlink() and os.readlink(short)==foreign.name
    assert foreign.read_bytes()==primary.read_bytes()==b'new managed entry' and receipt.read_bytes()==before
    # A dangling link has no verified primary and must not be adopted on a fresh install.
    short.unlink();primary.unlink();short.symlink_to(primary.name)
    with pytest.raises(SystemExit,match='unrelated launcher'):
        installer.activate_windows(root,third,bin_dir)
    assert short.is_symlink() and os.readlink(short)==primary.name and not primary.exists()
    assert receipt.read_bytes()==before


@WINDOWS
@pytest.mark.parametrize('kind',['staging','installed','profile'])
def test_launcher_temp_outputs_bind_current_user_before_publish(tmp_path,kind,assert_private):
    import tempfile
    from codex_deepseek.windows_launchers import make_installed_entry
    package=package_at(tmp_path/'package')
    if kind=='staging':
        # High Python 3.13 tempfile directories begin with BA/OW privacy.
        directory=Path(tempfile.mkdtemp(dir=tmp_path))
        make_launchers(directory,launcher_recipe(package_root=package))
        outputs=[directory,directory/'codex.exe',directory/'codex-deepseek.exe']
    elif kind=='installed':
        entry=tmp_path/'codex-deepseek.exe'
        entry.write_bytes(b'initial pip entry')
        make_installed_entry(entry)
        outputs=[entry]
    else:
        directory=profile_launchers(tmp_path/'home',package_root=package)
        outputs=[directory,directory/'codex.exe',directory/'codex-deepseek.exe']
    for output in outputs:
        assert_private(output)
