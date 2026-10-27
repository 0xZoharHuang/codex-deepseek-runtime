"""Cross-token offline evidence must retain its actual source and raw bytes."""
from copy import deepcopy
import json
from pathlib import Path,PureWindowsPath
import shutil
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
import assemble_release_acceptance as acceptance
from run_acceptance import import_offline_evidence


def save(path,value):
    path.write_text(json.dumps(value),encoding='utf-8')


@pytest.fixture
def offline_source(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();(source/'run_acceptance.py').write_text('frozen controller')
    monkeypatch.setattr(acceptance,'SOURCE',source)
    root=tmp_path/'offline';root.mkdir()
    native={'target':'x86_64-pc-windows-msvc'}
    installation={'source_id':'same-source','release_environment':r'D:\runtime\release\venv','console_script_sha256':'same-entry-hash'}
    artifact={'installation':installation,'native_manifest':native,'target':native['target'],
              'baseline_matrix_sha256':acceptance.BASELINE_MATRIX_SHA256,
              'entry':str(PureWindowsPath(installation['release_environment'])/'Scripts/codex-deepseek.exe'),
              'entry_sha256':installation['console_script_sha256'],
              'controller_environment':{'optimize':0,'windows_token':{'sid':'S-1-5-21-1-2-3-500','elevated':True}},
              'selected_gates':['offline'],'verifier_source_sha256':acceptance.qa_python_hashes(source)}
    from offline_origin_fixture import write_origins
    origins_sha=write_origins(root,installation,native)
    xml=ET.Element('testsuite');items=[]
    for index,(name,reason) in enumerate([('test_example::test_symlink_creation',None),*acceptance.expected_offline_skips(native['target']).items()]):
        module,case=name.split('::');classname='tests.'+module
        item=ET.SubElement(xml,'testcase',classname=classname,name=case)
        if reason:ET.SubElement(item,'skipped',type='pytest.skip',message=reason)
        items.append({'nodeid':str(index),'classname':classname,'name':case})
    ET.ElementTree(xml).write(root/'offline.xml',encoding='utf-8')
    save(root/'offline-collection.json',{'schema_version':1,'deselected':0,'items':items})
    (root/'offline.log').write_text('Actual offline execution log',encoding='utf-8')
    verdict={'passed':True,'entry_unchanged':True,'verifier_source_unchanged':True,'native_probes_unchanged':True,
             'gates':[{'gate':'offline','exit':0,'seconds':3.25,'log':'offline.log'}],
             'offline_collection_sha256':acceptance.sha(root/'offline-collection.json'),
             'offline_files_sha256':acceptance.offline_file_hashes(root)}
    verdict['installed_product_origins_sha256']=origins_sha
    save(root/'artifact.json',artifact);save(root/'verdict.json',verdict);save(root/'results.json',verdict['gates'])
    return root,installation,native,{'target':native['target']}


def import_into_runtime(source,tmp_path):
    original,installation,native,scope=source
    external=acceptance.external_offline_receipt(*source)
    root=tmp_path/'runtime';root.mkdir()
    artifact=acceptance.load(original/'artifact.json');artifact['selected_gates']=sorted(acceptance.GATES)
    artifact['controller_environment']['windows_token']={'sid':'S-1-5-21-1-2-3-1001','elevated':False}
    artifact['external_offline']=external
    rows=[{'gate':name,'exit':0,'seconds':1,'log':name+'.log'} for name in sorted(acceptance.GATES) if name!='offline']
    rows.append({**external['gate'],'execution':'separate_complete_offline_run','external_root':external['root']})
    verdict=acceptance.load(original/'verdict.json');verdict.update(gates=rows,external_offline_unchanged=True)
    for name in acceptance.OFFLINE_FILES:shutil.copyfile(original/name,root/name)
    save(root/'artifact.json',artifact);save(root/'verdict.json',verdict);save(root/'results.json',rows)
    return root,installation,native,scope


def test_reference_preserves_elevated_execution_and_non_elevated_runtime_context(offline_source,tmp_path):
    runtime=import_into_runtime(offline_source,tmp_path)
    proof=acceptance.validate_offline_run(*runtime)
    external=proof['external_execution']
    assert proof['tests_passed']==1
    assert external['controller_environment']['windows_token']['elevated'] is True
    assert acceptance.load(runtime[0]/'artifact.json')['controller_environment']['windows_token']['elevated'] is False
    assert set(external['files_sha256'])=={'artifact.json','verdict.json','results.json','offline-installed-origins.json',*acceptance.OFFLINE_FILES}
    assert external['gate']['seconds']==3.25


@pytest.mark.parametrize('change',['foreign_entry','foreign_entry_hash','no_token','non_elevated_source','invalid_elevation',
    'missing_sid','changed_junit','changed_log','unbound_raw_files','changed_results','referenced_gate_without_metadata','false_exit'])
def test_external_receipt_rejects_wrong_origin_or_post_execution_replacement(offline_source,change):
    root,*_=offline_source;artifact=acceptance.load(root/'artifact.json');verdict=acceptance.load(root/'verdict.json')
    if change=='foreign_entry':artifact['entry']=r'D:\other\codex-deepseek.exe'
    elif change=='foreign_entry_hash':artifact['entry_sha256']='different'
    elif change=='no_token':artifact['controller_environment'].pop('windows_token')
    elif change=='non_elevated_source':artifact['controller_environment']['windows_token']['elevated']=False
    elif change=='invalid_elevation':artifact['controller_environment']['windows_token']['elevated']=1
    elif change=='missing_sid':artifact['controller_environment']['windows_token'].pop('sid')
    elif change=='changed_junit':(root/'offline.xml').write_bytes((root/'offline.xml').read_bytes()+b'\n')
    elif change=='changed_log':(root/'offline.log').write_text('Replacement log')
    elif change=='unbound_raw_files':verdict.pop('offline_files_sha256')
    elif change=='changed_results':save(root/'results.json',[{'gate':'offline','exit':1}])
    elif change=='referenced_gate_without_metadata':
        verdict['gates'][0]['execution']='separate_complete_offline_run';save(root/'results.json',verdict['gates'])
    elif change=='false_exit':verdict['gates'][0]['exit']=False;save(root/'results.json',verdict['gates'])
    save(root/'artifact.json',artifact);save(root/'verdict.json',verdict)
    with pytest.raises(SystemExit):acceptance.external_offline_receipt(*offline_source)


@pytest.mark.parametrize('change',['elevated_runtime','no_runtime_token','wrong_runtime_source','unmarked_import',
    'wrong_origin','copied_junit_changed','copied_log_changed','source_log_changed','unstable_reference','changed_result_rows'])
def test_assembler_revalidates_actual_import_and_both_copies(offline_source,tmp_path,change):
    runtime=import_into_runtime(offline_source,tmp_path);root=runtime[0]
    artifact=acceptance.load(root/'artifact.json');verdict=acceptance.load(root/'verdict.json')
    offline=next(row for row in verdict['gates'] if row['gate']=='offline')
    if change=='elevated_runtime':artifact['controller_environment']['windows_token']['elevated']=True
    elif change=='no_runtime_token':artifact['controller_environment'].pop('windows_token')
    elif change=='wrong_runtime_source':artifact['verifier_source_sha256']['run_acceptance.py']='another-source'
    elif change=='unmarked_import':offline.pop('execution');save(root/'results.json',verdict['gates'])
    elif change=='wrong_origin':offline['external_root']=str(tmp_path/'other');save(root/'results.json',verdict['gates'])
    elif change=='copied_junit_changed':(root/'offline.xml').write_bytes((root/'offline.xml').read_bytes()+b'\n')
    elif change=='copied_log_changed':(root/'offline.log').write_text('Replacement')
    elif change=='source_log_changed':(offline_source[0]/'offline.log').write_text('Replacement')
    elif change=='unstable_reference':verdict['external_offline_unchanged']=False
    elif change=='changed_result_rows':save(root/'results.json',verdict['gates'][:-1])
    save(root/'artifact.json',artifact);save(root/'verdict.json',verdict)
    with pytest.raises(SystemExit):acceptance.validate_offline_run(*runtime)


@pytest.mark.parametrize('cycle',['self','two_reports'])
def test_reference_cycles_are_rejected_before_recursive_validation(offline_source,tmp_path,cycle):
    root,*_=offline_source;artifact=acceptance.load(root/'artifact.json')
    target=root
    if cycle=='two_reports':
        target=tmp_path/'second';shutil.copytree(root,target)
        second=deepcopy(artifact);second['external_offline']={'root':str(root)};save(target/'artifact.json',second)
    artifact['external_offline']={'root':str(target)};save(root/'artifact.json',artifact)
    with pytest.raises(SystemExit,match='Nested'):acceptance.external_offline_receipt(*offline_source)


def test_file_change_while_validating_cannot_create_a_mixed_receipt(offline_source,monkeypatch):
    original=acceptance.validate_offline_run
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        (offline_source[0]/'offline.log').write_text('Changed during validation')
        return result
    monkeypatch.setattr(acceptance,'validate_offline_run',changed)
    with pytest.raises(SystemExit,match='changed during validation'):acceptance.external_offline_receipt(*offline_source)


@pytest.mark.parametrize('filename',acceptance.OFFLINE_FILES)
def test_changed_donor_bytes_produce_an_explicit_import_failure_not_an_orphan_run(offline_source,tmp_path,filename):
    external=acceptance.external_offline_receipt(*offline_source)
    root=tmp_path/'consumer';root.mkdir()
    assert import_offline_evidence(external,root)['exit']==0
    (offline_source[0]/filename).write_text('Changed after preflight',encoding='utf-8')
    failed=import_offline_evidence(external,root)
    assert failed['exit']==1 and failed['execution']=='external_offline_import_failed'
    assert failed['source_gate']==external['gate']
    assert acceptance.load(root/failed['log'])==failed
