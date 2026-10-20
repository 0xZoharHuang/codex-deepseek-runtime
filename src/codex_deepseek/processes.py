"""Identify owned processes by PID and kernel start time before signalling them."""
import ctypes
import os
from pathlib import Path
import signal
import sys
import time


def identity(pid):
    try:
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
        text=(Path('/proc')/str(pid)/'stat').read_text()
        values=text[text.rfind(')')+2:].split()
        return None if values[0]=='Z' else values[19]
    except (OSError,ValueError,IndexError):
        return None


def matches(pid,birth):
    return bool(pid and birth and identity(pid)==birth)


def terminate(pid,birth,timeout=5):
    if not matches(pid,birth):return
    try:os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:return
    until=time.monotonic()+timeout
    while matches(pid,birth) and time.monotonic()<until:time.sleep(.025)
    if matches(pid,birth):
        try:os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:pass
