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
    call={'type':'function_call','name':'exec_command','call_id':'owned',
          'arguments':json.dumps({'cmd':'python interrupt_fixture.py'})}
    def check(output,call_id='owned',source=call):
        rows=[{'type':'response_item','payload':source},{'type':'response_item','payload':{'type':'function_call_output','call_id':call_id,'output':output}}]
        path.write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
        return yielded_background_session(path,'interrupt_fixture.py')
    assert not check('aborted by user after 1.0s')
    assert not check('{"session_id":null}')
    assert not check('{"session_id":123}',call_id='unrelated')
    assert not check('{"session_id":123}',source={**call,'arguments':json.dumps({'cmd':'python other.py'})})
    result=check(json.dumps([{'type':'input_text','text':json.dumps({'session_id':123,'output':'ALIVE'})}]))
    assert result['call_id']=='owned' and result['session_id']=='123'
    path.write_text(json.dumps({'type':'event_msg','payload':{'type':'turn_aborted'}})+'\n'+path.read_text(encoding='utf-8'),encoding='utf-8')
    assert not yielded_background_session(path,'interrupt_fixture.py')


def _interrupt_async_rows(tmp_path):
    cwd=tmp_path.resolve();fixture=cwd/'interrupt_fixture.py';thread='fixture-thread';turn='fixture-turn'
    code='const r = await tools.exec_command('+json.dumps({'cmd':'python '+str(fixture),'workdir':str(cwd),'yield_time_ms':1000})+'); text(JSON.stringify(r));'
    rows=[
        {'type':'session_meta','payload':{'id':thread,'cwd':str(cwd)}},
        {'type':'event_msg','payload':{'type':'task_started','turn_id':turn}},
        {'type':'turn_context','payload':{'turn_id':turn,'cwd':str(cwd)}},
        {'type':'response_item','payload':{'type':'custom_tool_call','name':'exec','call_id':'owned','input':code}},
        {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'owned','output':'Script running with cell ID 1\nWall time 1.0 seconds\nOutput:\n'}},
        {'type':'response_item','payload':{'type':'function_call','name':'wait','call_id':'wait-one','arguments':'{"cell_id":"1"}'}},
        {'type':'response_item','payload':{'type':'function_call_output','call_id':'wait-one','output':'Script running with cell ID 1\nWall time 1.0 seconds\nOutput:\n'}},
        {'type':'response_item','payload':{'type':'function_call','name':'wait','call_id':'wait-two','arguments':'{"cell_id":"1"}'}},
        {'type':'response_item','payload':{'type':'function_call_output','call_id':'wait-two','output':[{'type':'input_text','text':'Script completed\nWall time 1.0 seconds\nOutput:\n'},{'type':'input_text','text':'{"session_id":123,"output":"ALIVE"}'}]}}
    ]
    return rows,{'expected_thread':thread,'expected_turn':turn,'expected_cwd':cwd,'expected_fixture':fixture,'expected_command':'python '+str(fixture)}


def _check_interrupt_rows(tmp_path,rows,scope):
    from verify_tui_interrupt import yielded_background_session
    path=tmp_path/'async-rollout.jsonl';path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
    return yielded_background_session(path,'interrupt_fixture.py',**scope)


def test_interrupt_async_exec_cell_wait_preserves_native_provenance(tmp_path):
    rows,scope=_interrupt_async_rows(tmp_path)
    result=_check_interrupt_rows(tmp_path,rows,scope)
    assert result['call_id']=='owned' and result['result_call_id']=='wait-two'
    assert result['session_id']=='123' and result['cell_id']=='1'
    assert result['call_chain']==['owned','wait-one','wait-two']
    assert result['thread_id']==scope['expected_thread'] and result['turn_id']==scope['expected_turn']


