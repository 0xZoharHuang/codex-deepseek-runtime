"""Adversarial checks for reconstructing runtime evidence, not application success."""
import copy
import json
from verify_complex_runtime import lifecycle, request_sessions, source_imports, sha, application_outcome


def observations():
    parent='p';turn='t1'
    first=[{'method':'initialize','id':1},{'method':'turn/interrupt','id':2,'params':{'threadId':parent,'turnId':turn}}]
    second=[{'method':'initialize','id':1},{'method':'thread/resume','id':2,'params':{'threadId':parent}}]
    e1=[{'id':1,'result':{}},{'id':2,'result':{}},{'method':'turn/completed','params':{'threadId':parent,'turn':{'id':turn,'status':'interrupted','error':None}}}]
    e2=[{'id':1,'result':{}},{'id':2,'result':{'thread':{'id':parent}}},{'method':'item/completed','params':{'threadId':parent,'turnId':'t2','item':{'id':'tool','type':'commandExecution','exitCode':0}}}]
    first.insert(1,{'method':'turn/start','id':3,'params':{'threadId':parent}})
    second.append({'method':'turn/start','id':3,'params':{'threadId':parent}})
    e1.insert(1,{'id':3,'result':{'turn':{'id':turn}}})
    e2.insert(1,{'id':3,'result':{'turn':{'id':'t2'}}})
    e2.append({'method':'turn/completed','params':{'threadId':parent,'turn':{'id':'t2','status':'completed','error':None}}})
    return [first,second],[e1,e2]


def test_explicit_interrupt_cold_resume_and_actual_tool():
    s,e=observations()
    assert lifecycle(s,e,'p')['passed']
    assert request_sessions(s[0]+s[1])==s


def test_interrupted_other_turn_cannot_satisfy_controller_interrupt():
    s,e=observations();e[0][-1]['params']['turn']['id']='different'
    assert not lifecycle(s,e,'p')['passed']


def test_interrupted_terminal_with_error_fails():
    s,e=observations();e[0][-1]['params']['turn']['error']={'message':'transport broken'}
    assert not lifecycle(s,e,'p')['passed']


def test_resume_other_thread_or_without_post_resume_tool_fails():
    s,e=observations();e[1][2]['result']['thread']['id']='other'
    assert not lifecycle(s,e,'p')['passed']
    s,e=observations();e[1].pop(3)
    assert not lifecycle(s,e,'p')['passed']
    s,e=observations();e[1][3]['params']['turnId']='t1'
    assert not lifecycle(s,e,'p')['passed']


def test_missing_or_duplicate_rpc_response_fails():
    s,e=observations();e[0].pop(1)
    assert not lifecycle(s,e,'p')['passed']
    s,e=observations();e[0].append(copy.deepcopy(e[0][0]))
    assert not lifecycle(s,e,'p')['passed']


def test_source_import_receipt_rejects_changed_or_external_module(tmp_path):
    root=tmp_path/'httpcore';work=root/'work';work.mkdir(parents=True)
    module=work/'module.py';module.write_text('x=1')
    receipt={'package':'httpcore','expected_root':str(work),'modules':{'httpcore':{'file':str(module),'sha256':sha(module)}},'passed':True,'invalid_modules':[]}
    assert source_imports(root,'workspace',receipt)
    module.write_text('x=2')
    assert not source_imports(root,'workspace',receipt)
    outside=tmp_path/'external.py';outside.write_text('x=1')
    receipt['modules']['httpcore']['file']=str(outside)
    assert not source_imports(root,'workspace',receipt)


def test_application_failure_and_budget_interruption_are_preserved():
    report={'passed':False,'turn_status':None,'error':'Native acceptance event timed out','diff_check':0}
    suites=[{'suite':'workspace','exit':0},{'suite':'upstream-heldout','exit':1}]
    value=application_outcome(report,suites,True)
    assert value['application_task_passed'] is False
    assert value['original_report_passed'] is False
    assert value['controller_error']==report['error']
    assert value['suites']==suites
    report.update(passed=True,turn_status='completed',error=None)
    assert application_outcome(report,suites,True)['application_task_passed'] is False


def test_tool_on_unstarted_turn_cannot_prove_resume():
    s,e=observations();e[1][3]['params']['turnId']='invented'
    assert not lifecycle(s,e,'p')['passed']


