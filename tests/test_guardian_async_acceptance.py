from copy import deepcopy
import json
import sqlite3
import contextlib
import pytest
import verify_safety as safety


def fixtures():
    actions=[{'tool':'exec','arguments':'run()'},{'tool':'exec_command','cmd':'python fixture.py','sandbox_permissions':'require_escalated'}]
    samples=[{'receipt':'r'+str(i),'call_id':'call'+str(i),'action':a} for i,a in enumerate(actions)]
    scores=[{'type':'security_risk_score','payload':{'call_id':s['call_id'],'action':s['action'],'scores':{'action_risk':0.0}}} for s in samples]
    return samples,scores


def test_requires_each_classifier_receipt_and_its_own_score():
    samples,scores=fixtures()
    detail=safety.guardian_classification_state(samples,{'r0':('completed',200,10),'r1':('running',None,10)},scores[:1])
    assert not detail['passed'] and not detail.get('error') and detail['pending'][0]['receipt']=='r1'
    complete=safety.guardian_classification_state(samples,{'r0':('completed',200,10),'r1':('completed',200,10)},scores)
    assert complete['passed'] and len(complete['classifications'])==2


@pytest.mark.parametrize('status',['cancelled','failed','owner_lost','owner_closed','legacy_owner_unknown'])
def test_terminal_never_fakes_http_completion_or_hides_abnormal_failure(status):
    samples,scores=fixtures()
    detail=safety.guardian_classification_state(samples,{'r0':('completed',200,10),'r1':(status,200,10)},scores)
    if status=='cancelled':
        assert detail['passed'] and detail['classifications'][1]['native_publication']=='published'
        assert detail['classifications'][1]['http_status']=='cancelled' and not detail['classifications'][1]['http_completed']
    else:assert not detail['passed'] and detail['failed_receipt']=='r1' and status in detail['error']


def test_first_score_cannot_cover_second_completed_classifier():
    samples,scores=fixtures()
    detail=safety.guardian_classification_state(samples,{'r0':('completed',200,10),'r1':('completed',200,10)},scores[:1])
    assert not detail['passed'] and detail['pending']==[{'receipt':'r1','call_id':'call1','started':10}]


def test_same_call_id_with_wrong_action_is_immediate_failure():
    samples,scores=fixtures();scores[1]['payload']['action']={'tool':'another'}
    detail=safety.guardian_classification_state(samples,{'r0':('completed',200,10),'r1':('completed',200,10)},scores)
    assert not detail['passed'] and 'mismatch' in detail['error']


@pytest.mark.parametrize('bad',['duplicate-score','duplicate-call','http500','missing-receipt'])
def test_ambiguous_or_bad_completion_rejected(bad):
    samples,scores=fixtures();receipts={'r0':('completed',200,10),'r1':('completed',200,10)}
    if bad=='duplicate-score':scores.append(deepcopy(scores[1]))
    elif bad=='duplicate-call':samples[1]['call_id']=samples[0]['call_id']
    elif bad=='http500':receipts['r1']=('completed',500,10)
    else:receipts.pop('r1')
    detail=safety.guardian_classification_state(samples,receipts,scores)
    assert not detail['passed'] and detail.get('error')


def prepare_db(tmp_path):
    root=tmp_path/'case';p=root/'home/provider-state/adapter.sqlite';p.parent.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(p)) as db, db:
        db.execute('create table receipts(id text,status text,upstream_status integer,started real)')
        db.executemany('insert into receipts values (?,?,?,?)',[('r0','completed',200,10),('r1','running',None,10)])
    return root,p


def test_wait_keeps_peer_window_open_until_second_terminal_and_score(tmp_path,monkeypatch):
    root,path=prepare_db(tmp_path);samples,scores=fixtures();calls=[]
    monkeypatch.setattr(safety.time,'time',lambda:11)
    monkeypatch.setattr(safety,'guardian_rollout_records',lambda *args:scores if calls else scores[:1])
    def advance(_):
        calls.append('waited')
        with contextlib.closing(sqlite3.connect(path)) as db, db:db.execute("update receipts set status='completed',upstream_status=200 where id='r1'")
    monkeypatch.setattr(safety.time,'sleep',advance)
    result=safety.wait_guardian_classifications(root,'thread',samples)
    assert result['passed'] and calls==['waited']


