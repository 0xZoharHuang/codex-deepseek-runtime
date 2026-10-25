"""The no-external-tools gate separates retired diagnostics from effect evidence."""
from copy import deepcopy
import json

import pytest

from test_native_payload_retry import no_tools


def clean_record():
    events=[
        {'type':'thread.started','thread_id':'11111111-1111-4111-8111-111111111111'},
        {'type':'turn.started'},
        {'type':'item.completed','item':{'id':'item_1','type':'agent_message','text':'FIXTURE_OK'}},
        {'type':'turn.completed','usage':{'input_tokens':1,'cached_input_tokens':0,'cache_write_input_tokens':0,'output_tokens':1,'reasoning_output_tokens':0}},
    ]
    return {'stdout':'\n'.join(json.dumps(event) for event in events)+'\n','stderr':'',
            'exit_code':0,'children':[],'natural_main_exit':True,'main_timed_out':False,
            'remaining_before_cleanup':[],'forced_owned_cleanup':[],'remaining_after_cleanup':[],
            'cleanup_errors':[],'unknown_live':[],'observation_errors':[],
            'uncertain_observations':[]}


def retired_missing_name(record):
    record['children']=[{'pid':3,'birth':'linux:synthetic-boot:100','stable':True,
                         'ownership':'stable_direct_parent_edge'}]
    record['uncertain_observations']=[{'pid':3,'birth':'linux:synthetic-boot:100','birth_after':None,'diagnostic_birth_before':None,
                                      'reason':'identity_changed_during_owned_diagnostics'}]


def test_retired_diagnostic_race_retains_unknown_name_and_requires_complete_native_stream():
    record=clean_record();retired_missing_name(record);before=deepcopy(record)
    proof=no_tools(record)
    assert proof['ended_unknown_metadata']==[
        {'pid':3,'birth':'linux:synthetic-boot:100',
         'classification':'retired_owned_identity_diagnostics_unavailable_name_unknown'}]
    assert record==before and 'name' not in record['children'][0]


def test_permitted_native_plan_bookkeeping_and_failed_no_tool_turn():
    record=clean_record();events=[json.loads(line) for line in record['stdout'].splitlines()]
    events.insert(2,{'type':'item.started','item':{'id':'plan','type':'todo_list','items':[]}})
    events.insert(3,{'type':'item.completed','item':{'id':'plan','type':'todo_list','items':[]}})
    record['stdout']='\n'.join(json.dumps(event) for event in events)+'\n'
    assert no_tools(record)['native_stream']['terminal']=='turn.completed'
    events[-1]={'type':'turn.failed','error':{'message':'synthetic expected 413'}}
    record.update(exit_code=1,stdout='\n'.join(json.dumps(event) for event in events)+'\n')
    assert no_tools(record)['native_stream']['terminal']=='turn.failed'


