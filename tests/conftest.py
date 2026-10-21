"""Native networking tests use an explicit built/installed transport executable."""
import os
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def native_http_test_binary(monkeypatch):
    value=os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY')
    if value:
        path=Path(value).resolve()
        assert path.is_file(),'The explicitly selected native HTTP test binary is missing'
        monkeypatch.setattr('codex_deepseek.native_http.native_helper',lambda:path)


@pytest.fixture
def assert_private():
    def check(path):
        if os.name!='nt':
            assert path.stat().st_mode&0o777==(0o700 if path.is_dir() else 0o600)
            return
        import win32api,win32security,win32con
        token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
        try:sid=win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
        finally:token.Close()
        descriptor=win32security.GetFileSecurity(str(path),win32security.DACL_SECURITY_INFORMATION)
        acl=descriptor.GetSecurityDescriptorDacl();assert acl is not None
        entries=[acl.GetAce(i) for i in range(acl.GetAceCount())]
        assert all(entry[0][0]==win32security.ACCESS_ALLOWED_ACE_TYPE and entry[1]&0x1f01ff==0x1f01ff for entry in entries)
        assert len(entries)==2
        assert {win32security.ConvertSidToStringSid(entry[2]) for entry in entries}=={sid,'S-1-5-18'}
    return check
