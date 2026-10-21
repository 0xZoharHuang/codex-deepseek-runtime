"""Identify owned processes by PID and kernel start time before signalling them."""
import ctypes
import os
from pathlib import Path
import signal
import sys
import time


if sys.platform=='win32':
    from ctypes import wintypes
    _kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    _kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];_kernel.OpenProcess.restype=wintypes.HANDLE
    _kernel.CloseHandle.argtypes=[wintypes.HANDLE];_kernel.CloseHandle.restype=wintypes.BOOL
    _kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)];_kernel.GetProcessTimes.restype=wintypes.BOOL
    _kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];_kernel.WaitForSingleObject.restype=wintypes.DWORD
    _kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];_kernel.TerminateProcess.restype=wintypes.BOOL


def _windows_birth(handle):
    from ctypes import wintypes
    times=[wintypes.FILETIME() for _ in range(4)]
    if not _kernel.GetProcessTimes(handle,*map(ctypes.byref,times)):return None
    return 'windows:'+str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)


def identity(pid):
    try:
        if sys.platform=='win32':
            handle=_kernel.OpenProcess(0x100000|0x1000,False,int(pid))
            if not handle:return None
            try:return _windows_birth(handle) if _kernel.WaitForSingleObject(handle,0)==258 else None
            finally:_kernel.CloseHandle(handle)
        if sys.platform=='darwin':
            class BSDInfo(ctypes.Structure):
                _fields_=[('head',ctypes.c_uint32*12),('comm',ctypes.c_char*16),('name',ctypes.c_char*32),
                          ('tail',ctypes.c_uint32*6),('seconds',ctypes.c_uint64),('microseconds',ctypes.c_uint64)]
            lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
            lib.proc_pidinfo.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
            lib.proc_pidinfo.restype=ctypes.c_int
            info=BSDInfo()
            count=lib.proc_pidinfo(int(pid),3,0,ctypes.byref(info),ctypes.sizeof(info))
            if count!=ctypes.sizeof(info) or info.head[3]!=pid or info.head[1]==5:return None
            return f'{info.seconds}:{info.microseconds}'
        text=(Path('/proc')/str(pid)/'stat').read_text(encoding='utf-8')
        values=text[text.rfind(')')+2:].split()
        return None if values[0]=='Z' else 'linux:'+Path('/proc/sys/kernel/random/boot_id').read_text(encoding='utf-8').strip()+':'+values[19]
    except (OSError,ValueError,IndexError):
        return None


def matches(pid,birth):
    return bool(pid and birth and identity(pid)==birth)


def terminate(pid,birth,timeout=5):
    if sys.platform=='win32':
        if not pid or not birth:return
        handle=_kernel.OpenProcess(0x100000|0x1000|1,False,int(pid))
        if not handle:return
        try:
            if _windows_birth(handle)!=birth or _kernel.WaitForSingleObject(handle,0)!=258:return
            if not _kernel.TerminateProcess(handle,1):raise ctypes.WinError(ctypes.get_last_error())
            if _kernel.WaitForSingleObject(handle,int(timeout*1000))==258:raise TimeoutError('Owned Windows process did not exit')
        finally:_kernel.CloseHandle(handle)
        return
    if not matches(pid,birth):return
    try:os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:return
    until=time.monotonic()+timeout
    while matches(pid,birth) and time.monotonic()<until:time.sleep(.025)
    if matches(pid,birth):
        try:os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:pass
