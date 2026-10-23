"""Approval and denial gates require actual, current native tool outcomes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
from verify_safety import approval_execution_evidence
import verify_safety as safety
from verify_features import sandbox_denial_evidence,code_mode_denials
from process_acceptance import python_command,shell_command


def event(method,**params):
    return {'method':method,'params':{'threadId':'thread','turnId':'turn',**params}}


def approved_events():
    command={'type':'commandExecution','id':'command-1','status':'completed','exitCode':0,'aggregatedOutput':'APPROVAL_NATIVE_OK\r\n','command':'host-python -I fixture.py'}
    review=event('item/autoApprovalReview/completed',targetItemId='command-1',review={'status':'approved'},action={'type':'command','command':command['command']})
    return [review,event('item/completed',item=command)]


def test_approved_item_is_bound_to_exact_successful_native_execution():
    assert approval_execution_evidence(approved_events(),'thread','turn','APPROVAL_NATIVE_OK')['passed']
    result=subprocess.run(python_command('print("APPROVAL_NATIVE_OK")'),capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0 and result.stdout.strip()=='APPROVAL_NATIVE_OK'


def test_approval_script_survives_native_shell_argument_passing(tmp_path):
    fixture=tmp_path/'approval fixture.py'
    fixture.write_text('print("APPROVAL_NATIVE_OK")\n',encoding='utf-8')
    command=shell_command([python_command('')[0],'-I',str(fixture)])
    shell=([str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),
            '-NoProfile','-NonInteractive','-Command',command] if os.name=='nt' else ['/bin/sh','-c',command])
    result=subprocess.run(shell,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0 and result.stdout.strip()=='APPROVAL_NATIVE_OK'


def test_approval_thread_requires_review_while_cancellation_keeps_full_access(tmp_path,monkeypatch):
    class CancellationThreadReached(Exception):pass
    calls=[]
    class FixturePeer:
        sandbox_mode='danger-full-access'
        def __init__(self,root):self.events=[]
        def new(self,**options):
            calls.append({'options':options,'sandbox':options.get('sandbox',self.sandbox_mode)})
            if len(calls)==2:raise CancellationThreadReached()
            return 'thread'
        def start(self,thread,prompt):
            assert 'sandbox_permissions `require_escalated`' in prompt
            self.events=approved_events()
            return 'turn',0
        def complete(self,thread,start,**options):return []
        def close(self):pass
    monkeypatch.setattr(safety,'Peer',FixturePeer)
    monkeypatch.setattr(safety,'wait_guardian_approval',lambda *args,**kwargs:{'passed':True,'scope':'Approval routing tested separately'})
    monkeypatch.setenv('CODEX_DEEPSEEK_TEST_SANDBOX_MODE','danger-full-access')
    monkeypatch.setattr(sys,'argv',['verify_safety.py','--output',str(tmp_path)])
    with pytest.raises(CancellationThreadReached):safety.main()
    assert calls==[
        {'options':{'sandbox':'read-only','approvalsReviewer':'auto_review'},'sandbox':'read-only'},
        {'options':{},'sandbox':'danger-full-access'},
    ]
    result=json.loads(next(tmp_path.glob('*/results.json')).read_text())[0]
    assert result['passed'] is True and result['sandbox_mode']=='read-only'
    assert result['fixture_sandbox_mode']=='danger-full-access'


@pytest.mark.parametrize('failure',['exit_one','failed_status','incomplete_status','wrong_item','wrong_command','review_denied',
    'no_review','no_command','answer_only','wrong_thread','old_turn','late_review','wrong_stdout'])
def test_marker_or_approval_without_exact_success_cannot_pass(failure):
    events=approved_events();review=events[0]['params'];command=events[1]['params']['item']
    if failure=='exit_one':command['exitCode']=1
    elif failure=='failed_status':command['status']='failed'
    elif failure=='incomplete_status':command['status']='inProgress'
    elif failure=='wrong_item':review['targetItemId']='other-command'
    elif failure=='wrong_command':review['action']['command']='different command'
    elif failure=='review_denied':review['review']['status']='denied'
    elif failure=='no_review':events.pop(0)
    elif failure=='no_command':events.pop(1)
    elif failure=='answer_only':events=[event('item/completed',item={'type':'agentMessage','text':'APPROVAL_NATIVE_OK'})]
    elif failure=='wrong_thread':review['threadId']='other'
    elif failure=='old_turn':events[1]['params']['turnId']='old'
    elif failure=='late_review':events.reverse()
    elif failure=='wrong_stdout':command['aggregatedOutput']='The command would print APPROVAL_NATIVE_OK'
    assert not approval_execution_evidence(events,'thread','turn','APPROVAL_NATIVE_OK')['passed']


def declined_events(outside):
    command={'type':'commandExecution','id':'write','status':'declined','command':'host-python -I write.py '+str(outside),'exitCode':None,'aggregatedOutput':None}
    request=event('item/commandExecution/requestApproval',itemId='write',command=command['command']);request['id']=7
    return [request,event('item/completed',item=command)],[{'id':7,'result':{'decision':'decline'}}]


def test_policy_approval_refusal_requires_the_matching_native_decline_and_absent_effect(tmp_path):
    outside=tmp_path/'outside.txt';events,replies=declined_events(outside)
    result=sandbox_denial_evidence(events,replies,[],'thread','turn',outside)
    assert result['passed'] and result['native_declined_approvals']
    assert 'OS sandbox enforcement is qualified separately' in result['scope']
    outside.write_text('unexpected write')
    assert not sandbox_denial_evidence(events,replies,[],'thread','turn',outside)['passed']


@pytest.mark.parametrize('failure',['nothing_ran','model_claim','missing_reply','wrong_reply','accepted_reply','wrong_item',
    'wrong_command','other_thread','old_turn','permission_words_only_in_command','successful_quoted_denial','non_permission_failure'])
def test_missing_file_and_denial_words_cannot_replace_native_refusal(tmp_path,failure):
    outside=tmp_path/'outside.txt';events,replies=declined_events(outside)
    item=events[1]['params']['item']
    if failure=='nothing_ran':events=[]
    elif failure=='model_claim':events=[event('item/completed',item={'type':'agentMessage','text':'permission denied: '+str(outside)})]
    elif failure=='missing_reply':replies=[]
    elif failure=='wrong_reply':replies[0]['id']=8
    elif failure=='accepted_reply':replies[0]['result']['decision']='accept'
    elif failure=='wrong_item':events[0]['params']['itemId']='other'
    elif failure=='wrong_command':events[0]['params']['command']='different command'
    elif failure=='other_thread':events[1]['params']['threadId']='other'
    elif failure=='old_turn':events[1]['params']['turnId']='old'
    else:
        item.update(status='failed',exitCode=1,aggregatedOutput='unrelated failure')
        if failure=='permission_words_only_in_command':item['command']+=' # permission denied'
        elif failure=='successful_quoted_denial':item.update(status='completed',exitCode=0,aggregatedOutput='permission denied')
        elif failure=='non_permission_failure':item['aggregatedOutput']='python executable not found'
    assert not sandbox_denial_evidence(events,replies,[],'thread','turn',outside)['passed']


def code_rollout(outside):
    return [{'type':'session_meta','payload':{'id':'thread'}},
            {'type':'turn_context','payload':{'turn_id':'turn'}},
            {'type':'response_item','payload':{'type':'custom_tool_call','name':'exec','call_id':'call-1','input':'await tools.exec_command({cmd: '+json.dumps('write '+str(outside))+'})'}},
            {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'call-1','output':[{'type':'input_text','text':json.dumps({'exit_code':1,'output':'Permission denied: '+str(outside)})}]}}]


def test_native_code_mode_result_is_checked_in_its_own_thread_and_turn(tmp_path):
    outside=tmp_path/'outside.txt';records=code_rollout(outside)
    assert code_mode_denials([records],'thread','turn',outside)
    assert sandbox_denial_evidence([],[],[records],'thread','turn',outside)['passed']


@pytest.mark.parametrize('failure',['other_thread','old_turn','explicit_other_turn','wrong_call','successful_exit','wrong_output_path','prose_only'])
def test_native_code_mode_denial_cannot_reuse_other_turn_or_prose(tmp_path,failure):
    outside=tmp_path/'outside.txt';records=code_rollout(outside)
    output=records[3]['payload']
    if failure=='other_thread':records[0]['payload']['id']='other'
    elif failure=='old_turn':records[1]['payload']['turn_id']='old'
    elif failure=='explicit_other_turn':output['internal_chat_message_metadata_passthrough']={'turn_id':'old'}
    elif failure=='wrong_call':output['call_id']='unrelated-call'
    elif failure=='successful_exit':output['output'][0]['text']=json.dumps({'exit_code':0,'output':'Permission denied: '+str(outside)})
    elif failure=='wrong_output_path':output['output'][0]['text']=json.dumps({'exit_code':1,'output':'Permission denied: /unrelated'})
    elif failure=='prose_only':output['output'][0]['text']='Permission denied: '+str(outside)
    assert not code_mode_denials([records],'thread','turn',outside)