def test_deadline_is_receipt_started_not_new_budget_after_parent_turn(tmp_path,monkeypatch):
    root,path=prepare_db(tmp_path);samples,scores=fixtures()
    monkeypatch.setattr(safety.time,'time',lambda:191)
    monkeypatch.setattr(safety,'guardian_rollout_records',lambda *args:scores[:1])
    monkeypatch.setattr(safety.time,'sleep',lambda _:pytest.fail('Expired request must not wait again'))
    result=safety.wait_guardian_classifications(root,'thread',samples)
    assert not result['passed'] and 'budget' in result['error']


def test_cancelled_receipt_fails_without_waiting(tmp_path,monkeypatch):
    root,path=prepare_db(tmp_path);samples,scores=fixtures()
    with contextlib.closing(sqlite3.connect(path)) as db, db:db.execute("update receipts set status='cancelled' where id='r1'")
    monkeypatch.setattr(safety,'guardian_rollout_records',lambda *args:scores[:1])
    monkeypatch.setattr(safety.time,'sleep',lambda _:pytest.fail('Terminal failure must not be polled'))
    assert not safety.wait_guardian_classifications(root,'thread',samples)['passed']


def test_samples_bind_current_parent_turn_and_both_native_call_ids(tmp_path):
    root=tmp_path;traces=root/'adapter-traces';traces.mkdir();sessions=root/'home/sessions';sessions.mkdir(parents=True)
    samples,_=fixtures()
    outer={'type':'response_item','payload':{'type':'custom_tool_call','call_id':'call0','name':'exec','input':'run()','internal_chat_message_metadata_passthrough':{'turn_id':'turn'}}}
    (sessions/'rollout-thread.jsonl').write_text(json.dumps(outer)+'\n')
    for i,sample in enumerate(samples):
        payload={'prompt_cache_key':'guardian-v2:thread','model':'deepseek/deepseek-v4.1-flash-1017','provider':{'only':['deepseek']},
                 'client_metadata':{'parent_turn_id':'turn'},'input':[{'role':'user','content':[{'type':'input_text','text':'>>> APPROVAL REQUEST START\n'},{'type':'input_text','text':json.dumps(sample['action'])},{'type':'input_text','text':'>>> APPROVAL REQUEST END\n'}]}]}
        (traces/('r'+str(i)+'-request.json')).write_text(json.dumps(payload))
    stale=deepcopy(payload);stale['client_metadata']['parent_turn_id']='old'
    (traces/'old-request.json').write_text(json.dumps(stale))
    events=[{'method':'item/completed','params':{'threadId':'thread','turnId':'turn','item':{'type':'commandExecution','id':'call1','commandActions':[{'command':'python fixture.py'}]}}}]
    actual=safety.guardian_classifier_samples(root,'thread','turn',events)
    assert [(s['receipt'],s['call_id']) for s in actual]==[('r0','call0'),('r1','call1')]
    events[0]['params']['turnId']='different'
    with pytest.raises(AssertionError,match='one native call_id'):safety.guardian_classifier_samples(root,'thread','turn',events)


def test_unmarked_json_cannot_replace_planned_action():
    with pytest.raises(AssertionError,match='one explicit planned action'):
        safety.guardian_planned_action({'input':[{'role':'user','content':[{'text':'{"tool":"exec"}'}]}]})


def test_early_published_score_does_not_wait_for_http_drain():
    samples,scores=fixtures()
    result=safety.guardian_classification_state(samples,{'r0':('running',None,10),'r1':('running',None,10)},scores)
    assert result['passed']
    assert all(row['native_publication']=='published' and row['http_status']=='running' and not row['http_completed'] for row in result['classifications'])
    terminal=safety.guardian_classification_state(samples,{'r0':('running',None,10),'r1':('running',None,10)},scores,require_http_terminal=True)
    assert not terminal['passed'] and len(terminal['pending'])==2


