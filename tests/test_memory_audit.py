"""Runtime memory evidence must survive paraphrases and reject missing effects."""
import copy
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
import verify_memory_audit as audit


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source_work(tmp_path):
    work=tmp_path/'work';work.mkdir()
    ledger=work/'ledger.py';ledger.write_text('def credit(db,key,amount):\n    db.execute("INSERT INTO credits VALUES (?,?)",(key,amount))\n    db.commit()\n')
    ledger_hash=sha(ledger)
    test=work/'test_ledger.py'
    test.write_text('import sqlite3\nfrom ledger import credit\ndef test_repeat():\n    db=sqlite3.connect(":memory:")\n    db.execute("CREATE TABLE credits (key TEXT PRIMARY KEY, amount INTEGER NOT NULL)")\n    credit(db,"same",125)\n    credit(db,"same",125)\n    assert db.execute("SELECT sum(amount) FROM credits").fetchone()[0]==125\n')
    verifier=tmp_path/'verify_ledger_fixture.py'
    verifier.write_text('import sys\nsys.path.insert(0,sys.argv[1])\nfrom test_ledger import test_repeat\ntest_repeat()\nprint("NATIVE_LEDGER_VERIFIED")\n')
    fixtures={test:sha(test),verifier:sha(verifier)}
    ledger.write_text('def credit(db,key,amount):\n    db.execute("INSERT INTO credits VALUES (?,?) ON CONFLICT(key) DO NOTHING",(key,amount))\n    db.commit()\n')
    command=[sys.executable,'-I',str(verifier),str(work)]
    verified=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
    assert verified.returncode==0
    items=[{'type':'commandExecution','id':'verify','status':'completed','exitCode':0,
            'command':audit.shell_command(command),'aggregatedOutput':verified.stdout},
           {'type':'fileChange','id':'edit','status':'completed','changes':[{'path':str(ledger)}]},
           {'type':'agentMessage','text':'Repeated identical credits now leave the original row intact on retries.'}]
    return {'items':items,'verified':verified,'ledger':ledger,'ledger_hash':ledger_hash,
            'fixtures':fixtures,'interpreter':sys.executable,'verifier':verifier,'nonce':'MIGRATION_fixture'}


@pytest.mark.parametrize('answer',['Repeated identical credits now leave the original row intact on retries.','重试成功，账目保持不变。',''])
def test_real_ledger_effects_pass_independent_of_answer_wording(source_work,answer):
    source_work['items'][-1]['text']=answer
    evidence=audit.source_work_evidence(**source_work)
    assert evidence['passed']
    assert evidence['verification_exit_code']==0 and evidence['verification_fixture_unchanged']
    assert evidence['ledger_changed'] and evidence['native_verification_commands']
    assert evidence['native_completed_file_changes']
    assert evidence['model_quality_observation']=={
        'user_identifier_retained':False,'integer_word_present':False,
        'idempotency_word_present':False,'outside_runtime_pass_criterion':True}


@pytest.mark.parametrize('failure',['oracle_failed','oracle_marker_missing','native_command_missing','native_command_failed',
    'native_command_incomplete','native_marker_missing','different_interpreter','different_fixture','different_workspace',
    'changed_test','deleted_verifier','unchanged_ledger','deleted_ledger'])
def test_good_sounding_answer_cannot_replace_runtime_evidence(source_work,failure):
    source_work['items'][-1]['text']='MIGRATION_fixture uses integer cents and idempotent keys. NATIVE_LEDGER_VERIFIED'
    command=source_work['items'][0]
    if failure=='oracle_failed':source_work['verified'].returncode=1
    elif failure=='oracle_marker_missing':source_work['verified'].stdout=''
    elif failure=='native_command_missing':source_work['items'].pop(0)
    elif failure=='native_command_failed':command['exitCode']=1
    elif failure=='native_command_incomplete':command['status']='inProgress'
    elif failure=='native_marker_missing':command['aggregatedOutput']=''
    elif failure=='different_interpreter':source_work['interpreter']='/other/python'
    elif failure=='different_fixture':source_work['verifier']=source_work['verifier'].parent/'other.py'
    elif failure=='different_workspace':command['command']=audit.shell_command([sys.executable,'-I',str(source_work['verifier']),'/other/work'])
    elif failure=='changed_test':next(iter(source_work['fixtures'])).write_text('def test_repeat(): pass\n')
    elif failure=='deleted_verifier':source_work['verifier'].unlink()
    elif failure=='unchanged_ledger':source_work['ledger_hash']=sha(source_work['ledger'])
    elif failure=='deleted_ledger':source_work['ledger'].unlink()
    assert not audit.source_work_evidence(**source_work)['passed']