def test_native_codemode_exit_label_and_output_only_are_distinct():
    from verify_complex_runtime import function_outputs
    parts=[{'type':'input_text','text':'Traceback\r\nreal failure\r\n'}, {'type':'input_text','text':'exit=1'}]
    values=function_outputs(parts,{})
    assert values==[{'exit_code':1,'output':parts[0]['text'],'projection':'output_and_exit_code'}]
    call={'name':'exec','arguments':json.dumps({'input':'const r = await tools.exec_command({cmd:"python task.py"}); text(r.output);'})}
    values=function_outputs(parts[:1],call)
    assert values==[{'exit_code':None,'output':parts[0]['text'],'projection':'model_selected_output_only'}]


def test_arbitrary_text_or_wrong_call_does_not_prove_native_projection():
    from verify_complex_runtime import function_outputs
    parts=[{'type':'input_text','text':'Traceback\nfailed\n'}]
    assert function_outputs(parts,{})==[]
    for source in ['text("Traceback\\nfailed\\n");', 'const r=await tools.exec_command({cmd:"x"}); r.output="fake"; text(r.output);', 'const r=await tools.exec_command({cmd:"x"}); text(r.output.slice(0,10));']:
        assert function_outputs(parts,{'name':'exec','arguments':json.dumps({'input':source})})==[]

def test_complex_review_requires_the_same_successful_child_turn_and_parent():
    from verify_complex_runtime import review_delivery_evidence
    history=[{'type':'event_msg','payload':{'type':'task_complete','turn_id':'child-turn',
              'root_turn_id':'parent-turn','last_agent_message':'Reviewed.'}}]
    events=[{'method':'turn/completed','params':{'threadId':'child','turn':{
        'id':'child-turn','rootTurnId':'parent-turn','status':'completed','error':None}}}]
    delivery=[('/root/reviewer','Reviewed.','call')]
    def check(h=history,e=events,d=delivery,parent='parent-turn'):
        return review_delivery_evidence(h,e,'child','/root/reviewer',d,parent)['passed']
    assert check()
    assert not check(parent='other-parent')
    assert not check(parent=None)
    assert not check(d=[('/root/other','Reviewed.','call')])
    failed=copy.deepcopy(events);failed[0]['params']['turn'].update(status='failed',error={'message':'429'})
    assert not check(e=failed)
    wrong=copy.deepcopy(events);wrong[0]['params']['turn']['id']='different-turn'
    assert not check(e=wrong)
    empty=copy.deepcopy(history);empty[0]['payload']['last_agent_message']=None
    assert not check(h=empty)
    assert not check(e=events+failed)


def test_real_work_provenance_binds_windows_setup_and_workspace_sources(tmp_path):
    from verify_complex_runtime import producer_provenance
    names={'verify_real_work.py','verify_live.py','acceptance_assertions.py',
           'host_execution_acceptance.py','real_work_imports.py',
           'windows_acceptance_setup.py','qa_workspace.py'}
    for name in names | {'verify_real_work_followup.py'}:
        (tmp_path/name).write_text(name)
    artifact={'verifier_sha256':{name:sha(tmp_path/name) for name in names}}
    assert producer_provenance(artifact,followup=False,source=tmp_path)
    for missing in ['windows_acceptance_setup.py','qa_workspace.py']:
        incomplete=copy.deepcopy(artifact);incomplete['verifier_sha256'].pop(missing)
        assert not producer_provenance(incomplete,followup=False,source=tmp_path)
    assert not producer_provenance(artifact,followup=True,source=tmp_path)
    artifact['verifier_sha256']['verify_real_work_followup.py']=sha(tmp_path/'verify_real_work_followup.py')
    assert producer_provenance(artifact,followup=True,source=tmp_path)
    (tmp_path/'qa_workspace.py').write_text('changed control')
    assert not producer_provenance(artifact,followup=True,source=tmp_path)


def provider_rows():
    model = 'deepseek/fixed-model'
    requests = {'known': {'model': model}, 'second': {'model': model}}
    receipts = [('known', 'completed', 200, 'deepseek', 'context', 'official_default'),
                ('second', 'cancelled', None, None, 'context', 'official_default')]
    return model, requests, receipts


def test_provider_transport_requires_every_actual_request_receipt():
    from verify_complex_runtime import provider_transport_evidence
    model, requests, receipts = provider_rows()
    proof = provider_transport_evidence(requests, receipts[:1], model)
    assert proof['passed'] is False
    assert proof['missing_receipt_ids'] == ['second']
    assert proof['receipt_ids'] == ['known']


