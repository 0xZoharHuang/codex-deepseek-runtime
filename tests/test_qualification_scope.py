"""Release acceptance cannot widen scope or borrow evidence from another runtime."""
from contextlib import closing
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
    assert len(linux)==40 and len(mac)==41 and len(windows)==4
    assert not any('terminal_acceptance' in key or 'test_activation_preserves_unrelated' in key for key in mac)
    assert set(mac)-set(linux)=={'test_platforms::test_live_linux_daemon_retains_provider_service_and_rejects_stale_boot'}
    assert {name:reason for name,reason in windows.items() if name.startswith('test_cds_skill_inspect::')}=={
        'test_cds_skill_inspect::test_posix_actual_space_path_and_exact_version_argument':'POSIX shell/shebang fixture; real Windows native execution is a separate acceptance check',
        'test_cds_skill_inspect::test_posix_actual_offline_timeout_is_bounded':'POSIX shell/shebang timeout fixture; not Windows process execution evidence'}
    assert not any(name.startswith('test_cds_skill_inspect::') for name in linux|mac)
    assert 'test_windows_console::test_native_console_interrupt_keeps_original_signal_and_exit_status[1-3221225786-SIGBREAK]' in linux
    assert len([name for name in linux if name.startswith('test_windows_launchers::')])==6
    handshake={name:reason for name,reason in linux.items() if 'test_slow_socket_worker_handshake_keeps_identity_check' in name}
    assert handshake=={
        'test_windows_console::test_slow_socket_worker_handshake_keeps_identity_check['+parameters+']':'Windows socket ownership handshake'
        for parameters in ['None-TimeoutError','reply1-RuntimeError','reply2-None']}
    assert not set(handshake)&set(windows)


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
def test_legacy_source_audit_cannot_qualify_the_new_recipe(change):
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
    # Old boolean/count-only proofs never qualify the new source/package contract.
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
    names={'verify_tui_audit.py','terminal_acceptance.py','credential_acceptance.py','verify_live.py','windows_acceptance_setup.py','qa_workspace.py','process_acceptance.py'}
    if interrupt:names|={'verify_tui_interrupt.py'}
    files={name:acceptance.sha(ROOT/name) for name in sorted(names)}
    installation={'source_id':'fixture','console_script_sha256':acceptance.sha(entry),'native_archive_sha256':'native'}
    qa={'qa_files_sha256':files,'qa_snapshot_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
        'entry':str(entry),'entry_sha256':installation['console_script_sha256'],'adapter_source_id':'fixture','native_archive_sha256':'native'}
    root.mkdir();(root/'home/sessions').mkdir(parents=True);(root/'work').mkdir()
    (root/'qa-source.json').write_text(json.dumps(qa),encoding='utf-8')
    (root/'home/config.toml').write_text('sandbox_mode="workspace-write"\n')
    terminal='conpty' if sys.platform=='win32' else 'pty'
    native={'target':'x86_64-pc-windows-msvc' if sys.platform=='win32' else 'x86_64-unknown-linux-gnu'}
    if interrupt:
        transcript='• TUI_INTERRUPT_RECOVERED_2719\n'.encode();(root/'transcript.pty').write_bytes(transcript)
        content=b'AFTER_CTRL_C_2719';(root/'work/after-interrupt.txt').write_bytes(content)
        records=[{'kind':'session_start'},{'dir':'from_tui','kind':'op','payload':'CleanBackgroundTerminals'},{'kind':'session_end'}]
        (root/'native-session.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in records))
        from process_acceptance import shell_command
        work=(root/'work').resolve();fixture=work/'interrupt_fixture.py';fixture.write_text('# synthetic controlled process fixture\n',encoding='utf-8')
        fixture_argv=[sys.executable,'-I','-u',str(fixture)]
        command=shell_command(fixture_argv)
        code='const r = await tools.exec_command('+json.dumps({'cmd':command,'workdir':str(work),'yield_time_ms':1000})+'); text(JSON.stringify(r));'
        events=[{'type':'session_meta','payload':{'id':'thread','cwd':str(work)}},
                {'type':'event_msg','payload':{'type':'task_started','turn_id':'old'}},
                {'type':'turn_context','payload':{'turn_id':'old','cwd':str(work)}},
                {'type':'response_item','payload':{'type':'custom_tool_call','name':'exec','call_id':'fixture-exec','input':code,'internal_chat_message_metadata_passthrough':{'turn_id':'old'}}},
                {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'fixture-exec','output':'Script running with cell ID 1\nWall time 1.0 seconds\nOutput:\n'}},
                {'type':'response_item','payload':{'type':'function_call','name':'wait','call_id':'fixture-wait-1','arguments':'{"cell_id":"1"}'}},
                {'type':'response_item','payload':{'type':'function_call_output','call_id':'fixture-wait-1','output':'Script running with cell ID 1\nWall time 1.0 seconds\nOutput:\n'}},
                {'type':'response_item','payload':{'type':'function_call','name':'wait','call_id':'fixture-wait-2','arguments':'{"cell_id":"1"}'}},
                {'type':'response_item','payload':{'type':'function_call_output','call_id':'fixture-wait-2','output':[{'type':'input_text','text':'Script completed\nWall time 1.0 seconds\nOutput:\n'},{'type':'input_text','text':'{"session_id":1234,"output":"ALIVE"}'}]}},
                {'type':'event_msg','payload':{'type':'turn_aborted','turn_id':'old','reason':'interrupted'}},
                {'type':'event_msg','payload':{'type':'task_started','turn_id':'new'}},
                {'type':'event_msg','payload':{'type':'task_complete','turn_id':'new','last_agent_message':'TUI_INTERRUPT_RECOVERED_2719'}}]
        (root/'home/sessions/rollout.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events))
        result={'passed':True,'entry':str(entry),'terminal':terminal,'backend':'shared','sandbox_mode':'workspace-write',
                'qa_snapshot_sha256':qa['qa_snapshot_sha256'],'thread_id':'thread','interrupted_turn_id':'old','continued_turn_id':'new',
                'background_retained_after_interrupt':True,'native_stop_terminated_fixture':True,'terminal_exit_code':0,
                'fixture_argv':fixture_argv,
                'background_session':{'call_id':'fixture-exec','result_call_id':'fixture-wait-2','session_id':'1234','cell_id':'1','call_chain':['fixture-exec','fixture-wait-1','fixture-wait-2'],'thread_id':'thread','turn_id':'old'},
                'continued_file_sha256':hashlib.sha256(content).hexdigest(),'fixture_pid':123,'fixture_birth':'saved-birth',
                'native_session_log_records':len(records),'transcript_bytes':len(transcript)}
    else:
        result=[]
        for index,(label,reply) in enumerate([('initial','TUI_VERIFIED_INITIAL_5271'),('cold-resume','TUI_VERIFIED_RESUMED_5271')]):
            (root/(label+'.pty')).write_text('• '+reply+'\n',encoding='utf-8')
            (root/(label+'-native-session.jsonl')).write_text('{"kind":"session_start"}\n{"kind":"session_end"}\n')
            result.append({'name':label,'passed':True,'terminal':terminal,'backend':'shared','thread_id':'same-thread',
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
                                  'cross_turn','cross_thread','wrong_cell','wrong_command_literal','no_abort','completed_before_abort','duplicate_abort','foreign_abort','missing_thread_metadata',
                                  'missing_fixture_argv','wrong_fixture_argv','wrong_python_argv','extra_fixture_arg','changed_fixture_flags',
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
        path=root/'home/sessions/rollout.jsonl';rows=[json.loads(line) for line in path.read_text().splitlines()]
        complete=next(row for row in rows if row['payload'].get('type')=='task_complete')
        rows.remove(complete);index=next(i for i,row in enumerate(rows) if row['payload'].get('type')=='turn_aborted');rows.insert(index,complete)
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    elif change=='missing_background':proof.pop('background_session')
    elif change=='wrong_background':proof['background_session']['session_id']='9999'
    elif change in {'missing_yield','yield_after_interrupt','cross_turn','cross_thread','wrong_cell','wrong_command_literal','no_abort','completed_before_abort','duplicate_abort','foreign_abort','missing_thread_metadata'}:
        path=root/'home/sessions/rollout.jsonl';rows=[json.loads(line) for line in path.read_text().splitlines()]
        yielded=next(row for row in rows if row['payload'].get('type')=='function_call_output' and row['payload'].get('call_id')=='fixture-wait-2')
        aborted=next(row for row in rows if row['payload'].get('type')=='turn_aborted')
        if change in {'missing_yield','yield_after_interrupt'}:
            rows.remove(yielded)
            if change=='yield_after_interrupt':rows.insert(rows.index(aborted)+1,yielded)
        elif change=='cross_turn':yielded['payload']['internal_chat_message_metadata_passthrough']={'turn_id':'foreign'}
        elif change=='cross_thread':next(row for row in rows if row.get('type')=='session_meta')['payload']['id']='foreign'
        elif change=='wrong_cell':next(row for row in rows if row['payload'].get('name')=='wait' and row['payload'].get('call_id')=='fixture-wait-2')['payload']['arguments']='{"cell_id":"99"}'
        elif change=='wrong_command_literal':
            call=next(row for row in rows if row['payload'].get('name')=='exec')
            call['payload']['input']='const label='+json.dumps(proof['fixture_argv'][3])+'; const r=await tools.exec_command({cmd:"echo other"}); text(JSON.stringify(r));'
        elif change=='no_abort':rows.remove(aborted)
        elif change=='completed_before_abort':rows.insert(rows.index(aborted),{'type':'event_msg','payload':{'type':'task_complete','turn_id':'old'}})
        elif change=='duplicate_abort':rows.insert(rows.index(aborted)+1,deepcopy(aborted))
        elif change=='foreign_abort':aborted['payload']['turn_id']='foreign'
        elif change=='missing_thread_metadata':rows=[row for row in rows if row.get('type')!='session_meta']
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    elif change=='missing_fixture_argv':proof.pop('fixture_argv')
    elif change=='wrong_fixture_argv':proof['fixture_argv'][3]=str(root/'work/other.py')
    elif change=='wrong_python_argv':proof['fixture_argv'][0]=str(entry)
    elif change=='extra_fixture_arg':proof['fixture_argv'].append('extra')
    elif change=='changed_fixture_flags':proof['fixture_argv'][2]='-c'
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
    with closing(sqlite3.connect(root/'state.sqlite')) as db, db:
        db.execute('CREATE TABLE receipts(status TEXT, provider TEXT, response_id TEXT, detail TEXT, started INTEGER)')
        db.executemany('INSERT INTO receipts VALUES(?,?,?,?,?)',[row+[index] for index,row in enumerate(receipt)])
    artifact={'kind':'schema','installation':metadata,'target':native['target'],'native_manifest':native,
              'entry_sha256':metadata['console_script_sha256'],'native_binary_sha256':'native','controller_optimize':0,
              'verifier_sha256':{name:acceptance.sha(ROOT/name) for name in ['verify_protocol_recovery.py','verify_live.py','install.py','windows_acceptance_setup.py','qa_workspace.py']}}
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
        with closing(sqlite3.connect(root/'state.sqlite')) as db, db:db.execute('UPDATE receipts SET response_id=? WHERE started=1',('resp_controlled_fault',))
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
                                  'missing_verifier','standard_gate','shared_helper','terminal_without_revalidation','terminal_revalidated','shared_terminal_helper',
                                  'fault_verifier','fault_without_snapshot'])
def test_frozen_regression_lineage_only_admits_reporting_methods_and_test_changes(tmp_path,monkeypatch,change):
    import hashlib
    producer=tmp_path/'producer';consumer=tmp_path/'consumer';producer.mkdir();consumer.mkdir()
    files={'assemble_release_acceptance.py':b'old consumer','verify_remaining_local.py':b'old methods',
           'verify_safety.py':b'unchanged gate','verify_live.py':b'unchanged helper','src/pkg/runtime.py':b'unchanged runtime','tests/test_case.py':b'old test',
           'verify_tui_audit.py':b'original terminal verifier','terminal_acceptance.py':b'unchanged terminal helper',
           'verify_fault_boundaries.py':b'original independent fault verifier'}
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
    elif change in {'fault_verifier','fault_without_snapshot'}:(consumer/'verify_fault_boundaries.py').write_bytes(b'proven-edge fault observer')
    (tmp_path/'producer.manifest.json').write_text(json.dumps(receipt));monkeypatch.setattr(acceptance,'SOURCE',consumer)
    if change in {'none','terminal_revalidated','fault_verifier'}:
        proof=acceptance.validate_regression_source(artifact,metadata,producer,terminal_revalidated=change=='terminal_revalidated')
        assert proof['producer']=='explicit_frozen_snapshot' and proof['producer_snapshot_sha256']==receipt['qa_sha256']
        expected=['assemble_release_acceptance.py','tests/test_case.py','verify_remaining_local.py']
        if change=='terminal_revalidated':expected.append('verify_tui_audit.py')
        if change=='fault_verifier':expected.append('verify_fault_boundaries.py')
        assert proof['changed_qa_files']==sorted(expected)
    else:
        with pytest.raises(SystemExit):acceptance.validate_regression_source(artifact,metadata,None if change in {'no_explicit_snapshot','fault_without_snapshot'} else producer,terminal_revalidated=change=='shared_terminal_helper')


@pytest.mark.parametrize('change',['none','source','subset','runtime','filtered','junit_missing','nested_external'])
def test_current_offline_supplement_requires_full_current_collection_and_exact_target(tmp_path,monkeypatch,change):
    import xml.etree.ElementTree as ET
    source=tmp_path/'source';source.mkdir();(source/'assemble_release_acceptance.py').write_text('current consumer')
    root=tmp_path/'offline';root.mkdir();metadata,native,_=installed();scope={'target':native['target']}
    artifact={'installation':metadata,'native_manifest':native,'target':native['target'],'baseline_matrix_sha256':acceptance.BASELINE_MATRIX_SHA256,
              'entry':str(acceptance.PureWindowsPath(metadata['release_environment'])/'Scripts/codex-deepseek.exe'),
              'entry_sha256':metadata['console_script_sha256'],
              'controller_environment':{'optimize':0,'windows_token':{'sid':'S-1-5-21-1-2-3-500','elevated':True}},
              'selected_gates':['offline'],'verifier_source_sha256':acceptance.qa_python_hashes(source)}
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
    elif change=='nested_external':artifact['external_offline']={'root':str(root)}
    ET.ElementTree(xml).write(root/'offline.xml',encoding='utf-8');(root/'offline-collection.json').write_text(json.dumps(collection))
    verdict={'passed':True,'entry_unchanged':True,'verifier_source_unchanged':True,'native_probes_unchanged':True,'gates':[{'gate':'offline','exit':0,'log':'offline.log'}],
             'offline_collection_sha256':acceptance.sha(root/'offline-collection.json')}
    (root/'offline.log').write_text('complete test execution')
    verdict['offline_files_sha256']=acceptance.offline_file_hashes(root)
    (root/'results.json').write_text(json.dumps(verdict['gates']))
    (root/'artifact.json').write_text(json.dumps(artifact));(root/'verdict.json').write_text(json.dumps(verdict));monkeypatch.setattr(acceptance,'SOURCE',source)
    if change=='none':
        assert acceptance.validate_offline_run(root,metadata,native,scope,current=True)['tests_passed']==1
        external=acceptance.external_offline_receipt(root,metadata,native,scope)
        assert external['execution']=='separate_complete_offline_run'
        assert external['validation']['tests_passed']==1
        assert external['files_sha256']['offline.xml']==acceptance.sha(root/'offline.xml')
        with pytest.raises(SystemExit):acceptance.external_offline_receipt(root,metadata,native,{'target':'aarch64-apple-darwin'})
    else:
        with pytest.raises(SystemExit):acceptance.validate_offline_run(root,metadata,native,scope,current=True)
        with pytest.raises(SystemExit):acceptance.external_offline_receipt(root,metadata,native,scope)


@pytest.mark.parametrize('change',['none','default','wrong_snapshot','execution_helper','missing_helper'])
def test_methods_can_only_reuse_the_exact_audited_scope_helper(tmp_path,monkeypatch,change):
    current=tmp_path/'current';snapshot=tmp_path/'snapshot';current.mkdir();snapshot.mkdir()
    names=['verify_remaining_methods.py','verify_remaining_local.py','verify_live.py','assemble_release_acceptance.py','process_acceptance.py','install.py','windows_acceptance_setup.py','qa_workspace.py']
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


def test_postrun_interrupt_replay_requires_actual_abort_and_leaves_live_mode_strict(tmp_path):
    from verify_tui_interrupt import yielded_background_session
    from process_acceptance import shell_command
    entry=tmp_path/'entry';entry.write_bytes(b'actual entry');root=tmp_path/'run'
    _,_,proof=terminal_fixture(root,entry,True)
    rollout=root/'home/sessions/rollout.jsonl'
    scope={'expected_thread':'thread','expected_turn':'old','expected_cwd':(root/'work').resolve(),
           'expected_fixture':(root/'work/interrupt_fixture.py').resolve(),'expected_command':shell_command(proof['fixture_argv'])}
    assert yielded_background_session(rollout,'interrupt_fixture.py',**scope) is None
    assert yielded_background_session(rollout,'interrupt_fixture.py',**scope,as_of_interrupt=True)==proof['background_session']
    rows=[json.loads(line) for line in rollout.read_text().splitlines()]
    cutoff=next(i for i,row in enumerate(rows) if row['payload'].get('type')=='turn_aborted')
    rollout.write_text(''.join(json.dumps(row)+'\n' for row in rows[:cutoff]))
    assert yielded_background_session(rollout,'interrupt_fixture.py',**scope)==proof['background_session']
    assert yielded_background_session(rollout,'interrupt_fixture.py',**scope,as_of_interrupt=True) is None
    assert yielded_background_session(rollout,'interrupt_fixture.py',expected_turn='old',as_of_interrupt=True) is None


def _observed_process_graph(monkeypatch, *, foreign=False, root_exits=True):
    """Deterministic kernel snapshots; all signal operations are synthetic spies."""
    from types import SimpleNamespace
    import psutil
    from ci import qualify_native as qualify
    import codex_deepseek.processes as process_ids
    original_children=psutil.Process.children
    graph={100:{'birth':'root-10','ctime':10,'ppid':1,'alive':True},
           200:{'birth':'foreign-50' if foreign else 'child-20','ctime':50 if foreign else 20,'ppid':999 if foreign else 100,'alive':True},
           300:{'birth':'grandchild-60' if foreign else 'grandchild-30','ctime':60 if foreign else 30,'ppid':200,'alive':True}}
    callbacks={'diagnostic_calls':{}};signals=[]
    class Process:
        def __init__(self,pid):
            if pid not in graph or not graph[pid]['alive']:raise psutil.NoSuchProcess(pid)
            self.pid=pid;self.initial=graph[pid]['birth']
        def _raise_if_pid_reused(self):
            if self.initial!=graph[self.pid]['birth']:raise psutil.NoSuchProcess(self.pid)
        def create_time(self):return graph[self.pid]['ctime']
        def ppid(self):
            callback=callbacks.pop(('ppid',self.pid),None)
            if callback:callback()
            return graph[self.pid]['ppid']
        def name(self):
            key=(self.pid,'name');callbacks['diagnostic_calls'][key]=callbacks['diagnostic_calls'].get(key,0)+1
            return 'python.exe'
        def exe(self):
            key=(self.pid,'exe');callbacks['diagnostic_calls'][key]=callbacks['diagnostic_calls'].get(key,0)+1
            return '/synthetic/python.exe'
        def cmdline(self):
            key=(self.pid,'argv');callbacks['diagnostic_calls'][key]=callbacks['diagnostic_calls'].get(key,0)+1
            callback=callbacks.pop(('cmdline',self.pid),None)
            if callback:callback()
            return graph[self.pid].get('argv',['python.exe','synthetic-fixture.py'])
        def children(self,recursive=False):
            recursive_flags.append(recursive)
            values=original_children(self,recursive=recursive)
            callback=callbacks.pop(('children',self.pid),None)
            if callback:callback()
            return values
    recursive_flags=[]
    monkeypatch.setattr(psutil,'Process',Process)
    monkeypatch.setattr(psutil,'_ppid_map',lambda:{100:1,200:100,300:200})
    monkeypatch.setattr(psutil,'pid_exists',lambda pid:graph.get(pid,{}).get('alive',False))
    def identity(pid):
        callback=callbacks.pop(('identity',pid),None)
        if callback:callback()
        return graph.get(pid,{}).get('birth') if graph.get(pid,{}).get('alive') else None
    monkeypatch.setattr(process_ids,'identity',identity)
    monkeypatch.setattr(process_ids,'matches',lambda pid,birth:bool(birth) and identity(pid)==birth)
    def terminate(pid,birth,timeout=0):
        assert identity(pid)==birth
        signals.append((pid,birth));graph[pid]['alive']=False
    monkeypatch.setattr(process_ids,'terminate',terminate)
    class Root:
        pid=100;returncode=None;polls=0
        def poll(self):
            self.polls+=1
            if root_exits and self.polls>=3:
                self.returncode=0;graph[100]['alive']=False
            return self.returncode
        def terminate(self):self.returncode=1;graph[100]['alive']=False
        def kill(self):self.terminate()
        def wait(self,timeout=None):return self.returncode
    root=Root();monkeypatch.setattr(qualify.subprocess,'Popen',lambda *a,**k:root)
    clock=[0.]
    monkeypatch.setattr(qualify,'time',SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda dt:clock.__setitem__(0,clock[0]+dt)))
    return qualify,graph,callbacks,signals,recursive_flags,root