def test_fixture_integrity_rejects_tampering_even_when_modified_oracle_succeeds(source_work):
    source_work['verifier'].write_text('print("NATIVE_LEDGER_VERIFIED")\n')
    source_work['verified']=subprocess.run([sys.executable,'-I',str(source_work['verifier']),str(source_work['ledger'].parent)],capture_output=True,text=True)
    assert source_work['verified'].returncode==0
    evidence=audit.source_work_evidence(**source_work)
    assert not evidence['passed'] and not evidence['verification_fixture_unchanged']


def test_real_oracle_rejects_changed_but_incorrect_ledger_despite_claimed_native_success(source_work):
    source_work['ledger'].write_text('def credit(db,key,amount):\n    pass\n')
    source_work['verified']=subprocess.run([sys.executable,'-I',str(source_work['verifier']),str(source_work['ledger'].parent)],capture_output=True,text=True)
    assert source_work['verified'].returncode!=0
    evidence=audit.source_work_evidence(**source_work)
    assert evidence['ledger_changed'] and evidence['verification_fixture_unchanged']
    assert not evidence['passed']


def test_native_shell_edits_do_not_require_the_model_to_choose_a_file_change_tool(source_work):
    source_work['items']=[item for item in source_work['items'] if item.get('type')!='fileChange']
    evidence=audit.source_work_evidence(**source_work)
    assert evidence['passed'] and not evidence['native_completed_file_changes']


@pytest.fixture
def prefixed_verification(tmp_path):
    workspace=tmp_path/'ledger project';workspace.mkdir()
    ledger='def credit(db, key, amount):\n    pass\n'
    (workspace/'ledger.py').write_text(ledger,encoding='utf-8')
    interpreter='/native/controller/bin/python'
    verifier=tmp_path/'verify_ledger_fixture.py'
    exact=shlex.join([interpreter,'-I',str(verifier),str(workspace)])
    item={'type':'commandExecution','status':'completed','exitCode':0,'cwd':str(workspace),
          'command':shlex.join(['/bin/bash','-lc','cat ledger.py && '+exact]),
          'aggregatedOutput':ledger+'NATIVE_LEDGER_VERIFIED\n'}
    return item,interpreter,verifier,workspace,exact


@pytest.mark.parametrize('read_path',['ledger.py','./ledger.py','absolute'])
def test_constrained_ledger_read_then_exact_verifier_accepts_actual_file_prefix(prefixed_verification,read_path):
    item,python,verifier,work,exact=prefixed_verification
    if read_path=='absolute':read_path=str(work/'ledger.py')
    item['command']=shlex.join(['/bin/bash','-lc',shlex.join(['cat',read_path])+' && '+exact])
    assert audit.native_verification_commands([item],python,verifier,work)==[item]


@pytest.mark.parametrize('script',[
    'echo {exact}', 'printf NATIVE_LEDGER_VERIFIED # {exact}',
    'false || {exact}', 'if false; then {exact}; fi; echo NATIVE_LEDGER_VERIFIED',
    '{exact} | cat', '{exact} > result.txt', '{exact}; echo NATIVE_LEDGER_VERIFIED',
    'cat ledger.py && {exact} && printf NATIVE_LEDGER_VERIFIED',
    'cat ledger.py "&&" {exact}', 'cat ledger.py || {exact}',
    'cat ../ledger.py && {exact}', 'cat ledger.py && false && {exact}',
    '$(echo {exact})', '{exact}\nprintf NATIVE_LEDGER_VERIFIED',
    'cat ledger.py && {exact} # extra', 'cat ledger.py && {exact} --extra',
])
def test_shell_spoofs_and_unconstrained_prefixes_cannot_claim_native_verification(prefixed_verification,script):
    item,python,verifier,work,exact=prefixed_verification
    item['command']=shlex.join(['/bin/bash','-lc',script.format(exact=exact)])
    assert not audit.native_verification_commands([item],python,verifier,work)


@pytest.mark.parametrize('failure',['wrong_cwd','missing_ledger','changed_prefix','marker_substring','extra_tail','marker_not_separate'])
def test_read_prefix_requires_real_matching_contents_and_final_independent_marker(prefixed_verification,failure):
    item,python,verifier,work,exact=prefixed_verification
    if failure=='wrong_cwd':item['cwd']=str(work.parent)
    elif failure=='missing_ledger':(work/'ledger.py').unlink()
    elif failure=='changed_prefix':item['aggregatedOutput']='unrelated text\nNATIVE_LEDGER_VERIFIED\n'
    elif failure=='marker_substring':item['aggregatedOutput']+='prefixNATIVE_LEDGER_VERIFIEDsuffix\n'
    elif failure=='extra_tail':item['aggregatedOutput']+='unrelated tail\n'
    elif failure=='marker_not_separate':
        (work/'ledger.py').write_text('no newline',encoding='utf-8')
        item['aggregatedOutput']='no newlineNATIVE_LEDGER_VERIFIED\n'
    assert not audit.native_verification_commands([item],python,verifier,work)


