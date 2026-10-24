"""Only all four OAuth variants may be explicitly revalidated; original failures remain."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import pytest
import assemble_release_acceptance as acceptance
import verify_rpc_observations as rpc
from test_offline_reference import offline_source,import_into_runtime,save
from test_qualification_scope import installed,trace_fixture

@pytest.fixture
def oauth_run(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir()
    metadata,native,_=installed()
    closure=acceptance.OAUTH_EXECUTION_SOURCES|set(metadata['adapter_source_sha256'])
    for name in closure|{'assemble_release_acceptance.py','verify_rpc_observations.py'}:
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('frozen '+name)
    monkeypatch.setattr(acceptance,'SOURCE',source)
    probes={}
    for name in ['revocation','keyring']:
        path=tmp_path/(name+'.exe');path.write_bytes(name.encode());probes[name]={'path':str(path),'sha256':acceptance.sha(path)}
    original={'entry':'same-entry','entry_sha256':'same-entry-sha','release':'same-release','installation':metadata,
              'native_manifest':native,'target':native['target'],'tui_sandbox_mode':'workspace-write',
              'qa_default_sandbox_mode':'workspace-write','sandbox_scope':'explicit policy','native_probes':probes,
              'required_local_methods':['fixed-method'],'baseline_matrix_sha256':acceptance.BASELINE_MATRIX_SHA256,
              'controller_environment':{'optimize':0,'windows_token':{'sid':'S-1-5-21-1-2-3-1001','elevated':False}},
              'selected_gates':sorted(acceptance.GATES),'verifier_source_sha256':acceptance.qa_python_hashes(source)}
    rows=[{'gate':name,'exit':int(name in {'oauth-auto','oauth-keyring'}),'seconds':1,'log':name+'.log'} for name in sorted(acceptance.GATES)]
    before={'passed':False,'gates':rows,'entry_unchanged':True,'verifier_source_unchanged':True,'native_probes_unchanged':True}
    root=tmp_path/'new';root.mkdir()
    (source/'verify_independent_audit.py').write_text('bounded current OAuth verifier')
    new={**deepcopy(original),'selected_gates':sorted(acceptance.OAUTH_GATES),'verifier_source_sha256':acceptance.qa_python_hashes(source)}
    after={'passed':True,'gates':[{'gate':name,'exit':0,'seconds':2,'log':name+'.log'} for name in sorted(acceptance.OAUTH_GATES)],
           'entry_unchanged':True,'verifier_source_unchanged':True,'native_probes_unchanged':True}
    for gate in acceptance.OAUTH_GATES:
        evidence=root/'evidence'/gate;evidence.mkdir(parents=True)
        (root/(gate+'.log')).write_text('EVIDENCE '+str(evidence)+'\n')
    return source,root,original,before,new,after

@pytest.mark.parametrize('change',[
    'none','old_oauth_all_failed','old_oauth_all_passed','missing_original','duplicate_original','other_failed','terminal_failed',
    'false_original_aggregate','old_mutated','partial','duplicate','extra','failed','boolean_exit','wrong_results','wrong_log',
    'foreign_installation','foreign_native','foreign_entry','foreign_target','foreign_probes','probe_bytes',
    'different_policy','foreign_sid','elevated','python_optimized','different_python','stale_verifier','stale_fixture','shared_changed',
    'fixture_changed','missing_source','report_change','foreign_root','duplicate_root','nested_root','missing_root','no_scope'])
def test_only_complete_current_oauth_can_replace_exact_old_oauth(oauth_run,change):
    source,root,original,before,new,after=oauth_run
    if change=='old_oauth_all_failed':
        for row in before['gates']:
            if row['gate'] in acceptance.OAUTH_GATES:row['exit']=1
    elif change=='old_oauth_all_passed':
        for row in before['gates']:row['exit']=0
        before['passed']=True
    elif change=='missing_original':before['gates'].pop()
    elif change=='duplicate_original':before['gates'][-1]=before['gates'][0]
    elif change in {'other_failed','terminal_failed'}:
        next(row for row in before['gates'] if row['gate']==('guardian' if change=='other_failed' else 'tui'))['exit']=1
    elif change=='false_original_aggregate':before['passed']=True
    elif change=='old_mutated':before['verifier_source_unchanged']=False
    elif change=='partial':after['gates'].pop()
    elif change=='duplicate':after['gates'][-1]=after['gates'][0]
    elif change=='extra':after['gates'].append({'gate':'guardian','exit':0})
    elif change=='failed':after['gates'][0]['exit']=1
    elif change=='boolean_exit':after['gates'][0]['exit']=False
    elif change=='wrong_log':after['gates'][0]['log']='../foreign.log'
    elif change=='foreign_installation':new['installation']['source_id']='foreign'
    elif change=='foreign_native':new['native_manifest']['target']='foreign'
    elif change=='foreign_entry':new['entry_sha256']='foreign'
    elif change=='foreign_target':new['target']='foreign'
    elif change=='foreign_probes':new['native_probes']['keyring']['sha256']='foreign'
    elif change=='probe_bytes':Path(new['native_probes']['keyring']['path']).write_bytes(b'modified')
    elif change=='different_policy':new['qa_default_sandbox_mode']='danger-full-access'
    elif change=='foreign_sid':new['controller_environment']['windows_token']['sid']='S-1-5-21-1-2-3-500'
    elif change=='elevated':
        new['controller_environment']['windows_token']['elevated']=True
        original['controller_environment']['windows_token']['elevated']=True
    elif change=='python_optimized':new['controller_environment']['optimize']=1
    elif change=='different_python':new['controller_environment']['python']='other'
    elif change=='stale_verifier':new['verifier_source_sha256']['verify_independent_audit.py']='old'
    elif change=='stale_fixture':new['verifier_source_sha256']['tests/oauth_fixture.py']='old'
    elif change in {'shared_changed','fixture_changed'}:
        name='verify_live.py' if change=='shared_changed' else 'tests/oauth_fixture.py'
        (source/name).write_text('changed dependency')
        new['verifier_source_sha256']=acceptance.qa_python_hashes(source)
    elif change=='missing_source':new['verifier_source_sha256'].pop('tests/oauth_fixture.py')
    elif change=='report_change':new['verifier_source_sha256']['assemble_release_acceptance.py']='previous consumer'
    elif change=='foreign_root':(root/'oauth-auto.log').write_text('EVIDENCE '+str(root.parent)+'\n')
    elif change=='duplicate_root':(root/'oauth-auto.log').write_text((root/'oauth-file.log').read_text())
    elif change=='nested_root':
        child=root/'evidence/oauth-file/child';child.mkdir()
        (root/'oauth-auto.log').write_text('EVIDENCE '+str(child)+'\n')
    elif change=='missing_root':shutil.rmtree(root/'evidence/oauth-auto')
    save(root/'artifact.json',new);save(root/'verdict.json',after)
    save(root/'results.json',[] if change=='wrong_results' else after['gates'])
    preserved=json.dumps([original,before],sort_keys=True)
    if change in {'none','old_oauth_all_failed','old_oauth_all_passed','report_change','fixture_changed'}:
        effective,lineage=acceptance.validate_standard_gates(original,before,root,revalidation_scope='oauth')
        assert len(effective)==30 and all(row['exit']==0 for row in effective)
        assert all(row['seconds']==(2 if row['gate'] in acceptance.OAUTH_GATES else 1) for row in effective)
        assert lineage['single_run_all_passed'] is False and lineage['scope']=='all_four_oauth_variants'
        assert lineage['original_failed_gate_count']==sum(row['exit']!=0 for row in before['gates'])
        assert len(lineage['original_oauth_gates'])==len(lineage['replacement_logs_sha256'])==4
        assert json.dumps([original,before],sort_keys=True)==preserved
    else:
        with pytest.raises(SystemExit):
            acceptance.validate_standard_gates(original,before,root,revalidation_scope='terminal' if change=='no_scope' else 'oauth')

@pytest.mark.parametrize('change',['none','no_scope','both_scopes','tampered_snapshot','shared_changed','other_gate_changed','other_fixture_changed','oauth_fixture_changed','oauth_fixture_without_scope','fixture_only_without_scope','fixture_only_terminal_scope','stale_current','raw_changed'])
def test_oauth_snapshot_keeps_old_external_offline_exact_and_current_offline_strict(offline_source,tmp_path,change):
    root,installation,native,scope=offline_source;source=acceptance.SOURCE
    for name in ['assemble_release_acceptance.py','verify_independent_audit.py','tests/oauth_fixture.py','verify_live.py','src/pkg/runtime.py']:
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('old '+name)
    installation['source_sha256']={'src/pkg/runtime.py':acceptance.sha(source/'src/pkg/runtime.py')}
    artifact=acceptance.load(root/'artifact.json')
    artifact.update(installation=installation,verifier_source_sha256=acceptance.qa_python_hashes(source))
    save(root/'artifact.json',artifact)
    runtime=import_into_runtime(offline_source,tmp_path);old_descriptor=acceptance.load(runtime[0]/'artifact.json')['external_offline']
    verdict=acceptance.load(runtime[0]/'verdict.json');verdict['passed']=False
    for row in verdict['gates']:
        if row['gate'] in {'oauth-keyring','oauth-auto'}:row['exit']=1
    save(runtime[0]/'verdict.json',verdict);save(runtime[0]/'results.json',verdict['gates'])
    snapshot=tmp_path/'producer';shutil.copytree(source,snapshot)
    files={p.relative_to(snapshot).as_posix():acceptance.sha(p) for p in snapshot.rglob('*') if p.is_file()}
    save(tmp_path/'producer.manifest.json',{'source_id':installation['source_id'],'files':files,'qa_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()})
    (source/'verify_independent_audit.py').write_text('bounded action feedback')
    if change in {'oauth_fixture_changed','oauth_fixture_without_scope','fixture_only_without_scope','fixture_only_terminal_scope'}:(source/'tests/oauth_fixture.py').write_text('atomic fixture event snapshot')
    if change in {'fixture_only_without_scope','fixture_only_terminal_scope'}:(source/'verify_independent_audit.py').write_bytes((snapshot/'verify_independent_audit.py').read_bytes())
    if change=='tampered_snapshot':(snapshot/'verify_live.py').write_text('tampered')
    elif change=='shared_changed':(source/'verify_live.py').write_text('changed')
    elif change=='other_gate_changed':(source/'verify_memory_audit.py').write_text('changed')
    elif change=='other_fixture_changed':
        (source/'tests').mkdir(exist_ok=True);(source/'tests/plugin_fixture.py').write_text('changed runtime fixture')
    elif change=='raw_changed':(root/'offline.log').write_text('new log')
    if change in {'none','oauth_fixture_changed'}:
        proof=acceptance.validate_offline_run(*runtime,producer_snapshot=snapshot,oauth_revalidated=True)
        assert proof['external_execution']==old_descriptor
        current=tmp_path/'current';shutil.copytree(root,current)
        value=acceptance.load(current/'artifact.json');value['verifier_source_sha256']=acceptance.qa_python_hashes(source);save(current/'artifact.json',value)
        assert acceptance.validate_offline_run(current,installation,native,scope,current=True)['tests_passed']==1
    else:
        with pytest.raises(SystemExit):
            if change=='stale_current':acceptance.validate_offline_run(*offline_source,current=True,producer_snapshot=snapshot,oauth_revalidated=True)
            else:acceptance.validate_offline_run(*runtime,producer_snapshot=snapshot,oauth_revalidated=change not in {'no_scope','oauth_fixture_without_scope','fixture_only_without_scope','fixture_only_terminal_scope'},terminal_revalidated=change in {'both_scopes','fixture_only_terminal_scope'})

def test_all_old_oauth_witnesses_excluded_even_previously_passing_variants(tmp_path,monkeypatch):
    root=tmp_path/'old';root.mkdir();target='x86_64-unknown-linux-gnu'
    traces={}
    for gate in acceptance.OAUTH_GATES:
        trace,*_=trace_fixture(root/'evidence'/gate);traces[gate]=trace
        (root/(gate+'.log')).write_text('EVIDENCE '+str(trace.parent)+'\n')
    excluded=acceptance.oauth_evidence_roots(root)
    assert len(excluded)==4
    for gate in acceptance.OAUTH_GATES:
        trace=traces[gate]
        found,count,errors=rpc.trace_observations(trace,'/selected/entry','entry-hash',target);assert not errors
        report={'methods':found,'native_client_sessions':count,'verifier_and_artifact_unchanged':True,
                'verifier_sha256':{name:acceptance.sha(acceptance.SOURCE/name) for name in rpc.VERIFIERS}}
        with pytest.raises(SystemExit,match='superseded'):
            acceptance.validate_rpc_witnesses(report,{root:('/selected/entry','entry-hash')},target,set(found),excluded_roots=excluded)

@pytest.mark.parametrize('module',[acceptance,rpc])
def test_cli_scopes_are_mutually_exclusive_before_artifact_reads(monkeypatch,module):
    required=['entry','regression','methods','local-account','output']
    if module is acceptance:
        required+=['browser','browser-protocol','protocol-recovery','native-lifetime','user-verification','schema','native-source-audit','fixtures','rpc-observations','hook-trust']
    argv=['collector',*[arg for name in required for arg in ['--'+name,'missing']], '--terminal-regression','terminal','--oauth-regression','oauth']
    monkeypatch.setattr(sys,'argv',argv)
    with pytest.raises(SystemExit) as error:module.main()
    assert error.value.code==2


@pytest.mark.parametrize('raw,expected',[
    (r'D:\evidence\case',r'\\?\D:\evidence\case'),
    ('D:/evidence/case',r'\\?\D:\evidence\case'),
    (r'\\server\share\evidence',r'\\?\UNC\server\share\evidence'),
    (r'\\?\D:\evidence\case',r'\\?\D:\evidence\case'),
    (r'\\?\UNC\server\share\evidence',r'\\?\UNC\server\share\evidence'),
])
def test_windows_evidence_io_prefix_is_correct_and_idempotent(monkeypatch,raw,expected):
    from types import SimpleNamespace
    monkeypatch.setattr(acceptance,'os',SimpleNamespace(name='nt'))
    assert acceptance.evidence_io_root(raw)==expected
    assert acceptance.evidence_io_root(expected)==expected

def test_non_windows_evidence_io_root_is_unchanged(monkeypatch,tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(acceptance,'os',SimpleNamespace(name='posix'))
    assert acceptance.evidence_io_root(tmp_path) is tmp_path

def test_extended_io_prefix_keeps_physical_containment(tmp_path):
    root=tmp_path/'root';root.mkdir()
    child=root/'case';child.mkdir()
    outside=tmp_path/'outside';outside.mkdir()
    canonical=acceptance.comparison_path(root)
    assert acceptance.comparison_path(acceptance.evidence_io_root(child)).is_relative_to(canonical)
    assert not acceptance.comparison_path(acceptance.evidence_io_root(outside)).is_relative_to(canonical)

def test_long_evidence_walk_reads_only_declared_tree(tmp_path):
    from qa_workspace import evidence_files
    root=tmp_path/'inside';root.mkdir()
    outside=tmp_path/'outside';outside.mkdir();(outside/'client-requests.jsonl').write_text('outside')
    deep=root
    while len(str(deep))<330:deep=deep/('nested-'+'x'*32)
    deep=Path(acceptance.evidence_io_root(deep));deep.mkdir(parents=True)
    expected=deep/'client-requests.jsonl';expected.write_text('owned')
    found=list(evidence_files(acceptance.evidence_io_root(root),'client-requests.jsonl'))
    assert len(found)==1 and found[0].read_text()=='owned'
    assert acceptance.comparison_path(found[0])==acceptance.comparison_path(expected)
    assert acceptance.comparison_path(found[0]).is_relative_to(acceptance.comparison_path(root))


@pytest.mark.parametrize('change',['none','no_oauth_scope','shared_helper','other_fixture','snapshot_bytes','scope_changed','wrong_product'])
def test_methods_snapshot_can_follow_only_explicit_oauth_fixture_revalidation(tmp_path,monkeypatch,change):
    source=tmp_path/'source';source.mkdir()
    snapshot=tmp_path/'methods-producer'
    method_files={'verify_remaining_methods.py','verify_remaining_local.py','verify_live.py','assemble_release_acceptance.py',
                  'process_acceptance.py','install.py','windows_acceptance_setup.py','qa_workspace.py'}
    for name in method_files|{'verify_independent_audit.py','tests/oauth_fixture.py','src/pkg/runtime.py'}:
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('unchanged '+name)
    # The scope fingerprint is the real frozen applicability implementation.
    original_collector=Path(acceptance.__file__).read_text()
    (source/'assemble_release_acceptance.py').write_text(original_collector)
    installation={'source_id':'same-product','source_sha256':{'src/pkg/runtime.py':acceptance.sha(source/'src/pkg/runtime.py')}}
    artifact={'installation':installation,'verifier_sha256':{name:acceptance.sha(source/name) for name in method_files}}
    shutil.copytree(source,snapshot)
    files={p.relative_to(snapshot).as_posix():acceptance.sha(p) for p in snapshot.rglob('*') if p.is_file()}
    save(tmp_path/'methods-producer.manifest.json',{'source_id':installation['source_id'],'files':files,
         'qa_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()})
    (source/'assemble_release_acceptance.py').write_text(original_collector+'\n# OAuth-only report consumer update\n')
    (source/'verify_independent_audit.py').write_text('HTTP event snapshot')
    (source/'tests/oauth_fixture.py').write_text('atomic OAuth-only event snapshot')
    if change=='shared_helper':(source/'verify_live.py').write_text('changed')
    elif change=='other_fixture':(source/'tests/plugin_fixture.py').write_text('changed')
    elif change=='snapshot_bytes':(snapshot/'tests/oauth_fixture.py').write_text('tampered')
    elif change=='scope_changed':
        value=(source/'assemble_release_acceptance.py').read_text().replace(acceptance.BASELINE_MATRIX_SHA256,'0'*64)
        (source/'assemble_release_acceptance.py').write_text(value)
    elif change=='wrong_product':installation['source_sha256']['src/pkg/runtime.py']='foreign'
    monkeypatch.setattr(acceptance,'SOURCE',source)
    if change=='none':
        checked=acceptance.validate_method_verifiers(artifact,method_snapshot=snapshot,oauth_revalidated=True)
        assert checked['scope_helper_from_regression_snapshot']['producer_sha256']==artifact['verifier_sha256']['assemble_release_acceptance.py']
    else:
        with pytest.raises(SystemExit):
            acceptance.validate_method_verifiers(artifact,method_snapshot=snapshot,oauth_revalidated=change!='no_oauth_scope')
