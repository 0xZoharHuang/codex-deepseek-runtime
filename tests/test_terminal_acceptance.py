"""Prove the acceptance transport really supplies a terminal, including input."""
import json
import os
from pathlib import Path
import re
import sys
import time

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
from terminal_acceptance import terminal


def test_native_session_end_allows_only_known_asynchronous_title_tail():
    from verify_tui_audit import session_log_complete
    start={'kind':'session_start'};end={'kind':'session_end'}
    title={'ts':'2026-10-21T11:18:47.712Z','dir':'to_tui','kind':'app_event','variant':'GeneratedThreadTitle'}
    assert session_log_complete([start,end])
    assert session_log_complete([start,{'kind':'app_event','variant':'Exit'},end,title])
    for rows in [[],[start],[end,start],[start,start,end],[start,end,end],
                 [start,end,{'kind':'op','dir':'from_tui','payload':'CleanBackgroundTerminals'}],
                 [start,end,{**title,'variant':'Exit'}],[start,end,{**title,'dir':'from_tui'}],
                 [start,end,{**title,'payload':'unexpected'}],[start,end,None]]:
        assert not session_log_complete(rows),rows


def test_tui_reply_assertion_rejects_prompt_and_replayed_history():
    from verify_tui_audit import rendered_reply
    marker='TUI_VERIFIED_RESUMED_5271'
    assert not rendered_reply(('› Read the file and reply '+marker).encode(),marker)
    assert not rendered_reply('• TUI_VERIFIED_INITIAL_5271'.encode(),marker)
    assert not rendered_reply(('• '+marker+'_OTHER').encode(),marker)
    assert rendered_reply(b'\x1b[2m'+('• ').encode()+b'\x1b[22m'+marker.encode()+b'\x1b[0m',marker)


def test_interrupt_requires_yielded_native_session_bound_to_fixture_call(tmp_path):
    from verify_tui_interrupt import yielded_background_session
    path=tmp_path/'rollout.jsonl'
    call={'type':'custom_tool_call','call_id':'owned','input':'exec_command interrupt_fixture.py'}
    def check(output,call_id='owned',source=call):
        rows=[{'type':'response_item','payload':source},{'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':call_id,'output':output}}]
        path.write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
        return yielded_background_session(path,'interrupt_fixture.py')
    assert not check('aborted by user after 1.0s')
    assert not check('{"session_id":null}')
    assert not check('{"session_id":123}',call_id='unrelated')
    assert not check('{"session_id":123}',source={**call,'input':'exec_command other.py'})
    assert check(json.dumps([{'type':'text','text':json.dumps({'session_id':123,'output':'ALIVE'})}]))=={'call_id':'owned','session_id':'123'}
    path.write_text(json.dumps({'type':'event_msg','payload':{'type':'turn_aborted'}})+'\n'+path.read_text(encoding='utf-8'),encoding='utf-8')
    assert not yielded_background_session(path,'interrupt_fixture.py')


def collect_until(child,predicate,timeout=12):
    transcript=bytearray();deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        transcript.extend(child.read(.1))
        if predicate(transcript):return bytes(transcript)
    raise AssertionError('Terminal evidence missing: '+repr(bytes(transcript)))


def test_acceptance_terminal_is_real_tty_and_preserves_utf8_input(tmp_path):
    program='''import json,os,sys
print('READY',flush=True)
value=input()
print('RESULT '+json.dumps({'stdin_tty':sys.stdin.isatty(),'stdout_tty':sys.stdout.isatty(),
    'size':list(os.get_terminal_size(1)),'input':value},ensure_ascii=False,separators=(',',':')),flush=True)
raise SystemExit(17)
'''
    child=terminal([sys.executable,'-I','-X','utf8','-c',program],env=dict(os.environ),cwd=tmp_path,columns=100,rows=28)
    try:
        collect_until(child,lambda value:b'READY' in value)
        child.write('终端 & % ! input\r\n'.encode())
        transcript=collect_until(child,lambda value:b'RESULT ' in value and b'}' in value)
        match=re.search(rb'RESULT (\{[^\r\n]*\})',transcript);assert match,transcript
        assert json.loads(match[1])=={'stdin_tty':True,'stdout_tty':True,'size':[100,28],'input':'终端 & % ! input'}
        end=time.monotonic()+10
        while child.poll() is None and time.monotonic()<end:child.read(.1)
        assert child.poll()==17
    finally:child.close()


def test_terminal_ctrl_c_is_confined_to_its_owned_console(tmp_path):
    program='''import os,signal,time
if os.name=='nt':
 import ctypes
 # The disposable fixture opts into control events even if an SSH ancestor
 # used CREATE_NEW_PROCESS_GROUP (whose ignore-Ctrl-C flag is inherited).
 ctypes.windll.kernel32.SetConsoleCtrlHandler(None,False)
def interrupted(*args):
 print('OWNED_INTERRUPT',flush=True)
 raise SystemExit(7)
signal.signal(signal.SIGINT,interrupted)
print('WAITING_FOR_CTRL_C',flush=True)
while True:time.sleep(.1)
'''
    child=terminal([sys.executable,'-I','-X','utf8','-c',program],env=dict(os.environ),cwd=tmp_path)
    try:
        collect_until(child,lambda value:b'WAITING_FOR_CTRL_C' in value)
        child.write(b'\x03')
        collect_until(child,lambda value:b'OWNED_INTERRUPT' in value)
        end=time.monotonic()+10
        while child.poll() is None and time.monotonic()<end:child.read(.1)
        assert child.poll()==7
    finally:child.close()


def test_terminal_failed_run_cleanup_reaps_its_owned_process(tmp_path):
    from codex_deepseek.processes import identity,matches
    program='''import os,signal,time
if os.name!='nt':
 signal.signal(signal.SIGTERM,signal.SIG_IGN)
 signal.signal(signal.SIGHUP,signal.SIG_IGN)
print('WAITING_FOR_CLEANUP',flush=True)
while True:time.sleep(.1)
'''
    child=terminal([sys.executable,'-I','-X','utf8','-c',program],env=dict(os.environ),cwd=tmp_path)
    try:
        collect_until(child,lambda value:b'WAITING_FOR_CLEANUP' in value)
        birth=identity(child.pid);assert birth and matches(child.pid,birth)
    finally:child.close()
    assert not matches(child.pid,birth),'Failed fixture survived terminal cleanup'