@pytest.mark.parametrize('status,http',[('running',None),('cancelled',200),('cancelled',None),('completed',200)])
def test_nonrequired_auxiliary_needs_no_fabricated_score(status,http):
    samples,scores=fixtures()
    result=safety.guardian_classification_state(samples,{'r0':('completed',200,10),'r1':(status,http,10)},scores[:1],required_call_ids={'call0'})
    assert result['passed']
    aux=result['classifications'][1]
    assert aux['score'] is None and aux['native_publication']=='not_published' and aux['http_status']==status
    assert aux['http_completed'] is (status=='completed')


@pytest.mark.parametrize('status,http',[('failed',500),('owner_lost',200),('completed',500),('running',500)])
def test_nonrequired_auxiliary_abnormal_failure_still_blocks(status,http):
    samples,scores=fixtures()
    assert not safety.guardian_classification_state(samples,{'r0':('completed',200,10),'r1':(status,http,10)},scores[:1],required_call_ids={'call0'})['passed']


@pytest.mark.parametrize('required',[set(),{'unrelated'}])
def test_missing_required_identity_never_downgrades_to_any_published_score(required):
    samples,scores=fixtures()
    result=safety.guardian_classification_state(samples,{'r0':('completed',200,10),'r1':('completed',200,10)},scores,required_call_ids=required)
    assert not result['passed'] and result.get('error')


def approval_fixture(tmp_path):
    import shlex
    root=tmp_path/'approval';traces=root/'adapter-traces';traces.mkdir(parents=True)
    sessions=root/'home/sessions';sessions.mkdir(parents=True)
    dbpath=root/'home/provider-state/adapter.sqlite';dbpath.parent.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(dbpath)) as db, db:
        db.execute('create table receipts(id text,status text,upstream_status integer,started real,finished real)')
        db.execute('insert into receipts values (?,?,?,?,?)',('review','completed',200,10,11))
    command=shlex.join(['host-python','-I','fixture.py'])
    review={'targetItemId':'inner','reviewId':'review-id','completedAtMs':12000,'decisionSource':'agent',
            'review':{'status':'approved','riskLevel':'low','userAuthorization':'unknown','rationale':'Auto-review returned a low-risk allow decision.'},
            'action':{'type':'command','source':'unifiedExec','command':command,'cwd':'/project'}}
    item={'type':'commandExecution','id':'inner','status':'completed','exitCode':0,'command':command,'commandActions':[{'command':'host-python -I fixture.py'}],'aggregatedOutput':'APPROVAL_NATIVE_OK'}
    events=[{'method':'item/autoApprovalReview/completed','params':{'threadId':'thread','turnId':'turn',**review}},
            {'method':'item/completed','params':{'threadId':'thread','turnId':'turn','item':item}}]
    approval=safety.approval_execution_evidence(events,'thread','turn','APPROVAL_NATIVE_OK')
    action={'tool':'exec_command','command':['host-python','-I','fixture.py'],'cwd':'/project'}
    request={'prompt_cache_key':'guardian:thread','model':'deepseek/deepseek-v4.1-flash-1017',
             'client_metadata':{'parent_turn_id':'turn','session_id':'thread','thread_id':'review-thread','turn_id':'review-turn'},
             'input':[{'role':'user','content':[{'text':'>>> APPROVAL REQUEST START'},{'text':json.dumps(action)},{'text':'>>> APPROVAL REQUEST END'}]}]}
    (traces/'review-request.json').write_text(json.dumps(request))
    message={'timestamp':'1970-01-01T00:00:11.100Z','type':'response_item','payload':{'type':'message','role':'assistant','status':'completed',
             'internal_chat_message_metadata_passthrough':{'turn_id':'review-turn'},'content':[{'type':'output_text','text':'{"outcome":"allow"}'}]}}
    (sessions/'rollout-review-thread.jsonl').write_text(json.dumps(message)+'\n')
    return root,approval,request,message,dbpath,events


def test_synchronous_approval_binds_receipt_native_defaults_and_exact_effect(tmp_path):
    root,approval,_,_,_,events=approval_fixture(tmp_path)
    result=safety.guardian_approval_evidence(root,'thread','turn',approval,events)
    assert result['passed'] and result['decisions'][0]['source']=='synchronous_guardian_review'
    assert result['decisions'][0]['receipt']=='review'
    assert result['decisions'][0]['assessment']==approval['approved_executions'][0]['review']['review']


