from copy import deepcopy
import xml.etree.ElementTree as ET
import pytest
from acceptance_assertions import (pytest_execution_evidence,real_work_pytest_executed,
    pytest_inventory_baseline,pytest_inventory_coverage)

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

def test_upstream_suites_require_protected_original_inventory_and_declared_skips(tmp_path):
    proof=pytest_execution_evidence(xml(tmp_path,('passed','skipped')))
    allow=[{'classname':proof['cases'][1]['classname'],'name':proof['cases'][1]['name'],'skip_type':None}]
    baseline=pytest_inventory_baseline(proof,suite='workspace',source_sha256='a'*64,tools_sha256='b'*64,allowed_skips=allow)
    suite={'suite':'workspace','pytest_execution':proof}
    assert not real_work_pytest_executed(suite)
    assert real_work_pytest_executed(suite,baseline=baseline)
    proof=pytest_execution_evidence(xml(tmp_path,('skipped','skipped')))
    assert not real_work_pytest_executed({'suite':'workspace','pytest_execution':proof},baseline=baseline)


def test_script_suites_do_not_claim_to_be_pytest_runs():
    assert real_work_pytest_executed({'suite':'real-tcp-0'})


def inventory_proof(names=('test_first','test_second'),outcomes=None):
    outcomes=outcomes or ['passed']*len(names)
    cases=[{'classname':'tests.original','name':name,'outcome':outcome} for name,outcome in zip(names,outcomes)]
    return {'sha256':'c'*64,'cases':cases,'counts':{kind:sum(c['outcome']==kind for c in cases)
        for kind in ('passed','failure','error','skipped')}}


def protected(proof=None,**options):
    return pytest_inventory_baseline(proof or inventory_proof(),suite='workspace',
        source_sha256='a'*64,tools_sha256='b'*64,**options)


@pytest.mark.parametrize('change',['one_survivor','renamed','wrong_class','new_skip','new_added_skip','duplicate','boolean_counts'])
def test_full_original_inventory_cannot_be_replaced_by_survivors_or_new_skips(change):
    baseline=protected();actual=inventory_proof()
    if change=='one_survivor':actual=inventory_proof(('test_first',))
    elif change=='renamed':actual=inventory_proof(('test_first','test_replacement'))
    elif change=='wrong_class':actual['cases'][1]['classname']='tests.other'
    elif change=='new_skip':actual=inventory_proof(outcomes=['passed','skipped'])
    elif change=='new_added_skip':actual=inventory_proof(('test_first','test_second','test_new'),['passed','passed','skipped'])
    elif change=='duplicate':actual['cases'][1]=deepcopy(actual['cases'][0])
    elif change=='boolean_counts':actual['counts']['error']=False
    assert pytest_inventory_coverage(actual,baseline)['passed'] is False
    assert not real_work_pytest_executed({'suite':'workspace','pytest_execution':actual},baseline=baseline)


def test_new_executed_regression_may_increase_inventory_but_not_replace_originals():
    baseline=protected();actual=inventory_proof(('test_first','test_second','test_new'))
    result=pytest_inventory_coverage(actual,baseline)
    assert result['passed'] and result['required_count']==2 and result['observed_count']==3 and result['added_count']==1
    failing=inventory_proof(outcomes=['passed','failure'])
    assert pytest_inventory_coverage(failing,baseline)['passed']
    assert real_work_pytest_executed({'suite':'workspace','pytest_execution':failing},baseline=baseline)
    # This is coverage only: real_work_passed separately rejects failed behavior.


@pytest.mark.parametrize('change',['baseline_hash','wrong_suite','drop_required','allow_new_identity'])
def test_baseline_is_external_pinned_and_cannot_be_rewritten_by_final_report(change):
    baseline=protected()
    if change=='baseline_hash':baseline['sha256']='0'*64
    elif change=='wrong_suite':baseline['suite']='upstream-heldout'
    elif change=='drop_required':baseline['required_cases'].pop()
    elif change=='allow_new_identity':baseline['allowed_skips'].append({'classname':'tests.original','name':'test_new','skip_type':'pytest.skip'})
    assert not real_work_pytest_executed({'suite':'workspace','pytest_execution':inventory_proof()},baseline=baseline)


def test_skip_policy_is_node_and_type_specific_and_fixed_before_model():
    proof=inventory_proof(outcomes=['passed','skipped']);proof['cases'][1].update(skip_type='pytest.xfail',skip_reason='pinned upstream')
    allow=[{'classname':'tests.original','name':'test_second','skip_type':'pytest.xfail','skip_reason':'pinned upstream'}]
    with pytest.raises(ValueError,match='Undeclared'):protected(proof)
    baseline=protected(proof,allowed_skips=allow)
    assert pytest_inventory_coverage(proof,baseline)['passed']
    for field,value in [('skip_type','pytest.skip'),('skip_reason','model removed assertion')]:
        changed=deepcopy(proof);changed['cases'][1][field]=value
        assert not pytest_inventory_coverage(changed,baseline)['passed']
    with pytest.raises(ValueError,match='Unknown'):
        protected(allowed_skips=[{'classname':'tests.original','name':'test_missing','skip_type':'pytest.skip'}])


def test_raw_junit_preserves_skip_type_and_reason_for_whitelist(tmp_path):
    path=xml(tmp_path,('passed','skipped'));root=ET.parse(path);skip=root.find('.//skipped')
    skip.set('type','pytest.xfail');skip.set('message','pinned reason');root.write(path,encoding='utf8')
    proof=pytest_execution_evidence(path)
    assert proof['cases'][1]['skip_type']=='pytest.xfail' and proof['cases'][1]['skip_reason']=='pinned reason'
