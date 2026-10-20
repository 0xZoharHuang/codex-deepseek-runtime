"""Actual native TUI, automatic shared daemon, and cold TUI resume via PTY."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import termios
import time
import uuid

from codex_deepseek.processes import identity,terminate,matches
from codex_deepseek.service import native_active


def session(entry,root,arguments,target,expected,label):
    pid,fd=pty.fork()
    if pid==0:
        environment={**os.environ,'TERM':'xterm-256color','CODEX_DEEPSEEK_HOME':str(root/'home')}
        os.execvpe(str(entry),[str(entry),*arguments],environment)
    birth=identity(pid);transcript=bytearray();exited=False
    existing={str(p):p.stat().st_size for p in (root/'home/sessions').rglob('*.jsonl')}
    fcntl.ioctl(fd,termios.TIOCSWINSZ,struct.pack('HHHH',30,120,0,0))
    try:
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if select.select([fd],[],[],.2)[0]:
                try:data=os.read(fd,65536)
                except OSError:break
                if not data:break
                transcript.extend(data)
                if b'\x1b[6n' in data:os.write(fd,b'\x1b[1;1R')
            complete=False
            for rollout in (root/'home/sessions').rglob('*.jsonl'):
                with rollout.open() as stream:
                    stream.seek(existing.get(str(rollout),0))
                    for line in stream:
                        try:event=json.loads(line)
                        except ValueError:continue
                        payload=event.get('payload',{})
                        if event.get('type')=='event_msg' and payload.get('type')=='task_complete' and payload.get('last_agent_message')=='TUI_VERIFIED':complete=True
            if target.exists() and target.read_text().strip()==expected and complete:break
        else:raise TimeoutError('Native TUI did not finish its actual file operation')
        assert target.exists() and target.read_text().strip()==expected,'TUI tool effect missing'
        assert b'TUI_VERIFIED' in transcript,'TUI final response missing'
        assert b'Running without the shared background server' not in transcript, 'TUI unexpectedly selected embedded mode'
        record=json.loads((root/'home/app-server-daemon/daemon.pid').read_text())
        native_birth=record['processIdentity']
        assert matches(record['pid'],str(native_birth['startSeconds'])+':'+str(native_birth['startMicroseconds'])),'Automatic shared daemon kernel identity is missing'
        assert native_active(root/'home'),'Automatic shared daemon is missing'
        os.write(fd,b'\x04')
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            child,status=os.waitpid(pid,os.WNOHANG)
            if child:
                exited=True;assert os.waitstatus_to_exitcode(status)==0;break
            if select.select([fd],[],[],.1)[0]:
                try:transcript.extend(os.read(fd,65536))
                except OSError:pass
        assert exited,'TUI did not exit normally'
        return {'name':label,'passed':True,'bytes':len(transcript),'shared_daemon':True,'daemon_pid':record['pid']}
    finally:
        (root/(label+'.pty')).write_bytes(transcript)
        if not exited:
            terminate(pid,birth)
            try:os.waitpid(pid,0)
            except ChildProcessError:pass
        os.close(fd)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True,mode=0o700);(root/'work').mkdir()
    (root/'home/config.toml').write_text('model_reasoning_effort="low"\napproval_policy="never"\nsandbox_mode="workspace-write"\n[tui]\nanimations=false\n[projects.'+json.dumps(str(root/'work'))+']\ntrust_level="trusted"\n')
    entry=args.entry.resolve();results=[]
    prompt='Do not delegate. Use native tools to write tui-first.txt containing exactly TUI_FIRST_5271 with no trailing newline, read it back, and reply TUI_VERIFIED.'
    try:
        results.append(session(entry,root,['--no-alt-screen','-C',str(root/'work'),prompt],root/'work/tui-first.txt','TUI_FIRST_5271','initial'))
        restart=subprocess.run([str(entry),'app-server','daemon','stop'],env={**os.environ,'CODEX_DEEPSEEK_HOME':str(root/'home')},capture_output=True,text=True,timeout=45)
        (root/'daemon-cold-restart.txt').write_text(restart.stdout+restart.stderr)
        assert restart.returncode==0,'Unable to stop the own daemon before cold restart'
        prompt='Continue the saved session. Read tui-first.txt and write tui-resumed.txt containing exactly its stripped content followed by _RESUMED with no trailing newline. Read it back and reply TUI_VERIFIED. Do not delegate.'
        results.append(session(entry,root,['--no-alt-screen','-C',str(root/'work'),'resume','--last',prompt],root/'work/tui-resumed.txt','TUI_FIRST_5271_RESUMED','cold-resume'))
    finally:
        stopped=subprocess.run([str(entry),'app-server','daemon','stop'],env={**os.environ,'CODEX_DEEPSEEK_HOME':str(root/'home')},capture_output=True,text=True,timeout=45)
        (root/'daemon-stop.txt').write_text(stopped.stdout+stopped.stderr)
        (root/'results.json').write_text(json.dumps(results,indent=2))
        print('EVIDENCE',root,flush=True)
    assert len(results)==2 and stopped.returncode==0 and results[0]['daemon_pid']!=results[1]['daemon_pid']
    print(json.dumps(results),flush=True)


if __name__=='__main__':main()