def test_recursive_psutil_snapshot_can_join_reused_parent_to_foreign_processes(monkeypatch):
    import psutil
    original_children=psutil.Process.children
    qualify,graph,_,_,_,_=_observed_process_graph(monkeypatch,foreign=True)
    values=original_children(psutil.Process(100),recursive=True)
    assert {p.pid for p in values}=={200,300}
    assert graph[200]['ppid']==999  # The root itself remained the same instance.
    seen={(100,'root-10'):{'pid':100,'birth':'root-10'}}
    uncertain={};rejected={};errors={}
    qualify._observe_process_family(seen,uncertain,rejected,errors)
    assert set(seen)=={(100,'root-10')} and not uncertain and not errors
    assert [x['pid'] for x in rejected.values()]==[200]


def test_qualification_observer_never_signals_reused_foreign_parent_or_its_child(monkeypatch,tmp_path):
    import io
    qualify,graph,_,signals,recursive_flags,_=_observed_process_graph(monkeypatch,foreign=True)
    result=qualify.run_observed(['synthetic'],cwd=tmp_path,env={},log=io.StringIO(),timeout=30)
    assert result['passed'] and result['returncode']==0 and not signals
    assert {x['pid'] for x in result['observed']}=={100}
    assert graph[200]['alive'] and graph[300]['alive']
    assert result['rejected_processes'][0]['ppid']==999
    assert not any(recursive_flags)


