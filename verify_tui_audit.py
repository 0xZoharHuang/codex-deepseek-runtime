"""Actual native TUI, automatic shared daemon, and cold TUI resume via PTY."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from codex_deepseek.processes import matches
from codex_deepseek.platforms import private_directory
from codex_deepseek.service import native_active
from credential_acceptance import private_file
from terminal_acceptance import terminal,write_qa_receipt


def plain_terminal(transcript):
    plain=re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]',b'',bytes(transcript))
    return re.sub(rb'\x1b\][^\x07]*?(?:\x07|\x1b\\)',b'',plain)


def rendered_reply(transcript,reply):
    # Ignore styling/cursor controls, then require the native assistant-cell
    # prefix. Merely seeing the marker inside the user's prompt is insufficient.
    plain=plain_terminal(transcript)
    return re.search('•'.encode()+rb'\s*'+re.escape(reply.encode())+rb'(?![A-Za-z0-9_])',plain) is not None


def session_log_complete(records):
    """Native log_session_end appends a marker without closing its logger."""
    if not records or any(not isinstance(row,dict) for row in records):return False
    starts=[index for index,row in enumerate(records) if row.get('kind')=='session_start']
    ends=[index for index,row in enumerate(records) if row.get('kind')=='session_end']
    if starts!=[0] or len(ends)!=1 or ends[0]<=0:return False
    # The fixed upstream logger remains enabled after log_session_end; its
    # asynchronous title result can race with shutdown. Admit only that observed
    # app event, never operations, fresh sessions or arbitrary tail records.
    return all(row.get('dir')=='to_tui' and row.get('kind')=='app_event'
               and row.get('variant')=='GeneratedThreadTitle'
               and set(row)<={'ts','dir','kind','variant'} for row in records[ends[0]+1:])


def expected_backend():
    if os.name!='nt':return 'shared'
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);api=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel.GetCurrentProcess.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    api.OpenProcessToken.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)];api.OpenProcessToken.restype=wintypes.BOOL
    api.GetTokenInformation.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)];api.GetTokenInformation.restype=wintypes.BOOL
    token=wintypes.HANDLE();elevated=wintypes.DWORD();size=wintypes.DWORD()
    if not api.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(token)):raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not api.GetTokenInformation(token,20,ctypes.byref(elevated),ctypes.sizeof(elevated),ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
        return 'embedded' if elevated.value else 'shared'
    finally:kernel.CloseHandle(token)


def daemon_birth(record):
    if os.name=='nt':return 'windows:'+record['processStartTime']
    detail=record['processIdentity']
    return ('linux:'+detail['bootId']+':'+str(detail['startTicks'])) if 'startTicks' in detail else str(detail['startSeconds'])+':'+str(detail['startMicroseconds'])


def daemon_record(home):
    record=json.loads((home/'app-server-daemon/daemon.pid').read_text(encoding='utf-8'))
    birth=daemon_birth(record)
    assert matches(record['pid'],birth),'Automatic shared daemon kernel identity is missing'
    assert native_active(home),'Automatic shared daemon is missing'
    return record


def stop_daemon(entry,root,label,record):
    stopped=subprocess.run([str(entry),'app-server','daemon','stop'],env={**os.environ,'CODEX_DEEPSEEK_HOME':str(root/'home')},capture_output=True,text=True,encoding='utf-8',timeout=45)
    (root/label).write_text(stopped.stdout+stopped.stderr,encoding='utf-8')
    assert stopped.returncode==0,'Unable to stop the own daemon before tracing its completed phase'
    deadline=time.monotonic()+10
    while matches(record['pid'],daemon_birth(record)) and time.monotonic()<deadline:time.sleep(.05)
    assert not matches(record['pid'],daemon_birth(record)),'Own native daemon did not exit after its stop command'
    assert not native_active(root/'home'),'Own native daemon remains active after its stop command'


def session(entry,root,arguments,target,expected,label,backend,reply,prior=None):
    environment={**os.environ,'TERM':'xterm-256color','CODEX_DEEPSEEK_HOME':str(root/'home'),
                     'CODEX_TUI_RECORD_SESSION':'1','CODEX_TUI_SESSION_LOG_PATH':str(root/(label+'-native-session.jsonl')),
                     'CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT':'1','CODEX_ROLLOUT_TRACE_ROOT':str(root/'native-rollout-traces')}
    existing={str(p):p.stat().st_size for p in (root/'home/sessions').rglob('*.jsonl')}
    trace_root=root/'native-rollout-traces'
    trace_before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in trace_root.rglob('*') if p.is_file()}
    child=terminal([str(entry),*arguments],env=environment,cwd=root/'work');transcript=bytearray();query_tail=b''
    def receive():
        nonlocal query_tail
        data=child.read(.2);transcript.extend(data)
        scan=query_tail+data
        if b'\x1b[6n' in scan:child.write(b'\x1b[1;1R')
        query_tail=scan[-3:]
    try:
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            receive()
            assert child.poll() is None,'Native TUI exited before completing the operation: '+str(child.poll())
            completed=set()
            for rollout in (root/'home/sessions').rglob('*.jsonl'):
                with rollout.open('rb') as stream:
                    stream.seek(existing.get(str(rollout),0))
                    for line in stream:
                        try:event=json.loads(line)
                        except ValueError:continue
                        payload=event.get('payload',{})
                        if event.get('type')=='event_msg' and payload.get('type')=='task_complete' and payload.get('last_agent_message')==reply:completed.add(rollout)
            if target.exists() and target.read_bytes()==expected.encode('utf-8') and completed and rendered_reply(transcript,reply):break
        else:raise TimeoutError('Native TUI did not finish its actual file operation')
        assert target.exists() and target.read_bytes()==expected.encode('utf-8'),'TUI tool effect missing or bytes differ'
        assert rendered_reply(transcript,reply),'TUI final response missing'
        assert len(completed)==1,'Expected one native TUI session completion'
        with next(iter(completed)).open(encoding='utf-8') as source:metadata=json.loads(source.readline())
        assert metadata['type']=='session_meta'
        thread_id=metadata['payload']['id']
        if prior:assert thread_id==prior['thread_id'],'Cold resume created a different native session'
        if backend=='shared':
            assert b'Running without the shared background server' not in transcript, 'TUI unexpectedly selected embedded mode'
            record=daemon_record(root/'home')
        else:
            # Upstream deliberately selects embedded mode in elevated Windows
            # terminals. This expectation is established from the OS token.
            warning='Running as administrator: shared background server disabled.'
            def visible_warning():return warning in ' '.join(plain_terminal(transcript).decode('utf-8',errors='replace').split())
            if not visible_warning():
                # Current native TUI collapses warnings into an F2 badge.
                # Open that real UI surface before checking the exact warning.
                child.write(b'\x1bOQ')
                deadline=time.monotonic()+5;pages=[]
                while time.monotonic()<deadline and not pages:
                    receive();pages=re.findall(rb'Warnings .*?(\d+) of (\d+)',plain_terminal(transcript))
                assert pages,'Native F2 warnings panel did not open'
                for _ in range(int(pages[-1][1])):
                    if visible_warning():break
                    child.write(b'\x1b[C')
                    deadline=time.monotonic()+1
                    while time.monotonic()<deadline and not visible_warning():receive()
                child.write(b'\x1b');receive()
            assert visible_warning(),'Native elevated Windows mode was not reported'
            assert not (root/'home/app-server-daemon/daemon.pid').exists(),'Unexpected elevated shared daemon'
            record={}
        child.write(b'\x04')
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            receive()
            code=child.poll()
            if code is not None:assert code==0;break
        else:raise AssertionError('TUI did not exit normally')
        if backend=='shared':
            # The native trace writer belongs to the daemon. TUI exit alone
            # does not flush its final records; bind the receipt after the
            # identified daemon has exited, including its final trace files.
            stop_daemon(entry,root,'daemon-cold-restart.txt' if label=='initial' else 'daemon-stop.txt',record)
        native_log=root/(label+'-native-session.jsonl')
        records=[json.loads(line) for line in native_log.read_text(encoding='utf-8').splitlines()]
        assert session_log_complete(records) and records[0]['model_provider_id']=='deepseek_runtime'
        assert private_file(native_log),'Native session recording permissions are not private'
        traces=[p for p in trace_root.rglob('*') if p.is_file()]
        changed_traces=[p for p in traces if p.stat().st_size and trace_before.get(str(p))!=hashlib.sha256(p.read_bytes()).hexdigest()]
        assert changed_traces,'Native rollout tracing produced no evidence for this TUI phase'
        return {'name':label,'passed':True,'bytes':len(transcript),'terminal':'conpty' if os.name=='nt' else 'pty',
                'backend':backend,'shared_daemon':backend=='shared','daemon_pid':record.get('pid'),'tui_pid':child.pid,'thread_id':thread_id,'rendered_reply':reply,
                'native_session_log_records':len(records),'native_rollout_trace_files':len(traces),'native_rollout_trace_files_changed':len(changed_traces)}
    finally:
        (root/(label+'.pty')).write_bytes(transcript)
        child.close()


def main():
    if not __debug__:raise SystemExit('Acceptance must run with Python assertions enabled')
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sandbox-mode',choices=['workspace-write','danger-full-access'],default='workspace-write');args=parser.parse_args()
    root=args.output.resolve()/(uuid.uuid4().hex[:12] if os.name=='nt' else str(uuid.uuid4()));private_directory(root);(root/'home').mkdir();(root/'work').mkdir()
    (root/'home/config.toml').write_text('model_reasoning_effort="low"\napproval_policy="never"\nsandbox_mode='+json.dumps(args.sandbox_mode)+'\n[tui]\nanimations=false\n[projects.'+json.dumps(str(root/'work'))+']\ntrust_level="trusted"\n',encoding='utf-8')
    entry=args.entry.resolve();results=[];backend=expected_backend()
    write_qa_receipt(root,entry,['verify_tui_audit.py','terminal_acceptance.py','credential_acceptance.py'])
    prompt='Do not delegate. Use native tools to write tui-first.txt containing exactly TUI_FIRST_5271 with no trailing newline and read it back. Your final assistant message must be exactly TUI_VERIFIED_INITIAL_5271 with no other text, explanation, formatting or punctuation.'
    try:
        results.append(session(entry,root,['--no-alt-screen','-C',str(root/'work'),prompt],root/'work/tui-first.txt','TUI_FIRST_5271','initial',backend,'TUI_VERIFIED_INITIAL_5271'))
        if backend!='shared':
            assert not native_active(root/'home'),'Embedded native TUI survived its clean terminal exit'
        prompt='Continue the saved session. Read tui-first.txt and write tui-resumed.txt containing exactly its stripped content followed by _RESUMED with no trailing newline. Read it back. Your final assistant message must be exactly TUI_VERIFIED_RESUMED_5271 with no other text, explanation, formatting or punctuation. Do not delegate.'
        results.append(session(entry,root,['--no-alt-screen','-C',str(root/'work'),'resume','--last',prompt],root/'work/tui-resumed.txt','TUI_FIRST_5271_RESUMED','cold-resume',backend,'TUI_VERIFIED_RESUMED_5271',prior=results[0]))
    finally:
        stopped=None
        if backend=='shared' and native_active(root/'home'):
            stopped=subprocess.run([str(entry),'app-server','daemon','stop'],env={**os.environ,'CODEX_DEEPSEEK_HOME':str(root/'home')},capture_output=True,text=True,encoding='utf-8',timeout=45)
            (root/'daemon-stop.txt').write_text(stopped.stdout+stopped.stderr,encoding='utf-8')
        (root/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
        print('EVIDENCE',root,flush=True)
        if stopped is not None:assert stopped.returncode==0,'Unable to stop the own daemon after verification'
    assert len(results)==2 and results[0]['tui_pid']!=results[1]['tui_pid']
    if backend=='shared':assert results[0]['daemon_pid']!=results[1]['daemon_pid']
    print(json.dumps(results),flush=True)


if __name__=='__main__':main()