@pytest.mark.parametrize('bad',['other-parent-turn','other-session','other-review-turn','other-command-prefix','other-cwd','other-model',
                               'wrong-risk','wrong-authorization','wrong-rationale','denial-output','cancelled-receipt','late-receipt','missing-receipt','duplicate-request','wrong-decision-source','future-response'])
def test_synchronous_review_cannot_borrow_other_identity_or_assessment(tmp_path,bad):
    root,approval,request,message,dbpath,events=approval_fixture(tmp_path)
    if bad=='other-parent-turn':request['client_metadata']['parent_turn_id']='old'
    elif bad=='other-session':request['client_metadata']['session_id']='other'
    elif bad=='other-review-turn':message['payload']['internal_chat_message_metadata_passthrough']['turn_id']='other'
    elif bad=='other-command-prefix':
        action=json.loads(request['input'][0]['content'][1]['text']);action['command'][0]='different-python';request['input'][0]['content'][1]['text']=json.dumps(action)
    elif bad=='other-cwd':
        action=json.loads(request['input'][0]['content'][1]['text']);action['cwd']='/other';request['input'][0]['content'][1]['text']=json.dumps(action)
    elif bad=='other-model':request['model']='different/model'
    elif bad=='wrong-decision-source':approval['approved_executions'][0]['review']['decisionSource']='user'
    elif bad=='future-response':message['timestamp']='1970-01-01T00:00:13Z'
    elif bad=='wrong-risk':approval['approved_executions'][0]['review']['review']['riskLevel']='high'
    elif bad=='wrong-authorization':approval['approved_executions'][0]['review']['review']['userAuthorization']='high'
    elif bad=='wrong-rationale':approval['approved_executions'][0]['review']['review']['rationale']='unrelated'
    elif bad=='denial-output':message['payload']['content'][0]['text']='{"outcome":"deny"}'
    elif bad=='duplicate-request':
        (root/'adapter-traces/review2-request.json').write_text(json.dumps(request))
        with contextlib.closing(sqlite3.connect(dbpath)) as db, db:db.execute('insert into receipts values (?,?,?,?,?)',('review2','completed',200,10,11))
    else:
        with contextlib.closing(sqlite3.connect(dbpath)) as db, db:
            if bad=='cancelled-receipt':db.execute("update receipts set status='cancelled'")
            elif bad=='late-receipt':db.execute('update receipts set finished=13')
            else:db.execute('delete from receipts')
    (root/'adapter-traces/review-request.json').write_text(json.dumps(request))
    (root/'home/sessions/rollout-review-thread.jsonl').write_text(json.dumps(message)+'\n')
    assert not safety.guardian_approval_evidence(root,'thread','turn',approval,events)['passed']


def cached_fixture(tmp_path):
    root,approval,_,_,_,events=approval_fixture(tmp_path)
    approval['approved_executions'][0]['review']['review'].update(
        rationale='Approved using a current low-risk async score.',riskLevel=None,userAuthorization=None)
    approval['approved_executions'][0]['review']['decisionSource']=None
    action={'tool':'exec','arguments':'run()'}
    payload={'prompt_cache_key':'guardian-v2:thread','model':'deepseek/deepseek-v4.1-flash-1017',
             'client_metadata':{'parent_turn_id':'turn'},
             'input':[{'role':'user','content':[{'text':'>>> APPROVAL REQUEST START'},{'text':json.dumps(action)},{'text':'>>> APPROVAL REQUEST END'}]}]}
    (root/'adapter-traces/score-request.json').write_text(json.dumps(payload))
    records=[{'type':'response_item','payload':{'type':'custom_tool_call','call_id':'outer','name':'exec','input':'run()','internal_chat_message_metadata_passthrough':{'turn_id':'turn'}}},
             {'timestamp':'1970-01-01T00:00:11Z','type':'security_risk_score','payload':{'call_id':'outer','action':action,'sampled_at':'1970-01-01T00:00:10.900Z','scores':{'action_risk':0.0}}}]
    (root/'home/sessions/rollout-thread.jsonl').write_text('\n'.join(map(json.dumps,records))+'\n')
    return root,approval,events,records


def test_cached_route_is_not_claimed_as_synchronous_review(tmp_path):
    root,approval,events,_=cached_fixture(tmp_path)
    result=safety.guardian_approval_evidence(root,'thread','turn',approval,events)
    assert result['passed'] and result['decisions'][0]['source']=='native_cached_async_score'
    assert 'receipt' not in result['decisions'][0]