def test_qualification_observer_keeps_real_owned_survivor_as_failure(monkeypatch,tmp_path):
    import io
    qualify,_,_,signals,_,_=_observed_process_graph(monkeypatch)
    result=qualify.run_observed(['synthetic'],cwd=tmp_path,env={},log=io.StringIO(),timeout=30)
    assert not result['passed']
    assert {x['pid'] for x in result['remaining_before_cleanup']}=={200,300}
    assert {pid for pid,_ in signals}=={200,300} and result['remaining_after_cleanup']==[]
    owned={x['pid']:x for x in result['observed']}
    assert owned[300]['parent_pid']==200 and owned[300]['parent_birth']=='child-20'
    assert owned[200]['argv']==['python.exe','synthetic-fixture.py']
    assert owned[200]['exe']=='/synthetic/python.exe' and owned[200]['name']=='python.exe'


def test_qualification_parent_reuse_during_sampling_leaves_child_uncertain_and_unsignalled(monkeypatch,tmp_path):
    import io
    qualify,graph,callbacks,signals,_,_=_observed_process_graph(monkeypatch)
    callbacks[('children',200)]=lambda:graph[200].update(birth='foreign-70',ctime=70,ppid=999)
    result=qualify.run_observed(['synthetic'],cwd=tmp_path,env={},log=io.StringIO(),timeout=30)
    assert not result['passed'] and not signals
    assert 300 in result['unknown_live'] and graph[300]['alive']
    assert any(x['reason']=='parent_changed_during_collection' for x in result['uncertain_observations'])
    row=next(x for x in result['uncertain_observations'] if x['reason']=='parent_changed_during_collection')
    assert row['parent_birth_before']=='child-20' and row['parent_birth_after']=='foreign-70'
    assert {x['pid'] for x in result['observed']}=={100,200}


