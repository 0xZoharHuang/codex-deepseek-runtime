"""Read only the dedicated provider credential through OS stores or process env."""
import ctypes
import getpass
import os
import shutil
import subprocess
import sys

SERVICE='ai.codex.deepseek.openrouter'


def _windows_api():
    from ctypes import wintypes
    class CREDENTIAL(ctypes.Structure):
        _fields_=[('Flags',wintypes.DWORD),('Type',wintypes.DWORD),('TargetName',wintypes.LPWSTR),
                  ('Comment',wintypes.LPWSTR),('LastWritten',wintypes.FILETIME),('CredentialBlobSize',wintypes.DWORD),
                  ('CredentialBlob',ctypes.POINTER(ctypes.c_ubyte)),('Persist',wintypes.DWORD),
                  ('AttributeCount',wintypes.DWORD),('Attributes',ctypes.c_void_p),('TargetAlias',wintypes.LPWSTR),('UserName',wintypes.LPWSTR)]
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    api.CredReadW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(CREDENTIAL))];api.CredReadW.restype=wintypes.BOOL
    api.CredWriteW.argtypes=[ctypes.POINTER(CREDENTIAL),wintypes.DWORD];api.CredWriteW.restype=wintypes.BOOL
    api.CredFree.argtypes=[ctypes.c_void_p];api.CredFree.restype=None
    return api,CREDENTIAL


def credential():
    override=os.environ.get('OPENROUTER_API_KEY','').strip()
    if override:return override
    if sys.platform=='win32':
        api,kind=_windows_api();pointer=ctypes.POINTER(kind)()
        if api.CredReadW(SERVICE,1,0,ctypes.byref(pointer)):
            try:return ctypes.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode('utf-8').strip()
            finally:api.CredFree(pointer)
        error=ctypes.get_last_error()
        if error not in {1168,1312}:raise ctypes.WinError(error)
    else:
        command=['security','find-generic-password','-a','default','-s',SERVICE,'-w'] if sys.platform=='darwin' else ['secret-tool','lookup','service',SERVICE,'account','default']
        if shutil.which(command[0]):
            result=subprocess.run(command,capture_output=True,text=True,timeout=30)
            if result.returncode==0 and result.stdout.strip():return result.stdout.strip()
    raise RuntimeError('No OpenRouter credential. Run codex-deepseek runtime setup-key with an OS credential store, or set OPENROUTER_API_KEY in the launching process.')


def setup_key():
    if sys.platform=='darwin':
        # Native hidden prompt; the secret never enters command arguments.
        return subprocess.call(['security','add-generic-password','-U','-a','default','-s',SERVICE,'-w'])
    if sys.platform=='win32':
        secret=getpass.getpass('OpenRouter API key (Windows Credential Manager): ').strip()
        if not secret:raise RuntimeError('Empty credential was not stored')
        raw=secret.encode('utf-8');buffer=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
        api,kind=_windows_api();record=kind(Type=1,TargetName=SERVICE,CredentialBlobSize=len(raw),CredentialBlob=buffer,Persist=2,UserName='default')
        if not api.CredWriteW(ctypes.byref(record),0):
            error=ctypes.get_last_error()
            if error==1312:raise RuntimeError('Windows Credential Manager requires an interactive Windows sign-in session. For SSH/CI, supply OPENROUTER_API_KEY through the secure process environment.')
            raise ctypes.WinError(error)
        return 0
    if not shutil.which('secret-tool'):
        raise RuntimeError('Linux setup-key requires libsecret secret-tool and an unlocked Secret Service session. In headless sessions, supply OPENROUTER_API_KEY through the process environment.')
    secret=getpass.getpass('OpenRouter API key (Secret Service): ').strip()
    if not secret:raise RuntimeError('Empty credential was not stored')
    return subprocess.run(['secret-tool','store','--label=Codex DeepSeek OpenRouter','service',SERVICE,'account','default'],input=secret,text=True,timeout=60).returncode
