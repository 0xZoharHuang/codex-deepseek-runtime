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
    events=[{'method':'item/completed','params':{'threadId':'parent','turnId':'current','item':item}} for item in chain()]
    assert checks.current_browser_evidence(events,'parent',turn='current')['passed']
    start=0
    if change=='old_turn':start=len(events)
    elif change=='other_thread':events[1]['params']['threadId']='reviewer'
    elif change=='tool_error':events[1]['params']['item']['result']['isError']=True
    elif change=='no_matches':events[1]['params']['item']['result']['structuredContent']['match_count']=0
    assert not checks.current_browser_evidence(events,'parent',start,turn='current')['passed']


@pytest.mark.parametrize('change',['prior_turn','wrong_find_ref','wrong_find_receipt','wrong_click_ref',
                                   'unknown_link_id','wrong_edge_receipt','wrong_order','same_document'])
def test_final_turn_requires_ordered_linked_browser_operations(change):
    items=chain();events=[{'method':'item/completed','params':{'threadId':'parent','turnId':'current','item':item}} for item in items]
    assert checks.current_browser_evidence(events,'parent',turn='current')['passed']
    if change=='prior_turn':
        for event in events:event['params']['turnId']='previous'
    elif change=='wrong_find_ref':items[1]['arguments']['ref_id']='unrelated'
    elif change=='wrong_find_receipt':items[1]['result']['structuredContent']['ref_id']='unrelated'
    elif change=='wrong_click_ref':items[2]['arguments']['ref_id']='unrelated'
    elif change=='unknown_link_id':items[2]['arguments']['link_id']=99
    elif change=='wrong_edge_receipt':items[2]['result']['structuredContent']['followed_from']['link_id']=99
    elif change=='wrong_order':events.reverse()
    elif change=='same_document':items[2]['result']['structuredContent']['url']='https://example.com/start#section'
    assert not checks.current_browser_evidence(events,'parent',turn='current')['passed']


def test_final_turn_must_be_explicit_and_link_receipt_allows_http_redirects():
    items=chain();items[2]['result']['structuredContent']['url']='https://redirected.example/final'
    events=[{'method':'item/completed','params':{'threadId':'parent','turnId':'current','item':item}} for item in items]
    assert not checks.current_browser_evidence(events,'parent')['passed']
    proof=checks.current_browser_evidence(events,'parent',turn='current')
    assert proof['passed'] and proof['turn_id']=='current'
    assert proof['linked_chains'][0]['link_url']=='https://example.com/next'
    assert proof['linked_chains'][0]['target_url']=='https://redirected.example/final'


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


def test_windows_complex_task_requires_native_interpreter_and_no_os_bridge():
    def event(command):return {'method':'item/completed','params':{'item':{'type':'commandExecution','command':command,'exitCode':0}}}
    python='D:/Fixture/tools/Scripts/python.exe'
    good=event('& "D:\\Fixture\\tools\\Scripts\\python.exe" -m pytest')
    assert checks.native_host_execution([good],python,'win32')['passed']
    assert checks.native_host_execution([event(good['params']['item']['command'].replace('\\','\\\\'))],python,'win32')['passed']
    failed=event(good['params']['item']['command']);failed['params']['item']['exitCode']=1
    assert not checks.native_host_execution([failed],python,'win32')['passed']
    assert not checks.native_host_execution([],python,'win32')['passed']
    assert not checks.native_host_execution([event('python -m pytest')],python,'win32')['passed']
    assert not checks.native_host_execution([good,event('wsl.exe python3 -m pytest')],python,'win32')['passed']


@pytest.mark.parametrize('line_ending',[b'\n',b'\r\n'])
def test_task_patch_replays_staged_unstaged_and_new_binary_files_without_index_mutation(tmp_path,line_ending):
    import subprocess
    work=tmp_path/'work';work.mkdir();replay=tmp_path/'replay';replay.mkdir()
    subprocess.run(['git','init','-q'],cwd=replay,check=True)
    def git(*args):return subprocess.check_output(['git',*args],cwd=work)
    git('init','-q')
    # Test exact patch bytes under an explicit repository policy. Host Git
    # autocrlf settings and Python's platform newline conversion are not inputs.
    for root in (work,replay):
        (root/'.gitattributes').write_bytes(b'* -text\n')
        (root/'tracked.py').write_bytes(b'before'+line_ending)
    git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','--no-gpg-sign','-qm','base')
    (work/'tracked.py').write_bytes(b'staged'+line_ending);git('add','tracked.py');(work/'tracked.py').write_bytes(b'final'+line_ending)
    (work/'new file.bin').write_bytes(b'\0new\xffdata');before=git('diff','--cached','--binary')
    patch=checks.complete_work_patch(work)
    assert git('diff','--cached','--binary')==before
    subprocess.run(['git','apply','-'],input=patch,cwd=replay,check=True)
    assert (replay/'tracked.py').read_bytes()==(work/'tracked.py').read_bytes()
    assert (replay/'new file.bin').read_bytes()==(work/'new file.bin').read_bytes()


def test_oracle_permissions_really_deny_reads_and_restore_content(tmp_path):
    oracle=tmp_path/'oracle';oracle.mkdir();file=oracle/'source.py';file.write_text('SENTINEL = 42\n')
    nested=oracle/'nested';nested.mkdir();child=nested/'nested.py';child.write_text('NESTED = 43\n')
    guard=checks.OracleGuard(oracle)
    try:
        guard.lock()
        with pytest.raises(PermissionError):file.read_bytes()
        with pytest.raises(PermissionError):child.read_bytes()
    finally:guard.restore()
    assert file.read_text()=='SENTINEL = 42\n'
    assert child.read_text()=='NESTED = 43\n'
    assert guard.evidence['read_probe_denied'] and guard.evidence['restored_probe_unchanged']


@pytest.mark.parametrize('missing',['recovery','oracle_denial','oracle_restore','suite','suite_failed','review','browser','driver_changed','driver_missing','source_imports'])
def test_complex_task_cannot_pass_with_missing_runtime_evidence(missing):
    report={'case':'httpcore','restarted':True,'fresh_review':True,'turn_status':'completed','diff_check':0,
            'oracle_guard':dict.fromkeys(['source_unchanged','read_probe_denied','restored_probe_unchanged'],True),
            'web_research':{'required':True,'current_turn':{'passed':True}},
            'validation':[{'suite':name,'exit':0,'source_imports':{'passed':True}} for name in ['workspace','upstream-heldout','independent-cancellation','real-tcp-0','real-tcp-1','real-tcp-2','tls-socket']]}
    controls=dict.fromkeys(checks.real_work_input_names('httpcore'),'0'*64)
    report['fixture_inputs']={'before':controls.copy(),'after':controls.copy(),'unchanged':True}
    assert checks.real_work_passed(report)
    if missing=='recovery':report['restarted']=False
    elif missing=='oracle_denial':report['oracle_guard'].pop('read_probe_denied')
    elif missing=='oracle_restore':report['oracle_guard']['restored_probe_unchanged']=False
    elif missing=='suite':report['validation'].pop()
    elif missing=='suite_failed':report['validation'][-1]['exit']=1
    elif missing=='source_imports':report['validation'][-1].pop('source_imports')
    elif missing=='review':report['fresh_review']=False
    elif missing=='browser':report['web_research']['current_turn']['passed']=False
    elif missing=='driver_changed':report['fixture_inputs']['after']['network_stress.py']='1'*64
    elif missing=='driver_missing':
        report['fixture_inputs']['before'].pop('network_stress.py');report['fixture_inputs']['after'].pop('network_stress.py')
    assert not checks.real_work_passed(report)