def test_qualification_child_reuse_during_metadata_read_is_never_owned(monkeypatch,tmp_path):
    import io
    qualify,graph,callbacks,signals,_,_=_observed_process_graph(monkeypatch)
    callbacks[('cmdline',200)]=lambda:graph[200].update(birth='foreign-80',ctime=80,ppid=999,argv=['foreign-program','FOREIGN_TEXT_MUST_NOT_PERSIST'])
    graph[300]['alive']=False
    result=qualify.run_observed(['synthetic'],cwd=tmp_path,env={},log=io.StringIO(),timeout=30)
    assert not result['passed'] and not signals
    assert result['unknown_live']==[200]
    assert {(x['pid'],x['birth']) for x in result['observed']}=={(100,'root-10'),(200,'child-20')}
    assert all(x['birth']!='foreign-80' for x in result['observed'])
    assert 'FOREIGN_TEXT_MUST_NOT_PERSIST' not in json.dumps(result)


def test_qualification_stale_ppid_with_child_older_than_parent_is_rejected(monkeypatch):
    qualify,graph,_,signals,_,_=_observed_process_graph(monkeypatch)
    graph[200]['ctime']=1
    seen={(100,'root-10'):{'pid':100,'birth':'root-10'}};uncertain={};rejected={};errors={}
    qualify._observe_process_family(seen,uncertain,rejected,errors)
    assert set(seen)=={(100,'root-10')} and not signals and not uncertain
    # psutil's own nonrecursive ctime filter can reject this before our edge check.
    assert not errors


