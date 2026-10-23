"""OS primitives for the adapter; native Codex continues to own runtime behavior."""
from __future__ import annotations
from contextlib import contextmanager
import asyncio
import ctypes
import os
from pathlib import Path
import signal
import sys

WINDOWS=os.name=='nt'
EXE='.exe' if WINDOWS else ''


def executable(directory,name):return Path(directory)/(name+EXE)


def python_entry(environment):return Path(environment)/('Scripts/python.exe' if WINDOWS else 'bin/python')


def console_entry(environment):return Path(environment)/('Scripts/codex-deepseek.exe' if WINDOWS else 'bin/codex-deepseek')


def site_packages(environment):
    return Path(environment)/'Lib/site-packages' if WINDOWS else next((Path(environment)/'lib').glob('python*/site-packages'))


def private_fd(fd,mode=0o600):
    # Windows files inherit the private installation/home ACL; fchmod is POSIX.
    if not WINDOWS:os.fchmod(fd,mode)


@contextmanager
def file_lock(stream,*,blocking=True):
    if not WINDOWS:
        import fcntl
        fcntl.flock(stream,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)
        return
    import msvcrt
    from ctypes import wintypes
    class OVERLAPPED(ctypes.Structure):
        _fields_=[('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),('Offset',wintypes.DWORD),('OffsetHigh',wintypes.DWORD),('hEvent',wintypes.HANDLE)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LockFileEx.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(OVERLAPPED)]
    kernel.LockFileEx.restype=wintypes.BOOL
    kernel.UnlockFileEx.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(OVERLAPPED)]
    kernel.UnlockFileEx.restype=wintypes.BOOL
    handle=msvcrt.get_osfhandle(stream.fileno());overlap=OVERLAPPED()
    if not kernel.LockFileEx(handle,2|(0 if blocking else 1),0,1,0,ctypes.byref(overlap)):
        error=ctypes.get_last_error()
        if error==33:raise BlockingIOError(error,'Profile lease is held')
        raise ctypes.WinError(error)
    try:yield
    finally:
        if not kernel.UnlockFileEx(handle,0,1,0,ctypes.byref(overlap)):raise ctypes.WinError(ctypes.get_last_error())


def install_signals(loop,handlers):
    """Return a cleanup callback using the platform's supported signal interface."""
    old={}
    for signum,callback in handlers.items():
        if WINDOWS:
            old[signum]=signal.getsignal(signum)
            signal.signal(signum,lambda _signum,_frame,callback=callback:loop.call_soon_threadsafe(callback))
        else:loop.add_signal_handler(signum,callback)
    def restore():
        for signum in handlers:
            if WINDOWS:signal.signal(signum,old[signum])
            else:loop.remove_signal_handler(signum)
    return restore


def private_directory(path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not WINDOWS:path.chmod(0o700);return
    from ctypes import wintypes
    api=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    api.OpenProcessToken.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)];api.OpenProcessToken.restype=wintypes.BOOL
    api.GetTokenInformation.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)];api.GetTokenInformation.restype=wintypes.BOOL
    api.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(wintypes.LPWSTR)];api.ConvertSidToStringSidW.restype=wintypes.BOOL
    token=wintypes.HANDLE();size=wintypes.DWORD();sid_text=wintypes.LPWSTR()
    if not api.OpenProcessToken(kernel.GetCurrentProcess(),0x0008,ctypes.byref(token)):raise ctypes.WinError(ctypes.get_last_error())
    try:
        api.GetTokenInformation(token,1,None,0,ctypes.byref(size))
        if not size.value:raise ctypes.WinError(ctypes.get_last_error())
        user=ctypes.create_string_buffer(size.value)
        if not api.GetTokenInformation(token,1,user,size,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
        sid_pointer=ctypes.cast(user,ctypes.POINTER(ctypes.c_void_p))[0]
        if not api.ConvertSidToStringSidW(sid_pointer,ctypes.byref(sid_text)):raise ctypes.WinError(ctypes.get_last_error())
        sid=sid_text.value
    finally:
        if sid_text:kernel.LocalFree(sid_text)
        kernel.CloseHandle(token)
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(wintypes.DWORD)]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=wintypes.BOOL
    api.SetFileSecurityW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,ctypes.c_void_p];api.SetFileSecurityW.restype=wintypes.BOOL
    descriptor=ctypes.c_void_p()
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;OICI;FA;;;'+sid+')(A;OICI;FA;;;SY)',1,ctypes.byref(descriptor),None):raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not api.SetFileSecurityW(str(path),0x80000004,descriptor):raise ctypes.WinError(ctypes.get_last_error())
    finally:kernel.LocalFree(descriptor)


def replace_file(source,destination):
    # Windows readers and antivirus may momentarily deny FILE_SHARE_DELETE.
    # Keep replacement atomic; never fall back to truncating the destination.
    import time
    deadline=time.monotonic()+1
    while True:
        try:return os.replace(source,destination)
        except OSError as error:
            if not WINDOWS or getattr(error,'winerror',None) not in {5,32,33} or time.monotonic()>=deadline:raise
            time.sleep(.01)