@pytest.mark.parametrize('bad',['missing','future','wrong-action','other-call','other-turn','ambiguous'])
def test_cached_marker_requires_unambiguous_preceding_bound_score(tmp_path,bad):
    root,approval,events,records=cached_fixture(tmp_path)
    if bad=='missing':records.pop()
    elif bad=='future':records[-1]['timestamp']='1970-01-01T00:00:13Z'
    elif bad=='wrong-action':records[-1]['payload']['action']={'tool':'unrelated'}
    elif bad=='other-call':records[-1]['payload']['call_id']='unrelated'
    elif bad=='other-turn':records[0]['payload']['internal_chat_message_metadata_passthrough']['turn_id']='other'
    else:records.append(deepcopy(records[-1]))
    (root/'home/sessions/rollout-thread.jsonl').write_text('\n'.join(map(json.dumps,records))+'\n')
    if bad=='other-turn':
        with pytest.raises(AssertionError):safety.guardian_approval_evidence(root,'thread','turn',approval,events)
    else:assert not safety.guardian_approval_evidence(root,'thread','turn',approval,events)['passed']


@pytest.mark.parametrize('value',[{'outcome':'maybe'},{'outcome':'allow','risk_level':''},{'outcome':'allow','user_authorization':''},{'outcome':'allow','rationale':42}])
def test_native_assessment_enum_and_optional_defaults_are_not_loosened(value):
    with pytest.raises(ValueError):safety.guardian_assessment(json.dumps(value))


def reviewed_execution(call_id='call1',cmd='python fixture.py'):
    return {'review':{'targetItemId':call_id,'review':{'status':'approved'},
                      'action':{'type':'command','command':cmd}},
            'command':{'id':call_id,'command':cmd,'commandActions':[{'command':cmd}]}}


def reviewed_approval(*executions):
    return {'passed':True,'approved_executions':list(executions) or [reviewed_execution()]}


def test_required_publication_binds_approved_nested_action_despite_direct_wrapper():
    samples,scores=fixtures()
    samples[0]['binding_kind']='direct_model_call';samples[1]['binding_kind']='nested_command'
    required=safety.required_guardian_calls(samples,reviewed_approval())
    assert required=={'call1'}
    result=safety.guardian_classification_state(samples,{'r0':('completed',200,10),'r1':('completed',200,10)},scores[1:],required_call_ids=required)
    assert result['passed'] and result['required_call_ids']==['call1']
    outer=result['classifications'][0]
    assert not outer['required'] and outer['score'] is None and outer['http_completed']
    assert outer['native_publication']=='not_published' and outer['publication_absence_cause']=='unknown'
    assert result['classifications'][1]['score']==scores[1]


def test_direct_exec_command_target_is_required_without_a_code_mode_wrapper():
    samples,_=fixtures();samples=samples[1:];samples[0]['binding_kind']='direct_model_call'
    assert safety.required_guardian_calls(samples,reviewed_approval())=={'call1'}


@pytest.mark.parametrize('bad',['no-approval','empty','missing-target','wrong-target',
                                'command-id','not-approved','review-command','sample-action',
                                'sample-tool','duplicate-sample','duplicate-execution'])
def test_required_guardian_publication_rejects_wrong_or_ambiguous_execution(bad):
    samples,_=fixtures();approval=reviewed_approval()
    if bad=='no-approval':approval['passed']=False
    elif bad=='empty':approval['approved_executions']=[]
    elif bad=='missing-target':approval['approved_executions'][0]['review'].pop('targetItemId')
    elif bad=='wrong-target':
        approval['approved_executions'][0]['review']['targetItemId']='unrelated'
        approval['approved_executions'][0]['command']['id']='unrelated'
    elif bad=='command-id':approval['approved_executions'][0]['command']['id']='unrelated'
    elif bad=='not-approved':approval['approved_executions'][0]['review']['review']['status']='denied'
    elif bad=='review-command':approval['approved_executions'][0]['review']['action']['command']='other command'
    elif bad=='sample-action':samples[1]['action']['cmd']='other command'
    elif bad=='sample-tool':samples[1]['action']['tool']='exec'
    elif bad=='duplicate-sample':samples.append(deepcopy(samples[1]))
    else:approval['approved_executions'].append(deepcopy(approval['approved_executions'][0]))
    with pytest.raises(AssertionError):safety.required_guardian_calls(samples,approval)