def test_qualification_root_identity_change_during_snapshot_cannot_enumerate_new_root(monkeypatch):
    qualify,graph,callbacks,signals,_,_=_observed_process_graph(monkeypatch)
    callbacks[('ppid',100)]=lambda:graph[100].update(birth='foreign-root-90',ctime=90,ppid=999)
    seen={(100,'root-10'):{'pid':100,'birth':'root-10'}}
    qualify._observe_process_family(seen,{}, {}, {})
    assert set(seen)=={(100,'root-10')} and not signals


def test_qualification_edge_identity_rechecked_before_admission(monkeypatch,tmp_path):
    import io
    qualify,graph,callbacks,signals,_,_=_observed_process_graph(monkeypatch)
    reads=[0]
    def change_on_third_read():
        reads[0]+=1
        if reads[0]==3:graph[200].update(birth='foreign-95',ctime=95,ppid=999)
        else:callbacks[('identity',200)]=change_on_third_read
    callbacks[('identity',200)]=change_on_third_read
    graph[300]['alive']=False  # No surviving old grandchild in this PID-reuse case.
    result=qualify.run_observed(['synthetic'],cwd=tmp_path,env={},log=io.StringIO(),timeout=30)
    assert result['passed'] and not signals and graph[200]['alive']
    assert any(x['reason']=='edge_changed_before_admission' for x in result['uncertain_observations'])
    assert {x['pid'] for x in result['observed']}=={100} and result['unknown_live']==[]