def test_provider_transport_keeps_untraced_unknown_receipt_failure():
    from verify_complex_runtime import provider_transport_evidence
    model, requests, receipts = provider_rows()
    receipts.append(('unknown', 'running', None, None, None, None))
    proof = provider_transport_evidence(requests, receipts, model)
    assert proof['passed'] is False
    assert proof['untraced_receipt_ids'] == ['unknown']
    assert proof['unresolved_or_failed_receipt_ids'] == ['unknown']


def test_provider_transport_rejects_duplicate_receipt_identity():
    from verify_complex_runtime import provider_transport_evidence
    model, requests, receipts = provider_rows()
    proof = provider_transport_evidence(requests, receipts + receipts[:1], model)
    assert proof['passed'] is False
    assert proof['duplicate_receipt_ids'] == ['known']


def test_provider_transport_preserves_completed_and_explicit_cancelled():
    from verify_complex_runtime import provider_transport_evidence
    model, requests, receipts = provider_rows()
    proof = provider_transport_evidence(requests, receipts, model)
    assert proof['passed'] is True
    for field in ['missing_receipt_ids', 'untraced_receipt_ids',
                  'duplicate_receipt_ids', 'unresolved_or_failed_receipt_ids']:
        assert proof[field] == []
    assert provider_transport_evidence({}, [], model)['passed'] is False


def test_provider_transport_preserves_model_status_and_upstream_checks():
    from verify_complex_runtime import provider_transport_evidence
    model, requests, receipts = provider_rows()
    assert provider_transport_evidence(requests, receipts, 'other-model')['passed'] is False
    for status in ['running', 'failed', 'upstream_error']:
        bad = [receipts[0], ('second', status, 429, 'deepseek', 'context', 'official_default')]
        proof = provider_transport_evidence(requests, bad, model)
        assert proof['passed'] is False
        assert proof['unresolved_or_failed_receipt_ids'] == ['second']
    for upstream, provider in [(429, 'deepseek'), (200, None)]:
        bad = [('known', 'completed', upstream, provider, 'context', 'official_default'),
               receipts[1]]
        assert provider_transport_evidence(requests, bad, model)['passed'] is False

import os
from pathlib import Path
import pytest
from verify_complex_runtime import (file_effect_evidence, readonly_control_evidence,
                                    control_owner_evidence, process_observation)

@pytest.fixture
def edit_case(tmp_path, monkeypatch):
    root=tmp_path/'case';work=root/'work';work.mkdir(parents=True)
    (work/'changed.py').write_text('new')
    patch=b'diff --git a/changed.py b/changed.py\n--- a/changed.py\n+++ b/changed.py\n@@ -1 +1 @@\n-old\n+new\n'
    import verify_complex_runtime as collector
    monkeypatch.setattr(collector,'complete_work_patch',lambda p:patch)
    return root,work,patch

def changes(path):
    return [{'type':'fileChange','status':'completed','changes':[{'path':str(path)}]}]

def junction(alias,physical):
    import qa_workspace as layout
    if os.name=='nt':layout._junction(alias,physical)
    else:alias.symlink_to(physical,target_is_directory=True)

def test_file_effect_normal_and_extended_alias(edit_case):
    root,work,patch=edit_case
    assert file_effect_evidence(root,changes(work/'changed.py'),patch)['passed']
    if os.name=='nt':
        assert file_effect_evidence(root,changes(Path('\\\\?\\'+str(work/'changed.py'))),patch)['passed']

def test_file_effect_owned_mapping_checks_both_physical_and_alias_paths(edit_case,tmp_path,monkeypatch):
    import qa_workspace as layout
    root,work,patch=edit_case
    monkeypatch.setattr(layout,'WINDOWS',True)
    monkeypatch.setenv(layout.ENV,json.dumps({'run_id':'collector-unit','workspace_root':str(tmp_path/'physical'),'case_roots':[str(root)]}))
    if os.name!='nt':monkeypatch.setattr(layout,'_junction',junction)
    physical=layout.prepare_workspace(root)
    for path in [physical/'changed.py',root/'work/changed.py']:
        proof=file_effect_evidence(root,changes(path),patch)
        assert proof['passed'] and set(proof['files'])=={'changed.py'}
        assert proof['workspace_record_sha256']==sha(root/layout.RECORD)
    record=json.loads((root/layout.RECORD).read_text());record['physical_identity']=[-1,-1]
    (root/layout.RECORD).write_text(json.dumps(record))
    assert not file_effect_evidence(root,changes(physical/'changed.py'),patch)['passed']

