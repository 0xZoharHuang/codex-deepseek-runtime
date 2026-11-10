"""Real-work QA preparation/control failures must remain explicit evidence."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest
import verify_real_work as work


@pytest.fixture
def case(tmp_path, monkeypatch):
    root=tmp_path/'jinja';(root/'work').mkdir(parents=True);(root/'oracle').mkdir()
    (root/'oracle/probe.py').write_text('VALUE=1\n')
    monkeypatch.setattr(work,'workspace_path',lambda root:root/'work')
    monkeypatch.setattr(work,'tool_python',lambda root:root/'fake-python.exe')
    monkeypatch.delenv('CODEX_DEEPSEEK_QA_GIT_SHA256',raising=False)
    return root


def test_git_missing_is_recorded_before_any_version_or_peer(case,monkeypatch):
    original=os.environ.get('PATH')
    monkeypatch.setattr(work.shutil,'which',lambda name:None)
    monkeypatch.setattr(work.subprocess,'run',lambda *a,**k:pytest.fail('No executable should run'))
    result=work.job_git_preflight(case)
    assert not result['passed'] and result['os_exit_code'] is None
    assert result['checked_before_peer'] and result['error_type']=='FileNotFoundError'
    assert Path(result['evidence_path']).is_file() and os.environ.get('PATH')==original


def test_existing_git_is_hashed_versioned_and_optionally_pinned(case,monkeypatch):
    git=case/'git.exe';git.write_bytes(b'fixture Git')
    monkeypatch.setattr(work.shutil,'which',lambda name:str(git))
    monkeypatch.setattr(work.subprocess,'run',lambda args,**kw:subprocess.CompletedProcess(args,0,b'git version fixture\n',b''))
    sha=hashlib.sha256(git.read_bytes()).hexdigest()
    monkeypatch.setenv('CODEX_DEEPSEEK_QA_GIT_SHA256',sha)
    result=work.job_git_preflight(case)
    assert result['passed'] and result['sha256']==sha and result['os_exit_code']==0
    monkeypatch.setenv('CODEX_DEEPSEEK_QA_GIT_SHA256','0'*64)
    assert not work.job_git_preflight(case)['passed']


def test_scenario_missing_git_preserves_failed_results_without_creating_peer(case,monkeypatch):
    release=case/'release';release.mkdir()
    (release/'installation.json').write_text(json.dumps({'source_id':'fixture'}))
    entry=case/'entry.exe';entry.write_bytes(b'fixture entry')
    monkeypatch.setenv('CODEX_DEEPSEEK_TEST_ENTRY',str(entry))
    monkeypatch.setattr(work,'BASE',case.parent)
    monkeypatch.setattr(work,'RUNTIME_HOME',None)
    monkeypatch.setattr(work,'prepare_workspace',lambda root:root/'work')
    monkeypatch.setattr(work,'real_work_tools_binding',lambda *a:{'same_bytes':True})
    monkeypatch.setattr(work,'load_work_controls',lambda *a:{})
    monkeypatch.setattr(work,'real_work_input_hashes',lambda *a:{})
    monkeypatch.setattr(work,'qualified_entry_release',lambda *a:release)
    monkeypatch.setattr(work.shutil,'which',lambda name:None)
    monkeypatch.setattr(work,'Peer',lambda *a,**k:pytest.fail('Peer must not start'))
    class Guard:
        def __init__(self,path):pass
        def lock(self):pytest.fail('Preflight must precede native preparation')
        def restore(self):pass
    monkeypatch.setattr(work,'OracleGuard',Guard)
    result=work.scenario('jinja')
    saved=json.loads((case/'results.json').read_bytes())
    assert not result['passed'] and not saved['passed']
    assert 'Git preflight' in saved['error'] and saved['primary_error']['type']=='RuntimeError'
    assert saved['validation_status']=='not_started_after_preflight_or_operator_stop'


class FakePeer:
    def __init__(self,status='interrupted'):
        self.events=[{'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'other','status':'completed','error':None}}}]
        self.process=SimpleNamespace(poll=lambda:None)
        self.calls=[];self.completions=[];self.status=status
    def call(self,method,params):
        self.calls.append((method,params));return {}
    def complete(self,thread,start,timeout=180,*,expected_status='completed',turn_id=None):
        self.completions.append((thread,turn_id,expected_status,timeout))
        self.events.append({'method':'turn/completed','params':{'threadId':thread,
            'turn':{'id':turn_id,'status':self.status,'error':None}}})
        return []


def test_operator_interrupt_uses_same_peer_and_exact_original_turn(case):
    (case/'operator-interrupt.request').write_bytes(b'operator stop')
    peer=FakePeer();_,result=work.complete_real_work_turn(peer,case,'thread',0,'original',timeout=30)
    assert result['native_confirmed']
    assert peer.calls==[('turn/interrupt',{'threadId':'thread','turnId':'original'})]
    assert peer.completions[0][:3]==('thread','original','interrupted')
    assert 0<=peer.completions[0][3]<=30
    assert Path(result['evidence_path']).is_file()
    assert (case/'operator-interrupt.request').is_file()  # Explicit operator state is not silently discarded.


def test_wrong_or_unconfirmed_native_terminal_cannot_be_an_operator_success(case):
    (case/'operator-interrupt.request').write_text('stop')
    peer=FakePeer(status='completed')
    result=work.operator_interrupt(peer,case,'thread',0,'original',deadline=time.monotonic()+30)
    assert not result['native_confirmed'] and result['error_type']=='RuntimeError'
    assert len(peer.calls)==len(peer.completions)==1


def test_operator_request_is_external_to_writable_workspace(case,monkeypatch):
    (case/'operator-interrupt.request').write_text('stop')
    monkeypatch.setattr(work,'workspace_path',lambda root:root)
    with pytest.raises(ValueError,match='external'):
        work.operator_interrupt(FakePeer(),case,'thread',0,'original',deadline=time.monotonic()+30)


@pytest.mark.parametrize('runner,label,timeout',[
    ('run_test','workspace',180),('run_script','tls-socket',60)])
@pytest.mark.parametrize('kind',['timeout','oserror'])
def test_validation_process_failure_keeps_partial_bytes_without_invented_exit(case,monkeypatch,runner,label,timeout,kind):
    def fail(args,**kw):
        if kind=='timeout':raise subprocess.TimeoutExpired(args,timeout,output=b'\xbe partial stdout',stderr=b'partial stderr')
        raise OSError(2,'fixture executable unavailable')
    monkeypatch.setattr(work.subprocess,'run',fail)
    result=(work.run_test(case,label,['-q','tests'],case/'work') if runner=='run_test'
            else work.run_script(case,label,'tls_socket_regression.py'))
    assert result['exit'] is None and result['os_exit_code'] is None
    assert result['timed_out']==(kind=='timeout')
    assert result['source_imports']['passed'] is False
    if kind=='timeout':
        assert Path(result['raw_streams']['stdout']['path']).read_bytes()==b'\xbe partial stdout'
        assert result['timeout_seconds']==timeout and '\ufffd' in result['partial_stdout']


def fake_validation(case,monkeypatch,*,xml=b'<testsuites><testsuite><testcase classname="tests.real" name="test_one"/></testsuite></testsuites>',imports=b'{"passed":true}'):
    seen={}
    def run(args,**kw):
        seen.update(args=args,kwargs=kw)
        Path(args[args.index('--receipt')+1]).write_bytes(imports)
        junit=next(value.split('=',1)[1] for value in args if value.startswith('--junitxml='))
        Path(junit).write_bytes(xml)
        return subprocess.CompletedProcess(args,0,b'\xbe actual stdout',b'actual stderr')
    monkeypatch.setattr(work.subprocess,'run',run)
    return seen


@pytest.mark.parametrize('label,expected_root',[
    ('workspace','work'),('upstream-heldout','oracle'),('independent-cancellation','.')])
def test_pytest_roots_private_temps_and_raw_capture_are_explicit(case,monkeypatch,label,expected_root):
    seen=fake_validation(case,monkeypatch)
    result=work.run_test(case,label,['-q','tests'],case/'work')
    assert result['exit']==result['os_exit_code']==0
    assert '--rootdir='+str(case/expected_root if expected_root!='.' else case) in seen['args']
    assert any(arg.startswith('--basetemp='+str(case/'validation-temp'/label)) for arg in seen['args'])
    env=seen['kwargs']['env']
    assert env['TEMP']==env['TMP']==env['TMPDIR']
    assert Path(env['TEMP']).is_relative_to(case)
    assert 'text' not in seen['kwargs'] and 'encoding' not in seen['kwargs']
    assert Path(result['raw_streams']['stdout']['path']).read_bytes()==b'\xbe actual stdout'
    assert '\ufffd' in result['tail']


@pytest.mark.parametrize('xml,expected_type',[
    (b'<testsuite><testcase name="test_without_class"/></testsuite>','ValueError'),
    (b'<testsuite><testcase','ParseError')])
def test_invalid_pytest_identity_or_xml_is_failed_without_reconstructed_case_ids(case,monkeypatch,xml,expected_type):
    fake_validation(case,monkeypatch,xml=xml)
    result=work.run_test(case,'workspace',[],case/'work')
    proof=result['pytest_execution']
    assert result['exit']==1 and result['os_exit_code']==0
    assert proof['status']=='invalid' and proof['error_type']==expected_type and 'cases' not in proof
    assert proof['junit_sha256']==hashlib.sha256(xml).hexdigest()
    assert Path(proof['junit_path']).read_bytes()==xml


def test_malformed_imports_and_parser_fault_do_not_erase_primary_checkpoint(case,monkeypatch):
    primary={'type':'RuntimeError','error':'first environment failure'}
    report={'passed':True,'error':primary['error'],'primary_error':primary,'validation':[{'suite':'old','exit':0}]}
    work.validation_checkpoint(case,report)
    fake_validation(case,monkeypatch,imports=b'{broken')
    result=work.run_test(case,'workspace',[],case/'work')
    assert result['exit']==1 and result['source_imports']['passed'] is False
    assert result['source_imports']['status']=='invalid_or_missing'
    saved=json.loads((case/'results.json').read_bytes())
    assert saved['passed'] is False and saved['primary_error']==primary and saved['error']==primary['error']
    assert saved['validation']==[] and saved['validation_status']=='not_completed'


def test_unreadable_xml_hash_cannot_raise_a_secondary_validation_exception(case,monkeypatch):
    import acceptance_assertions as checks
    fake_validation(case,monkeypatch)
    def denied(*args,**kwargs):
        raise PermissionError('fixture XML read is denied')
    monkeypatch.setattr(checks,'pytest_execution_evidence',denied)
    monkeypatch.setattr(work,'file_sha',denied)
    result=work.run_test(case,'workspace',[],case/'work')
    assert result['exit']==1 and result['os_exit_code']==0
    assert result['pytest_execution']['error_type']=='PermissionError'
    assert result['pytest_execution']['junit_sha256'] is None


@pytest.mark.parametrize('flag,value',[('protocol_error','fixture protocol error'),('exited',True)])
def test_operator_aware_wait_preserves_native_protocol_and_eof_failures(case,monkeypatch,flag,value):
    peer=FakePeer();peer.events=[];setattr(peer,flag,value)
    monkeypatch.setattr(work.time,'sleep',lambda delay:pytest.fail('Native failure must not wait for the wall deadline'))
    with pytest.raises(RuntimeError):
        work.complete_real_work_turn(peer,case,'thread',0,'original',timeout=30)
    assert not peer.calls and not peer.completions


def test_error_free_terminal_still_wins_over_subsequent_eof(case):
    peer=FakePeer(status='completed');peer.exited=True
    peer.events=[{'method':'turn/completed','params':{'threadId':'thread',
        'turn':{'id':'original','status':'completed','error':None}}}]
    _,interruption=work.complete_real_work_turn(peer,case,'thread',0,'original',timeout=30)
    assert interruption is None and peer.completions[0][:3]==('thread','original','completed')
