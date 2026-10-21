"""Exercise platform primitives through real handles, locks and kernel sockets."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from codex_deepseek.platforms import WINDOWS,file_lock,private_directory
from codex_deepseek.processes import identity,matches,terminate
from codex_deepseek.isolation import register_listener,registry,local_addresses
from codex_deepseek.service import native_active
from codex_deepseek.bootstrap import module_environment


def test_real_process_identity_rejects_wrong_birth_before_termination():
    process=subprocess.Popen([sys._base_executable if WINDOWS else sys.executable,'-I','-c','import time;time.sleep(30)'])
    birth=identity(process.pid)
    try:
        assert birth and matches(process.pid,birth)
        terminate(process.pid,'wrong-birth',timeout=.1)
        assert process.poll() is None
        terminate(process.pid,birth,timeout=3);process.wait(timeout=3)
        assert not matches(process.pid,birth)
    finally:
        if process.poll() is None:process.kill();process.wait()


def test_real_lock_rejects_other_handles_and_releases_on_close(tmp_path):
    path=tmp_path/'lease'
    with path.open('a') as first:
        with file_lock(first):
            with path.open('a') as second:
                with pytest.raises(BlockingIOError):
                    with file_lock(second,blocking=False):raise AssertionError('Foreign lease acquired')
        with path.open('a') as second:
            with file_lock(second,blocking=False):pass


async def test_listener_is_registered_from_kernel_owned_socket(tmp_path):
    home=tmp_path/'profile';private_directory(home);(home/'provider-state').mkdir()
    program="import socket,time;s=socket.socket();s.bind(('127.0.0.1',0));s.listen();print(s.getsockname()[1],flush=True);time.sleep(30)"
    process=await asyncio.create_subprocess_exec(sys._base_executable if WINDOWS else sys.executable,'-I','-c',program,env=module_environment(),stdout=asyncio.subprocess.PIPE)
    try:
        port=int(await asyncio.wait_for(process.stdout.readline(),5))
        await register_listener(home,'ws://127.0.0.1:0',process)
        rows=registry(home);assert list(rows)==['ws://127.0.0.1:'+str(port)]
        assert rows[next(iter(rows))]=={'pid':process.pid,'birth':identity(process.pid)}
        assert any(value.is_loopback for value in local_addresses())
    finally:
        if process.returncode is None:process.terminate()
        await process.wait()


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Native Linux daemon process identity contract')
def test_live_linux_daemon_retains_provider_service_and_rejects_stale_boot(tmp_path):
    home=tmp_path/'profile';state=home/'app-server-daemon';state.mkdir(parents=True)
    birth=identity(os.getpid());_,boot,ticks=birth.split(':')
    record={'pid':os.getpid(),'processIdentity':{'bootId':boot,'startTicks':int(ticks)}}
    path=state/'daemon.pid';path.write_text(json.dumps(record));assert native_active(home)
    record['processIdentity']['bootId']='different-boot';path.write_text(json.dumps(record));assert not native_active(home)


def test_directory_and_child_inherit_private_platform_permissions(tmp_path,assert_private):
    home=tmp_path/'独立 private & percent%';private_directory(home);assert_private(home)
    path=home/'secret'
    with open(path,'w',opener=lambda name,flags:os.open(name,flags,0o600)) as stream:stream.write('FIXTURE')
    assert_private(path)


@pytest.mark.skipif(not WINDOWS,reason='Win32 extended-length directory aliases')
def test_extended_paths_cannot_alias_codex_app_state(tmp_path,monkeypatch):
    from codex_deepseek.policy import isolated_path
    from codex_deepseek.protocol_errors import ProtocolError
    protected=tmp_path/'.codex';protected.mkdir()
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    prefix='\\\\?\\'
    for path in [protected,protected/'child',tmp_path]:
        with pytest.raises(ProtocolError,match='overlap'):isolated_path(Path(prefix+str(path)))
    own=tmp_path/'independent'
    assert isolated_path(Path(prefix+str(own))).name=='independent'


@pytest.mark.skipif(not WINDOWS,reason='Native Windows daemon creation FILETIME record')
@pytest.mark.parametrize('name',['daemon.pid','app-server.pid'])
def test_live_windows_daemon_retains_service_and_rejects_stale_creation_time(tmp_path,name):
    # Frozen upstream app-server-daemon backend/pid_start.rs emits no
    # processIdentity on Windows, only decimal FILETIME in processStartTime.
    home=tmp_path/'profile';state=home/'app-server-daemon';state.mkdir(parents=True)
    process=subprocess.Popen([sys._base_executable,'-I','-c','import time;time.sleep(30)'])
    birth=identity(process.pid)
    try:
        assert birth and birth.startswith('windows:')
        ticks=birth.removeprefix('windows:');record={'pid':process.pid,'processStartTime':ticks}
        path=state/name;path.write_text(json.dumps(record),encoding='utf-8')
        assert native_active(home)
        path.write_text(json.dumps({**record,'processStartTime':str(int(ticks)+1)}),encoding='utf-8')
        assert not native_active(home) and process.poll() is None
        path.write_text(json.dumps(record),encoding='utf-8')
        process.terminate();process.wait(timeout=5)
        assert not native_active(home)
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)


def test_native_daemon_package_check_uses_host_executable_suffix(tmp_path):
    from codex_deepseek.isolation import check_daemon_package
    from codex_deepseek.platforms import executable
    from codex_deepseek.protocol_errors import ProtocolError
    home=tmp_path/'profile';current=executable(home/'packages/app-server-daemon/current/bin','codex')
    current.parent.mkdir(parents=True);current.write_bytes(b'OWN_NATIVE_PACKAGE')
    selected=executable(tmp_path,'codex');selected.write_bytes(current.read_bytes())
    for args in [['app-server','daemon','start'],['app-server','proxy']]:
        check_daemon_package(args,home,selected)
    current.write_bytes(b'FOREIGN_NATIVE_PACKAGE')
    for args in [['app-server','daemon','start'],['app-server','proxy']]:
        with pytest.raises(ProtocolError,match='different native daemon build'):
            check_daemon_package(args,home,selected)
    check_daemon_package(['app-server','daemon','stop'],home,selected)