def test_file_effect_rejects_foreign_mapping_even_with_internal_changed_file(edit_case,tmp_path):
    root,work,patch=edit_case
    moved=tmp_path/'outside';work.rename(moved);junction(work,moved)
    proof=file_effect_evidence(root,changes(moved/'changed.py'),patch)
    assert proof['passed'] is False

@pytest.mark.parametrize('style',['direct','nested_link','prefix_sibling','relative'])
def test_file_effect_rejects_escape_even_when_another_change_is_valid(edit_case,tmp_path,style):
    root,work,patch=edit_case
    outside=tmp_path/'outside';outside.mkdir();(outside/'changed.py').write_text('outside')
    if style=='direct':path=outside/'changed.py'
    elif style=='nested_link':junction(work/'escape',outside);path=work/'escape/changed.py'
    elif style=='prefix_sibling':
        sibling=root/'work-other';sibling.mkdir();path=sibling/'changed.py';path.write_text('outside')
    else:path=Path('changed.py')
    assert file_effect_evidence(root,changes(work/'changed.py')+changes(path),patch)['passed'] is False

def test_file_effect_rejects_filename_substring_and_stale_patch(edit_case,monkeypatch):
    import verify_complex_runtime as collector
    root,work,patch=edit_case
    stale=patch+b'extra';assert not file_effect_evidence(root,changes(work/'changed.py'),stale)['passed']
    substring=patch.replace(b'+++ b/changed.py\n',b'+++ b/changed.py.backup\n')
    monkeypatch.setattr(collector,'complete_work_patch',lambda p:substring)
    assert not file_effect_evidence(root,changes(work/'changed.py'),substring)['passed']

def controls():
    requests=[{'id':1,'method':'initialize'},{'method':'initialized'},
              {'id':2,'method':'config/read','params':{'includeLayers':False}}]
    events=[{'id':1,'result':{}},{'method':'remoteControl/status/changed','params':{'status':'disabled'},'emittedAtMs':200000},
            {'id':2,'result':{}}]
    owner={'owner_id':'owned','pid':1,'birth':'windows:116444737000000000',
           'supervisor_pid':2,'supervisor_birth':'windows:116444737100000000',
           'native_pid':3,'native_birth':'windows:116444737200000000','closed':220.0}
    return requests,events,owner

def gone(pid,birth):
    return {'pid':pid,'expected_birth':birth,'observed_birth':None,'state':'gone'}

def test_readonly_control_not_task_evidence_and_unknown_exit_stays_unknown():
    requests,events,owner=controls();proof=control_owner_evidence(events,[owner],gone)
    assert proof['passed'] and proof['native_exit_code'] is None and proof['natural_eof_verified'] is False
    control=readonly_control_evidence(requests,events,proof)
    assert control['passed'] and control['contributes_task_lifecycle_evidence'] is False
    result=lifecycle([requests],[events],'p',require_resume=False,control_owners={0:proof})
    assert result['passed'] is False and result['task_connections']==[]
    sessions,groups=observations();sessions.insert(1,requests);groups.insert(1,events)
    result=lifecycle(sessions,groups,'p',control_owners={1:proof})
    assert result['passed'] and result['task_connections']==[0,2]

@pytest.mark.parametrize('mutation',['tool','write','turn','server_tool','terminal','pending','error','duplicate','extra_reply','unknown_event','uninitialized','missing_owner'])
def test_readonly_control_cannot_hide_activity_or_incomplete_rpc(mutation):
    requests,events,owner=controls();proof=control_owner_evidence(events,[owner],gone)
    if mutation in {'tool','write','turn'}:
        method={'tool':'command/exec','write':'config/value/write','turn':'turn/start'}[mutation]
        requests.append({'id':3,'method':method});events.append({'id':3,'result':{}})
    elif mutation=='server_tool':events.append({'id':90,'method':'item/tool/call','params':{}})
    elif mutation=='terminal':events.append({'method':'turn/completed','params':{}})
    elif mutation=='pending':events.pop()
    elif mutation=='error':events[-1]={'id':2,'error':{'code':-1}}
    elif mutation=='duplicate':events.append(events[-1].copy())
    elif mutation=='extra_reply':events.append({'id':9,'result':{}})
    elif mutation=='unknown_event':events.append({'surprise':'untyped'})
    elif mutation=='uninitialized':requests.pop(1)
    else:proof=None
    assert not readonly_control_evidence(requests,events,proof)['passed']

