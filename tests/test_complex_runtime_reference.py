"""A frozen Windows workflow reference never becomes a local Unix task claim."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
import assemble_release_acceptance as a
import verify_native_source as sourceaudit

@pytest.fixture
def reference(tmp_path,monkeypatch):
    directory=tmp_path/'reference';directory.mkdir();source=tmp_path/'source';(source/'native').mkdir(parents=True)
    monkeypatch.setattr(a,'SOURCE',source)
    installation={'source_id':'2d43ed50050d0a687f55','source_sha256':{str(i):str(i) for i in range(95)},
                  'adapter_source_sha256':{'src/a.py':'same'},'model':{'model':'fixed'}}
    native={'target':'x86_64-unknown-linux-gnu','upstream_commit':a.BASELINE_COMMIT}
    contract={'before':{'codex-rs/core/src/agent/main.rs':'same','codex-rs/windows-sandbox-rs/a.rs':'base'},
              'final':{'codex-rs/core/src/agent/main.rs':'same','codex-rs/windows-sandbox-rs/a.rs':'windows'},
              'protected':{'codex-rs/core/src/agent/main.rs':'same','codex-rs/windows-sandbox-rs/a.rs':'base'},
              'unchanged_protected':{'codex-rs/core/src/agent/main.rs':'same'},
              'declared_protected':{'codex-rs/windows-sandbox-rs/a.rs':'windows'},'recipe_id':'windows-recipe'}
    local=deepcopy(contract);local['final']['codex-rs/windows-sandbox-rs/a.rs']='base'
    report={'status':'accepted_for_frozen_target_local_runtime_scope','target':'x86_64-pc-windows-msvc',
            'source_id':installation['source_id'],'upstream_commit':a.BASELINE_COMMIT,'baseline_matrix_sha256':a.BASELINE_MATRIX_SHA256,
            'adapter_source_sha256':installation['adapter_source_sha256'],'model':installation['model'],'native_archive_sha256':'windows-archive',
            'native_source_audit':{'native_recipe_id':'windows-recipe','upstream_source_files':2,'unchanged_protected_runtime_files':1,
                'declared_protected_source_sha256':contract['declared_protected'],'patch_replay_passed':True,'external_cargo_packages_unchanged':True},
            'real_work':{name:{'passed':name=='jinja','final_source_id':installation['source_id'],'runtime_mechanisms_passed':True,
                'application_task_outcome':{'original_report_passed':name=='jinja','failed_suites':[] if name=='jinja' else ['tcp']},
                'runtime_mechanisms':{**{k:{'passed':True} for k in a.COMPLEX_REFERENCE_MECHANISMS},'passed':True}} for name in ['jinja','httpcore']}}
    frozen_install=deepcopy(installation);frozen_install['native_archive_sha256']='windows-archive'
    save=lambda path,value:path.write_text(json.dumps(value),encoding='utf-8')
    save(source/'native/runtime-x86_64-pc-windows-msvc.json',{'archive':{'sha256':'windows-archive'}})
    audit=directory/'audit.json';save(audit,{'upstream_archive':'fixed-source-archive'})
    original_sha=a.sha
    def sha(path):
        if Path(path)==directory/'acceptance.json':return a.COMPLEX_REFERENCE_REPORT_SHA256
        if Path(path)==directory/'installation.json':return a.COMPLEX_REFERENCE_INSTALL_SHA256
        return original_sha(path)
    monkeypatch.setattr(a,'sha',sha)
    monkeypatch.setattr(sourceaudit,'source_contract',lambda target,archive:contract if target=='x86_64-pc-windows-msvc' else local)
    return directory,installation,native,audit,report,frozen_install,contract,local,save

@pytest.mark.parametrize('change',['none','windows_target','wrong_report_hash','wrong_install_hash','other_source','partial_product','adapter_mismatch',
    'other_model','wrong_upstream','other_reference_target','stale_recipe','changed_shared_core','partial_mechanisms','failed_runtime',
    'application_laundering','missing_task','native_archive'])
def test_only_exact_accepted_reference_with_shared_source_is_attributed(reference,monkeypatch,change):
    directory,installation,native,audit,report,original,contract,local,save=reference
    if change=='windows_target':native['target']='x86_64-pc-windows-msvc'
    elif change in {'wrong_report_hash','wrong_install_hash'}:
        prior=a.sha;bad=directory/('acceptance.json' if change=='wrong_report_hash' else 'installation.json')
        monkeypatch.setattr(a,'sha',lambda path:'foreign' if Path(path)==bad else prior(path))
    elif change=='other_source':original['source_id']='other'
    elif change=='partial_product':installation['source_sha256'].pop('0')
    elif change=='adapter_mismatch':original['adapter_source_sha256']={'src/other.py':'other'}
    elif change=='other_model':report['model']={'model':'other'}
    elif change=='wrong_upstream':report['upstream_commit']='other'
    elif change=='other_reference_target':report['target']='aarch64-apple-darwin'
    elif change=='stale_recipe':report['native_source_audit']['native_recipe_id']='old'
    elif change=='changed_shared_core':local['final']['codex-rs/core/src/agent/main.rs']='changed'
    elif change=='partial_mechanisms':report['real_work']['jinja']['runtime_mechanisms'].pop('file_effect')
    elif change=='failed_runtime':report['real_work']['jinja']['runtime_mechanisms']['file_effect']['passed']=False
    elif change=='application_laundering':
        report['real_work']['httpcore']['passed']=True;report['real_work']['httpcore']['application_task_outcome']['original_report_passed']=True
    elif change=='missing_task':report['real_work'].pop('httpcore')
    elif change=='native_archive':original['native_archive_sha256']='foreign'
    save(directory/'acceptance.json',report);save(directory/'installation.json',original)
    if change=='none':
        real,proof=a.validate_complex_runtime_reference(directory,installation,native,audit)
        assert proof['local_task_execution_claimed'] is False and proof['shared_product_source_files']==95
        assert proof['shared_native_protected_files']==1 and proof['executed_target']=='x86_64-pc-windows-msvc'
        assert real['httpcore']['passed'] is False and real['httpcore']['application_task_outcome']['failed_suites']==['tcp']
        assert all(row['local_task_execution_claimed'] is False and row['qualification_target']==native['target'] for row in real.values())
    else:
        with pytest.raises(SystemExit):a.validate_complex_runtime_reference(directory,installation,native,audit)