def comparison_path(path):
    """Compare Win32 and extended-length aliases of the same resolved path."""
    value=Path(path).expanduser().resolve()
    if not WINDOWS:return value
    text=str(value)
    if text.startswith('\\\\?\\UNC\\'):text='\\\\'+text[8:]
    elif text.startswith('\\\\?\\'):text=text[4:]
    return Path(text)


def paths_overlap(left,right):
    first=comparison_path(left);second=comparison_path(right)
    if first==second or first.is_relative_to(second) or second.is_relative_to(first):return True
    if WINDOWS:
        # Also cover an accessible SMB/volume alias whose spelling differs.
        for path,target in [(path,second) for path in [first,*first.parents]]+[(first,path) for path in second.parents]:
            try:
                if os.path.samefile(path,target):return True
            except OSError:pass
    return False


def native_artifact_access(root,package,manifest_files):
    """Windows RX for verified public native assets, never private install data.

    Match the official package's restricted-token RX requirement. Everyone is
    intentional: restricted tokens require access through their restricting SIDs
    as well as their user SID. No write/delete or inheritable ancestor grant.
    The installer must verify every manifest hash before and after this call.
    """
    if not WINDOWS:return
    import stat
    from ctypes import wintypes
    root=Path(root).absolute();package=Path(package).absolute()
    if package.parent!=root/'native':raise ValueError('Native artifact outside its owned container')
    def physical(path,directory=None):
        info=path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Redirected native artifact path')
        if directory is True and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Native artifact ancestor is not a directory')
        if not stat.S_ISDIR(info.st_mode) and not (stat.S_ISREG(info.st_mode) and info.st_nlink==1):
            raise ValueError('Native artifact is nonregular or shared')
        return stat.S_ISDIR(info.st_mode)
    for path in [package,*package.parents]:physical(path,True)
    members=[];pending=[package];actual=set()
    while pending:
        directory=pending.pop();members.append((directory,True))
        for path in directory.iterdir():
            if physical(path):pending.append(path)
            else:members.append((path,False));actual.add(path.relative_to(package).as_posix())
    if actual!=set(manifest_files):raise ValueError('Native artifact inventory changed before permissions')
    # Normalize only the owned native container and public package. Root retains
    # its existing private ACL; no parent above root is modified.
    private_directory(root/'native')
    private_directory(package)
    api=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class TRUSTEE(ctypes.Structure):
        _fields_=[('multiple',ctypes.c_void_p),('operation',ctypes.c_int),
                  ('form',ctypes.c_int),('type',ctypes.c_int),('name',ctypes.c_void_p)]
    class ACCESS(ctypes.Structure):
        _fields_=[('permissions',wintypes.DWORD),('mode',ctypes.c_int),
                  ('inheritance',wintypes.DWORD),('trustee',TRUSTEE)]
    api.GetNamedSecurityInfoW.argtypes=[wintypes.LPWSTR,ctypes.c_int,wintypes.DWORD,
        ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    api.GetNamedSecurityInfoW.restype=wintypes.DWORD
    api.SetEntriesInAclW.argtypes=[wintypes.ULONG,ctypes.POINTER(ACCESS),ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    api.SetEntriesInAclW.restype=wintypes.DWORD
    api.SetNamedSecurityInfoW.argtypes=[wintypes.LPWSTR,ctypes.c_int,wintypes.DWORD,
        ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
    api.SetNamedSecurityInfoW.restype=wintypes.DWORD
    api.CreateWellKnownSid.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD)]
    api.CreateWellKnownSid.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    size=wintypes.DWORD(68);world=ctypes.create_string_buffer(size.value)
    if not api.CreateWellKnownSid(1,None,world,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
    def read_acl(path):
        acl=ctypes.c_void_p();descriptor=ctypes.c_void_p()
        error=api.GetNamedSecurityInfoW(str(path),1,4,None,None,ctypes.byref(acl),None,ctypes.byref(descriptor))
        if error:raise ctypes.WinError(error)
        return acl,descriptor
    def with_read(acl,mask,inheritance):
        updated=ctypes.c_void_p()
        entry=ACCESS(mask,1,inheritance,TRUSTEE(None,0,0,5,ctypes.cast(world,ctypes.c_void_p)))
        error=api.SetEntriesInAclW(1,ctypes.byref(entry),acl,ctypes.byref(updated))
        if error:raise ctypes.WinError(error)
        return updated
    def apply(path,acl,protected=False):
        physical(path)
        error=api.SetNamedSecurityInfoW(str(path),1,4|(0x80000000 if protected else 0),None,None,acl,None)
        if error:raise ctypes.WinError(error)
    private,descriptor=read_acl(package)
    try:
        for is_directory in [True,False]:
            readable=with_read(private,0x1200a9,3 if is_directory else 0)
            try:
                for path,directory in members:
                    if directory==is_directory:apply(path,readable,True)
            finally:kernel.LocalFree(readable)
    finally:kernel.LocalFree(descriptor)
    for path in [root,root/'native']:
        physical(path,True);acl,descriptor=read_acl(path)
        try:
            reachable=with_read(acl,0xa0,0)
            try:apply(path,reachable)
            finally:kernel.LocalFree(reachable)
        finally:kernel.LocalFree(descriptor)
