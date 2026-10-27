"""OS primitives for the adapter; native Codex continues to own runtime behavior."""
from __future__ import annotations
from contextlib import contextmanager
import asyncio
import ctypes
import os
import ntpath
from urllib.parse import quote
from pathlib import Path
import signal
import sys

WINDOWS=os.name=='nt'
EXE='.exe' if WINDOWS else ''


def sqlite_filename(path, *, read_only=False):
    """Use SQLite's extended Windows filename for its database and sidecars.

    This formats only the I/O argument. Callers retain logical Path identities,
    link checks, transactions, journal policy and explicit uri=True for readers.
    """
    value=os.fspath(path)
    if WINDOWS:
        if not value.startswith('\\\\?\\'):
            # Lexical fullpath normalization does not follow filesystem reparses.
            value=ntpath.abspath(value)
            value=('\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value)
        if read_only:return 'file:'+quote(value,safe='')+'?mode=ro'
    elif read_only:return Path(value).absolute().as_uri()+'?mode=ro'
    return value


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


class _WindowsPrivate:
    """Owner/DACL construction for caller-authorized private state objects."""
    def __init__(self):
        from ctypes import wintypes as w
        self.w=w;self.a=ctypes.WinDLL('advapi32',use_last_error=True);self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        P=ctypes.c_void_p;D=w.DWORD;H=w.HANDLE;B=w.BOOL
        self.nt=ctypes.WinDLL('ntdll',use_last_error=True)
        def function(lib,name,args,result):
            f=getattr(lib,name);f.argtypes=args;f.restype=result
        for name,args,result in [
            ('GetCurrentProcess',[],H),('CloseHandle',[H],B),('LocalFree',[P],P),
            ('GetFileType',[H],D),('GetFileInformationByHandle',[H,P],B),
            ('CreateFileW',[w.LPCWSTR,D,D,P,D,D,H],H),
            ('CreateDirectoryW',[w.LPCWSTR,P],B)]:
            function(self.k,name,args,result)
        for name,args,result in [
            ('GetKernelObjectSecurity',[H,D,P,D,ctypes.POINTER(D)],B),
            ('OpenProcessToken',[H,D,ctypes.POINTER(H)],B),
            ('GetTokenInformation',[H,ctypes.c_int,P,D,ctypes.POINTER(D)],B),
            ('ConvertSidToStringSidW',[P,ctypes.POINTER(w.LPWSTR)],B),
            ('ConvertStringSecurityDescriptorToSecurityDescriptorW',[w.LPCWSTR,D,ctypes.POINTER(P),ctypes.POINTER(D)],B),
            ('GetSecurityDescriptorOwner',[P,ctypes.POINTER(P),ctypes.POINTER(B)],B),
            ('GetSecurityDescriptorDacl',[P,ctypes.POINTER(B),ctypes.POINTER(P),ctypes.POINTER(B)],B),
            ('GetSecurityDescriptorControl',[P,ctypes.POINTER(w.WORD),ctypes.POINTER(D)],B),
            ('IsValidAcl',[P],B),('GetAce',[P,D,ctypes.POINTER(P)],B),
            ('IsValidSid',[P],B),('GetLengthSid',[P],D),
            ('SetSecurityInfo',[H,ctypes.c_int,D,P,P,P,P],D)]:
            function(self.a,name,args,result)
        function(self.nt,'NtCreateFile',[ctypes.POINTER(H),D,P,P,P,D,D,D,D,P,D],ctypes.c_long)
        function(self.nt,'RtlNtStatusToDosError',[ctypes.c_long],D)
        token=H();size=D()
        if not self.a.OpenProcessToken(self.k.GetCurrentProcess(),8,ctypes.byref(token)):raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.a.GetTokenInformation(token,1,None,0,ctypes.byref(size))
            if not 0<size.value<=65536:raise OSError('Invalid private-state TokenUser size')
            value=ctypes.create_string_buffer(size.value)
            if not self.a.GetTokenInformation(token,1,value,size,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
            self.sid=self.sid_text(ctypes.cast(value,ctypes.POINTER(P))[0])
        finally:self.k.CloseHandle(token)
    def sid_text(self,pointer):
        text=self.w.LPWSTR()
        if not pointer or not self.a.IsValidSid(pointer):raise OSError('Invalid private-state SID')
        if not self.a.ConvertSidToStringSidW(pointer,ctypes.byref(text)):raise ctypes.WinError(ctypes.get_last_error())
        try:return text.value
        finally:self.k.LocalFree(text)
    def descriptor(self,directory):
        value=ctypes.c_void_p();flags='OICI' if directory else ''
        text='O:'+self.sid+'D:P(A;'+flags+';FA;;;'+self.sid+')(A;'+flags+';FA;;;SY)'
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(text,1,ctypes.byref(value),None):raise ctypes.WinError(ctypes.get_last_error())
        return value
    def information(self,handle,directory):
        class INFO(ctypes.Structure):
            _fields_=[('attributes',self.w.DWORD),('created',self.w.FILETIME),('accessed',self.w.FILETIME),
                      ('written',self.w.FILETIME),('volume',self.w.DWORD),('size_high',self.w.DWORD),
                      ('size_low',self.w.DWORD),('links',self.w.DWORD),('index_high',self.w.DWORD),('index_low',self.w.DWORD)]
        value=INFO()
        if self.k.GetFileType(handle)!=1 or not self.k.GetFileInformationByHandle(handle,ctypes.byref(value)):raise OSError('Private object must be a disk file/directory')
        if value.attributes&0x400 or bool(value.attributes&0x10)!=directory or (not directory and value.links!=1):
            raise PermissionError('Refusing linked, reparse or wrong-type private object')
        return value.volume,value.index_high,value.index_low
    def security(self,handle):
        n=self.w.DWORD();self.a.GetKernelObjectSecurity(handle,5,None,0,ctypes.byref(n))
        if not 0<n.value<=65536:raise OSError('Invalid private-state descriptor size')
        raw=ctypes.create_string_buffer(n.value)
        if not self.a.GetKernelObjectSecurity(handle,5,raw,n,ctypes.byref(n)):raise ctypes.WinError(ctypes.get_last_error())
        owner=ctypes.c_void_p();default=self.w.BOOL()
        if not self.a.GetSecurityDescriptorOwner(raw,ctypes.byref(owner),ctypes.byref(default)):raise ctypes.WinError(ctypes.get_last_error())
        return raw,self.sid_text(owner)
    def private_acl(self,raw):
        present=self.w.BOOL();default=self.w.BOOL();acl=ctypes.c_void_p()
        if not self.a.GetSecurityDescriptorDacl(raw,ctypes.byref(present),ctypes.byref(acl),ctypes.byref(default)):return False
        start=ctypes.addressof(raw);end=start+len(raw)
        if not present.value or not acl.value or not start<=acl.value<=end-8 or not self.a.IsValidAcl(acl):return False
        size=ctypes.c_ushort.from_address(acl.value+2).value;count=ctypes.c_ushort.from_address(acl.value+4).value
        if count!=2 or acl.value+size>end:return False
        principals=[]
        for i in range(count):
            ace=ctypes.c_void_p()
            if not self.a.GetAce(acl,i,ctypes.byref(ace)) or not acl.value<=ace.value<=acl.value+size-16:return False
            kind=ctypes.c_ubyte.from_address(ace.value).value;flags=ctypes.c_ubyte.from_address(ace.value+1).value
            length=ctypes.c_ushort.from_address(ace.value+2).value
            if kind!=0 or flags&~0x17 or flags&8 or length<16 or ace.value+length>acl.value+size:return False
            if ctypes.c_ulong.from_address(ace.value+4).value!=0x1f01ff:return False
            sid=ace.value+8
            if not self.a.IsValidSid(sid) or self.a.GetLengthSid(sid)>length-8:return False
            principals.append(self.sid_text(sid))
        return set(principals)=={self.sid,'S-1-5-18'} and len(set(principals))==2
    def apply(self,handle,directory,*,preserve_acl=False):
        self.information(handle,directory);raw,owner=self.security(handle)
        # Existing BA ownership is recognized only for the old exact per-user
        # private ACL, not for arbitrary administrator-owned/shared directories.
        control=self.w.WORD();revision=self.w.DWORD()
        protected=bool(self.a.GetSecurityDescriptorControl(raw,ctypes.byref(control),ctypes.byref(revision)) and control.value&0x1000)
        if owner!=self.sid and not (owner=='S-1-5-32-544' and protected and self.private_acl(raw)):
            raise PermissionError('Refusing foreign or unproven shared private-state owner')
        if preserve_acl and owner==self.sid:return
        descriptor=self.descriptor(directory)
        try:
            target=ctypes.c_void_p();acl=ctypes.c_void_p();default=self.w.BOOL();present=self.w.BOOL()
            if not self.a.GetSecurityDescriptorOwner(descriptor,ctypes.byref(target),ctypes.byref(default)):raise ctypes.WinError(ctypes.get_last_error())
            if not self.a.GetSecurityDescriptorDacl(descriptor,ctypes.byref(present),ctypes.byref(acl),ctypes.byref(default)):raise ctypes.WinError(ctypes.get_last_error())
            change_owner=owner!=self.sid
            flags=(1 if change_owner else 0)|(0 if preserve_acl else 0x80000004)
            error=self.a.SetSecurityInfo(handle,1,flags,target if change_owner else None,None,None if preserve_acl else acl,None)
            if error:raise ctypes.WinError(error)
        finally:self.k.LocalFree(descriptor)
        after,owner=self.security(handle)
        if owner!=self.sid:raise PermissionError('Private-state owner readback differs from TokenUser')
        if not preserve_acl:
            control=self.w.WORD();revision=self.w.DWORD()
            if not self.a.GetSecurityDescriptorControl(after,ctypes.byref(control),ctypes.byref(revision)) or not control.value&0x1000 or not self.private_acl(after):
                raise PermissionError('Private-state DACL readback failed')
    def relative(self,parent,component,descriptor,*,final,access=None):
        if not component or component in ('.','..') or any(c in component for c in ('/','\\','\0',':')) or component.endswith((' ','.')):
            raise PermissionError('Unsafe native profile component')
        base=component.split('.',1)[0].upper()
        if base in {'CON','PRN','AUX','NUL','CONIN$','CONOUT$',*[p+n for p in ('COM','LPT') for n in '123456789\u00b9\u00b2\u00b3']}:
            raise PermissionError('Reserved native profile component')
        encoded=component.encode('utf-16-le','surrogatepass')
        if len(encoded)>65532:raise ValueError('Native profile component too long')
        class NAME(ctypes.Structure):
            _fields_=[('length',self.w.USHORT),('maximum',self.w.USHORT),('buffer',ctypes.c_void_p)]
        class ATTR(ctypes.Structure):
            _fields_=[('length',self.w.DWORD),('root',self.w.HANDLE),('name',ctypes.POINTER(NAME)),
                      ('flags',self.w.DWORD),('security',ctypes.c_void_p),('quality',ctypes.c_void_p)]
        class IO(ctypes.Structure):
            _fields_=[('status',ctypes.c_void_p),('information',ctypes.c_size_t)]
        value=ctypes.create_string_buffer(encoded+b'\0\0')
        name=NAME(len(encoded),len(encoded)+2,ctypes.addressof(value))
        attributes=ATTR(ctypes.sizeof(ATTR),parent,ctypes.pointer(name),0x1040,descriptor,None)
        output=self.w.HANDLE();io=IO()
        if access is None:access=0x1000a0|(0x60000 if final else 0)
        status=self.nt.NtCreateFile(ctypes.byref(output),access,ctypes.byref(attributes),ctypes.byref(io),None,
                                    0x10,3,3,0x00200021,None,0)
        if status<0:raise ctypes.WinError(self.nt.RtlNtStatusToDosError(status))
        try:self.information(output,True)
        except BaseException:self.k.CloseHandle(output);raise
        return output
    def directory(self,path,*,preserve_existing_acl=False):
        path=Path(path).absolute()
        if '..' in path.parts or not path.anchor or len(path.parts)<2:
            raise PermissionError('Unsafe native profile root')
        descriptor=self.descriptor(True);parents=[]
        try:
            # Anchor once, then resolve only single relative components. Traverse
            # access and no DELETE sharing pin each directory until readback.
            anchor=self.k.CreateFileW(path.anchor,0x1000a0,3,None,3,0x02200000,None)
            if anchor in (None,ctypes.c_void_p(-1).value):raise ctypes.WinError(ctypes.get_last_error())
            parents.append(anchor);self.information(anchor,True)
            components=path.parts[1:]
            for index,component in enumerate(components):
                final=index+1==len(components)
                access=0x1200a0 if final and preserve_existing_acl else None
                handle=self.relative(parents[-1],component,descriptor,final=final,access=access)
                parents.append(handle)
            raw,owner=self.security(parents[-1])
            if preserve_existing_acl and owner==self.sid:return
            if owner!=self.sid:
                # Only the exact prior private BA-owned layout may migrate owner.
                # Ordinary user-owned directories need WRITE_DAC, not WRITE_OWNER.
                control=self.w.WORD();revision=self.w.DWORD()
                protected=bool(self.a.GetSecurityDescriptorControl(raw,ctypes.byref(control),ctypes.byref(revision)) and control.value&0x1000)
                if owner!='S-1-5-32-544' or not protected or not self.private_acl(raw):
                    raise PermissionError('Refusing foreign or unproven shared private-state owner')
                writable=self.relative(parents[-2],components[-1],descriptor,final=True,
                                       access=0x1a00a0 if preserve_existing_acl else 0x1e00a0)
                parents.append(writable)
                if self.information(parents[-1],True)!=self.information(parents[-2],True):
                    raise PermissionError('Native private-state identity changed')
            self.apply(parents[-1],True,preserve_acl=preserve_existing_acl)
        finally:
            for handle in reversed(parents):self.k.CloseHandle(handle)
            self.k.LocalFree(descriptor)


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


def private_runtime_home(path):
    """Construct an authorized native profile with explicit current-user ownership."""
    path=Path(path)
    if not WINDOWS:
        private_directory(path);cache=path/'cache'
        if cache.is_symlink():raise PermissionError('Redirected native profile cache')
        cache.mkdir(mode=0o700,exist_ok=True);return
    security=_WindowsPrivate()
    security.directory(path)
    # Native uses this shared cache with private=false: preserve existing grants.
    security.directory(path/'cache',preserve_existing_acl=True)


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
