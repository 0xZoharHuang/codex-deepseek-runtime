"""A single explicit user correction must never replay an observed OAuth action."""
import copy
import json
from types import SimpleNamespace
import pytest
import verify_independent_audit as audit


def observation(prompt="original", *, action=False):
    items=[{'type':'userMessage','content':[{'type':'text','text':prompt,'text_elements':[]}]},
           {'type':'agentMessage','text':'I will call the tool.'}]
    if action:items.append({'type':'mcpToolCall'})
    return {'items':items,'prompt':prompt,'events':[{'method':'thread/tokenUsage/updated','params':{}}],
            'raw_response_types':['message','function_call'] if action else ['message'],
            'receipts':[{'id':'r1','status':'completed','upstream_status':200}],
            'expected_receipt_ids':['r1'],'fixture_events':[],
            'oauth_server_ready':True,'native_turn_record_complete':True,'native_session':'fixture.jsonl'}


@pytest.mark.parametrize('mutation',[
    lambda o:o['items'].append({'type':'commandExecution'}),
    lambda o:o['events'].append({'id':3,'method':'item/commandExecution/requestApproval','params':{}}),
    lambda o:o['raw_response_types'].clear(),
    lambda o:o['raw_response_types'].append('custom_tool_call'),
    lambda o:o['raw_response_types'].append('function_call'),
    lambda o:o['fixture_events'].append({'kind':'tool_executed'}),
    lambda o:o['fixture_events'].append({'kind':'mcp','method':'resources/read'}),
    lambda o:o['fixture_events'].append({'kind':'mcp','method':'resources/list'}),
    lambda o:o['fixture_events'].append({'kind':'unknown_side_effect'}),
    lambda o:o['receipts'][0].update(status='adapter_error'),
    lambda o:o['receipts'][0].update(upstream_status=502),
    lambda o:o.update(oauth_server_ready=False),
    lambda o:o.update(native_turn_record_complete=False),
    lambda o:o['items'][0]['content'][0].update(text='unrelated'),
    lambda o:o['events'].append({'method':'turn/completed','params':{'turn':{'status':'interrupted'}}}),
])
def test_any_action_failure_or_unknown_observation_forbids_feedback(mutation):
    value=observation();assert audit._oauth_no_observed_action(value)
    mutation(value)
    assert not audit._oauth_no_observed_action(value)


class FakePeer:
    def __init__(self, fail=False, first=None, second=None):
        self.prompts=[];self.completed=[];self.fail=fail;self.events=[]
        self.answers=[first or observation(),second or observation(action=True)]
    def start(self,thread,prompt):
        self.prompts.append((thread,prompt))
        return 't'+str(len(self.prompts)),len(self.prompts)
    def complete(self,thread,start,*,turn_id):
        self.completed.append((thread,turn_id))
        if self.fail:raise RuntimeError('Native turn did not reach error-free completed')
        value=copy.deepcopy(self.answers[len(self.prompts)-1]['items'])
        value[0]['content'][0]['text']=self.prompts[-1][1]
        return value


def run(monkeypatch,tmp_path, *, first=None,second=None,fail=False):
    (tmp_path/'fixture-events.json').write_text('[]')
    peer=FakePeer(fail,first,second);seen=[]
    monkeypatch.setattr(audit,'read_fixture_events',lambda root,**kwargs:json.loads((root/'fixture-events.json').read_text()))
    monkeypatch.setattr(audit,'_oauth_receipt_ids',lambda peer,thread,turn:[turn+'-receipt'])
    def observe(peer,thread,turn,start,items,prompt,events):
        value=copy.deepcopy(first if len(seen)==0 and first is not None else
                            second if seen and second is not None else
                            observation(prompt,action=bool(seen)))
        value['prompt']=prompt
        value['items'][0]['content'][0]['text']=prompt
        value['expected_receipt_ids']=[turn+'-receipt']
        value['receipts'][0]['id']=turn+'-receipt'
        seen.append(value)
        return value
    monkeypatch.setattr(audit,'_oauth_turn_observation',observe)
    path=tmp_path/'attempts.json'
    return peer,path,lambda:audit.oauth_action_turn(peer,'same-thread','original',tmp_path,path,'cold')


def test_clean_nonaction_gets_one_same_thread_feedback_with_both_turn_receipts(monkeypatch,tmp_path):
    peer,path,invoke=run(monkeypatch,tmp_path)
    invoke()
    rows=json.loads(path.read_text())
    assert [p[0] for p in peer.prompts]==['same-thread','same-thread']
    assert 'ALL_TOOLS' in peer.prompts[1][1]
    assert [(r['turn_id'],r['receipt_ids']) for r in rows]==[
        ('t1',['t1-receipt']),('t2',['t2-receipt'])]
    assert rows[0]['reply']=='I will call the tool.'
    assert rows[0]['no_observed_tool_action'] is True
    assert rows[1]['feedback_decision']=='observed_action_or_unqualified_observation'


@pytest.mark.parametrize('kind',['native','fixture','receipt','native_error'])
def test_partial_actions_or_transport_failures_are_never_replayed(monkeypatch,tmp_path,kind):
    first=observation()
    if kind=='native':first['raw_response_types'].append('custom_tool_call')
    if kind=='fixture':first['fixture_events'].append({'kind':'tool_executed'})
    if kind=='receipt':first['receipts'][0]['status']='adapter_error'
    if kind=='native_error':first['events'].append({'method':'error','params':{'error':{'message':'failed'}}})
    peer,path,invoke=run(monkeypatch,tmp_path,first=first)
    invoke()
    assert len(peer.prompts)==1