def test_wrapper_score_cannot_satisfy_missing_reviewed_action_publication():
    samples,scores=fixtures()
    required=safety.required_guardian_calls(samples,reviewed_approval())
    result=safety.guardian_classification_state(samples,{'r0':('completed',200,10),'r1':('completed',200,10)},scores[:1],required_call_ids=required)
    assert not result['passed'] and result['pending']==[{'receipt':'r1','call_id':'call1','started':10}]


def test_each_distinct_approved_action_requires_its_own_exact_publication():
    samples,scores=fixtures()
    sample={'receipt':'r2','call_id':'call2','action':{'tool':'exec_command','cmd':'python another.py'}}
    samples.append(sample)
    approval=reviewed_approval(reviewed_execution(),reviewed_execution('call2','python another.py'))
    required=safety.required_guardian_calls(samples,approval)
    assert required=={'call1','call2'}
    receipts={'r0':('completed',200,10),'r1':('completed',200,10),'r2':('completed',200,10)}
    result=safety.guardian_classification_state(samples,receipts,scores[1:],required_call_ids=required)
    assert not result['passed'] and result['pending'][0]['call_id']=='call2'
    scores.append({'type':'security_risk_score','payload':{'call_id':'call2','action':sample['action'],'scores':{'action_risk':0.0}}})
    assert safety.guardian_classification_state(samples,receipts,scores[1:],required_call_ids=required)['passed']


@pytest.mark.parametrize('bad',['decision-source','missing-sampled','future-sampled'])
def test_cached_score_requires_native_origin_and_valid_sample_time(tmp_path,bad):
    root,approval,events,records=cached_fixture(tmp_path)
    if bad=='decision-source':approval['approved_executions'][0]['review']['decisionSource']='agent'
    elif bad=='missing-sampled':records[-1]['payload'].pop('sampled_at')
    else:records[-1]['payload']['sampled_at']='1970-01-01T00:00:13Z'
    (root/'home/sessions/rollout-thread.jsonl').write_text('\n'.join(map(json.dumps,records))+'\n')
    assert not safety.guardian_approval_evidence(root,'thread','turn',approval,events)['passed']


@pytest.mark.parametrize('newest_risk',[0.0,1.0])
def test_cached_route_uses_newest_sample_instead_of_any_earlier_low_score(tmp_path,newest_risk):
    root,approval,events,records=cached_fixture(tmp_path)
    action={'tool':'exec','arguments':'second()'}
    request=json.loads((root/'adapter-traces/score-request.json').read_text())
    request['input'][0]['content'][1]['text']=json.dumps(action)
    (root/'adapter-traces/newer-request.json').write_text(json.dumps(request))
    records.extend([
        {'type':'response_item','payload':{'type':'custom_tool_call','call_id':'outer2','name':'exec','input':'second()','internal_chat_message_metadata_passthrough':{'turn_id':'turn'}}},
        {'timestamp':'1970-01-01T00:00:11.100Z','type':'security_risk_score','payload':{'call_id':'outer2','action':action,'sampled_at':'1970-01-01T00:00:10.950Z','scores':{'action_risk':newest_risk}}}
    ])
    (root/'home/sessions/rollout-thread.jsonl').write_text('\n'.join(map(json.dumps,records))+'\n')
    result=safety.guardian_approval_evidence(root,'thread','turn',approval,events)
    assert result['passed'] is (newest_risk==0.0)
    if result['passed']:assert result['decisions'][0]['score']['payload']['call_id']=='outer2'