def test_qualification_denied_optional_diagnostics_do_not_erase_owned_survivors(monkeypatch,tmp_path):
    import io,psutil
    qualify,graph,callbacks,signals,_,_=_observed_process_graph(monkeypatch)
    def denied():raise psutil.AccessDenied(200)
    callbacks[('cmdline',200)]=denied
    result=qualify.run_observed(['synthetic'],cwd=tmp_path,env={},log=io.StringIO(),timeout=30)
    assert not result['passed'] and {pid for pid,_ in signals}=={200,300}
    # The admission proof persists even if a later sample can read argv normally.
    assert {x['pid'] for x in result['observed']}=={100,200,300}
    assert result['observation_errors']==[]


def test_qualification_required_parent_metadata_denial_is_failure_without_foreign_signal(monkeypatch,tmp_path):
    import io,psutil
    qualify,graph,_,signals,_,_=_observed_process_graph(monkeypatch)
    original=psutil.Process.ppid
    def ppid(self):
        if self.pid==200:raise psutil.AccessDenied(200)
        return original(self)
    monkeypatch.setattr(psutil.Process,'ppid',ppid)
    result=qualify.run_observed(['synthetic'],cwd=tmp_path,env={},log=io.StringIO(),timeout=30)
    assert not result['passed'] and not signals and result['unknown_live']==[200]
    assert result['observation_errors'] and result['uncertain_observations']
    assert graph[200]['alive'] and graph[300]['alive']


