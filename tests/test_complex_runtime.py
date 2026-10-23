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
