"""Read-only exact Windows stdout FILE_OBJECT tracing, diagnostic only."""
import os,sys,ctypes,subprocess
from ctypes import wintypes as w

class Entry(ctypes.Structure):
    _fields_=[("object",ctypes.c_void_p),("pid",ctypes.c_size_t),("handle",ctypes.c_size_t),
              ("access",w.ULONG),("backtrace",w.USHORT),("type",w.USHORT),
              ("attributes",w.ULONG),("reserved",w.ULONG)]

def snapshot():
    api=ctypes.WinDLL("ntdll").NtQuerySystemInformation
    api.argtypes=[w.ULONG,w.LPVOID,w.ULONG,ctypes.POINTER(w.ULONG)]
    api.restype=ctypes.c_long
    size=1024*1024
    while size<=64*1024*1024:
        buffer=ctypes.create_string_buffer(size);needed=w.ULONG()
        status=api(64,buffer,size,ctypes.byref(needed))
        if status==0:
            count=ctypes.c_size_t.from_buffer(buffer).value
            offset=2*ctypes.sizeof(ctypes.c_size_t)
            if offset+count*ctypes.sizeof(Entry)>size:raise RuntimeError("Invalid handle snapshot")
            return buffer,count,offset
        if status!=-1073741820:raise OSError("NtQuerySystemInformation failed",status)
        size=max(size*2,needed.value+65536)
    raise RuntimeError("Handle snapshot exceeded diagnostic memory bound")

def entries():
    buffer,count,offset=snapshot()
    for i in range(count):
        yield Entry.from_buffer_copy(buffer,offset+i*ctypes.sizeof(Entry))

class TracedPopen(subprocess.Popen):
    def _get_handles(self,stdin,stdout,stderr):
        values=super()._get_handles(stdin,stdout,stderr)
        self.stdout_writer_object=None
        if sys.platform=="win32" and stdout==subprocess.PIPE:
            writer=int(values[3])
            for row in entries():
                if row.pid==os.getpid() and row.handle==writer:
                    self.stdout_writer_object=row.object
                    break
            if not self.stdout_writer_object:raise RuntimeError("Child stdout writer was not observed")
        return values

def writer_holders(peer,family):
    import hashlib
    from codex_deepseek.processes import identity,matches
    target=peer.stdout_writer_object
    if not target:return {"status":"no_writer_object_captured"}
    known={row["pid"]:row["birth"] for row in family}
    rows=[]
    for row in entries():
        if row.object!=target:continue
        birth=identity(row.pid)
        rows.append({"pid":row.pid,"birth":birth,"handle":hex(row.handle),
                     "granted_access":hex(row.access),"known_family":row.pid in known,
                     "matches_observed_birth":row.pid in known and birth is not None
                        and known[row.pid]==birth and matches(row.pid,birth)})
    return {"status":"captured_exact_original_stdout_writer_object",
            "object_fingerprint":hashlib.sha256(str(target).encode()).hexdigest(),
            "holders":rows,"read_only":True,"handles_closed":0}