@pytest.mark.parametrize('disappear_on_read',[1,2])
def test_qualification_natural_exit_during_child_or_parent_snapshot_is_not_a_live_error(monkeypatch,tmp_path,disappear_on_read):
    import io,psutil
    qualify,graph,_,signals,_,_=_observed_process_graph(monkeypatch)
    graph[300]['alive']=False
    original=psutil.Process.ppid;reads=[0]
    def ppid(self):
        if self.pid==200:
            reads[0]+=1
            if reads[0]==disappear_on_read:
                graph[200]['alive']=False
                raise psutil.NoSuchProcess(200)
        return original(self)
    monkeypatch.setattr(psutil.Process,'ppid',ppid)
    result=qualify.run_observed(['synthetic'],cwd=tmp_path,env={},log=io.StringIO(),timeout=30)
    assert result['passed'] and not signals
    assert result['unknown_live']==[] and result['observation_errors']==[]
    assert result['remaining_before_cleanup']==result['remaining_after_cleanup']==[]


def test_qualification_foreign_candidates_never_expose_executable_or_command_line(monkeypatch):
    qualify,graph,callbacks,signals,_,_=_observed_process_graph(monkeypatch,foreign=True)
    graph[200]['argv']=['other-project','FOREIGN_TASK_OR_CREDENTIAL']
    seen={(100,'root-10'):{'pid':100,'birth':'root-10'}};uncertain={};rejected={};errors={}
    qualify._observe_process_family(seen,uncertain,rejected,errors)
    calls=callbacks['diagnostic_calls']
    assert calls.get((200,'exe'),0)==calls.get((200,'argv'),0)==calls.get((200,'name'),0)==0
    assert calls.get((300,'exe'),0)==calls.get((300,'argv'),0)==0
    assert not signals and not uncertain and not errors
    assert {row['pid'] for row in rejected.values()}=={200}
    assert 'FOREIGN_TASK_OR_CREDENTIAL' not in json.dumps([*rejected.values(),*uncertain.values(),*seen.values()])
    assert not any(key in row for row in rejected.values() for key in ('name','exe','argv'))