@pytest.mark.parametrize('change',[
    'command_execution','file_change','mcp_tool_call','web_search','unknown_tool','unknown_event',
    'invalid_json','truncated_json','duplicate_key','missing_terminal','duplicate_terminal',
    'missing_turn_start','duplicate_turn_start','wrong_exit','invalid_thread','blank_line',
    'missing_retirement','wrong_retirement_birth','reused_identity','missing_birth','unproven_edge',
    'unknown_live','observation_error','forced_cleanup','remaining_child',
    'forbidden_name','forbidden_exe','forbidden_argv',
])
def test_no_external_tools_rejects_uncertain_effect_or_retirement_evidence(change):
    record=clean_record();retired_missing_name(record)
    events=[json.loads(line) for line in record['stdout'].splitlines()]
    if change in {'command_execution','file_change','mcp_tool_call','web_search','unknown_tool'}:
        events.insert(2,{'type':'item.started','item':{'id':'external','type':change}})
    elif change=='unknown_event':events.insert(2,{'type':'unrecognized.external'})
    elif change=='missing_terminal':events.pop()
    elif change=='duplicate_terminal':events.append(deepcopy(events[-1]))
    elif change=='missing_turn_start':events.pop(1)
    elif change=='duplicate_turn_start':events.insert(2,deepcopy(events[1]))
    elif change=='wrong_exit':record['exit_code']=1
    elif change=='invalid_thread':events[0]['thread_id']='not-a-thread'
    elif change=='missing_retirement':record['uncertain_observations']=[]
    elif change=='wrong_retirement_birth':record['uncertain_observations'][0]['birth']='different'
    elif change=='reused_identity':record['uncertain_observations'][0]['birth_after']='new-instance'
    elif change=='missing_birth':record['children'][0].pop('birth')
    elif change=='unproven_edge':record['children'][0]['ownership']='pid_guess'
    elif change=='unknown_live':record['unknown_live']=[3]
    elif change=='observation_error':record['observation_errors']=[{'pid':3}]
    elif change=='forced_cleanup':record['forced_owned_cleanup']=[{'pid':3}]
    elif change=='remaining_child':record['remaining_after_cleanup']=[{'pid':3}]
    elif change=='forbidden_name':record['children'][0]['name']='codex-command-runner'
    elif change=='forbidden_exe':record['children'][0]['exe']='/owned/codex-windows-sandbox-setup.exe'
    elif change=='forbidden_argv':record['children'][0]['argv']=['/owned/codex-command-runner']
    record['stdout']='\n'.join(json.dumps(event) for event in events)+'\n'
    if change=='invalid_json':record['stdout']='not json\n'+record['stdout']
    elif change=='truncated_json':record['stdout']+='{'
    elif change=='duplicate_key':record['stdout']=record['stdout'].replace(
        '"type": "turn.completed"','"type": "turn.completed", "type": "turn.completed"')
    elif change=='blank_line':record['stdout']+='\n'
    with pytest.raises((AssertionError,ValueError)):
        no_tools(record)


@pytest.mark.parametrize('change',[
    'usage_missing','usage_extra','usage_bool','usage_float','usage_negative','nan','infinity',
    'message_missing','message_extra','message_not_text','error_without_message',
    'todo_not_list','todo_completed_not_bool','todo_extra_field','event_extra_item',
    'terminal_error_not_object','terminal_error_extra','turn_start_extra',
])
def test_fixed_native_json_schema_rejects_corruption_even_for_allowed_types(change):
    record=clean_record()
    events=[json.loads(line) for line in record['stdout'].splitlines()]
    if change=='usage_missing':events[-1].pop('usage')
    elif change=='usage_extra':events[-1]['usage']['extra']=1
    elif change=='usage_bool':events[-1]['usage']['input_tokens']=True
    elif change=='usage_float':events[-1]['usage']['input_tokens']=1.0
    elif change=='usage_negative':events[-1]['usage']['input_tokens']=-1
    elif change=='nan':events[-1]['usage']['input_tokens']=float('nan')
    elif change=='infinity':events[-1]['usage']['input_tokens']=float('inf')
    elif change=='message_missing':events[2]['item'].pop('text')
    elif change=='message_extra':events[2]['item']['command']='external'
    elif change=='message_not_text':events[2]['item']['text']={}
    elif change=='error_without_message':events[2]['item']={'id':'error','type':'error'}
    elif change in {'todo_not_list','todo_completed_not_bool','todo_extra_field'}:
        item={'id':'plan','type':'todo_list','items':[{'text':'step','completed':False}]}
        if change=='todo_not_list':item['items']={}
        elif change=='todo_completed_not_bool':item['items'][0]['completed']='false'
        else:item['items'][0]['command']='external'
        events[2]['item']=item
    elif change=='event_extra_item':events[-1]['item']={'type':'command_execution'}
    elif change in {'terminal_error_not_object','terminal_error_extra'}:
        events[-1]={'type':'turn.failed','error':{'message':'expected'}}
        record['exit_code']=1
        if change=='terminal_error_not_object':events[-1]['error']='error'
        else:events[-1]['error']['command']='external'
    elif change=='turn_start_extra':events[1]['command']='external'
    record['stdout']='\n'.join(json.dumps(event) for event in events)+'\n'
    with pytest.raises((AssertionError,ValueError)):
        no_tools(record)