def nested_binding_fixture(tmp_path, *, publish=True):
    root=tmp_path;traces=root/'adapter-traces';traces.mkdir()
    sessions=root/'home/sessions';sessions.mkdir(parents=True)
    first=str(tmp_path/'work-a');second=str(tmp_path/'work-b')
    actions=[{'tool':'exec_command','cmd':'read fixture.py'},
             {'tool':'exec_command','cmd':'read fixture.py','workdir':second}]
    records=[{'type':'turn_context','payload':{'turn_id':'turn','cwd':first}}]
    if publish:
        records.extend({'type':'security_risk_score','payload':{'call_id':'call'+str(i),'action':a,'scores':{'action_risk':0.0}}}
                       for i,a in enumerate(actions))
    for i,action in enumerate(actions):
        payload={'prompt_cache_key':'guardian-v2:thread','client_metadata':{'parent_turn_id':'turn'},
                 'input':[{'role':'user','content':[{'text':'>>> APPROVAL REQUEST START'},
                           {'text':json.dumps(action)},{'text':'>>> APPROVAL REQUEST END'}]}]}
        (traces/('r'+str(i)+'-request.json')).write_text(json.dumps(payload))
    rollout=sessions/'rollout-thread.jsonl'
    rollout.write_text('\n'.join(map(json.dumps,records))+'\n')
    events=[{'method':'item/completed','params':{'threadId':'thread','turnId':'turn',
             'item':{'type':'commandExecution','id':'call'+str(i),'cwd':cwd,'status':'failed',
                     'commandActions':[{'command':'read fixture.py'}]}}} for i,cwd in enumerate((first,second))]
    return root,events,records,rollout


@pytest.mark.parametrize('publish',[False,True])
def test_same_command_uses_default_or_explicit_native_cwd_without_borrowing_call(tmp_path,publish):
    root,events,_,_=nested_binding_fixture(tmp_path,publish=publish)
    samples=safety.guardian_classifier_samples(root,'thread','turn',events)
    assert [(s['receipt'],s['call_id']) for s in samples]==[('r0','call0'),('r1','call1')]


@pytest.mark.parametrize('bad',['other-turn','other-thread','wrong-cwd','swapped-cwd','missing-cwd'])
def test_nested_action_cannot_borrow_same_command_from_other_context(tmp_path,bad):
    root,events,records,rollout=nested_binding_fixture(tmp_path)
    if bad=='other-turn':events[1]['params']['turnId']='other'
    elif bad=='other-thread':events[1]['params']['threadId']='other'
    elif bad=='wrong-cwd':events[1]['params']['item']['cwd']=str(tmp_path/'unrelated')
    elif bad=='swapped-cwd':
        events[0]['params']['item']['cwd'],events[1]['params']['item']['cwd']=events[1]['params']['item']['cwd'],events[0]['params']['item']['cwd']
    else:events[1]['params']['item'].pop('cwd')
    if bad=='swapped-cwd':
        samples=safety.guardian_classifier_samples(root,'thread','turn',events)
        receipts={'r0':('completed',200,10),'r1':('completed',200,10)}
        assert not safety.guardian_classification_state(samples,receipts,records)['passed']
    else:
        with pytest.raises(AssertionError,match='one native call_id'):
            safety.guardian_classifier_samples(root,'thread','turn',events)


@pytest.mark.parametrize('bad',[None,'missing-score','wrong-action','other-call','duplicate-action'])
def test_equal_command_and_cwd_requires_exact_full_native_action(tmp_path,bad):
    root,events,records,rollout=nested_binding_fixture(tmp_path,publish=False)
    (root/'adapter-traces/r1-request.json').unlink()
    extra=deepcopy(events[0]);extra['params']['item']['id']='different';events.append(extra)
    action={'tool':'exec_command','cmd':'read fixture.py'}
    score={'type':'security_risk_score','payload':{'call_id':'call0','action':action,'scores':{'action_risk':0.0}}}
    if bad=='wrong-action':score['payload']['action']={**action,'sandbox_permissions':'require_escalated'}
    elif bad=='other-call':score['payload']['call_id']='not-in-current-events'
    if bad!='missing-score':records.append(score)
    if bad=='duplicate-action':
        duplicate=deepcopy(score);duplicate['payload']['call_id']='different';records.append(duplicate)
    rollout.write_text('\n'.join(map(json.dumps,records))+'\n')
    if bad is None:
        samples=safety.guardian_classifier_samples(root,'thread','turn',events)
        assert len(samples)==1 and samples[0]['call_id']=='call0'
    else:
        with pytest.raises(AssertionError,match='one native call_id'):
            safety.guardian_classifier_samples(root,'thread','turn',events)