def external_terminal_lineage_fixture(tmp_path,monkeypatch):
    """Use the real offline receipt and source-lineage checks at both call layers."""
    import hashlib
    import xml.etree.ElementTree as ET
    producer=tmp_path/'producer';consumer=tmp_path/'consumer'
    metadata,native,_=installed();scope={'target':native['target']}
    names=acceptance.TERMINAL_EXECUTION_SOURCES|set(metadata['source_sha256'])|{'assemble_release_acceptance.py'}
    for folder in [producer,consumer]:
        for name in names:
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixed '+name)
    metadata['source_sha256']={name:acceptance.sha(producer/name) for name in metadata['source_sha256']}
    metadata['adapter_source_sha256']={'src/adapter.py':metadata['source_sha256']['src/adapter.py']}
    files={path.relative_to(producer).as_posix():acceptance.sha(path) for path in producer.rglob('*') if path.is_file()}
    (tmp_path/'producer.manifest.json').write_text(json.dumps({'source_id':metadata['source_id'],'files':files,
        'qa_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()}))
    external=tmp_path/'external-offline';external.mkdir()
    artifact={'installation':metadata,'native_manifest':native,'target':native['target'],
              'baseline_matrix_sha256':acceptance.BASELINE_MATRIX_SHA256,
              'entry':str(acceptance.PureWindowsPath(metadata['release_environment'])/'Scripts/codex-deepseek.exe'),
              'entry_sha256':metadata['console_script_sha256'],'release':'fixed-release',
              'tui_sandbox_mode':'workspace-write','required_local_methods':['local'],
              'controller_environment':{'optimize':0,'windows_token':{'sid':'S-1-5-21-1-2-3-500','elevated':True}},
              'selected_gates':['offline'],'verifier_source_sha256':acceptance.qa_python_hashes(producer)}
    xml=ET.Element('testsuite');items=[]
    for i,(name,reason) in enumerate([('test_current::test_pass',None),*acceptance.expected_offline_skips(native['target']).items()]):
        module,case=name.split('::');classname='tests.'+module
        node=ET.SubElement(xml,'testcase',classname=classname,name=case)
        if reason:ET.SubElement(node,'skipped',type='pytest.skip',message=reason)
        items.append({'nodeid':str(i),'classname':classname,'name':case})
    ET.ElementTree(xml).write(external/'offline.xml',encoding='utf-8')
    (external/'offline-collection.json').write_text(json.dumps({'schema_version':1,'deselected':0,'items':items}))
    (external/'offline.log').write_text('complete test execution')
    verdict={'passed':True,'entry_unchanged':True,'verifier_source_unchanged':True,'native_probes_unchanged':True,
             'gates':[{'gate':'offline','exit':0,'log':'offline.log'}],
             'offline_collection_sha256':acceptance.sha(external/'offline-collection.json'),
             'offline_files_sha256':acceptance.offline_file_hashes(external)}
    for name,value in [('artifact.json',artifact),('verdict.json',verdict),('results.json',verdict['gates'])]:
        (external/name).write_text(json.dumps(value))
    monkeypatch.setattr(acceptance,'SOURCE',producer)
    receipt=acceptance.external_offline_receipt(external,metadata,native,scope)
    runtime=tmp_path/'runtime';runtime.mkdir()
    for name in acceptance.OFFLINE_FILES:(runtime/name).write_bytes((external/name).read_bytes())
    runtime_artifact={**deepcopy(artifact),'selected_gates':sorted(acceptance.GATES),'external_offline':receipt,
        'controller_environment':{'optimize':0,'windows_token':{'sid':'S-1-5-21-1-2-3-500','elevated':False}}}
    rows=[({'gate':name,'exit':1 if name=='tui' else 0,'log':name+'.log'} if name!='offline'
           else {**receipt['gate'],'execution':'separate_complete_offline_run','external_root':receipt['root']})
          for name in sorted(acceptance.GATES)]
    runtime_verdict={**deepcopy(verdict),'passed':False,'gates':rows,'external_offline_unchanged':True,
                     'offline_files_sha256':acceptance.offline_file_hashes(runtime)}
    for name,value in [('artifact.json',runtime_artifact),('verdict.json',runtime_verdict),('results.json',rows)]:
        (runtime/name).write_text(json.dumps(value))
    (consumer/'verify_tui_audit.py').write_text('current independently rerun terminal verifier')
    monkeypatch.setattr(acceptance,'SOURCE',consumer)
    replacement=tmp_path/'replacement';replacement.mkdir()
    replacement_artifact={**deepcopy(runtime_artifact),'selected_gates':sorted(acceptance.TERMINAL_GATES),
                          'verifier_source_sha256':acceptance.qa_python_hashes(consumer)}
    replacement_rows=[{'gate':name,'exit':0,'log':name+'.log'} for name in sorted(acceptance.TERMINAL_GATES)]
    replacement_verdict={**deepcopy(verdict),'gates':replacement_rows}
    (replacement/'artifact.json').write_text(json.dumps(replacement_artifact))
    (replacement/'verdict.json').write_text(json.dumps(replacement_verdict))
    return metadata,native,scope,producer,consumer,external,runtime,runtime_artifact,runtime_verdict,replacement


@pytest.mark.parametrize('change',['terminal_revalidated','terminal_without_revalidation','shared_helper',
                                   'shared_terminal_helper','failed_replacement','raw_external_changed','both'])
def test_external_offline_lineage_propagates_only_verified_terminal_revalidation(tmp_path,monkeypatch,change):
    metadata,native,scope,producer,consumer,external,runtime,artifact,verdict,replacement=external_terminal_lineage_fixture(tmp_path,monkeypatch)
    before={str(p):acceptance.sha(p) for folder in [external,runtime] for p in folder.iterdir()}
    terminal=False
    if change!='terminal_without_revalidation':
        effective,lineage=acceptance.validate_standard_gates(artifact,verdict,replacement)
        assert lineage['original_failed_gates']==['tui'] and len(effective)==30 and all(row['exit']==0 for row in effective)
        terminal=True
    if change=='shared_helper':(consumer/'verify_live.py').write_text('foreign shared helper')
    elif change=='shared_terminal_helper':(consumer/'terminal_acceptance.py').write_text('foreign terminal helper')
    elif change=='raw_external_changed':(external/'offline.log').write_text('mutated after receipt')
    elif change=='failed_replacement':
        value=json.loads((replacement/'verdict.json').read_text());value['gates'][0]['exit']=1
        (replacement/'verdict.json').write_text(json.dumps(value))
        with pytest.raises(SystemExit):acceptance.validate_standard_gates(artifact,verdict,replacement)
        return
    options={'producer_snapshot':producer,'terminal_revalidated':terminal,'oauth_revalidated':change=='both'}
    if change=='terminal_revalidated':
        proof=acceptance.validate_offline_run(runtime,metadata,native,scope,**options)
        assert proof['tests_passed']==1 and proof['external_execution']==artifact['external_offline']
        assert all(acceptance.sha(path)==digest for path,digest in before.items())
        assert acceptance.load(runtime/'verdict.json')['passed'] is False
    else:
        with pytest.raises(SystemExit):acceptance.validate_offline_run(runtime,metadata,native,scope,**options)
    if change=='both':
        with pytest.raises(SystemExit,match='mutually exclusive'):
            acceptance.external_offline_receipt(external,metadata,native,scope,**options)