@pytest.mark.parametrize('mutation',[
    'wrong-cell','wrong-output-call','other-fixture','missing-original-exec',
    'cross-turn','cross-thread','wrong-cwd','duplicate-output','assistant-text',
    'no-exec-command','printed-cell-header','aborted-turn','duplicate-session',
    'completed-turn','already-exited-session',
    'foreign-fixture-literal','comment-fake-call','string-fake-call','print-fake-session',
    'reassigned-result','prefixed-exit-real-newlines','prefixed-exit-literal-newlines',
    'text-block-prefixed-exit','native-header-exited','opaque-embedded-session'
])
def test_interrupt_async_provenance_rejects_missing_or_foreign_evidence(tmp_path,mutation):
    import copy
    rows,scope=_interrupt_async_rows(tmp_path)
    if mutation=='wrong-cell':
        for row in rows:
            item=row['payload']
            if item.get('name')=='wait':item['arguments']='{"cell_id":"2"}'
    elif mutation=='wrong-output-call':rows[-1]['payload']['call_id']='foreign'
    elif mutation=='other-fixture':rows[3]['payload']['input']=rows[3]['payload']['input'].replace('interrupt_fixture.py','other_fixture.py')
    elif mutation=='missing-original-exec':rows=[row for row in rows if row['payload'].get('call_id')!='owned']
    elif mutation=='cross-turn':rows[-1]['payload']['internal_chat_message_metadata_passthrough']={'turn_id':'foreign'}
    elif mutation=='cross-thread':scope={**scope,'expected_thread':'foreign'}
    elif mutation=='wrong-cwd':scope={**scope,'expected_cwd':tmp_path/'foreign'}
    elif mutation=='duplicate-output':rows.append(copy.deepcopy(rows[-1]))
    elif mutation=='assistant-text':rows[-1]={'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'{"session_id":123}'}]}}
    elif mutation=='no-exec-command':rows[3]['payload']['input']='text(JSON.stringify({session_id:123})); // '+str(scope['expected_fixture'])
    elif mutation=='printed-cell-header':rows[4]['payload']['output']='Script completed\nOutput:\nScript running with cell ID 1\n'
    elif mutation=='aborted-turn':rows.insert(-1,{'type':'event_msg','payload':{'type':'turn_aborted','turn_id':scope['expected_turn']}})
    elif mutation=='duplicate-session':
        extra=copy.deepcopy(rows[3]);extra['payload']['call_id']='second-exec'
        rows.extend([extra,{'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'second-exec','output':'{"session_id":123}'}}])
    elif mutation=='completed-turn':rows.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':scope['expected_turn']}})
    elif mutation=='already-exited-session':rows[-1]['payload']['output']='{"session_id":123,"exit_code":0}'
    elif mutation=='foreign-fixture-literal':
        rows[3]['payload']['input']='const label='+json.dumps(str(scope['expected_fixture']))+'; const r=await tools.exec_command({cmd:"echo other"}); text(JSON.stringify(r));'
    elif mutation=='comment-fake-call':
        rows[3]['payload']['input']='// await tools.exec_command({cmd:'+json.dumps(scope['expected_command'])+'});\ntext(JSON.stringify({session_id:123}));'
    elif mutation=='string-fake-call':
        rows[3]['payload']['input']='const label='+json.dumps('await tools.exec_command({cmd:'+json.dumps(scope['expected_command'])+'});')+'; text(JSON.stringify({session_id:123}));'
    elif mutation=='print-fake-session':
        rows[3]['payload']['input']='const r=await tools.exec_command('+json.dumps({'cmd':scope['expected_command']})+'); text(JSON.stringify({session_id:123}));'
    elif mutation=='reassigned-result':
        rows[3]['payload']['input']='let r=await tools.exec_command('+json.dumps({'cmd':scope['expected_command']})+'); r={session_id:123}; text(JSON.stringify(r));'
    elif mutation=='prefixed-exit-real-newlines':rows[-1]['payload']['output']='Script completed\nOutput:\n{"session_id":123,"exit_code":0}'
    elif mutation=='prefixed-exit-literal-newlines':rows[-1]['payload']['output']=r'Script completed\nOutput:\n{"session_id":123,"exit_code":0}'
    elif mutation=='text-block-prefixed-exit':rows[-1]['payload']['output']=[{'type':'input_text','text':'Script completed\nOutput:\n{"session_id":123,"exit_code":0}'}]
    elif mutation=='native-header-exited':rows[-1]['payload']['output']='Script completed\nWall time 1.0 seconds\nOutput:\n{"session_id":123,"exit_code":0}'
    elif mutation=='opaque-embedded-session':rows[-1]['payload']['output']='untrusted program text {"session_id":123} with trailing diagnostic'
    assert not _check_interrupt_rows(tmp_path,rows,scope)


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


@pytest.mark.parametrize('shape',['json-object','json-content-array','native-header-json','text-block-native-header'])
def test_interrupt_native_running_output_shapes_are_explicit(tmp_path,shape):
    rows,scope=_interrupt_async_rows(tmp_path)
    value={'session_id':123,'output':'ALIVE'}
    if shape=='json-object':output=json.dumps(value)
    elif shape=='json-content-array':output=json.dumps([{'type':'input_text','text':json.dumps(value)}])
    elif shape=='native-header-json':output='Script completed\nWall time 1.0 seconds\nOutput:\n'+json.dumps(value)
    else:output=[{'type':'input_text','text':'Script completed\nWall time 1.0 seconds\nOutput:\n'+json.dumps(value)}]
    rows[-1]['payload']['output']=output
    assert _check_interrupt_rows(tmp_path,rows,scope)['session_id']=='123'


def _execute_tui_fixture_command(tmp_path,phase):
    import subprocess
    from verify_tui_audit import fixture_command
    command=fixture_command(phase)
    interpreter=[str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-NonInteractive','-Command',command] if os.name=='nt' else ['bash','-lc',command]
    return subprocess.run(interpreter,cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=20)


def test_tui_fixed_commands_write_exact_bytes_and_cold_phase_reads_unchanged_prior_file(tmp_path):
    first=_execute_tui_fixture_command(tmp_path,'initial')
    assert first.returncode==0,first.stderr
    assert (tmp_path/'tui-first.txt').read_bytes()==b'TUI_FIRST_5271' and 'TUI_FIXTURE_BYTES=14' in first.stdout
    resumed=_execute_tui_fixture_command(tmp_path,'cold-resume')
    assert resumed.returncode==0,resumed.stderr
    assert (tmp_path/'tui-first.txt').read_bytes()==b'TUI_FIRST_5271'
    assert (tmp_path/'tui-resumed.txt').read_bytes()==b'TUI_FIRST_5271_RESUMED' and 'TUI_FIXTURE_BYTES=22' in resumed.stdout


@pytest.mark.parametrize('wrong',[None,b'TUI_FIRST_5271\n',b'FOREIGN'])
def test_tui_cold_command_cannot_hide_missing_or_wrong_prior_bytes(tmp_path,wrong):
    if wrong is not None:(tmp_path/'tui-first.txt').write_bytes(wrong)
    result=_execute_tui_fixture_command(tmp_path,'cold-resume')
    assert result.returncode!=0 and not (tmp_path/'tui-resumed.txt').exists()


@pytest.mark.parametrize('state',['missing','LF','wrong','directory'])
def test_tui_completed_wrong_effect_fails_immediately_without_changing_the_oracle(tmp_path,state):
    from verify_tui_audit import completed_file_effect
    target=tmp_path/'result'
    if state=='LF':target.write_bytes(b'EXACT\n')
    elif state=='wrong':target.write_bytes(b'OTHER')
    elif state=='directory':target.mkdir()
    assert completed_file_effect(target,b'EXACT',set()) is False
    with pytest.raises(AssertionError,match='required file effect|incorrect file bytes'):
        completed_file_effect(target,b'EXACT',{'own-rollout'})


def test_tui_completed_correct_effect_still_waits_for_real_assistant_render(tmp_path):
    from verify_tui_audit import completed_file_effect,rendered_reply
    target=tmp_path/'result';target.write_bytes(b'EXACT');reply='TUI_VERIFIED_INITIAL_5271'
    effect=completed_file_effect(target,b'EXACT',{'own-rollout'})
    assert effect and not rendered_reply(('User asks '+reply).encode(),reply)
    assert effect and rendered_reply(('• '+reply).encode(),reply)


def test_tui_fixed_prompt_contains_the_exact_command_and_rejects_unknown_phase():
    from verify_tui_audit import fixture_prompt,fixture_command
    for phase in ['initial','cold-resume']:
        prompt=fixture_prompt(phase,'EXACT_REPLY')
        assert fixture_command(phase) in prompt and 'native exec' in prompt and 'apply_patch' in prompt
    with pytest.raises(ValueError):fixture_command('foreign')
