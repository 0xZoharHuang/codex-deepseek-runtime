"""Actual installed CLI + native terminal Ctrl-C, continued turn and /stop."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from verify_live import prepare_workspace,workspace_path
import subprocess
import sys
import time
import uuid

import psutil

from codex_deepseek.platforms import private_directory
from codex_deepseek.processes import identity, matches, terminate
from credential_acceptance import private_file
from process_acceptance import shell_command,fixture_identity
from terminal_acceptance import terminal,write_qa_receipt
from verify_tui_audit import expected_backend, plain_terminal, rendered_reply,session_log_complete


def events(home):
    found=[]
    for path in (home/'sessions').rglob('*.jsonl'):
        with path.open('rb') as stream:
            for line in stream:
                try:row=json.loads(line)
                except ValueError:continue
                if row.get('type')=='event_msg':found.append((path,row['payload']))
    return found


def yielded_background_session(rollout,fixture_name):
    """Require a real exec result before asserting background retention."""
    calls=set()
    def session_id(value):
        if isinstance(value,dict):
            value_id=value.get('session_id')
            if type(value_id) is int and value_id>0:return str(value_id)
            for child in value.values():
                found=session_id(child)
                if found:return found
        elif isinstance(value,list):
            for child in value:
                found=session_id(child)
                if found:return found
        elif isinstance(value,str):
            try:decoded=json.loads(value)
            except ValueError:decoded=None
            if isinstance(decoded,(dict,list)):return session_id(decoded)
            match=re.search(r'"session_id"\s*:\s*([1-9][0-9]*)',value)
            if match:return match[1]
        return None
    for line in Path(rollout).read_text(encoding='utf-8').splitlines():
        try:row=json.loads(line)
        except ValueError:continue
        if row.get('type')=='event_msg' and row.get('payload',{}).get('type')=='turn_aborted':break
        if row.get('type')!='response_item':continue
        item=row.get('payload',{});kind=item.get('type');call_id=item.get('call_id')
        if kind in {'function_call','custom_tool_call'} and isinstance(call_id,str) and call_id and fixture_name in str(item.get('input',item.get('arguments',''))):calls.add(call_id)
        if kind in {'function_call_output','custom_tool_call_output'} and call_id in calls:
            value=session_id(item.get('output'))
            if value:return {'call_id':call_id,'session_id':value}
    return None


def run(entry,output,sandbox_mode):
    root=output.resolve()/(uuid.uuid4().hex[:12] if os.name=='nt' else str(uuid.uuid4()));private_directory(root)
    qa_snapshot=write_qa_receipt(root,entry,['verify_tui_interrupt.py','verify_tui_audit.py','terminal_acceptance.py','credential_acceptance.py','process_acceptance.py','verify_live.py','windows_acceptance_setup.py','qa_workspace.py'])
    home=root/'home';home.mkdir();work=root/'work';work.mkdir();work=prepare_workspace(root)
    (home/'config.toml').write_text('model_reasoning_effort="low"\napproval_policy="never"\nsandbox_mode='+json.dumps(sandbox_mode)+'\n[tui]\nanimations=false\n[projects.'+json.dumps(str(work))+']\ntrust_level="trusted"\n',encoding='utf-8')
    from windows_acceptance_setup import prepare_terminal_fixture
    prepare_terminal_fixture(root,entry)
    ready=work/'fixture-ready.json';fixture=work/'interrupt_fixture.py';nonce=uuid.uuid4().hex
    program=('import json,os,sys,time\nfrom pathlib import Path\n'
             'ready=Path('+repr(str(ready))+')\ntemporary=ready.with_suffix(".tmp")\n'
             'marker={"pid":os.getpid(),"nonce":'+repr(nonce)+'}\n'
             'if sys.platform=="linux":\n stat=Path("/proc/self/stat").read_text();marker["start_ticks"]=stat[stat.rfind(")")+2:].split()[19];marker["boot_id"]=Path("/proc/sys/kernel/random/boot_id").read_text().strip()\n'
             'temporary.write_text(json.dumps(marker),encoding="utf-8")\ntemporary.replace(ready)\n'
             'while True:\n print("NATIVE_INTERRUPT_FIXTURE_ALIVE",flush=True)\n time.sleep(.25)\n')
    fixture.write_text(program,encoding='utf-8');fixture_hash=hashlib.sha256(fixture.read_bytes()).hexdigest()
    command=shell_command([sys.executable,'-I','-u',str(fixture)])
    prompt=('Do not delegate. Run exactly this command with the native exec tool and yield_time_ms=1000. '
            'Print the full returned tool result including session_id before making another tool call; '
            'if using Code Mode, end that first code execution after printing this result. '
            'Then keep waiting for that command in separate tool calls. It intentionally runs until I interrupt you. '
            'Do not modify the fixture, create other processes, or finish the turn before it stops. Command: '+command)
    environment={**os.environ,'TERM':'xterm-256color','CODEX_DEEPSEEK_HOME':str(home),
                 'CODEX_TUI_RECORD_SESSION':'1','CODEX_TUI_SESSION_LOG_PATH':str(root/'native-session.jsonl'),
                 'CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT':'1','CODEX_ROLLOUT_TRACE_ROOT':str(root/'native-rollout-traces')}
    child=terminal([str(entry),'--no-alt-screen','-C',str(work),prompt],env=environment,cwd=work)
    transcript=bytearray();tail=b'';fixture_pid=None;fixture_birth=None
    proof={'passed':False,'entry':str(entry),'sandbox_mode':sandbox_mode,'backend':expected_backend(),'terminal':'conpty' if os.name=='nt' else 'pty','qa_snapshot_sha256':qa_snapshot}
    def receive():
        nonlocal tail
        data=child.read(.1);transcript.extend(data);scan=tail+data
        if b'\x1b[6n' in scan:child.write(b'\x1b[1;1R')
        tail=scan[-3:]
    def until(predicate,seconds,message):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            receive()
            assert child.poll() is None,'TUI exited unexpectedly: '+str(child.poll())
            value=predicate()
            if value:return value
        raise AssertionError(message)
    try:
        until(lambda:ready.exists(),180,'Native fixture process did not start')
        marker=json.loads(ready.read_text(encoding='utf-8'));assert marker['nonce']==nonce
        fixture_pid,fixture_birth=fixture_identity(marker,fixture)
        assert fixture_birth and matches(fixture_pid,fixture_birth),'Fixture kernel identity is missing'
        assert str(fixture) in psutil.Process(fixture_pid).cmdline(),'Readiness was not written by the expected real process'
        started=[(path,row) for path,row in events(home) if row['type']=='task_started'];assert len(started)==1
        rollout,first=started[0];first_turn=first['turn_id']
        assert not any(row['type']=='task_complete' and row.get('turn_id')==first_turn for _,row in events(home))
        with rollout.open(encoding='utf-8') as stream:thread_id=json.loads(stream.readline())['payload']['id']
        proof.update(thread_id=thread_id,interrupted_turn_id=first_turn,fixture_pid=fixture_pid,fixture_namespace_pid=marker['pid'],fixture_birth=fixture_birth)
        proof['background_session']=until(lambda:yielded_background_session(rollout,fixture.name),60,'Native exec did not yield an actual background session before interruption')
        assert matches(fixture_pid,fixture_birth),'Yielded native fixture already exited before Ctrl-C'
        sent=time.monotonic();interrupt_offset=len(transcript);child.write(b'\x03')
        if proof['backend']=='shared':
            # The native shared-daemon TUI offers cancel/background/exit;
            # the embedded Windows path interrupts directly. Select its actual
            # default Cancel task action, preserving the upstream interaction.
            until(lambda:all(value in plain_terminal(transcript[interrupt_offset:]) for value in [b'Task is still running',b'Cancel task',b'Run in background']),10,'Native task-exit chooser did not appear')
            child.write(b'\r');proof['native_interrupt_dialog_choice']='Cancel task'
        else:proof['native_interrupt_dialog_choice']=None
        aborted=until(lambda:next((row for path,row in events(home) if path==rollout and row['type']=='turn_aborted' and row.get('turn_id')==first_turn),None),20,'Ctrl-C did not produce a native turn_aborted event')
        assert aborted['reason']=='interrupted',aborted
        proof['interruption_seconds']=round(time.monotonic()-sent,3)
        # The daemon rollout event can precede the TUI's input restoration.
        # Wait for its actual native interruption cell before entering a new
        # prompt, or that pending UI transition can erase freshly pasted text.
        until(lambda:b'Conversation interrupted' in plain_terminal(transcript[interrupt_offset:]),10,'Native TUI did not render its interruption acknowledgement')
        # Native Op::Interrupt deliberately preserves background terminals.
        assert matches(fixture_pid,fixture_birth),'Ctrl-C changed native background-terminal retention semantics'
        proof['background_retained_after_interrupt']=True
        reply='TUI_INTERRUPT_RECOVERED_2719';expected=b'AFTER_CTRL_C_2719';target=work/'after-interrupt.txt'
        followup='Do not delegate. Continue now by using native tools to write after-interrupt.txt containing exactly AFTER_CTRL_C_2719 with no trailing newline. Read it back and reply only '+reply+'. Leave the prior background terminal alone; I will stop it separately.'
        child.write(b'\x1b[200~'+followup.encode()+b'\x1b[201~')
        # Wait until the native composer has consumed the paste before Enter;
        # sending both together can be treated as a pasted newline on Windows.
        until(lambda:reply.encode() in plain_terminal(transcript),10,'TUI composer did not accept the follow-up input')
        # Native paste-burst handling suppresses Enter for 120 ms. Pace this
        # like a user submitting an already rendered paste, across both PTYs.
        paste_settled=time.monotonic()+.25
        while time.monotonic()<paste_settled:receive()
        child.write(b'\r')
        def recovered():
            complete=next((row for path,row in events(home) if path==rollout and row['type']=='task_complete' and row.get('turn_id')!=first_turn and row.get('last_agent_message')==reply),None)
            return complete if complete and target.exists() and target.read_bytes()==expected and rendered_reply(transcript,reply) else None
        complete=until(recovered,180,'The same TUI did not complete its next native tool turn')
        proof['continued_turn_id']=complete['turn_id'];proof['continued_file_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
        assert matches(fixture_pid,fixture_birth),'The retained fixture exited before native /stop'
        command_offset=len(transcript)
        for value in b'/stop':
            child.write(bytes([value]));deadline=time.monotonic()+.05
            while time.monotonic()<deadline:receive()
        until(lambda:b'stop' in plain_terminal(transcript[command_offset:]),5,'Native composer did not accept /stop')
        child.write(b'\r')
        until(lambda:not matches(fixture_pid,fixture_birth),20,'Native /stop did not terminate the owned background process')
        proof['native_stop_terminated_fixture']=True
        assert hashlib.sha256(fixture.read_bytes()).hexdigest()==fixture_hash,'Model modified the acceptance fixture'
        child.write(b'\x04');deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            receive()
            if child.poll() is not None:break
        assert child.poll()==0,'TUI did not exit normally after interrupt and recovery'
        log=root/'native-session.jsonl';records=[json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
        assert session_log_complete(records) and private_file(log)
        assert any(row.get('dir')=='from_tui' and row.get('kind')=='op' and row.get('payload')=='CleanBackgroundTerminals' for row in records),'No native terminal-cleanup operation was recorded'
        proof.update(passed=True,terminal_exit_code=child.poll(),native_session_log_records=len(records),transcript_bytes=len(transcript))
    except BaseException as error:
        proof['failure_reason']=repr(error)
        raise
    finally:
        # Preserve the failure trace even if terminal or daemon cleanup itself
        # fails. A cleanup failure must remain a failed qualification receipt.
        (root/'transcript.pty').write_bytes(transcript)
        (root/'results.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
        if fixture_pid and fixture_birth and matches(fixture_pid,fixture_birth):
            # Only failed runs reach this fallback; it cannot satisfy /stop.
            proof['failed_run_cleanup_required']=True;terminate(fixture_pid,fixture_birth)
        try:
            child.close()
            if proof['backend']=='shared':
                stopped=subprocess.run([str(entry),'app-server','daemon','stop'],env={**os.environ,'CODEX_DEEPSEEK_HOME':str(home)},capture_output=True,text=True,encoding='utf-8',timeout=45)
                (root/'daemon-stop.txt').write_text(stopped.stdout+stopped.stderr,encoding='utf-8')
                if stopped.returncode:proof['passed']=False
        except Exception as error:
            proof.update(passed=False,cleanup_error=str(error))
        (root/'transcript.pty').write_bytes(transcript)
        (root/'results.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
        print('EVIDENCE',root,flush=True)
    assert proof['passed']
    print(json.dumps(proof),flush=True)


if __name__=='__main__':
    if not __debug__:raise SystemExit('Acceptance must run with Python assertions enabled')
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sandbox-mode',choices=['workspace-write','danger-full-access'],default='workspace-write');args=parser.parse_args()
    run(args.entry.resolve(),args.output,args.sandbox_mode)