@pytest.mark.parametrize('mutation',['no_clock','outside_interval','unclosed','ambiguous','no_birth','live','unknown','unrelated_live_owner'])
def test_control_closure_requires_exact_unique_owner_and_no_remaining_process(mutation):
    requests,events,owner=controls();owners=[owner];probe=gone
    if mutation=='no_clock':events[1].pop('emittedAtMs')
    elif mutation=='outside_interval':events[1]['emittedAtMs']=50000
    elif mutation=='unclosed':owner['closed']=None
    elif mutation=='ambiguous':owners.append({**owner,'owner_id':'other'})
    elif mutation=='no_birth':owner['supervisor_birth']=None
    elif mutation in {'live','unknown'}:probe=lambda pid,birth:{**gone(pid,birth),'state':mutation}
    else:
        owners.append({'pid':7,'birth':'windows:116444737100000000'})
        probe=lambda pid,birth:{**gone(pid,birth),'state':'live'if pid==7 else'gone'}
    assert not control_owner_evidence(events,owners,probe)['passed']


def test_control_owner_proof_stays_equal_across_gone_reused_gone():
    _,events,owner=controls()
    observations=[]
    for state,current in [('gone',None),('reused','windows:999999999999999999'),('gone',None)]:
        def probe(pid,birth,state=state,current=current):
            return {'pid':pid,'expected_birth':birth,'observed_birth':current,'state':state}
        observations.append(control_owner_evidence(events,[owner],probe))
    assert all(proof['passed'] for proof in observations)
    assert observations[0]==observations[1]==observations[2]
    assert all(row['ended'] is True for row in observations[0]['processes'])
    assert all(set(row)=={'pid','expected_birth','ended'} for row in observations[0]['processes'])

@pytest.mark.parametrize('state',['live','unknown'])
def test_stable_owner_proof_rechecks_expected_identity_each_time(state):
    _,events,owner=controls()
    assert control_owner_evidence(events,[owner],gone)['passed']
    checked=[]
    def probe(pid,birth):
        checked.append((pid,birth))
        return {'pid':pid,'expected_birth':birth,
                'observed_birth':birth if state=='live' else None,
                'state':state if pid==owner['native_pid']else'gone'}
    rejected=control_owner_evidence(events,[owner],probe)
    assert rejected['passed'] is False
    assert (owner['native_pid'],owner['native_birth']) in checked
    assert next(row for row in rejected['processes'] if row['pid']==owner['native_pid'])['ended'] is False


def recovery_rows():
    model = 'deepseek/fixed-model'
    requests = {key: {'model': model, 'thread': 'parent', 'turn': 'original'}
                for key in ('failure', 'success')}
    receipts = [('failure', 'adapter_error', 200, 'deepseek', 'shared-cache', 'official_default'),
                ('success', 'completed', 200, 'morph/fp8', 'shared-cache', 'unattested_native_history_with_hosted_tools')]
    evidence = {'receipt_observations': [
        {'id': 'failure', 'started': 1., 'finished': 2., 'detail': 'Provider returned an undeclared tool: update_plan'},
        {'id': 'success', 'started': 4., 'finished': 5., 'detail': None}],
        'native_events': [
            {'method': 'error', 'params': {'threadId': 'parent', 'turnId': 'original',
                'willRetry': True, 'error': {'additionalDetails': 'stream disconnected before completion: Provider returned an undeclared tool: update_plan',
                'codexErrorInfo': {'responseStreamDisconnected': {'httpStatusCode': None}}}},
                'emittedAtMs': 3000},
            {'method': 'turn/completed', 'params': {'threadId': 'parent',
                'turn': {'id': 'original', 'status': 'completed', 'error': None}}, 'emittedAtMs': 6000}]}
    return model, requests, receipts, evidence