@pytest.mark.parametrize('wrapped',[False,True])
def test_exact_windows_host_verifier_remains_supported_without_shell_evaluation(wrapped):
    python=r'D:\QA folder\python.exe';verifier=r'D:\QA folder\verify_ledger_fixture.py';work=r'D:\QA folder\work'
    command='& '+' '.join("'"+value+"'" for value in [python,'-I',verifier,work])
    if wrapped:
        command='"C:\\\\Windows\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe" -Command "'+command.replace('\\','\\\\')+'"'
    item={'type':'commandExecution','status':'completed','exitCode':0,'command':command,'aggregatedOutput':'NATIVE_LEDGER_VERIFIED\r\n'}
    assert audit.native_verification_commands([item],python,verifier,work)==[item]
    for spoof in ['echo '+command,command+'; Write-Output NATIVE_LEDGER_VERIFIED',command+' | Write-Output','if ($false) { '+command+' }']:
        assert not audit.native_verification_commands([{**item,'command':spoof}],python,verifier,work)


def message(role,text):
    return {'type':'message','role':role,'content':[{'type':'input_text','text':text}]}


def request(tmp_path,body):
    path=tmp_path/'fixture-request.json';path.write_text(json.dumps(body),encoding='utf-8')
    return path


def metadata(thread,**fields):
    return {'thread_id':thread,'x-codex-turn-metadata':json.dumps({'thread_id':thread,**fields})}


def test_sample_requires_native_fork_identity_and_loaded_source_history(tmp_path):
    body={'client_metadata':metadata('sample',thread_source='thread_sample',history_initialization='warm_fork',forked_from_thread_id='source'),
          'input':[message('user','Original project MIGRATION_fixture'),
                   {'type':'function_call_output','call_id':'verify','output':'NATIVE_LEDGER_VERIFIED'},
                   message('user','Recall the project') ]}
    def evidence(candidate):return audit.sample_context_evidence([request(tmp_path,candidate)],'source','Original project MIGRATION_fixture','Recall the project')
    assert evidence(body)
    for field,value in [('forked_from_thread_id','unrelated'),('thread_source','normal'),('history_initialization','new'),('thread_id','wrong')]:
        bad=copy.deepcopy(body);meta=json.loads(bad['client_metadata']['x-codex-turn-metadata']);meta[field]=value
        bad['client_metadata']['x-codex-turn-metadata']=json.dumps(meta)
        assert not evidence(bad),field
    for index in range(3):
        bad=copy.deepcopy(body);bad['input'].pop(index)
        assert not evidence(bad),index
    bad=copy.deepcopy(body);bad['input'][1]=message('assistant','NATIVE_LEDGER_VERIFIED')
    assert not evidence(bad)


@pytest.mark.parametrize('role',['system','developer'])
def test_cold_read_binds_exact_nonempty_artifact_to_new_thread_guidance(tmp_path,role):
    summary='项目使用整分记账，重复请求保持原账目。'
    body={'client_metadata':metadata('reader',history_initialization='new'),
          'input':[message(role,'Native memory guidance\n'+summary),message('user','Read memory')]}
    path=request(tmp_path,body)
    evidence=audit.cold_memory_evidence([path],'reader',{'home/memories/memory_summary.md':summary})
    assert len(evidence)==1
    assert evidence[0]['artifact_sha256']==hashlib.sha256(summary.encode()).hexdigest()
    assert evidence[0]['request_sha256']==sha(path)


@pytest.mark.parametrize('failure',['assistant_echo','user_echo','tool_echo','other_thread','resume','missing_metadata','empty_artifact','different_artifact'])
def test_cold_read_rejects_prose_echoes_and_wrong_persistence_context(tmp_path,failure):
    summary='integer money and idempotency conventions'
    body={'client_metadata':metadata('reader',history_initialization='new'),'input':[message('system',summary)]}
    snapshots={'home/memories/memory_summary.md':summary}
    if failure=='assistant_echo':body['input'][0]['role']='assistant'
    elif failure=='user_echo':body['input'][0]['role']='user'
    elif failure=='tool_echo':body['input']=[{'type':'function_call_output','output':summary}]
    elif failure=='other_thread':body['client_metadata']=metadata('other',history_initialization='new')
    elif failure=='resume':body['client_metadata']=metadata('reader',history_initialization='resume')
    elif failure=='missing_metadata':body['client_metadata'].pop('x-codex-turn-metadata')
    elif failure=='empty_artifact':snapshots={'home/memories/memory_summary.md':'  '}
    elif failure=='different_artifact':snapshots={'home/memories/memory_summary.md':'a different generated summary'}
    assert not audit.cold_memory_evidence([request(tmp_path,body)],'reader',snapshots)
