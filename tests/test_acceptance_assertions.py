"""Adversarial evidence: file effects, stale turns and quoted tasks do not prove MCP/delivery."""
import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('acceptance_assertions',Path(__file__).resolve().parents[1]/'acceptance_assertions.py')
checks=importlib.util.module_from_spec(spec);spec.loader.exec_module(checks)


def chain():
    def call(tool,args,result):return {'type':'mcpToolCall','server':'codex_web','tool':tool,'arguments':args,'status':'completed','result':{'structuredContent':result}}
    return [call('open',{'url_or_ref':'https://example.com/start'},{'url':'https://example.com/start','source_sha256':'a'*64,'ref_id':'page_a','links':[{'id':1,'url':'https://example.com/next'}]}),
            call('find',{'ref_id':'page_a','pattern':'checkpoint'},{'ref_id':'page_a','match_count':1}),
            call('click',{'ref_id':'page_a','link_id':1},{'url':'https://example.com/next','source_sha256':'b'*64,'content':'L1: NONCE','followed_from':{'ref_id':'page_a','link_id':1}})]


def valid(items):return checks.browser_chain(items,'codex_web','https://example.com/start','checkpoint','NONCE')


@pytest.mark.parametrize('change',['none','missing_find','wrong_order','failed_find','empty_find','other_reference','wrong_link'])
def test_network_hits_and_file_content_cannot_substitute_for_a_native_browser_chain(change):
    items=chain();assert valid(items)
    if change=='none':items=[]
    elif change=='missing_find':items.pop(1)
    elif change=='wrong_order':items.reverse()
    elif change=='failed_find':items[1]['result']['isError']=True
    elif change=='empty_find':items[1]['result']['structuredContent']['match_count']=0
    elif change=='other_reference':items[1]['arguments']['ref_id']='unrelated'
    elif change=='wrong_link':items[2]['result']['structuredContent']['url']='https://example.com/unrelated'
    assert not valid(items)


@pytest.mark.parametrize('change',['old_turn','other_thread','tool_error','no_matches'])
def test_new_real_task_requires_current_parent_turn_successful_browsing(change):
    events=[{'method':'item/completed','params':{'threadId':'parent','item':item}} for item in chain()]
    assert checks.current_browser_evidence(events,'parent')['passed']
    start=0
    if change=='old_turn':start=len(events)
    elif change=='other_thread':events[1]['params']['threadId']='reviewer'
    elif change=='tool_error':events[1]['params']['item']['result']['isError']=True
    elif change=='no_matches':events[1]['params']['item']['result']['structuredContent']['match_count']=0
    assert not checks.current_browser_evidence(events,'parent',start)['passed']


def test_parent_delivery_requires_an_actual_final_answer_from_the_expected_child():
    header='Message Type: FINAL_ANSWER\nTask name: /root\nSender: /root/arithmetic\nPayload:\n'
    def request(value):return {'input':[{'type':'function_call_output','call_id':'peer_1','output':[{'type':'input_text','text':value}]}]}
    markers=['CHILD-437','FOLLOWUP-OK']
    for text in ['The task is: reply CHILD-437',header.replace('/root/arithmetic','/root/other')+'CHILD-437',header+'Please reply CHILD-437']:
        assert not any(checks.parent_deliveries([request(text)],'/root/arithmetic',markers).values())
    assert checks.parent_deliveries([request(header+'CHILD-437')],'/root/arithmetic',markers)=={'CHILD-437':['peer_1'],'FOLLOWUP-OK':[]}


def test_native_text_content_result_is_verified_and_not_confused_with_a_successful_error():
    items=chain()
    for item in items:
        value=item['result']['structuredContent']
        item['result']={'structuredContent':None,'content':[{'type':'text','text':json.dumps(value)}]}
    assert valid(items)
    items[1]['result']['content'][0]['text']='Document operation failed: page not found'
    assert not valid(items)


def test_task_patch_replays_staged_unstaged_and_new_binary_files_without_index_mutation(tmp_path):
    import subprocess
    work=tmp_path/'work';work.mkdir();replay=tmp_path/'replay';replay.mkdir()
    subprocess.run(['git','init','-q'],cwd=replay,check=True)
    def git(*args):return subprocess.check_output(['git',*args],cwd=work)
    git('init','-q');(work/'tracked.py').write_text('before\n');(replay/'tracked.py').write_text('before\n')
    git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','--no-gpg-sign','-qm','base')
    (work/'tracked.py').write_text('staged\n');git('add','tracked.py');(work/'tracked.py').write_text('final\n')
    (work/'new file.bin').write_bytes(b'\0new\xffdata');before=git('diff','--cached','--binary')
    patch=checks.complete_work_patch(work)
    assert git('diff','--cached','--binary')==before
    subprocess.run(['git','apply','-'],input=patch,cwd=replay,check=True)
    assert (replay/'tracked.py').read_bytes()==(work/'tracked.py').read_bytes()
    assert (replay/'new file.bin').read_bytes()==(work/'new file.bin').read_bytes()
