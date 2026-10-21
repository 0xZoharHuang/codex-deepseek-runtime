"""Host permission checks and native-backend cleanup for disposable credentials."""
import json
import os
from pathlib import Path
import subprocess

from codex_deepseek.platforms import private_directory
from codex_deepseek.service import native_active

MARKER='NONFUNCTIONAL_TEST_CREDENTIALS_ONLY'


def prepare_home(home):
    private_directory(home)
    (home/'.credential-acceptance-fixture').write_text(MARKER,encoding='utf-8')


def private_file(path):
    if not path.is_file():return False
    if os.name!='nt':return path.stat().st_mode&0o777==0o600
    import win32api,win32con,win32security
    token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
    try:owner=win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
    finally:token.Close()
    acl=win32security.GetFileSecurity(str(path),win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
    if acl is None:return False
    allowed=set()
    for i in range(acl.GetAceCount()):
        kind,mask,sid=acl.GetAce(i)
        if kind[0]==win32security.ACCESS_ALLOWED_ACE_TYPE and mask:
            name=win32security.ConvertSidToStringSid(sid)
            if name not in {owner,'S-1-5-18'}:return False
            allowed.add(name)
    return owner in allowed


def clear_fixture_secret_key(home):
    """Use the compiled native keyring implementation, including canonical naming."""
    home=home.resolve()
    if (home/'.credential-acceptance-fixture').read_text(encoding='utf-8')!=MARKER:
        raise RuntimeError('Credential fixture marker does not match')
    if native_active(home):raise RuntimeError('A native credential owner is still running')
    probe=Path(os.environ['CODEX_DEEPSEEK_TEST_KEYRING_PROBE']).resolve()
    environment={k:v for k,v in os.environ.items() if not k.startswith('CODEX_') and k not in {'OPENROUTER_API_KEY','OPENAI_API_KEY'}}
    environment['CODEX_DEEPSEEK_KEYRING_FIXTURE_HOME']=str(home)
    result=subprocess.run([str(probe),'--ignored','--exact','native_fixture_secret_key','--nocapture'],
                          env=environment,capture_output=True,text=True,encoding='utf-8',timeout=45)
    (home/'native-keyring-cleanup.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
    lines=[line.removeprefix('KEYRING_FIXTURE_RESULT ') for line in result.stdout.splitlines() if line.startswith('KEYRING_FIXTURE_RESULT ')]
    if result.returncode or len(lines)!=1:raise RuntimeError('Native fixture keyring cleanup did not execute successfully')
    proof=json.loads(lines[0])
    if proof.get('present_after') is not False:raise RuntimeError('Native fixture keyring entry remains')
    return proof
