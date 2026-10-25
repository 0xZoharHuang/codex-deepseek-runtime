"""Native profile ownership ABI; generic artifact/file privacy retains its own contract."""
import os,stat
from pathlib import Path
import pytest
from codex_deepseek.platforms import private_runtime_home

def owner(path):
    if os.name!='nt':return str(path.stat().st_uid)
    import win32security
    return win32security.ConvertSidToStringSid(win32security.GetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,win32security.OWNER_SECURITY_INFORMATION).GetSecurityDescriptorOwner())
def user():
    if os.name!='nt':return str(os.getuid())
    import win32api,win32con,win32security
    token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
    try:return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
    finally:token.Close()
def security(path):
    if os.name!='nt':return stat.S_IMODE(path.stat().st_mode)
    import win32security
    sd=win32security.GetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,win32security.OWNER_SECURITY_INFORMATION|win32security.DACL_SECURITY_INFORMATION)
    return win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(sd,1,win32security.OWNER_SECURITY_INFORMATION|win32security.DACL_SECURITY_INFORMATION)

def test_native_runtime_home_and_cache_are_owned_by_current_user(tmp_path,assert_private):
    home=tmp_path/'home';private_runtime_home(home)
    for path in (home,home/'cache'):
        assert owner(path)==user()
        assert_private(path)

def test_existing_owned_native_cache_keeps_its_permissions(tmp_path):
    home=tmp_path/'home';private_runtime_home(home);cache=home/'cache'
    if os.name=='nt':
        import win32security
        sd=win32security.GetFileSecurity(str(cache),win32security.DACL_SECURITY_INFORMATION)
        acl=sd.GetSecurityDescriptorDacl();acl.AddAccessAllowedAceEx(win32security.ACL_REVISION,3,0x1200a9,win32security.ConvertStringSidToSid('S-1-1-0'))
        win32security.SetNamedSecurityInfo(str(cache),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION|win32security.PROTECTED_DACL_SECURITY_INFORMATION,None,None,acl,None)
    else:cache.chmod(0o750)
    before=security(cache);private_runtime_home(home)
    assert security(cache)==before and owner(cache)==user()

def test_native_runtime_cache_reparse_does_not_modify_its_target(tmp_path):
    home=tmp_path/'home';private_runtime_home(home);(home/'cache').rmdir()
    target=tmp_path/'target';private_runtime_home(target);(target/'canary').write_bytes(b'unchanged')
    before=security(target)
    if os.name=='nt':
        import _winapi
        _winapi.CreateJunction(str(target),str(home/'cache'))
    else:(home/'cache').symlink_to(target,target_is_directory=True)
    with pytest.raises((PermissionError,OSError)):private_runtime_home(home)
    assert security(target)==before and (target/'canary').read_bytes()==b'unchanged'

def test_file_or_hardlink_cannot_be_a_native_runtime_home(tmp_path):
    path=tmp_path/'file';path.write_bytes(b'unchanged');os.link(path,tmp_path/'alias')
    before=security(path)
    with pytest.raises((PermissionError,OSError)):private_runtime_home(path)
    assert security(path)==before and path.read_bytes()==b'unchanged' and (tmp_path/'alias').read_bytes()==b'unchanged'


@pytest.mark.parametrize('layout',['same','ancestor','normal'])
def test_cli_constructs_home_before_an_overlapping_data_container(tmp_path,monkeypatch,layout,assert_private):
    import asyncio
    import codex_deepseek.cli as cli
    import codex_deepseek.web_config as web_config
    base=tmp_path/'selected'
    home=base if layout!='normal' else base/'profile'
    root=base if layout!='ancestor' else base/'container'
    monkeypatch.setattr(cli,'data_root',lambda:root)
    monkeypatch.setenv('CODEX_DEEPSEEK_HOME',str(home))
    monkeypatch.delenv('CODEX_DEEPSEEK_TRACE_DIR',raising=False)
    class Initialized(Exception):pass
    def initialized(actual):
        assert actual==home.resolve()
        raise Initialized()
    # Stop at the first post-construction consumer: no native/model/service run.
    monkeypatch.setattr(web_config,'ensure_browser',initialized)
    with pytest.raises(Initialized):asyncio.run(cli.run(['runtime','status']))
    assert root.is_dir()
    for path in (home,home/'cache'):
        assert owner(path)==user()
        assert_private(path)


@pytest.mark.skipif(os.name!='nt',reason='Windows native parent-handle sharing contract')
def test_native_runtime_home_pins_parent_during_a_real_rename_attack(tmp_path,monkeypatch):
    import threading,_winapi
    import win32security
    from codex_deepseek import platforms
    parent=tmp_path/'parent';private_runtime_home(parent/'home')
    victim=tmp_path/'victim';private_runtime_home(victim/'home')
    canary=victim/'home/canary';canary.write_bytes(b'outside-same-user-object')
    # Make unintended target normalization observable as a DACL change.
    sd=win32security.GetFileSecurity(str(victim/'home'),win32security.DACL_SECURITY_INFORMATION)
    acl=sd.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION,3,0x1200a9,win32security.ConvertStringSidToSid('S-1-1-0'))
    win32security.SetNamedSecurityInfo(str(victim/'home'),win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION|win32security.PROTECTED_DACL_SECURITY_INFORMATION,None,None,acl,None)
    before=security(victim/'home');saved=tmp_path/'saved'
    os.rename(parent,saved);os.rename(saved,parent)  # Actual authority before pinning.
    attack={};done=threading.Event();threads=[]
    opening=platforms._WindowsPrivate.relative
    def attacker():
        attack['attempted']=True
        try:
            os.rename(parent,saved);attack['renamed']=True
            _winapi.CreateJunction(str(victim),str(parent));attack['junction']=True
        except OSError as error:attack['winerror']=error.winerror
        finally:done.set()
    def relative(self,handle,component,descriptor,**options):
        if component=='home' and not threads:
            thread=threading.Thread(target=attacker);threads.append(thread);thread.start()
            assert done.wait(3),'The attacker must actually attempt rename while the parent is pinned'
        return opening(self,handle,component,descriptor,**options)
    monkeypatch.setattr(platforms._WindowsPrivate,'relative',relative)
    try:private_runtime_home(parent/'home')
    finally:
        for thread in threads:thread.join(3)
        # Restore our own layout even when a future regression admits the attack.
        if attack.get('junction'):os.rmdir(parent)
        if attack.get('renamed'):os.rename(saved,parent)
    assert threads and all(not thread.is_alive() for thread in threads)
    assert done.is_set() and attack.get('attempted')
    assert attack.get('winerror')==32 and not attack.get('renamed') and not attack.get('junction')
    assert security(victim/'home')==before and canary.read_bytes()==b'outside-same-user-object'
    os.rename(parent,saved);os.rename(saved,parent)  # Handles released on return.
