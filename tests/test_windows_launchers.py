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
    handle=kernel.CreateFileW(str(launcher),0x80000000,1,None,3,0,None)
    assert handle!=ctypes.c_void_p(-1).value
    try:
        with pytest.raises(OSError):installer.activate_windows(root,following,tmp_path/'bin')
        assert launcher.read_bytes()==b'first entry'
        assert (root/'installation.json').read_bytes()==before
    finally:kernel.CloseHandle(handle)
    assert not list(launcher.parent.glob('.codex-deepseek-*.exe'))
    installer.activate_windows(root,following,tmp_path/'bin')
    assert launcher.read_bytes()==b'second entry'
