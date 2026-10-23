from copy import deepcopy
import xml.etree.ElementTree as ET
import pytest
from acceptance_assertions import pytest_execution_evidence,real_work_pytest_executed

def xml(tmp_path, outcomes=('passed','passed'), backends=('asyncio','trio')):
    root=ET.Element('testsuites');suite=ET.SubElement(root,'testsuite')
    for backend,outcome in zip(backends,outcomes):
        case=ET.SubElement(suite,'testcase',classname='cancellation_regression',
                           name=f'test_cancel_during_expired_connection_close_releases_queue[{backend}]')
        if outcome!='passed':ET.SubElement(case,outcome)
    path=tmp_path/'actual.junit.xml';path.write_bytes(ET.tostring(root))
    return path

def test_real_two_backend_pass_is_recorded_and_hash_bound(tmp_path):
    path=xml(tmp_path);proof=pytest_execution_evidence(path)
    assert proof['path']==str(path) and len(proof['sha256'])==64
    assert proof['counts']=={'passed':2,'failure':0,'error':0,'skipped':0}
    assert real_work_pytest_executed({'suite':'independent-cancellation','pytest_execution':proof})

@pytest.mark.parametrize('outcomes,backends',[
    (('skipped',),('unparametrized',)),
    (('skipped','skipped'),('asyncio','trio')),
    (('passed','skipped'),('asyncio','trio')),
    (('passed','error'),('asyncio','trio')),
    (('passed',),('asyncio',)),
    (('passed','passed'),('asyncio','other')),
    ((),()),
])
def test_exit_zero_or_partial_backends_cannot_substitute_for_execution(tmp_path,outcomes,backends):
    proof=pytest_execution_evidence(xml(tmp_path,outcomes,backends))
    assert not real_work_pytest_executed({'suite':'independent-cancellation','exit':0,'pytest_execution':proof})

def test_executed_failing_assertion_is_preserved_as_application_failure(tmp_path):
    proof=pytest_execution_evidence(xml(tmp_path,('passed','failure')))
    assert real_work_pytest_executed({'suite':'independent-cancellation','pytest_execution':proof})
    assert proof['counts']['failure']==1

@pytest.mark.parametrize('change',['missing','counts','boolean_counts','wrong_name','wrong_class','duplicate'])
def test_claimed_execution_requires_real_case_identities_and_consistent_counts(tmp_path,change):
    proof=pytest_execution_evidence(xml(tmp_path))
    if change=='missing':proof={}
    elif change=='counts':proof['counts']['passed']=3
    elif change=='boolean_counts':proof['counts']['error']=False
    elif change=='wrong_name':proof['cases'][1]['name']='test_something_else[trio]'
    elif change=='wrong_class':proof['cases'][1]['classname']='other_fixture'
    elif change=='duplicate':proof['cases'][1]=deepcopy(proof['cases'][0])
    assert not real_work_pytest_executed({'suite':'independent-cancellation','pytest_execution':proof})

def test_duplicate_raw_testcase_cannot_hide_missing_backend(tmp_path):
    with pytest.raises(ValueError,match='Duplicate'):
        pytest_execution_evidence(xml(tmp_path,backends=('asyncio','asyncio')))

def test_upstream_suites_may_have_declared_skips_but_cannot_all_skip(tmp_path):
    proof=pytest_execution_evidence(xml(tmp_path,('passed','skipped')))
    assert real_work_pytest_executed({'suite':'workspace','pytest_execution':proof})
    proof=pytest_execution_evidence(xml(tmp_path,('skipped','skipped')))
    assert not real_work_pytest_executed({'suite':'workspace','pytest_execution':proof})

def test_script_suites_do_not_claim_to_be_pytest_runs():
    assert real_work_pytest_executed({'suite':'real-tcp-0'})