def test_provider_native_recovery_is_explicit_and_preserves_the_original_failure():
    from verify_complex_runtime import provider_transport_evidence
    model, requests, receipts, evidence = recovery_rows()
    strict = provider_transport_evidence(requests, receipts, model)
    assert not strict['passed'] and strict['unresolved_or_failed_receipt_ids'] == ['failure']
    result = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
    assert result['passed'] and result['provider_evidence_version'] == 2
    assert result['single_run_all_success'] is False
    assert result['original_error_receipt_ids'] == result['recovered_error_receipt_ids'] == ['failure']
    assert result['unresolved_or_failed_receipt_ids'] == []
    assert result['errors'] == [{'id': 'failure', 'status': 'adapter_error', 'upstream_status': 200,
        'provider': 'deepseek', 'detail': evidence['receipt_observations'][0]['detail'],
        'route_reason': 'official_default', 'classification': 'recovered'}]


@pytest.mark.parametrize('change', [
    'other-thread', 'other-turn', 'same-cache-other-child', 'no-retry', 'retry-false',
    'retry-before-finish', 'retry-after-next-start', 'wrong-detail', 'wrong-error-info',
    'missing-error-event', 'duplicate-error-event', 'missing-terminal', 'duplicate-terminal',
    'failed-terminal', 'interrupted-terminal', 'wrong-terminal-turn', 'terminal-before-success',
    'success-before-failure', 'manual-followup', 'missing-request', 'missing-receipt',
    'untraced-receipt', 'duplicate-receipt', 'unknown-status', 'running-status',
    'model-drift', 'bad-success-http', 'missing-timing', 'duplicate-timing',
    'retry-truthy-one', 'duplicate-nonretry-event', 'ambiguous-next-request', 'whitespace-detail',
    'wrong-error-http-status', 'malformed-native-error', 'malformed-terminal', 'malformed-error-info',
    'bad-thread-type', 'bad-turn-type',
    'bad-finish', 'bool-time', 'nonfinite-time', 'malformed-evidence'
])
def test_provider_recovery_requires_unique_same_turn_raw_causality(change):
    from verify_complex_runtime import provider_transport_evidence
    model, requests, receipts, evidence = recovery_rows()
    retry = evidence['native_events'][0]
    terminal = evidence['native_events'][1]
    timing = evidence['receipt_observations']
    if change in {'other-thread', 'same-cache-other-child'}:
        requests['success']['thread'] = 'other-child'
    elif change == 'other-turn': requests['success']['turn'] = 'later-turn'
    elif change == 'bad-thread-type': requests['failure']['thread'] = []
    elif change == 'bad-turn-type': requests['failure']['turn'] = {'not': 'native-turn-id'}
    elif change == 'no-retry': retry['params'].pop('willRetry')
    elif change == 'retry-false': retry['params']['willRetry'] = False
    elif change == 'retry-before-finish': retry['emittedAtMs'] = 1000
    elif change == 'retry-after-next-start': retry['emittedAtMs'] = 4500
    elif change == 'wrong-detail': retry['params']['error']['additionalDetails'] = 'different error'
    elif change == 'wrong-error-info': retry['params']['error']['codexErrorInfo'] = {'other': {}}
    elif change == 'malformed-native-error': retry['params']['error'] = None
    elif change == 'malformed-terminal': terminal['params']['turn'] = None
    elif change == 'malformed-error-info': retry['params']['error']['codexErrorInfo'] = []
    elif change == 'wrong-error-http-status': retry['params']['error']['codexErrorInfo']['responseStreamDisconnected']['httpStatusCode'] = 429
    elif change == 'missing-error-event': evidence['native_events'].pop(0)
    elif change == 'duplicate-error-event': evidence['native_events'].insert(0, retry)
    elif change == 'missing-terminal': evidence['native_events'].pop()
    elif change == 'duplicate-terminal': evidence['native_events'].append(terminal)
    elif change == 'failed-terminal': terminal['params']['turn']['status'] = 'failed'
    elif change == 'interrupted-terminal': terminal['params']['turn']['status'] = 'interrupted'
    elif change == 'wrong-terminal-turn': terminal['params']['turn']['id'] = 'different-turn'
    elif change == 'terminal-before-success': terminal['emittedAtMs'] = 4000
    elif change == 'success-before-failure': timing[1].update(started=.1, finished=.5)
    elif change == 'manual-followup':
        requests['success']['turn'] = 'new-manual-turn'
        terminal['params']['turn']['id'] = 'new-manual-turn'
    elif change == 'missing-request': requests.pop('failure')
    elif change == 'missing-receipt': receipts.pop(0)
    elif change == 'untraced-receipt': receipts.append(('unknown', 'running', None, None, None, None))
    elif change == 'duplicate-receipt': receipts.append(receipts[0])
    elif change == 'unknown-status': receipts[0] = ('failure', 'foreign_terminal', 200, 'deepseek', 'shared-cache', 'official_default')
    elif change == 'running-status': receipts[0] = ('failure', 'running', 200, 'deepseek', 'shared-cache', 'official_default')
    elif change == 'model-drift': requests['failure']['model'] = 'changed-model'
    elif change == 'bad-success-http': receipts[1] = ('success', 'completed', 429, 'morph/fp8', 'shared-cache', 'explicit')
    elif change == 'retry-truthy-one': retry['params']['willRetry'] = 1
    elif change == 'duplicate-nonretry-event':
        from copy import deepcopy
        duplicate = deepcopy(retry);duplicate['params']['willRetry'] = False
        evidence['native_events'].insert(0, duplicate)
    elif change == 'ambiguous-next-request':
        requests['parallel-success'] = dict(requests['success'])
        receipts.append(('parallel-success', 'completed', 200, 'morph/fp8', 'shared-cache', 'explicit'))
        timing.append({'id': 'parallel-success', 'started': 4., 'finished': 5., 'detail': None})
    elif change == 'whitespace-detail':
        timing[0]['detail'] = ' '
        retry['params']['error']['additionalDetails'] += ' '
    elif change == 'missing-timing': timing.pop(0)
    elif change == 'duplicate-timing': timing.append(timing[0])
    elif change == 'bad-finish': timing[0]['finished'] = 0
    elif change == 'bool-time': timing[0]['started'] = True
    elif change == 'nonfinite-time': timing[0]['finished'] = float('nan')
    else: evidence = {}
    result = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
    assert result['passed'] is False


