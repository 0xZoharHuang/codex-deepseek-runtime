"""Release acceptance cannot widen scope or borrow evidence from another runtime."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
try:
    import assemble_release_acceptance as acceptance
    import run_acceptance as runner
    import verify_rpc_observations as rpc
finally:sys.path.pop(0)


def installed(target='x86_64-pc-windows-msvc'):
    native={'target':target,'upstream_commit':acceptance.BASELINE_COMMIT,
            'upstream_version':'0.164.0-alpha.7','archive':{'sha256':'archive'},'files':{'bin/codex.exe':'native'}}
    metadata={'release_id':'source','source_id':'source','release_environment':r'D:\host\releases\source\venv',
              'source':r'D:\host\source','source_sha256':{'install.py':'install','src/adapter.py':'adapter'},
              'adapter_source_sha256':{'src/adapter.py':'adapter'},'console_script_sha256':'host-launcher',
              'model_catalog_sha256':'catalog','dependencies_lock_sha256':'dependencies',
              'native_package':r'D:\host\native','native_version':'0.164.0-alpha.7',
              'native_archive_sha256':'archive','model':'deepseek/model'}
    guest=deepcopy(metadata)
    guest.update(release_environment=r'C:\guest\releases\source\venv',source=r'C:\guest\source',
                 native_package=r'C:\guest\native',console_script_sha256='guest-launcher')
    artifact={'installation':guest,'target':target,'native_manifest':deepcopy(native),
              'entry':r'C:\guest\releases\source\venv\Scripts\codex-deepseek.exe','entry_sha256':'guest-launcher'}
    return metadata,native,artifact


@pytest.mark.parametrize('target,count',[(target,count) for target,(_,count) in acceptance.TARGETS.items()])
def test_scope_comes_from_frozen_protocol_and_explicit_platform_variants(target,count):
    scope=acceptance.qualification_scope({'target':target,'upstream_commit':acceptance.BASELINE_COMMIT})
    assert len(scope['local_methods'])==count
    assert len(scope['matrix'])==174 and len({row['method'] for row in scope['matrix']})==174
    assert acceptance.LOCAL_ACCOUNT<=set(scope['local_methods'])
    assert not acceptance.REMOTE&set(scope['local_methods'])
    assert acceptance.WINDOWS_SANDBOX&set(scope['local_methods'])==(acceptance.WINDOWS_SANDBOX if target=='x86_64-pc-windows-msvc' else set())


def test_new_native_source_and_mutated_inventory_require_scope_reaudit(tmp_path,monkeypatch):
    native={'target':'aarch64-apple-darwin','upstream_commit':'unreviewed'}
    with pytest.raises(SystemExit,match='scope audit'):acceptance.qualification_scope(native)
    native['upstream_commit']=acceptance.BASELINE_COMMIT
    baseline=tmp_path/'reports/history/v0.3.0/runtime-method-matrix.json';baseline.parent.mkdir(parents=True)
    contents=(ROOT/'reports/history/v0.3.0/runtime-method-matrix.json').read_bytes()
    baseline.write_bytes(contents+b'\n');monkeypatch.setattr(acceptance,'SOURCE',tmp_path)
    with pytest.raises(SystemExit,match='hash changed'):acceptance.qualification_scope(native)


def test_explicit_windows_guest_preserves_content_and_records_path_dependent_launcher_identity():
    metadata,native,artifact=installed()
    result=acceptance.installation_lineage(metadata,native,artifact,relocated_windows=True)
    assert result['content_equivalent'] and result['deployment_relocated']
    assert result['entry']==artifact['entry'] and result['entry_sha256']=='guest-launcher'
    assert result['native_package']==artifact['installation']['native_package']
    with pytest.raises(SystemExit,match='installation differs'):acceptance.installation_lineage(metadata,native,artifact)


@pytest.mark.parametrize('field',sorted(acceptance.INSTALLATION_CONTENT_FIELDS))
def test_windows_relocation_cannot_change_any_content_identity(field):
    metadata,native,artifact=installed();artifact['installation'][field]='mismatched'
    with pytest.raises(SystemExit,match='identity (differs|mismatch)'):acceptance.installation_lineage(metadata,native,artifact,relocated_windows=True)


@pytest.mark.parametrize('change',['missing_source_map','missing_adapter_map','native_file','native_manifest_missing',
                                   'target','launcher_hash','launcher_location','future_identity','extra_field','non_windows'])
def test_guest_receipt_cannot_erase_or_weaken_native_or_installation_identity(change):
    metadata,native,artifact=installed()
    if change=='missing_source_map':artifact['installation'].pop('source_sha256')
    elif change=='missing_adapter_map':artifact['installation']['adapter_source_sha256']={}
    elif change=='native_file':artifact['native_manifest']['files']['bin/codex.exe']='different'
    elif change=='native_manifest_missing':artifact.pop('native_manifest')
    elif change=='target':artifact['target']='x86_64-unknown-linux-gnu'
    elif change=='launcher_hash':artifact['entry_sha256']='another-launcher'
    elif change=='launcher_location':artifact['entry']=r'C:\unrelated\codex-deepseek.exe'
    elif change=='future_identity':metadata['full_source_sha256']='same';artifact['installation']['full_source_sha256']='different'
    elif change=='extra_field':artifact['installation']['extra']='not-a-path'
    elif change=='non_windows':
        native['target']='x86_64-unknown-linux-gnu';artifact['target']=native['target'];artifact['native_manifest']=deepcopy(native)
    with pytest.raises(SystemExit):acceptance.installation_lineage(metadata,native,artifact,relocated_windows=True)


@pytest.mark.parametrize('gate',sorted(acceptance.KEYRING_PROBE_GATES))
def test_secret_credential_gates_cannot_run_without_explicit_native_cleanup_probe(tmp_path,gate):
    revocation=tmp_path/'native-revocation';revocation.write_bytes(b'compiled revocation probe')
    with pytest.raises(SystemExit,match='keyring-probe'):runner.native_probe_attestation([gate],revocation,None)
    keyring=tmp_path/'native-keyring';keyring.write_bytes(b'compiled native credential probe')
    proof=runner.native_probe_attestation([gate],revocation,keyring)
    assert proof['keyring']=={'path':str(keyring.resolve()),'sha256':acceptance.sha(keyring)}
    keyring.unlink()
    with pytest.raises(SystemExit,match='does not exist'):runner.native_probe_attestation([gate],revocation,keyring)


def trace_fixture(root):
    root.mkdir(parents=True,exist_ok=True)
    requests=[{'method':'initialize','id':1,'params':{}},{'method':'account/read','id':2,'params':{}}]
    events=[{'id':1,'result':{}},{'id':2,'result':{'account':None}},
            {'id':77,'method':'server/callback','params':{}}]
    trace=root/'client-requests.jsonl';trace.write_text(''.join(json.dumps(row)+'\n' for row in requests),encoding='utf-8')
    event=root/'events-100.json';event.write_text(json.dumps(events),encoding='utf-8')
    launch=root/'launch-100.json';launch.write_text(json.dumps({'resolved_entry':'/selected/entry','entry_sha256':'entry-hash'}),encoding='utf-8')
    return trace,requests,events,event,launch


def collect(trace):return rpc.trace_observations(trace,'/selected/entry','entry-hash','x86_64-unknown-linux-gnu')


def test_rpc_pairing_preserves_actual_artifact_and_payload_receipts(tmp_path):
    trace,_,_,event,launch=trace_fixture(tmp_path)
    observed,sessions,errors=collect(trace)
    assert sessions==1 and not errors and set(observed)=={'initialize','account/read'}
    witness=observed['account/read'][0]
    assert witness['requests_sha256']==acceptance.sha(trace)
    assert witness['events_sha256']==acceptance.sha(event)
    assert witness['launch_sha256']==acceptance.sha(launch)


@pytest.mark.parametrize('change',['duplicate_request','duplicate_response','orphan_response','wrong_entry_hash',
                                   'missing_launch','missing_events','bad_request_id','extra_segment','malformed_json'])
def test_ambiguous_or_unmatched_rpc_receipts_cannot_prove_method_coverage(tmp_path,change):
    trace,requests,events,event,launch=trace_fixture(tmp_path)
    if change=='duplicate_request':requests.append(deepcopy(requests[1]))
    elif change=='duplicate_response':events.append(deepcopy(events[1]))
    elif change=='orphan_response':events[1]['id']=3
    elif change=='wrong_entry_hash':launch.write_text(json.dumps({'resolved_entry':'/selected/entry','entry_sha256':'old'}))
    elif change=='missing_launch':launch.unlink()
    elif change=='missing_events':event.unlink()
    elif change=='bad_request_id':requests[1]['id']=True
    elif change=='extra_segment':requests.insert(1,deepcopy(requests[0]))
    trace.write_text(''.join(json.dumps(row)+'\n' for row in requests),encoding='utf-8')
    if event.exists():event.write_text(json.dumps(events),encoding='utf-8')
    if change=='malformed_json':trace.write_text('{broken')
    observed,_,errors=collect(trace)
    assert not observed and errors


def test_error_and_foreign_runtime_responses_do_not_count_as_success(tmp_path):
    trace,_,events,event,launch=trace_fixture(tmp_path)
    events[1]={'id':2,'error':{'code':-1,'message':'failed'}};event.write_text(json.dumps(events))
    observed,_,errors=collect(trace);assert 'account/read' not in observed and not errors
    launch.write_text(json.dumps({'resolved_entry':'/unrelated/entry','entry_sha256':'entry-hash'}))
    observed,sessions,errors=collect(trace);assert not observed and sessions==0 and not errors


def test_success_in_another_native_session_cannot_satisfy_an_unanswered_request(tmp_path):
    trace,requests,events,_,_=trace_fixture(tmp_path)
    trace.write_text(''.join(json.dumps(row)+'\n' for row in requests+requests),encoding='utf-8')
    (tmp_path/'events-100.json').write_text(json.dumps(events[:1]),encoding='utf-8')
    (tmp_path/'events-200.json').write_text(json.dumps([{'id':1,'result':{}},{'id':2,'error':{'code':-1}}]),encoding='utf-8')
    (tmp_path/'launch-200.json').write_bytes((tmp_path/'launch-100.json').read_bytes())
    observed,sessions,errors=collect(trace)
    assert not errors and sessions==2 and 'account/read' not in observed


def test_regression_log_paths_are_explicit_and_do_not_depend_on_checkout_layout(tmp_path):
    run=tmp_path/'unusual directory'/'运行';run.mkdir(parents=True)
    external=tmp_path/'explicit supplemental';external.mkdir()
    (run/'gate.log').write_text('EVIDENCE '+str(external)+'\n',encoding='utf-8')
    verdict={'gates':[{'exit':0,'log':'gate.log'}]};artifact={'evidence_roots':[str(run)]}
    with pytest.raises(SystemExit,match='escaped'):rpc.regression_roots(run,artifact,verdict)
    artifact['evidence_roots'].append(str(external))
    assert rpc.regression_roots(run,artifact,verdict)=={run.resolve(),external.resolve()}
    verdict['gates'][0]['log']='../../outside.log'
    with pytest.raises(SystemExit,match='log escaped'):rpc.regression_roots(run,artifact,verdict)


@pytest.mark.parametrize('change',['missing_case','duplicate_case','extra_case','wrong_parameter','failure','deselected','empty'])
def test_offline_junit_cannot_pass_with_a_truncated_or_filtered_test_set(change):
    import xml.etree.ElementTree as ET
    collection={'schema_version':1,'deselected':0,'items':[
        {'nodeid':'test_one.py::test_case[a]','classname':'test_one','name':'test_case[a]'},
        {'nodeid':'test_one.py::test_case[b]','classname':'test_one','name':'test_case[b]'}]}
    cases=[ET.Element('testcase',{'classname':row['classname'],'name':row['name']}) for row in collection['items']]
    acceptance.validate_offline_collection(cases,collection)
    if change=='missing_case':cases.pop()
    elif change=='duplicate_case':cases.append(deepcopy(cases[0]))
    elif change=='extra_case':cases.append(ET.Element('testcase',{'classname':'unrelated','name':'test_else'}))
    elif change=='wrong_parameter':cases[1].set('name','test_case[c]')
    elif change=='failure':ET.SubElement(cases[1],'failure')
    elif change=='deselected':collection['deselected']=1
    elif change=='empty':collection['items']=[];cases=[]
    with pytest.raises(SystemExit):acceptance.validate_offline_collection(cases,collection)


def test_real_pytest_collection_receipt_matches_junit_with_parameter_delimiters(tmp_path):
    import os
    import subprocess
    import xml.etree.ElementTree as ET
    test=tmp_path/'test_collection_fixture.py'
    test.write_text("import pytest\n@pytest.mark.parametrize('value',['x::y','[bracket]'])\ndef test_delimiters(value):\n assert value\n",encoding='utf-8')
    collection=tmp_path/'collection.json';junit=tmp_path/'junit.xml'
    env={**os.environ,'PYTHONPATH':os.pathsep.join([str(ROOT),str(ROOT/'src')]),
         'CODEX_DEEPSEEK_TEST_COLLECTION_PATH':str(collection)}
    env.pop('PYTEST_ADDOPTS',None);env.pop('PYTEST_PLUGINS',None)
    result=subprocess.run([sys.executable,'-m','pytest','-p','run_acceptance','-q',str(test),'--junitxml='+str(junit)],
                          cwd=tmp_path,env=env,capture_output=True,text=True,encoding='utf-8',timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    proof=json.loads(collection.read_text(encoding='utf-8'))
    assert len(proof['items'])==2
    acceptance.validate_offline_collection(ET.parse(junit).getroot().findall('.//testcase'),proof)


def test_rpc_companion_receipt_cannot_be_borrowed_through_a_symlink(tmp_path):
    trace,_,_,event,_=trace_fixture(tmp_path/'session')
    external=tmp_path/'borrowed-events.json';external.write_bytes(event.read_bytes());event.unlink()
    event.symlink_to(external)
    observed,_,errors=collect(trace)
    assert not observed and 'escaped its session directory' in errors[0]['reason']


def test_only_exact_os_inapplicable_cases_may_skip():
    linux=acceptance.expected_offline_skips('x86_64-unknown-linux-gnu')
    mac=acceptance.expected_offline_skips('aarch64-apple-darwin')
    windows=acceptance.expected_offline_skips('x86_64-pc-windows-msvc')
    assert len(linux)==8 and len(mac)==9 and len(windows)==1
    assert not any('terminal_acceptance' in key or 'test_activation_preserves_unrelated' in key for key in mac)
    assert set(mac)-set(linux)==set(windows)
    assert 'test_windows_console::test_native_console_interrupt_keeps_original_signal_and_exit_status[1-3221225786-SIGBREAK]' in linux
    assert len([name for name in linux if name.startswith('test_windows_launchers::')])==3


@pytest.mark.parametrize('change',['equal','missing','changed','both_empty','extra'])
def test_schema_equality_requires_complete_frozen_inventory(tmp_path,change):
    import importlib.util
    spec=importlib.util.spec_from_file_location('schema_acceptance',ROOT/'verify_native_schema.py')
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
    a=tmp_path/'official';b=tmp_path/'patched';a.mkdir();b.mkdir()
    for root in [a,b]:
        (root/'One.json').write_text('{"type":"object","properties":{"name":{"type":"string"}}}')
        (root/'Two.json').write_text('{"type":"integer"}')
    if change=='equal':(b/'One.json').write_text('{"properties":{"name":{"type":"string"}},"type":"object"}')
    elif change=='missing':(b/'Two.json').unlink()
    elif change=='changed':(b/'One.json').write_text('{"type":"string"}')
    elif change=='both_empty':
        for root in [a,b]:
            for path in root.iterdir():path.unlink()
    elif change=='extra':(b/'Extra.json').write_text('{}')
    result=verifier.compare_schemas(a,b,2)
    assert result['passed']==(change=='equal')


def test_native_browser_result_decodes_nullable_structured_content_without_swallowing_errors():
    spec=importlib.util.spec_from_file_location('native_browser_acceptance',ROOT/'verify_browser_runtime.py')
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
    payload={'ref_id':'page_1','match_count':1}
    reply={'isError':False,'structuredContent':None,'content':[{'type':'text','text':json.dumps(payload)}]}
    assert verifier.unwrap(reply)==payload
    reply['isError']=True
    with pytest.raises(RuntimeError,match='Native MCP tool error'):verifier.unwrap(reply)
    reply['isError']=False;reply['content']=[{'type':'text','text':'tool failed'}]
    with pytest.raises(json.JSONDecodeError):verifier.unwrap(reply)


@pytest.mark.parametrize('change',['none','target','source_archive','source_count','protected_count','external_dependencies',
                                  'no_full_scan','no_replay','missing_patch','extra_changed_file','missing_verifier'])
def test_source_audit_cannot_omit_native_business_or_build_identity_checks(change):
    from build_native import SOURCE_ASSET
    native=acceptance.load(ROOT/'native/runtime-x86_64-unknown-linux-gnu.json')
    common=acceptance.load(ROOT/'native/source-changes.json')
    proof={'passed':True,'target':native['target'],'upstream_commit':acceptance.BASELINE_COMMIT,
           'upstream_archive_sha256':SOURCE_ASSET[1],'source_files':9353,'unchanged_protected_runtime_files':348,
           'external_cargo_packages_unchanged':True,'full_source_hashes_verified':True,'patch_replay_passed':True,
           'changed_native_files':sorted(row['path'] for row in common['files']),
           'patches':{'codex-runtime.patch':acceptance.sha(ROOT/'native/codex-runtime.patch')},
           'verifier_sha256':{name:acceptance.sha(ROOT/name) for name in ['verify_native_source.py','build_native.py']}}
    if change=='target':proof['target']='x86_64-pc-windows-msvc'
    elif change=='source_archive':proof['upstream_archive_sha256']='unrelated'
    elif change=='source_count':proof['source_files']=9349
    elif change=='protected_count':proof['unchanged_protected_runtime_files']=347
    elif change=='external_dependencies':proof['external_cargo_packages_unchanged']=False
    elif change=='no_full_scan':proof['full_source_hashes_verified']=False
    elif change=='no_replay':proof['patch_replay_passed']=False
    elif change=='missing_patch':proof['patches']={}
    elif change=='extra_changed_file':proof['changed_native_files'].append('codex-rs/core/src/session.rs')
    elif change=='missing_verifier':proof['verifier_sha256'].pop('build_native.py')
    if change=='none':assert acceptance.validate_native_source_audit(proof,native)['unchanged_protected_runtime_files']==348
    else:
        with pytest.raises(SystemExit):acceptance.validate_native_source_audit(proof,native)


@pytest.mark.parametrize('script',['run_acceptance.py','verify_remaining_methods.py','verify_local_account.py'])
def test_optimized_python_cannot_silently_disable_acceptance_assertions(script):
    import os
    import subprocess
    environment={**os.environ,'PYTHONPATH':os.pathsep.join([str(ROOT),str(ROOT/'src')])}
    result=subprocess.run([sys.executable,'-O',str(ROOT/script),'--help'],env=environment,
                          capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode!=0
    assert 'assertions enabled' in result.stderr


@pytest.mark.parametrize('change',['none','target','installation','bwrap_hash','verifier','native_exit','tampered_exit',
                                  'boolean_exit','missing_denial','missing_digest_error','outside_changed','tampered_effect','missing_effect_check'])
def test_linux_bundled_resource_gate_requires_actual_enforcement_and_tamper_rejection(tmp_path,change):
    native={'target':'x86_64-unknown-linux-gnu','files':{'bin/codex':'native-cli','codex-resources/bwrap':'bundled-resource'}}
    installation={'source_id':'fixture','console_script_sha256':'entry'}
    artifact={'installation':deepcopy(installation),'native_manifest':deepcopy(native),'target':native['target'],
              'entry_sha256':'entry','binary_sha256':'native-cli','bwrap_sha256':'bundled-resource',
              'verifier_sha256':acceptance.sha(ROOT/'verify_linux_resources.py')}
    proof={'installation':deepcopy(installation),'target':native['target'],'native_exit_code':0,'tampered_exit_code':1,
           **dict.fromkeys(['passed','bundled_resource_forced','native_inside_write','native_outside_write_denied',
                            'tampered_bwrap_rejected_before_execution','verifier_and_artifact_unchanged'],True)}
    (tmp_path/'work').mkdir();(tmp_path/'outside.txt').write_bytes(b'PRESERVE')
    (tmp_path/'native-sandbox.txt').write_text('NATIVE_OUTSIDE_DENIED\n')
    (tmp_path/'native-digest-rejection.txt').write_text('bundled bubblewrap digest mismatch\n')
    if change=='target':artifact['target']='aarch64-apple-darwin'
    elif change=='installation':artifact['installation']['source_id']='another'
    elif change=='bwrap_hash':artifact['bwrap_sha256']='another-resource'
    elif change=='verifier':artifact['verifier_sha256']='another-verifier'
    elif change=='native_exit':proof['native_exit_code']=1
    elif change=='tampered_exit':proof['tampered_exit_code']=0
    elif change=='boolean_exit':proof['native_exit_code']=False
    elif change=='missing_denial':(tmp_path/'native-sandbox.txt').write_text('not denied')
    elif change=='missing_digest_error':(tmp_path/'native-digest-rejection.txt').write_text('unrelated failure')
    elif change=='outside_changed':(tmp_path/'outside.txt').write_bytes(b'ESCAPED')
    elif change=='tampered_effect':(tmp_path/'work/inside.txt').write_text('unexpected command ran')
    elif change=='missing_effect_check':proof.pop('native_inside_write')
    for name,data in [('artifact',artifact),('verdict',proof)]:
        (tmp_path/(name+'.json')).write_text(json.dumps(data),encoding='utf-8')
    if change=='none':assert acceptance.validate_linux_resources(tmp_path,installation,native)['native_outside_write_denied']
    else:
        with pytest.raises(SystemExit):acceptance.validate_linux_resources(tmp_path,installation,native)


def terminal_fixture(root,entry,interrupt=False):
    import hashlib
    names={'verify_tui_audit.py','terminal_acceptance.py','credential_acceptance.py'}
    if interrupt:names|={'verify_tui_interrupt.py','process_acceptance.py'}
    files={name:acceptance.sha(ROOT/name) for name in sorted(names)}
    installation={'source_id':'fixture','console_script_sha256':acceptance.sha(entry),'native_archive_sha256':'native'}
    qa={'qa_files_sha256':files,'qa_snapshot_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
        'entry':str(entry),'entry_sha256':installation['console_script_sha256'],'adapter_source_id':'fixture','native_archive_sha256':'native'}
    root.mkdir();(root/'home/sessions').mkdir(parents=True);(root/'work').mkdir()
    (root/'qa-source.json').write_text(json.dumps(qa),encoding='utf-8')
    (root/'home/config.toml').write_text('sandbox_mode="workspace-write"\n')
    native={'target':'x86_64-unknown-linux-gnu'}
    if interrupt:
        transcript='• TUI_INTERRUPT_RECOVERED_2719\n'.encode();(root/'transcript.pty').write_bytes(transcript)
        content=b'AFTER_CTRL_C_2719';(root/'work/after-interrupt.txt').write_bytes(content)
        records=[{'kind':'session_start'},{'dir':'from_tui','kind':'op','payload':'CleanBackgroundTerminals'},{'kind':'session_end'}]
        (root/'native-session.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in records))
        events=[{'type':'session_meta','payload':{'id':'thread'}},
                {'type':'response_item','payload':{'type':'custom_tool_call','call_id':'fixture-exec','input':'exec_command interrupt_fixture.py'}},
                {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'fixture-exec','output':'{"session_id":1234}'}},
                {'type':'event_msg','payload':{'type':'turn_aborted','turn_id':'old','reason':'interrupted'}},
                {'type':'event_msg','payload':{'type':'task_complete','turn_id':'new','last_agent_message':'TUI_INTERRUPT_RECOVERED_2719'}}]
        (root/'home/sessions/rollout.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events))
        result={'passed':True,'entry':str(entry),'terminal':'pty','backend':'shared','sandbox_mode':'workspace-write',
                'qa_snapshot_sha256':qa['qa_snapshot_sha256'],'thread_id':'thread','interrupted_turn_id':'old','continued_turn_id':'new',
                'background_retained_after_interrupt':True,'native_stop_terminated_fixture':True,'terminal_exit_code':0,
                'background_session':{'call_id':'fixture-exec','session_id':'1234'},
                'continued_file_sha256':hashlib.sha256(content).hexdigest(),'fixture_pid':123,'fixture_birth':'saved-birth',
                'native_session_log_records':len(records),'transcript_bytes':len(transcript)}
    else:
        result=[]
        for index,(label,reply) in enumerate([('initial','TUI_VERIFIED_INITIAL_5271'),('cold-resume','TUI_VERIFIED_RESUMED_5271')]):
            (root/(label+'.pty')).write_text('• '+reply+'\n',encoding='utf-8')
            (root/(label+'-native-session.jsonl')).write_text('{"kind":"session_start"}\n{"kind":"session_end"}\n')
            result.append({'name':label,'passed':True,'terminal':'pty','backend':'shared','thread_id':'same-thread',
                           'tui_pid':100+index,'daemon_pid':200+index,'native_rollout_trace_files_changed':1,
                           'native_rollout_trace_files':index+1,'rendered_reply':reply})
        traces=root/'native-rollout-traces';traces.mkdir()
        (traces/'one.json').write_text('{}');(traces/'two.json').write_text('{}')
        (root/'work/tui-first.txt').write_bytes(b'TUI_FIRST_5271');(root/'work/tui-resumed.txt').write_bytes(b'TUI_FIRST_5271_RESUMED')
    (root/'results.json').write_text(json.dumps(result),encoding='utf-8')
    return installation,native,result


@pytest.mark.parametrize('change',['none','new_thread','same_process','same_daemon','prompt_only','missing_traces','stale_phase','wrong_file',
                                  'title_after_end','unknown_after_end','duplicate_end','missing_end'])
def test_native_tui_cold_resume_cannot_borrow_an_old_reply_or_another_session(tmp_path,change):
    entry=tmp_path/'entry';entry.write_bytes(b'actual entry');root=tmp_path/'run'
    installation,native,results=terminal_fixture(root,entry)
    if change=='new_thread':results[1]['thread_id']='another'
    elif change=='same_process':results[1]['tui_pid']=results[0]['tui_pid']
    elif change=='same_daemon':results[1]['daemon_pid']=results[0]['daemon_pid']
    elif change=='prompt_only':(root/'cold-resume.pty').write_text('Please reply TUI_VERIFIED_RESUMED_5271')
    elif change=='missing_traces':(root/'native-rollout-traces/two.json').unlink()
    elif change=='stale_phase':results[1]['native_rollout_trace_files_changed']=0
    elif change=='wrong_file':(root/'work/tui-resumed.txt').write_bytes(b'TUI_FIRST_5271')
    elif change in {'title_after_end','unknown_after_end','duplicate_end','missing_end'}:
        log=root/'cold-resume-native-session.jsonl';records=[json.loads(line) for line in log.read_text().splitlines()]
        if change=='missing_end':records.pop()
        else:records.append({'kind':'session_end'} if change=='duplicate_end' else {'dir':'to_tui','kind':'app_event','variant':'GeneratedThreadTitle' if change=='title_after_end' else 'Exit'})
        log.write_text(''.join(json.dumps(row)+'\n' for row in records))
    (root/'results.json').write_text(json.dumps(results))
    if change in {'none','title_after_end'}:assert acceptance.validate_terminal_qa(root,entry,installation,native,'workspace-write')['same_thread_cold_resume']
    else:
        with pytest.raises(SystemExit):acceptance.validate_terminal_qa(root,entry,installation,native,'workspace-write')


@pytest.mark.parametrize('change',['none','same_turn','cleanup_fallback','no_native_stop','still_running','prompt_only','different_thread',
                                  'wrong_order','wrong_file','sandbox_mode','wrong_source','wrong_installation',
                                  'missing_background','wrong_background','missing_yield','yield_after_interrupt',
                                  'title_after_end','unknown_after_end','duplicate_end','missing_end'])
def test_tui_interrupt_must_continue_in_place_and_really_stop_its_background_process(tmp_path,monkeypatch,change):
    import codex_deepseek.processes
    monkeypatch.setattr(codex_deepseek.processes,'matches',lambda pid,birth:change=='still_running')
    entry=tmp_path/'entry';entry.write_bytes(b'actual entry');root=tmp_path/'run'
    installation,native,proof=terminal_fixture(root,entry,True)
    if change=='same_turn':proof['continued_turn_id']=proof['interrupted_turn_id']
    elif change=='cleanup_fallback':proof['failed_run_cleanup_required']=True
    elif change=='no_native_stop':(root/'native-session.jsonl').write_text('{"kind":"session_start"}\n{"kind":"session_end"}\n')
    elif change=='prompt_only':(root/'transcript.pty').write_text('Please reply TUI_INTERRUPT_RECOVERED_2719')
    elif change=='different_thread':proof['thread_id']='another'
    elif change=='wrong_order':
        path=root/'home/sessions/rollout.jsonl';lines=path.read_text().splitlines();path.write_text('\n'.join([*lines[:-2],lines[-1],lines[-2]])+'\n')
    elif change=='missing_background':proof.pop('background_session')
    elif change=='wrong_background':proof['background_session']['session_id']='9999'
    elif change in {'missing_yield','yield_after_interrupt'}:
        path=root/'home/sessions/rollout.jsonl';lines=path.read_text().splitlines()
        path.write_text('\n'.join([lines[i] for i in ([0,1,3,4] if change=='missing_yield' else [0,1,3,2,4])])+'\n')
    elif change=='wrong_file':(root/'work/after-interrupt.txt').write_bytes(b'old')
    elif change=='sandbox_mode':proof['sandbox_mode']='danger-full-access'
    elif change in {'title_after_end','unknown_after_end','duplicate_end','missing_end'}:
        log=root/'native-session.jsonl';records=[json.loads(line) for line in log.read_text().splitlines()]
        if change=='missing_end':records.pop()
        else:records.append({'kind':'session_end'} if change=='duplicate_end' else {'dir':'to_tui','kind':'app_event','variant':'GeneratedThreadTitle' if change=='title_after_end' else 'Exit'})
        log.write_text(''.join(json.dumps(row)+'\n' for row in records));proof['native_session_log_records']=len(records)
    elif change in {'wrong_source','wrong_installation'}:
        path=root/'qa-source.json';qa=json.loads(path.read_text())
        if change=='wrong_source':qa['qa_files_sha256']['verify_tui_interrupt.py']='wrong'
        else:qa['adapter_source_id']='other-source'
        path.write_text(json.dumps(qa))
    (root/'results.json').write_text(json.dumps(proof))
    if change in {'none','title_after_end'}:assert acceptance.validate_terminal_qa(root,entry,installation,native,'workspace-write',interrupt=True)['native_stop_kernel_cleanup_verified']
    else:
        with pytest.raises(SystemExit):acceptance.validate_terminal_qa(root,entry,installation,native,'workspace-write',interrupt=True)


def test_resolved_aliases_keep_rpc_and_terminal_evidence_inside_exact_roots(tmp_path):
    import os
    own=tmp_path/'own';own.mkdir();child=own/'session';trace,_,_,_,launch=trace_fixture(child)
    entry=own/'entry';entry.write_bytes(b'entry')
    alias=lambda path:Path('\\\\?\\'+str(path)) if os.name=='nt' else path
    launch.write_text(json.dumps({'resolved_entry':str(alias(entry)),'entry_sha256':'entry-hash'}),encoding='utf-8')
    observed,sessions,errors=rpc.trace_observations(alias(trace),str(entry),'entry-hash','x86_64-pc-windows-msvc' if os.name=='nt' else 'aarch64-apple-darwin')
    assert sessions==1 and not errors and 'account/read' in observed
    (own/'tui.log').write_text('EVIDENCE '+str(alias(child))+'\n',encoding='utf-8')
    assert acceptance.comparison_path(acceptance.gate_evidence(own,'tui'))==child
    roots=rpc.regression_roots(alias(own),{'evidence_roots':[str(own)]},{'gates':[{'exit':0,'log':'tui.log'}]})
    assert {rpc.comparison_path(path) for path in roots}=={own,child}
    outside=tmp_path/'own-sibling';outside.mkdir()
    (own/'tui.log').write_text('EVIDENCE '+str(alias(outside))+'\n',encoding='utf-8')
    with pytest.raises(SystemExit,match='escaped'):acceptance.gate_evidence(own,'tui')
    with pytest.raises(SystemExit,match='escaped'):rpc.regression_roots(alias(own),{'evidence_roots':[str(own)]},{'gates':[{'exit':0,'log':'tui.log'}]})


def recovery_fixture(root):
    import sqlite3
    root.mkdir()
    metadata,native,_=installed();answer='{"marker":"RECOVERY_NATIVE","value":437}'
    retry={'threadId':'thread','willRetry':True,'error':{'message':'controlled fault'}}
    item={'type':'agentMessage','text':answer};receipt=[['adapter_error','deepseek','resp_controlled_fault','structured output is not JSON'],['completed','deepseek','real-request',None]]
    observed={'thread':'thread','answer':answer,'answer_valid':True,'items':[item],'native_retry_errors':[retry],'client_tool_executions':[]}
    events=[{'method':'error','params':retry},{'method':'item/completed','params':{'threadId':'thread','item':item}},
            {'method':'turn/completed','params':{'threadId':'thread','turn':{'status':'completed'}}}]
    files={'request-bodies.json':[{'input':[]},{'input':[{'name':'cx_native_continuation_context'}]}],
           'receipts.json':receipt,'observed.json':observed,'events-1.json':events,'launch-1.json':{'entry_sha256':'native'}}
    for name,value in files.items():(root/name).write_text(json.dumps(value),encoding='utf-8')
    (root/'client-requests.jsonl').write_text('{"method":"turn/start","id":1}\n',encoding='utf-8')
    with sqlite3.connect(root/'state.sqlite') as db:
        db.execute('CREATE TABLE receipts(status TEXT, provider TEXT, response_id TEXT, detail TEXT, started INTEGER)')
        db.executemany('INSERT INTO receipts VALUES(?,?,?,?,?)',[row+[index] for index,row in enumerate(receipt)])
    artifact={'kind':'schema','installation':metadata,'target':native['target'],'native_manifest':native,
              'entry_sha256':metadata['console_script_sha256'],'native_binary_sha256':'native','controller_optimize':0,
              'verifier_sha256':{name:acceptance.sha(ROOT/name) for name in ['verify_protocol_recovery.py','verify_live.py','install.py']}}
    proof={key:value for key,value in observed.items() if key!='items'}
    proof.update(kind='schema',installation=metadata,target=native['target'],passed=True,verifier_and_artifact_unchanged=True,
                 requests=2,receipts=receipt,continuation_context_observed=True)
    proof['evidence_sha256']={name:acceptance.sha(root/name) for name in [*files,'client-requests.jsonl','state.sqlite']}
    (root/'artifact.json').write_text(json.dumps(artifact),encoding='utf-8');(root/'verdict.json').write_text(json.dumps(proof),encoding='utf-8')
    return metadata,native,artifact,proof


@pytest.mark.parametrize('change',['none','installation','target','native_manifest','native_binary','entry','optimized','verifier_missing',
                                  'verifier_changed','integrity','unbound_raw','raw_changed','missing_raw','second_session','request_count',
                                  'receipts_database','real_response','context','retry','tool_executed','incomplete_turn',
                                  'summary_answer','invalid_answer','items','kind'])
def test_protocol_recovery_cannot_publish_without_bound_raw_native_retry_and_final_effect(tmp_path,change):
    root=tmp_path/'recovery';metadata,native,artifact,proof=recovery_fixture(root)
    def edit(name,callback):
        value=json.loads((root/name).read_text(encoding='utf-8'));callback(value)
        (root/name).write_text(json.dumps(value),encoding='utf-8');proof['evidence_sha256'][name]=acceptance.sha(root/name)
    if change=='installation':artifact['installation']={**metadata,'source_id':'other'}
    elif change=='target':artifact['target']='other'
    elif change=='native_manifest':artifact['native_manifest']={**native,'upstream_commit':'other'}
    elif change=='native_binary':artifact['native_binary_sha256']='other'
    elif change=='entry':artifact['entry_sha256']='other'
    elif change=='optimized':artifact['controller_optimize']=1
    elif change=='verifier_missing':artifact['verifier_sha256'].pop('verify_live.py')
    elif change=='verifier_changed':artifact['verifier_sha256']['verify_live.py']='other'
    elif change=='integrity':proof['verifier_and_artifact_unchanged']=False
    elif change=='unbound_raw':proof['evidence_sha256']={}
    elif change=='raw_changed':(root/'request-bodies.json').write_text('[]')
    elif change=='missing_raw':proof['evidence_sha256'].pop('state.sqlite')
    elif change=='second_session':(root/'events-2.json').write_text('[]')
    elif change=='request_count':proof['requests']=3
    elif change=='receipts_database':edit('receipts.json',lambda rows:rows[-1].__setitem__(1,'different-provider'))
    elif change=='real_response':
        import sqlite3
        edit('receipts.json',lambda rows:rows[-1].__setitem__(2,'resp_controlled_fault'))
        proof['receipts']=json.loads((root/'receipts.json').read_text())
        with sqlite3.connect(root/'state.sqlite') as db:db.execute('UPDATE receipts SET response_id=? WHERE started=1',('resp_controlled_fault',))
        proof['evidence_sha256']['state.sqlite']=acceptance.sha(root/'state.sqlite')
    elif change=='context':edit('request-bodies.json',lambda rows:rows[-1].__setitem__('input',[]))
    elif change=='retry':edit('events-1.json',lambda rows:rows.pop(0))
    elif change=='tool_executed':edit('events-1.json',lambda rows:rows.append({'method':'item/tool/call','params':{}}))
    elif change=='incomplete_turn':edit('events-1.json',lambda rows:rows[-1]['params']['turn'].__setitem__('status','interrupted'))
    elif change=='summary_answer':proof['answer']='different'
    elif change=='invalid_answer':
        bad='{"marker":"RECOVERY_NATIVE","value":438}'
        edit('events-1.json',lambda rows:rows[1]['params']['item'].__setitem__('text',bad))
        edit('observed.json',lambda row:(row.__setitem__('answer',bad),row['items'][0].__setitem__('text',bad)))
        proof['answer']=bad
    elif change=='items':edit('observed.json',lambda row:row.__setitem__('items',[]))
    elif change=='kind':artifact['kind']='undeclared-tool'
    (root/'artifact.json').write_text(json.dumps(artifact),encoding='utf-8');(root/'verdict.json').write_text(json.dumps(proof),encoding='utf-8')
    if change=='none':assert acceptance.validate_protocol_recovery(root,metadata,native)['passed']
    else:
        with pytest.raises(SystemExit):acceptance.validate_protocol_recovery(root,metadata,native)


@pytest.mark.parametrize('change',['none','verifier','integrity','raw_response','saved_response','missing_witness','duplicate_witness','outside_root','sessions','superseded_terminal'])
def test_rpc_aggregate_cannot_reuse_stale_or_unbound_success_witnesses(tmp_path,change):
    trace,_,_,events,_=trace_fixture(tmp_path/'owned/session');target='x86_64-unknown-linux-gnu'
    found,count,errors=rpc.trace_observations(trace,'/selected/entry','entry-hash',target);assert not errors
    report={'methods':deepcopy(found),'native_client_sessions':count,'verifier_and_artifact_unchanged':True,
            'verifier_sha256':{name:acceptance.sha(ROOT/name) for name in rpc.VERIFIERS}}
    roots={tmp_path/'owned':('/selected/entry','entry-hash')};methods=set(found)
    if change=='verifier':report['verifier_sha256']['verify_rpc_observations.py']='stale'
    elif change=='integrity':report['verifier_and_artifact_unchanged']=False
    elif change=='raw_response':
        value=json.loads(events.read_text());value[1]['result']={'account':'changed'};events.write_text(json.dumps(value))
    elif change=='saved_response':report['methods']['account/read'][0]['result_sha256']='invented'
    elif change=='missing_witness':report['methods']['account/read']=[]
    elif change=='duplicate_witness':report['methods']['account/read']*=2
    elif change=='outside_root':roots={tmp_path/'another':('/selected/entry','entry-hash')}
    elif change=='sessions':report['native_client_sessions']+=1
    if change=='none':assert acceptance.validate_rpc_witnesses(report,roots,target,methods)['methods_reconstructed']==len(methods)
    else:
        with pytest.raises(SystemExit):acceptance.validate_rpc_witnesses(report,roots,target,methods,excluded_roots=[trace.parent] if change=='superseded_terminal' else [])


@pytest.mark.parametrize('change',['none','no_explicit_snapshot','snapshot_bytes','snapshot_extra','manifest_digest','runtime_source',
                                  'missing_verifier','standard_gate','shared_helper','terminal_without_revalidation','terminal_revalidated','shared_terminal_helper'])
def test_frozen_regression_lineage_only_admits_reporting_methods_and_test_changes(tmp_path,monkeypatch,change):
    import hashlib
    producer=tmp_path/'producer';consumer=tmp_path/'consumer';producer.mkdir();consumer.mkdir()
    files={'assemble_release_acceptance.py':b'old consumer','verify_remaining_local.py':b'old methods',
           'verify_safety.py':b'unchanged gate','verify_live.py':b'unchanged helper','src/pkg/runtime.py':b'unchanged runtime','tests/test_case.py':b'old test',
           'verify_tui_audit.py':b'original terminal verifier','terminal_acceptance.py':b'unchanged terminal helper'}
    for root in [producer,consumer]:
        for name,data in files.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (consumer/'verify_remaining_local.py').write_bytes(b'PTY fragment handling fixed')
    (consumer/'assemble_release_acceptance.py').write_bytes(b'explicit producer lineage consumer')
    (consumer/'tests/test_case.py').write_bytes(b'new fragment regression')
    metadata={'source_id':'same-product','source_sha256':{'src/pkg/runtime.py':acceptance.sha(producer/'src/pkg/runtime.py')}}
    hashes={name:acceptance.sha(producer/name) for name in files};receipt={'source_id':'same-product','files':hashes,
        'qa_sha256':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()}
    artifact={'verifier_source_sha256':acceptance.qa_python_hashes(producer)}
    if change=='snapshot_bytes':(producer/'verify_safety.py').write_bytes(b'edited old gate')
    elif change=='snapshot_extra':(producer/'unbound.json').write_text('{}')
    elif change=='manifest_digest':receipt['qa_sha256']='wrong'
    elif change=='runtime_source':metadata['source_sha256']['src/pkg/runtime.py']='different-runtime'
    elif change=='missing_verifier':artifact['verifier_source_sha256'].pop('verify_safety.py')
    elif change=='standard_gate':(consumer/'verify_safety.py').write_bytes(b'changed runtime gate')
    elif change=='shared_helper':(consumer/'verify_live.py').write_bytes(b'changed shared execution helper')
    elif change in {'terminal_without_revalidation','terminal_revalidated'}:(consumer/'verify_tui_audit.py').write_bytes(b'known async title after session end')
    elif change=='shared_terminal_helper':(consumer/'terminal_acceptance.py').write_bytes(b'changed terminal execution helper')
    (tmp_path/'producer.manifest.json').write_text(json.dumps(receipt));monkeypatch.setattr(acceptance,'SOURCE',consumer)
    if change in {'none','terminal_revalidated'}:
        proof=acceptance.validate_regression_source(artifact,metadata,producer,terminal_revalidated=change=='terminal_revalidated')
        assert proof['producer']=='explicit_frozen_snapshot' and proof['producer_snapshot_sha256']==receipt['qa_sha256']
        assert proof['changed_qa_files']==['assemble_release_acceptance.py','tests/test_case.py','verify_remaining_local.py']+(['verify_tui_audit.py'] if change=='terminal_revalidated' else [])
    else:
        with pytest.raises(SystemExit):acceptance.validate_regression_source(artifact,metadata,None if change=='no_explicit_snapshot' else producer,terminal_revalidated=change=='shared_terminal_helper')


@pytest.mark.parametrize('change',['none','source','subset','runtime','filtered','junit_missing'])
def test_current_offline_supplement_requires_full_current_collection_and_exact_target(tmp_path,monkeypatch,change):
    import xml.etree.ElementTree as ET
    source=tmp_path/'source';source.mkdir();(source/'assemble_release_acceptance.py').write_text('current consumer')
    root=tmp_path/'offline';root.mkdir();metadata,native,_=installed();scope={'target':native['target']}
    artifact={'installation':metadata,'native_manifest':native,'target':native['target'],'baseline_matrix_sha256':acceptance.BASELINE_MATRIX_SHA256,
              'controller_environment':{'optimize':0},'selected_gates':['offline'],'verifier_source_sha256':acceptance.qa_python_hashes(source)}
    xml=ET.Element('testsuite');items=[]
    for i,(name,reason) in enumerate([('test_current::test_pass',None),*acceptance.expected_offline_skips(native['target']).items()]):
        module,case=name.split('::');classname='tests.'+module;node=ET.SubElement(xml,'testcase',classname=classname,name=case)
        if reason:ET.SubElement(node,'skipped',type='pytest.skip',message=reason)
        items.append({'nodeid':str(i),'classname':classname,'name':case})
    collection={'schema_version':1,'deselected':0,'items':items}
    if change=='source':artifact['verifier_source_sha256']['assemble_release_acceptance.py']='stale'
    elif change=='subset':artifact['selected_gates']=['offline','live']
    elif change=='runtime':artifact['installation']={**metadata,'source_id':'different'}
    elif change=='filtered':collection['deselected']=1
    elif change=='junit_missing':xml.remove(xml[0])
    ET.ElementTree(xml).write(root/'offline.xml',encoding='utf-8');(root/'offline-collection.json').write_text(json.dumps(collection))
    verdict={'passed':True,'entry_unchanged':True,'verifier_source_unchanged':True,'gates':[{'gate':'offline','exit':0}],
             'offline_collection_sha256':acceptance.sha(root/'offline-collection.json')}
    (root/'artifact.json').write_text(json.dumps(artifact));(root/'verdict.json').write_text(json.dumps(verdict));monkeypatch.setattr(acceptance,'SOURCE',source)
    if change=='none':assert acceptance.validate_offline_run(root,metadata,native,scope,current=True)['tests_passed']==1
    else:
        with pytest.raises(SystemExit):acceptance.validate_offline_run(root,metadata,native,scope,current=True)


@pytest.mark.parametrize('change',['none','default','wrong_snapshot','execution_helper','missing_helper'])
def test_methods_can_only_reuse_the_exact_audited_scope_helper(tmp_path,monkeypatch,change):
    current=tmp_path/'current';snapshot=tmp_path/'snapshot';current.mkdir();snapshot.mkdir()
    names=['verify_remaining_methods.py','verify_remaining_local.py','verify_live.py','assemble_release_acceptance.py','process_acceptance.py','install.py']
    for name in names:(current/name).write_text('current '+name)
    (snapshot/'assemble_release_acceptance.py').write_text('audited scope helper')
    artifact={'verifier_sha256':{name:acceptance.sha(current/name) for name in names}}
    artifact['verifier_sha256']['assemble_release_acceptance.py']=acceptance.sha(snapshot/'assemble_release_acceptance.py')
    if change=='wrong_snapshot':(snapshot/'assemble_release_acceptance.py').write_text('unrelated old source')
    elif change=='execution_helper':artifact['verifier_sha256']['verify_live.py']='old helper'
    elif change=='missing_helper':artifact['verifier_sha256'].pop('process_acceptance.py')
    monkeypatch.setattr(acceptance,'SOURCE',current)
    if change=='none':assert acceptance.validate_method_verifiers(artifact,snapshot)['scope_helper_from_regression_snapshot']['producer_sha256']==artifact['verifier_sha256']['assemble_release_acceptance.py']
    else:
        with pytest.raises(SystemExit):acceptance.validate_method_verifiers(artifact,None if change=='default' else snapshot)


def test_direct_search_gate_never_expands_native_rpc_evidence_roots(tmp_path):
    root=tmp_path/'regression';root.mkdir();outside=tmp_path/'unrelated';outside.mkdir()
    (root/'search.log').write_text('EVIDENCE '+str(outside)+'\n')
    artifact={'evidence_roots':[str(root)]};gate={'gate':'search','exit':0,'log':'search.log'}
    assert rpc.regression_roots(root,artifact,{'gates':[gate]})=={root}
    for name in ['live','local-methods']:
        with pytest.raises(SystemExit,match='escaped'):rpc.regression_roots(root,artifact,{'gates':[{**gate,'gate':name}]})
    (root/'search.log').unlink()
    with pytest.raises(FileNotFoundError):rpc.regression_roots(root,artifact,{'gates':[gate]})


@pytest.mark.parametrize('change',['none','original_already_passed','no_replacement','nonterminal_failed','missing_original','duplicate_original',
                                  'original_mutated','inconsistent_original','replacement_one_gate','replacement_extra_gate','replacement_failed',
                                  'foreign_installation','foreign_target','different_sandbox','stale_qa','report_only_change','missing_execution_source',
                                  'disabled_assertions','changed_entry','replacement_mutated'])
def test_terminal_revalidation_replaces_only_two_current_gates_and_preserves_original_failure(tmp_path,monkeypatch,change):
    source=tmp_path/'source';source.mkdir();(source/'assemble_release_acceptance.py').write_text('current consumer');monkeypatch.setattr(acceptance,'SOURCE',source)
    root=tmp_path/'new-terminal';root.mkdir();metadata,native,_=installed('x86_64-unknown-linux-gnu')
    for name in acceptance.TERMINAL_EXECUTION_SOURCES|set(metadata['adapter_source_sha256']):
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('execution source '+name)
    original={'entry':'entry','entry_sha256':'entry-hash','release':'release','installation':metadata,'native_manifest':native,'target':native['target'],
              'tui_sandbox_mode':'workspace-write','required_local_methods':['local'],'baseline_matrix_sha256':acceptance.BASELINE_MATRIX_SHA256,
              'selected_gates':sorted(acceptance.GATES),'controller_environment':{'optimize':0}}
    rows=[{'gate':name,'exit':1 if name=='tui' else 0,'seconds':1,'log':name+'.log'} for name in sorted(acceptance.GATES)]
    before={'passed':False,'gates':rows,'entry_unchanged':True,'verifier_source_unchanged':True,'native_probes_unchanged':True}
    replacement={**deepcopy(original),'selected_gates':['tui','tui-interrupt'],'verifier_source_sha256':acceptance.qa_python_hashes(source)}
    after={'passed':True,'gates':[{'gate':name,'exit':0,'seconds':2,'log':name+'.log'} for name in ['tui','tui-interrupt']],
           'entry_unchanged':True,'verifier_source_unchanged':True,'native_probes_unchanged':True}
    if change=='original_already_passed':
        next(row for row in rows if row['gate']=='tui')['exit']=0;before['passed']=True
    elif change=='nonterminal_failed':next(row for row in rows if row['gate']=='guardian')['exit']=1
    elif change=='missing_original':rows.pop()
    elif change=='duplicate_original':rows[-1]=rows[0]
    elif change=='original_mutated':before['verifier_source_unchanged']=False
    elif change=='inconsistent_original':before['passed']=True
    elif change=='replacement_one_gate':after['gates'].pop()
    elif change=='replacement_extra_gate':after['gates'].append({'gate':'guardian','exit':0})
    elif change=='replacement_failed':after['gates'][0]['exit']=1
    elif change=='foreign_installation':replacement['installation']['source_id']='another'
    elif change=='foreign_target':replacement['target']='another'
    elif change=='different_sandbox':replacement['tui_sandbox_mode']='danger-full-access'
    elif change=='stale_qa':replacement['verifier_source_sha256']['terminal_acceptance.py']='old'
    elif change=='report_only_change':replacement['verifier_source_sha256']['assemble_release_acceptance.py']='previous report consumer'
    elif change=='missing_execution_source':replacement['verifier_source_sha256'].pop('process_acceptance.py')
    elif change=='disabled_assertions':replacement['controller_environment']['optimize']=1
    elif change=='changed_entry':replacement['entry_sha256']='different'
    elif change=='replacement_mutated':after['entry_unchanged']=False
    (root/'artifact.json').write_text(json.dumps(replacement));(root/'verdict.json').write_text(json.dumps(after))
    preserved=json.dumps(before,sort_keys=True)
    if change in {'none','original_already_passed','report_only_change'}:
        effective,lineage=acceptance.validate_standard_gates(original,before,root)
        assert len(effective)==30 and all(row['exit']==0 for row in effective)
        assert lineage['original_failed_gates']==([] if change=='original_already_passed' else ['tui'])
        assert json.dumps(before,sort_keys=True)==preserved
        assert all(row['seconds']==(2 if row['gate'] in acceptance.TERMINAL_GATES else 1) for row in effective)
        if change=='report_only_change':assert lineage['other_qa_changes_rechecked_by_current_offline']==['assemble_release_acceptance.py']
    else:
        with pytest.raises(SystemExit):acceptance.validate_standard_gates(original,before,None if change=='no_replacement' else root)


def test_original_failed_terminal_paths_cannot_leak_back_into_current_rpc_witnesses(tmp_path):
    root=tmp_path/'original';root.mkdir();failed=root/'tui/session';failed.mkdir(parents=True)
    (root/'tui.log').write_text('EVIDENCE '+str(failed)+'\n');(root/'tui-interrupt.log').write_text('failed before producing evidence\n')
    assert acceptance.superseded_terminal_roots(root)=={root/'tui',root/'tui-interrupt',failed}
    (root/'tui.log').write_text('EVIDENCE '+str(tmp_path/'foreign')+'\n')
    with pytest.raises(SystemExit,match='escaped'):acceptance.superseded_terminal_roots(root)


@pytest.mark.parametrize('change',['none','modified_raw','foreign_installation','wrong_case','missing_mechanism'])
def test_complex_runtime_requires_reconstruction_without_relabeling_application_failure(tmp_path,monkeypatch,change):
    from types import SimpleNamespace
    root=tmp_path/'httpcore';root.mkdir();entry=tmp_path/'entry';metadata={'source_id':'frozen'}
    proof={'case':'httpcore','installation':metadata,'runtime_mechanisms':{'passed':True},
           'application_task_outcome':{'passed':False,'termination':'harness_deadline_interrupt','suite_exits':[0,1,0]}}
    path=tmp_path/'proof.json';path.write_text(json.dumps(proof));rebuilt=deepcopy(proof)
    if change=='modified_raw':rebuilt['application_task_outcome']['suite_exits']=[0,2,0]
    elif change=='foreign_installation':rebuilt['installation']={'source_id':'foreign'};path.write_text(json.dumps(rebuilt))
    elif change=='wrong_case':rebuilt['case']='jinja';path.write_text(json.dumps(rebuilt))
    elif change=='missing_mechanism':rebuilt['runtime_mechanisms']['passed']=False;path.write_text(json.dumps(rebuilt))
    monkeypatch.setitem(sys.modules,'verify_complex_runtime',SimpleNamespace(reconstruct_case=lambda actual_root,actual_entry:rebuilt))
    if change=='none':
        accepted=acceptance.validate_complex_runtime(path,root,entry,metadata)
        assert accepted['runtime_mechanisms']['passed'] and accepted['application_task_outcome']['passed'] is False
        assert json.loads(path.read_text())==proof
    else:
        with pytest.raises(SystemExit):acceptance.validate_complex_runtime(path,root,entry,metadata)


def test_method_scope_identity_ignores_only_unexecuted_report_code(tmp_path):
    source=(ROOT/'assemble_release_acceptance.py').read_text()
    original=tmp_path/'original.py';original.write_text(source)
    changed=tmp_path/'changed.py';changed.write_text(source+'\ndef unrelated_report(): return "changed"\n')
    assert acceptance.scope_source_identity(original)==acceptance.scope_source_identity(changed)
    changed.write_text(source.replace('exactly174 distinct client methods','incorrectly relaxed method inventory'))
    assert acceptance.scope_source_identity(original)!=acceptance.scope_source_identity(changed)
    changed.write_text(source.replace("def qualification_scope(native):","def removed_scope(native):"))
    with pytest.raises(SystemExit,match='dependency inventory'):acceptance.scope_source_identity(changed)
