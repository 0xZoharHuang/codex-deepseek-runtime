"""Crash the native process itself and verify actual descendant cleanup."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
import argparse

from verify_live import Peer
from codex_deepseek.processes import identity,matches,terminate


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--baseline',action='store_true');args=parser.parse_args()
    source=Path(__file__).resolve().parent
    root=source.parent/'delivery/independent-audit/native-crash'/str(uuid.uuid4())
    (root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('mcp_oauth_credentials_store="file"\ncli_auth_credentials_store="file"\n')
    os.environ['PYTHONPATH']=str(source/'src')
    if args.baseline:
        from codex_deepseek.cli import native
        command=[str(native())]
    else:command=[sys.executable,'-m','codex_deepseek.cli']
    peer=Peer(root,command=command,environment={'CODEX_HOME':str(root/'home')});owned=[];result={}
    try:
        pidfile=root/'work/pids.jsonl'
        body=f'import json,os,pathlib,subprocess,time; target=pathlib.Path({str(pidfile)!r});target.write_text(json.dumps([os.getpid()]));child=subprocess.Popen(["sleep","120"]);target.write_text(json.dumps([os.getpid(),child.pid]));child.wait()'
        peer.send({'id':8001,'method':'command/exec','params':{'command':[sys.executable,'-c',body],'cwd':str(root/'work'),'processId':'raw-native-crash','sandboxPolicy':{'type':'workspaceWrite','writableRoots':[str(root/'work')]},'timeoutMs':130000}})
        end=time.monotonic()+10
        while time.monotonic()<end:
            if pidfile.exists():
                try:
                    if len(json.loads(pidfile.read_text()))==2:break
                except ValueError:pass
            time.sleep(.025)
        assert pidfile.exists(),'Native command never launched'
        owned=[(pid,identity(pid)) for pid in json.loads(pidfile.read_text())]
        assert all(birth for _,birth in owned),'Descendant identities could not be recorded'
        if args.baseline:native,birth=peer.process.pid,identity(peer.process.pid)
        else:
            with sqlite3.connect(root/'home/provider-state/adapter.sqlite') as db:
                native,birth=db.execute('SELECT native_pid,native_birth FROM runtime_owners WHERE native_pid IS NOT NULL AND closed IS NULL').fetchone()
        assert matches(native,birth)
        os.kill(native,signal.SIGKILL);peer.process.wait(timeout=10)
        end=time.monotonic()+8
        while any(matches(pid,birth) for pid,birth in owned) and time.monotonic()<end:time.sleep(.05)
        remaining=[pid for pid,birth in owned if matches(pid,birth)]
        result={'passed':not remaining,'baseline':args.baseline,'killed_native_pid':native,'owned_descendants':[pid for pid,_ in owned],'remaining_descendants':remaining,'wrapper_exit':peer.process.returncode}
        (root/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
        if remaining:raise AssertionError('Native SIGKILL left live owned descendants')
    finally:
        peer.close()
        for pid,birth in owned:terminate(pid,birth,timeout=1)
        print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
