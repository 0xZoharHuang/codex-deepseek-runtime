"""Acceptance-only native terminals: Unix PTY or the Windows ConPTY OS API.

The Windows sequence follows Microsoft's CreatePseudoConsole session example;
separate reader/writer threads prevent synchronous pipe-buffer deadlocks.
References: https://learn.microsoft.com/windows/console/creating-a-pseudoconsole-session
and microsoft/terminal, src/cascadia/TerminalConnection/ConptyConnection.cpp.
This module is never installed as part of the adapter.
"""
from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import threading
import time


def write_qa_receipt(root,entry,names):
    """Bind QA source independently of the immutable installed runtime source."""
    directory=Path(__file__).resolve().parent
    files={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in sorted(names)}
    snapshot=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
    entry=Path(entry).resolve()
    with entry.open('rb') as stream:entry_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    record={'qa_snapshot_sha256':snapshot,'qa_files_sha256':files,'entry':str(entry),'entry_sha256':entry_hash}
    installation=entry.parents[2]/'installation.json'
    if installation.is_file():
        metadata=json.loads(installation.read_text(encoding='utf-8'))
        assert entry_hash==metadata['console_script_sha256'],'QA entry differs from its immutable installation'
        record.update(adapter_source_id=metadata['source_id'],native_archive_sha256=metadata['native_archive_sha256'])
    (Path(root)/'qa-source.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    return snapshot


class PosixTerminal:
    def __init__(self, command, *, env, cwd=None, columns=120, rows=30):
        import fcntl, pty, struct, termios
        from codex_deepseek.processes import identity
        self.pid,self.fd=pty.fork();self.returncode=None
        if self.pid==0:
            if cwd:os.chdir(cwd)
            os.execvpe(command[0],command,env)
        self.birth=identity(self.pid)
        fcntl.ioctl(self.fd,termios.TIOCSWINSZ,struct.pack('HHHH',rows,columns,0,0))

    def read(self, timeout=.2):
        import select
        if select.select([self.fd],[],[],timeout)[0]:
            try:return os.read(self.fd,65536)
            except OSError:return b''
        return b''

    def write(self, data):os.write(self.fd,data)

    def poll(self):
        if self.returncode is None:
            child,status=os.waitpid(self.pid,os.WNOHANG)
            if child:self.returncode=os.waitstatus_to_exitcode(status)
        return self.returncode

    def close(self):
        from codex_deepseek.processes import terminate
        # Release the PTY master before reaping its session leader. On macOS a
        # dying terminal client can remain in exit while the master is open.
        # Never let failed acceptance cleanup block the evidence writer.
        if self.fd is not None:os.close(self.fd);self.fd=None
        if self.poll() is None:
            terminate(self.pid,self.birth)
            deadline=time.monotonic()+10
            while self.poll() is None and time.monotonic()<deadline:time.sleep(.025)
            if self.poll() is None:raise TimeoutError('The owned PTY session did not close')


class WindowsTerminal:
    def __init__(self, command, *, env, cwd=None, columns=120, rows=30):
        import ctypes
        from ctypes import wintypes as wt
        self.ctypes=ctypes;self.wt=wt;self.kernel=kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.output=queue.Queue();self.input=queue.Queue();self.errors=[];self.closed=False
        self.hpc=wt.HANDLE();self.process=wt.HANDLE();self.input_write=wt.HANDLE();self.output_read=wt.HANDLE()
        class COORD(ctypes.Structure):_fields_=[('X',ctypes.c_short),('Y',ctypes.c_short)]
        class STARTUPINFO(ctypes.Structure):
            _fields_=[('cb',wt.DWORD),('lpReserved',wt.LPWSTR),('lpDesktop',wt.LPWSTR),('lpTitle',wt.LPWSTR),
                      ('dwX',wt.DWORD),('dwY',wt.DWORD),('dwXSize',wt.DWORD),('dwYSize',wt.DWORD),
                      ('dwXCountChars',wt.DWORD),('dwYCountChars',wt.DWORD),('dwFillAttribute',wt.DWORD),
                      ('dwFlags',wt.DWORD),('wShowWindow',wt.WORD),('cbReserved2',wt.WORD),('lpReserved2',ctypes.c_void_p),
                      ('hStdInput',wt.HANDLE),('hStdOutput',wt.HANDLE),('hStdError',wt.HANDLE)]
        class STARTUPINFOEX(ctypes.Structure):_fields_=[('StartupInfo',STARTUPINFO),('lpAttributeList',ctypes.c_void_p)]
        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_=[('hProcess',wt.HANDLE),('hThread',wt.HANDLE),('dwProcessId',wt.DWORD),('dwThreadId',wt.DWORD)]
        kernel.CreatePipe.argtypes=[ctypes.POINTER(wt.HANDLE),ctypes.POINTER(wt.HANDLE),ctypes.c_void_p,wt.DWORD];kernel.CreatePipe.restype=wt.BOOL
        kernel.CloseHandle.argtypes=[wt.HANDLE];kernel.CloseHandle.restype=wt.BOOL
        kernel.CreatePseudoConsole.argtypes=[COORD,wt.HANDLE,wt.HANDLE,wt.DWORD,ctypes.POINTER(wt.HANDLE)];kernel.CreatePseudoConsole.restype=ctypes.c_long
        kernel.ClosePseudoConsole.argtypes=[wt.HANDLE];kernel.ClosePseudoConsole.restype=None
        kernel.InitializeProcThreadAttributeList.argtypes=[ctypes.c_void_p,wt.DWORD,wt.DWORD,ctypes.POINTER(ctypes.c_size_t)];kernel.InitializeProcThreadAttributeList.restype=wt.BOOL
        kernel.UpdateProcThreadAttribute.argtypes=[ctypes.c_void_p,wt.DWORD,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_void_p];kernel.UpdateProcThreadAttribute.restype=wt.BOOL
        kernel.DeleteProcThreadAttributeList.argtypes=[ctypes.c_void_p];kernel.DeleteProcThreadAttributeList.restype=None
        kernel.CreateProcessW.argtypes=[wt.LPCWSTR,wt.LPWSTR,ctypes.c_void_p,ctypes.c_void_p,wt.BOOL,wt.DWORD,ctypes.c_void_p,wt.LPCWSTR,ctypes.POINTER(STARTUPINFOEX),ctypes.POINTER(PROCESS_INFORMATION)];kernel.CreateProcessW.restype=wt.BOOL
        kernel.ReadFile.argtypes=[wt.HANDLE,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD),ctypes.c_void_p];kernel.ReadFile.restype=wt.BOOL
        kernel.WriteFile.argtypes=[wt.HANDLE,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD),ctypes.c_void_p];kernel.WriteFile.restype=wt.BOOL
        kernel.WaitForSingleObject.argtypes=[wt.HANDLE,wt.DWORD];kernel.WaitForSingleObject.restype=wt.DWORD
        kernel.GetExitCodeProcess.argtypes=[wt.HANDLE,ctypes.POINTER(wt.DWORD)];kernel.GetExitCodeProcess.restype=wt.BOOL
        kernel.TerminateProcess.argtypes=[wt.HANDLE,wt.UINT];kernel.TerminateProcess.restype=wt.BOOL
        input_read=wt.HANDLE();output_write=wt.HANDLE();attributes=None;attributes_initialized=False
        try:
            if not kernel.CreatePipe(ctypes.byref(input_read),ctypes.byref(self.input_write),None,0):raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.CreatePipe(ctypes.byref(self.output_read),ctypes.byref(output_write),None,0):raise ctypes.WinError(ctypes.get_last_error())
            result=kernel.CreatePseudoConsole(COORD(columns,rows),input_read,output_write,0,ctypes.byref(self.hpc))
            if result:raise OSError('CreatePseudoConsole failed: '+hex(result&0xffffffff))
            size=ctypes.c_size_t();kernel.InitializeProcThreadAttributeList(None,1,0,ctypes.byref(size))
            attributes=ctypes.create_string_buffer(size.value)
            if not kernel.InitializeProcThreadAttributeList(attributes,1,0,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
            attributes_initialized=True
            if not kernel.UpdateProcThreadAttribute(attributes,0,0x00020016,self.hpc,ctypes.sizeof(self.hpc),None,None):raise ctypes.WinError(ctypes.get_last_error())
            startup=STARTUPINFOEX();startup.StartupInfo.cb=ctypes.sizeof(startup)
            # A redirected host (SSH/pytest) otherwise lends its pipes to the
            # client even with handle inheritance disabled. Microsoft Terminal
            # sets STARTF_USESTDHANDLES with NULL handles for the same reason.
            startup.StartupInfo.dwFlags=0x00000100
            startup.lpAttributeList=ctypes.cast(attributes,ctypes.c_void_p)
            info=PROCESS_INFORMATION();line=ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            block=ctypes.create_unicode_buffer('\0'.join(key+'='+value for key,value in sorted(env.items(),key=lambda pair:pair[0].upper()))+'\0')
            if not kernel.CreateProcessW(command[0],line,None,None,False,0x00080000|0x00000400,block,str(cwd) if cwd else None,ctypes.byref(startup),ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.process=wt.HANDLE(info.hProcess);self.pid=info.dwProcessId;kernel.CloseHandle(info.hThread)
        except BaseException:
            if self.hpc:kernel.ClosePseudoConsole(self.hpc)
            for handle in [self.input_write,self.output_read]:
                if handle:kernel.CloseHandle(handle)
            raise
        finally:
            if attributes_initialized:kernel.DeleteProcThreadAttributeList(attributes)
            for handle in [input_read,output_write]:
                if handle:kernel.CloseHandle(handle)
        self.reader=threading.Thread(target=self._reader,daemon=True);self.writer=threading.Thread(target=self._writer,daemon=True)
        self.reader.start();self.writer.start()

    def _reader(self):
        buffer=self.ctypes.create_string_buffer(65536);count=self.wt.DWORD()
        while True:
            if not self.kernel.ReadFile(self.output_read,buffer,len(buffer),self.ctypes.byref(count),None):
                error=self.ctypes.get_last_error()
                if error not in {6,109,232}:self.errors.append(('read',error))
                return
            if not count.value:return
            self.output.put(buffer.raw[:count.value])

    def _writer(self):
        while True:
            data=self.input.get()
            if data is None:return
            count=self.wt.DWORD();offset=0
            while offset<len(data):
                remaining=data[offset:]
                if not self.kernel.WriteFile(self.input_write,remaining,len(remaining),self.ctypes.byref(count),None):
                    error=self.ctypes.get_last_error()
                    if error not in {6,109,232}:self.errors.append(('write',error))
                    return
                if not count.value:self.errors.append(('empty_write',0));return
                offset+=count.value

    def read(self,timeout=.2):
        if self.errors:raise OSError('ConPTY I/O error: '+repr(self.errors))
        try:return self.output.get(timeout=timeout)
        except queue.Empty:return b''

    def write(self,data):self.input.put(data)

    def poll(self):
        result=self.kernel.WaitForSingleObject(self.process,0)
        if result==258:return None
        if result!=0:raise self.ctypes.WinError(self.ctypes.get_last_error())
        value=self.wt.DWORD()
        if not self.kernel.GetExitCodeProcess(self.process,self.ctypes.byref(value)):raise self.ctypes.WinError(self.ctypes.get_last_error())
        return value.value

    def close(self):
        if self.closed:return
        self.closed=True;self.input.put(None)
        # Closing ConPTY signals only its own clients. Keep draining output on
        # the reader thread, as required on Windows builds before 26100.
        closer=threading.Thread(target=self.kernel.ClosePseudoConsole,args=(self.hpc,),daemon=True);closer.start();closer.join(15)
        if closer.is_alive():
            self.kernel.TerminateProcess(self.process,1)
            raise TimeoutError('The owned ConPTY session did not close')
        self.writer.join(2);self.reader.join(2)
        for handle in [self.input_write,self.output_read,self.process]:self.kernel.CloseHandle(handle)


def terminal(command,**kwargs):
    return (WindowsTerminal if os.name=='nt' else PosixTerminal)(command,**kwargs)