def test_provider_consecutive_retry_chain_requires_evidence_for_each_hop():
    from copy import deepcopy
    from verify_complex_runtime import provider_transport_evidence
    model, requests, receipts, evidence = recovery_rows()
    requests['second-error'] = dict(requests['failure'])
    receipts.insert(1, ('second-error', 'adapter_error', 200, 'morph/fp8', 'shared-cache', 'explicit'))
    evidence['receipt_observations'][1].update(started=7., finished=8.)
    evidence['receipt_observations'].append({'id': 'second-error', 'started': 4., 'finished': 5.,
                                           'detail': evidence['receipt_observations'][0]['detail']})
    second = deepcopy(evidence['native_events'][0]);second['emittedAtMs'] = 6000
    evidence['native_events'].insert(1, second);evidence['native_events'][-1]['emittedAtMs'] = 9000
    complete = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
    assert complete['passed'] and complete['recovered_error_receipt_ids'] == ['failure', 'second-error']
    assert complete['single_run_all_success'] is False
    evidence['native_events'].pop(1)
    incomplete = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
    assert not incomplete['passed'] and incomplete['recovered_error_receipt_ids'] == []


def test_provider_upstream_terminal_error_needs_the_same_observed_retry_chain():
    from verify_complex_runtime import provider_transport_evidence
    model, requests, receipts, evidence = recovery_rows()
    receipts[0] = ('failure', 'upstream_error', 429, 'deepseek', 'shared-cache', 'official_default')
    evidence['native_events'][0]['params']['error']['codexErrorInfo'] = {'httpConnectionFailed': {'httpStatusCode': 429}}
    result = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
    assert result['passed'] and result['single_run_all_success'] is False
    assert result['errors'][0]['status'] == 'upstream_error' and result['errors'][0]['upstream_status'] == 429
    evidence['native_events'][0]['params']['willRetry'] = False
    assert not provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)['passed']


def test_provider_adapter_network_error_without_upstream_status_still_needs_exact_native_evidence():
    from verify_complex_runtime import provider_transport_evidence
    model, requests, receipts, evidence = recovery_rows()
    receipts[0] = ('failure', 'adapter_error', None, 'deepseek', 'shared-cache', 'official_default')
    result = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
    assert result['passed'] and result['errors'][0]['upstream_status'] is None
    evidence['native_events'][0]['emittedAtMs'] = 4500
    assert not provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)['passed']