def test_failed_native_terminal_never_gets_feedback(monkeypatch,tmp_path):
    peer,path,invoke=run(monkeypatch,tmp_path,fail=True)
    with pytest.raises(RuntimeError):invoke()
    assert len(peer.prompts)==1
    rows=json.loads(path.read_text())
    assert rows[0]['turn_id']=='t1' and rows[0]['error']['type']=='RuntimeError'


def test_second_nonaction_is_failure_without_a_third_turn(monkeypatch,tmp_path):
    peer,path,invoke=run(monkeypatch,tmp_path,second=observation())
    with pytest.raises(AssertionError,match='after one explicit feedback'):invoke()
    assert len(peer.prompts)==2
    rows=json.loads(path.read_text())
    assert len(rows)==2 and rows[0]['reply']=='I will call the tool.'
    assert rows[1]['no_observed_tool_action'] is True and rows[1]['error']


def test_normal_action_never_waits_for_extra_recorder_or_receipt_guard(monkeypatch,tmp_path):
    peer,path,invoke=run(monkeypatch,tmp_path,first=observation(action=True))
    def forbidden(*args):raise AssertionError("normal actions must not use feedback guard")
    monkeypatch.setattr(audit,'_oauth_turn_observation',forbidden)
    items=invoke()
    assert any(item['type']=='mcpToolCall' for item in items)
    assert len(peer.prompts)==1
    assert json.loads(path.read_text())[0]['feedback_decision']=='observed_action_or_unqualified_observation'


def test_incomplete_persisted_observation_never_sends_feedback(monkeypatch,tmp_path):
    peer,path,invoke=run(monkeypatch,tmp_path)
    def incomplete(*args):raise AssertionError("native recorder has not persisted the terminal")
    monkeypatch.setattr(audit,'_oauth_turn_observation',incomplete)
    invoke()
    rows=json.loads(path.read_text())
    assert len(peer.prompts)==1 and rows[0]['feedback_decision']=='uncertain_no_feedback'
    assert rows[0]['observation_error']['type']=='AssertionError'


class ReadClock:
    def __init__(self):self.now=0
    def monotonic(self):return self.now
    def sleep(self,duration):self.now+=duration


@pytest.mark.parametrize('pending_kind',['rollout','receipt'])
def test_known_persistence_delay_can_settle_without_more_model_turns(monkeypatch,tmp_path,pending_kind):
    peer,path,invoke=run(monkeypatch,tmp_path)
    clock=ReadClock();monkeypatch.setattr(audit,'time',clock);reads=[]
    def delayed(peer,thread,turn,start,items,prompt,events):
        value=observation(prompt)
        value['expected_receipt_ids']=[turn+'-receipt'];value['receipts'][0]['id']=turn+'-receipt'
        reads.append(turn)
        if len(reads)==1:
            if pending_kind=='rollout':value['native_turn_record_complete']=False
            else:value['receipts'][0]['status']='running'
        return value
    monkeypatch.setattr(audit,'_oauth_turn_observation',delayed)
    invoke()
    rows=json.loads(path.read_text())
    assert reads==['t1','t1'] and len(peer.prompts)==2
    assert rows[0]['observation_count']==2 and 0<rows[0]['observation_seconds']<=5
    assert rows[0]['explicit_feedback_follows'] is True


def test_missing_terminal_persistence_reaches_fixed_read_only_bound(monkeypatch,tmp_path):
    peer,path,invoke=run(monkeypatch,tmp_path)
    clock=ReadClock();monkeypatch.setattr(audit,'time',clock)
    def pending(peer,thread,turn,start,items,prompt,events):
        value=observation(prompt);value['native_turn_record_complete']=False
        return value
    monkeypatch.setattr(audit,'_oauth_turn_observation',pending)
    invoke()
    row=json.loads(path.read_text())[0]
    assert len(peer.prompts)==1 and row['feedback_decision']=='uncertain_no_feedback'
    assert row['observation_timeout_seconds']==5 and clock.now==5
    assert 'explicit_feedback_follows' not in row


@pytest.mark.parametrize('late',['action','failed_receipt','error'])
def test_late_action_or_failure_stops_read_only_observation_and_never_replays(monkeypatch,tmp_path,late):
    peer,path,invoke=run(monkeypatch,tmp_path)
    clock=ReadClock();monkeypatch.setattr(audit,'time',clock);reads=[]
    def pending_then_deny(peer,thread,turn,start,items,prompt,events):
        value=observation(prompt);value['native_turn_record_complete']=False
        reads.append(turn)
        if len(reads)==1 and late=='action':
            (tmp_path/'fixture-events.json').write_text(json.dumps([{'kind':'tool_executed'}]))
        elif len(reads)==2 and late=='failed_receipt':
            value['receipts'][0]['status']='adapter_error'
        elif len(reads)==2 and late=='error':
            value['events'].append({'method':'error','params':{'error':{'message':'failed'}}})
        return value
    monkeypatch.setattr(audit,'_oauth_turn_observation',pending_then_deny)
    invoke()
    row=json.loads(path.read_text())[0]
    assert len(peer.prompts)==1 and len(reads)<=2 and clock.now<5
    assert row['feedback_decision'] in {'late_action_or_unqualified_observation','unqualified_observation_no_feedback'}
    assert 'explicit_feedback_follows' not in row
