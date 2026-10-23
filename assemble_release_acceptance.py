"""Publish aggregate facts only after checking every private acceptance receipt."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path,PureWindowsPath
import re
import shutil
import sqlite3
import tomllib
import xml.etree.ElementTree as ET
from install import valid_release,console_entry,source_identity,_support
from acceptance_assertions import real_work_passed,current_browser_evidence

SOURCE=Path(__file__).resolve().parent
comparison_path=_support.comparison_path
GATES={'tui','tui-interrupt','native-revocation','offline','live','plugin','features','modes','agents-nested','agents-full','agents-none','guardian','runtime-contract','plan-review','daemon','profiles','transports','local-methods','local-account','auth-stores','oauth-file','oauth-keyring','oauth-auto','oauth-secrets','memory-v1','memory-v2','goal-sample','crash-recovery','search','routes'}
OFFLINE_FILES=('offline.xml','offline-collection.json','offline.log')
REMOTE={'mcpServer/event/stream/start','mcpServer/event/stream/stop','plugin/skill/read','plugin/reconcile'}
LOCAL_ACCOUNT={'account/login/start','account/read','account/logout','account/login/cancel'}
WINDOWS_SANDBOX={'windowsSandbox/setupStart','windowsSandbox/readiness'}
BASELINE_COMMIT='fe136d4e70ef2b5bbed40a2d22b3aabf267796d3'
BASELINE_MATRIX_SHA256='95845b7f973fc9401888a5429ef6a09a94ac388bf12c80ad7b301a2e0c6f3a43'
TARGETS={'aarch64-apple-darwin':('macOS arm64',129),
         'x86_64-unknown-linux-gnu':('Linux x86_64 GNU',129),
         'x86_64-pc-windows-msvc':('Windows x86_64 MSVC',131)}
KEYRING_PROBE_GATES={'auth-stores','oauth-secrets'}
TERMINAL_GATES={'tui','tui-interrupt'}
TERMINAL_EXECUTION_SOURCES={'run_acceptance.py','install.py','verify_tui_audit.py','verify_tui_interrupt.py',
                            'terminal_acceptance.py','credential_acceptance.py','process_acceptance.py','verify_live.py','windows_acceptance_setup.py','qa_workspace.py'}
RECOVERY_KINDS={'undeclared-tool','schema','schema-multiple','schema-tool-final','schema-tool-before-terminal','schema-overflow'}
NATIVE_FAULT_KINDS={'truncated','tcp-reset','auth401','auth403','effect-before','effect-after','effect-worker'}
INSTALLATION_PATH_FIELDS={'release_id','release_environment','source','native_package','console_script_sha256'}
INSTALLATION_CONTENT_FIELDS={'source_id','source_sha256','adapter_source_sha256','model_catalog_sha256',
                             'dependencies_lock_sha256','native_version','native_archive_sha256','model'}


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def require(value,message):
    if not value:raise SystemExit(message)


def validate_complex_runtime(path,root,entry,installation):
    """Rebuild runtime evidence while retaining the separate application outcome."""
    from verify_complex_runtime import reconstruct_case
    saved=load(path);actual=reconstruct_case(root,entry)
    require(saved==actual,'Complex runtime proof differs from current raw reconstruction')
    require(actual.get('case')==root.name and actual.get('installation')==installation,
            'Complex runtime proof belongs to another task or installation')
    require(actual.get('runtime_mechanisms',{}).get('passed') is True,
            'Complex workflow lacks required native runtime mechanisms')
    return actual



def validate_peer_verifiers(artifact,expected,label):
    hashes=artifact.get('verifier_sha256',{})
    expected=set(expected)|{'verify_live.py','windows_acceptance_setup.py','qa_workspace.py'}
    require(isinstance(hashes,dict) and set(hashes)==expected,label+' verifier/helper inventory differs')
    for name,digest in hashes.items():require(sha(SOURCE/name)==digest,label+' verifier changed: '+name)
    return hashes

def validate_fault_rows(rows):
    expected={(kind,variant) for kind in NATIVE_FAULT_KINDS for variant in ('official','candidate')}
    require(isinstance(rows,list) and len(rows)==len(expected)
            and {(row.get('kind'),row.get('variant')) for row in rows}==expected
            and all(row.get('passed') is True for row in rows),
            'Native fault proof requires all fourteen unique passing cases')
    return rows


def validate_native_faults(root,entry,installation,native,official_sha256):
    from contextlib import closing
    from verify_fault_boundaries import VERIFIERS
    root=Path(root);artifact=load(root/'artifact.json');complete=load(root/'complete.json')
    require(complete.get('passed') is True and complete.get('all_scenarios') is True
            and complete.get('artifact')==sha(root/'artifact.json'),
            'Native fault proof is incomplete or belongs to another artifact')
    selected=complete.get('selected_scenarios',[])
    require(len(selected)==7 and set(selected)==NATIVE_FAULT_KINDS,
            'Native fault selected scenario set differs')
    require(artifact.get('model_calls')==complete.get('model_calls')==0
            and artifact.get('real_credentials')==complete.get('real_credentials')==0,
            'Controlled native fault proof used real credentials or inference')
    require(comparison_path(artifact.get('entry',''))==comparison_path(entry)
            and artifact.get('entry_sha256')==installation['console_script_sha256']
            and artifact.get('installation_sha256')==sha(entry.parent.parent.parent/'installation.json')
            and artifact.get('candidate_sha256')==native['files']['bin/codex.exe']
            and artifact.get('official_sha256')==official_sha256,
            'Native fault proof has a different installation or reference')
    checks=load(root/'final-artifact-check.json')
    require(set(checks)=={'entry','installation','candidate','reference','native_manifest','valid_release','direct_reference_verdicts'}
            and all(value is True for value in checks.values()),'Native fault final artifact checks failed')
    hashes=artifact.get('verifier_sha256',{})
    require(set(hashes)==set(VERIFIERS) and all(sha(SOURCE/name)==digest for name,digest in hashes.items()),
            'Native fault verifier/helper identity changed')
    raw=artifact.get('auth_native_default_basis',{}).get('raw',{})
    require(set(raw)=={'auth401','auth403'},'Native direct auth reference evidence is missing')
    evidence={}
    for kind,row in raw.items():
        path=Path(row['path']);require(sha(path)==row['sha256'],'Native direct auth reference changed')
        proof=load(path)
        require(proof.get('passed') is True and proof.get('binary_sha256')==official_sha256
                and proof.get('request_count')==row['requests'] and row['requests']>0,
                'Native direct auth reference identity or count differs')
        evidence['direct_'+kind]=row['sha256']
    rows=validate_fault_rows(load(root/'results.json'))
    comparisons=load(root/'comparisons.json')
    require(comparisons==complete.get('comparisons') and len(comparisons)==7
            and {row.get('kind') for row in comparisons}==NATIVE_FAULT_KINDS,
            'Native fault comparison set differs')
    rebuilt={}
    for row in rows:
        kind,variant=row['kind'],row['variant'];case=root/(kind+'-'+variant)
        require(comparison_path(row['evidence'])==comparison_path(case/'verdict.json'),
                'Native fault evidence escaped its named case')
        proof=load(case/'verdict.json')
        require(proof.get('passed') is True and proof.get('kind')==kind
                and proof.get('binary_sha256')==artifact['official_sha256' if variant=='official' else 'candidate_sha256']
                and proof.get('cleanup_errors')==[] and proof.get('remaining_before_forced_cleanup')==[],
                'Native fault case failed, changed identity or left owned processes')
        effect_path=case/'effects.jsonl'
        actual_effects=[json.loads(line) for line in effect_path.read_text(encoding='utf-8').splitlines() if line] if effect_path.exists() else []
        require(actual_effects==proof['effects'] and len(actual_effects)==(1 if kind in {'effect-after','effect-worker'} else 0),
                'Native fault side effects differ from actual append ledger')
        requests=load(case/'upstream-requests.json')
        require(len(requests)==proof['requests'],'Native fault actual request count differs')
        state=case/'home/provider-state/adapter.sqlite'
        with closing(sqlite3.connect(state.as_uri()+'?mode=ro',uri=True)) as db:
            actual_receipts=[dict(zip(['id','status','upstream_status','response_id','detail'],value))
                             for value in db.execute('SELECT id,status,upstream_status,response_id,detail FROM receipts ORDER BY started')]
        require(actual_receipts==proof['receipts'],'Native fault actual receipt history differs')
        cold=proof.get('cold_evidence',{})
        if kind in {'effect-after','effect-worker'}:
            require(cold.get('mcp_events')==[] and cold.get('ledger')==actual_effects and bool(cold.get('call')),
                    'Native fault cold recovery lacks original call or redispatched MCP')
        semantic={'requests':proof['requests'],'effects':len(actual_effects),
                  'receipts':[[item['status'],item['upstream_status']] for item in actual_receipts],
                  'auth_attempts':proof.get('failed_auth_attempts'),
                  'cold_mcp_count':len(cold.get('mcp_events',[]))}
        rebuilt[(kind,variant)]=semantic
    for row in comparisons:
        require(row.get('passed') is True and row.get('official')==rebuilt[(row['kind'],'official')]
                and row.get('candidate')==rebuilt[(row['kind'],'candidate')] and row['official']==row['candidate'],
                'Native fault comparison does not match its raw cases')
    for path in root.rglob('*'):
        if path.is_file():evidence[path.relative_to(root).as_posix()]=sha(path)
    return {'passed':True,'scenarios':sorted(NATIVE_FAULT_KINDS),'actual_cases':14,
            'model_calls':0,'real_credentials':0,'raw_effect_and_receipt_reconstruction':True,
            'evidence_sha256':evidence}


def validate_protocol_recovery(path,installation,native):
    """Reconstruct the retry outcome from hash-bound native and provider records."""
    path=Path(path);artifact=load(path/'artifact.json');proof=load(path/'verdict.json')
    require(artifact.get('installation')==proof.get('installation')==installation,'Protocol recovery installation differs')
    require(artifact.get('target')==proof.get('target')==native['target'] and artifact.get('native_manifest')==native,
            'Protocol recovery target/native manifest differs')
    require(proof.get('kind')==artifact.get('kind') and proof['kind'] in RECOVERY_KINDS,'Protocol recovery kind differs')
    require(artifact.get('entry_sha256')==installation['console_script_sha256'] and
            artifact.get('native_binary_sha256')==native['files']['bin/codex.exe' if native['target']=='x86_64-pc-windows-msvc' else 'bin/codex'],
            'Protocol recovery launcher/native binary differs')
    require(artifact.get('controller_optimize')==0 and proof.get('passed') is True and
            proof.get('verifier_and_artifact_unchanged') is True,'Protocol recovery integrity or scenario failed')
    validate_peer_verifiers(artifact,{'verify_protocol_recovery.py','install.py'},'Protocol recovery')
    events_paths=list(path.glob('events-*.json'));launch_paths=list(path.glob('launch-*.json'))
    require(len(events_paths)==len(launch_paths)==1,'Protocol recovery must have exactly one native session')
    names={'request-bodies.json','receipts.json','observed.json','client-requests.jsonl','state.sqlite',events_paths[0].name,launch_paths[0].name}
    require(set(proof.get('evidence_sha256',{}))==names,'Protocol recovery raw evidence inventory differs')
    for name,digest in proof['evidence_sha256'].items():require(sha(path/name)==digest,'Protocol recovery raw evidence changed: '+name)
    launch=load(launch_paths[0]);require(launch.get('entry_sha256')==artifact['native_binary_sha256'],
                                    'Protocol recovery did not launch the pinned native binary')
    requests=load(path/'request-bodies.json');receipts=load(path/'receipts.json');observed=load(path/'observed.json');events=load(events_paths[0])
    with sqlite3.connect((path/'state.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
        require([list(row) for row in db.execute('SELECT status,provider,response_id,detail FROM receipts ORDER BY started')]==receipts,
                'Protocol recovery provider receipts differ from raw state')
    require(type(proof.get('requests')) is int and len(requests)==len(receipts)==proof['requests'] and len(requests)>=2,
            'Protocol recovery request/receipt counts differ')
    require(receipts[0][0]=='adapter_error' and receipts[-1][0]=='completed' and receipts[0][2]=='resp_controlled_fault'
            and receipts[-1][2] and receipts[-1][2]!='resp_controlled_fault'
            and (proof['kind']=='undeclared-tool' or 'structured output' in (receipts[0][3] or '')),
            'Protocol recovery lacks a rejected controlled fault and real completed request')
    require(receipts==proof.get('receipts'),'Protocol recovery summary receipts differ')
    context=any(any(item.get('name')=='cx_native_continuation_context' for item in req.get('input',[]) if isinstance(item,dict)) for req in requests[1:])
    require(context and proof.get('continuation_context_observed') is True,'Protocol recovery continuation context missing')
    retries=[event['params'] for event in events if event.get('method')=='error' and event.get('params',{}).get('willRetry')]
    require(retries and retries==proof.get('native_retry_errors')==observed.get('native_retry_errors'),'Protocol recovery lacks matching native retry events')
    thread=observed.get('thread');require(thread and thread==proof.get('thread'),'Protocol recovery thread differs')
    items=[event['params']['item'] for event in events if event.get('method')=='item/completed' and event.get('params',{}).get('threadId')==thread]
    require(items==observed.get('items'),'Protocol recovery native completed items differ')
    tools=[item for item in items if item.get('type') in {'commandExecution','fileChange','mcpToolCall'}]
    tools.extend(event for event in events if event.get('method')=='item/tool/call')
    require(not tools and tools==proof.get('client_tool_executions')==observed.get('client_tool_executions'),
            'Protocol recovery executed a forbidden client tool')
    terminal=[event for event in events if event.get('method')=='turn/completed' and event.get('params',{}).get('threadId')==thread]
    require(len(terminal)==1 and terminal[0]['params']['turn']['status']=='completed','Protocol recovery native turn did not complete')
    answers=[item.get('text','') for item in items if item.get('type')=='agentMessage'];answer=answers[-1] if answers else ''
    require(answer==proof.get('answer')==observed.get('answer') and proof.get('answer_valid') is True and observed.get('answer_valid') is True,
            'Protocol recovery answer summary differs from native messages')
    try:valid=answer.strip()=='RECOVERY_NATIVE' if proof['kind']=='undeclared-tool' else json.loads(answer)=={'marker':'RECOVERY_NATIVE','value':437}
    except (TypeError,ValueError):valid=False
    require(valid,'Protocol recovery final answer is invalid')
    return proof


def validate_rpc_witnesses(report,roots,target,methods,*,excluded_roots=()):
    """A saved coverage boolean cannot replace current paired native receipts."""
    from verify_rpc_observations import trace_observations
    require(report.get('verifier_and_artifact_unchanged') is True,'RPC collector artifact/verifier integrity missing')
    require(set(report.get('verifier_sha256',{}))=={'verify_rpc_observations.py','assemble_release_acceptance.py','install.py'},
            'RPC collector verifier inventory differs')
    for name,digest in report['verifier_sha256'].items():require(sha(SOURCE/name)==digest,'RPC collector verifier changed: '+name)
    require(set(report.get('methods',{}))==set(methods),'RPC witness method inventory differs')
    traces={}
    for method,rows in report['methods'].items():
        require(isinstance(rows,list) and rows,'RPC method has no raw witness: '+method)
        for witness in rows:
            path=Path(witness['requests']);canonical=comparison_path(path)
            require(not any(canonical.is_relative_to(comparison_path(excluded)) for excluded in excluded_roots),'RPC witness came from a superseded terminal run')
            allowed=[(root,identity) for root,identity in roots.items() if canonical.is_relative_to(comparison_path(root))]
            require(allowed,'RPC witness escaped current qualified evidence roots')
            _,identity=max(allowed,key=lambda pair:len(comparison_path(pair[0]).parts))
            require(canonical not in traces or traces[canonical]==(path,identity),'RPC witness has ambiguous path aliases')
            traces[canonical]=(path,identity)
    rebuilt={};sessions=0
    for path,identity in traces.values():
        found,count,errors=trace_observations(path,*identity,target)
        require(not errors,'RPC raw native reconstruction failed: '+json.dumps(errors));sessions+=count
        for method,rows in found.items():
            if method in methods:rebuilt.setdefault(method,[]).extend(rows)
    # Preserve every paired witness, but tolerate independent directory iteration order.
    canonical_rows=lambda rows:sorted(json.dumps(row,sort_keys=True) for row in rows)
    require(set(rebuilt)==set(methods) and all(canonical_rows(rebuilt[method])==canonical_rows(report['methods'][method]) for method in methods),
            'RPC saved witnesses differ from current raw paired request/response evidence')
    require(sessions==report.get('native_client_sessions'),'RPC native session count differs from raw evidence')
    return {'raw_native_sessions_reconstructed':sessions,'methods_reconstructed':len(rebuilt)}


def installation_lineage(reference,native,artifact,*,relocated_windows=False):
    """Permit deployment-path changes only for an explicit Windows guest witness.

    The complete source map, adapter map, dependency lock, catalog and complete
    native manifest stay exact. Unknown metadata fields stay exact as well, so
    introducing a future content identity cannot silently weaken this check.
    Entry bytes may vary because the Windows launcher embeds its interpreter
    path; both actual path and hash remain part of the evidence.
    """
    candidate=artifact.get('installation')
    require(isinstance(candidate,dict),'Supplemental installation identity missing')
    require(artifact.get('target')==native.get('target'),'Supplemental native target mismatch')
    require(artifact.get('native_manifest')==native,'Supplemental full native manifest mismatch')
    require(all(reference.get(key) and candidate.get(key) for key in INSTALLATION_CONTENT_FIELDS),
            'Supplemental content identity incomplete')
    require(candidate['native_version']==native.get('upstream_version')
            and candidate['native_archive_sha256']==native.get('archive',{}).get('sha256'),
            'Supplemental installation/native archive identity mismatch')
    if relocated_windows:
        require(native.get('target')=='x86_64-pc-windows-msvc','Only explicit Windows sandbox evidence may relocate')
        require(set(candidate)==set(reference),'Supplemental installation metadata fields differ')
        for key in set(reference)-INSTALLATION_PATH_FIELDS:
            require(candidate[key]==reference[key],'Supplemental content identity differs: '+key)
        require(all(candidate.get(key) for key in INSTALLATION_PATH_FIELDS),'Supplemental deployment identity incomplete')
        expected=PureWindowsPath(candidate['release_environment'])/'Scripts'/'codex-deepseek.exe'
        require(PureWindowsPath(artifact.get('entry',''))==expected,'Supplemental entry is not its installed Windows launcher')
    else:
        require(candidate==reference,'Supplemental installation differs')
    require(artifact.get('entry_sha256')==candidate.get('console_script_sha256'),
            'Supplemental launcher hash differs from its installation')
    return {'content_equivalent':True,'deployment_relocated':candidate!=reference,
            'entry':artifact['entry'],'entry_sha256':artifact['entry_sha256'],
            'release_environment':candidate['release_environment'],'native_package':candidate['native_package'],
            'source_id':candidate['source_id'],'native_manifest_sha256':hashlib.sha256(json.dumps(native,sort_keys=True).encode()).hexdigest()}


def qualification_scope(native):
    """Exact frozen protocol inventory, with explicit platform/variant applicability."""
    target=native.get('target')
    require(target in TARGETS and native.get('upstream_commit')==BASELINE_COMMIT,
            'A new native target or upstream commit requires an explicit scope audit')
    baseline=SOURCE/'reports/history/v0.3.0/runtime-method-matrix.json'
    require(sha(baseline)==BASELINE_MATRIX_SHA256,'Frozen method inventory hash changed')
    matrix=load(baseline);names=[r['method'] for r in matrix]
    require(len(names)==174 and len(set(names))==174,'Frozen native protocol must contain exactly174 distinct client methods')
    for row in matrix:
        method=row['method']
        row.update(observed_in_passing_acceptance=False,execution_status='not_yet_verified_for_this_target',execution_evidence=None)
        if method in REMOTE or method.startswith('userVerification/'):
            row['scope']='backend_or_account_dependent_native_surface'
        elif method in LOCAL_ACCOUNT:
            row.update(scope='local_runtime_surface',upstream_source_note='Local API-key storage/read/logout and no-active-login cancellation are covered; hosted authentication flows and entitlements are separate variants.')
        elif method in WINDOWS_SANDBOX:
            row.update(scope='local_runtime_surface' if target=='x86_64-pc-windows-msvc' else 'other_platform_native_surface',
                       upstream_source_note='Windows native sandbox setup/readiness require positive isolated Windows lifecycle evidence; non-Windows targets retain the upstream platform boundary.')
    local=sorted(row['method'] for row in matrix if row['scope']=='local_runtime_surface')
    label,count=TARGETS[target]
    require(len(local)==count,'Native platform applicability differs from its explicit audited count')
    return {'target':target,'platform':label,'baseline_commit':BASELINE_COMMIT,
            'baseline_matrix_sha256':BASELINE_MATRIX_SHA256,'client_method_count':174,
            'native_schema_count':456,'local_methods':local,'matrix':matrix}


def expected_offline_skips(target):
    """Only named inapplicable OS contracts may skip; all other skips are failures."""
    result={}
    if target!='x86_64-unknown-linux-gnu':
        result['test_platforms::test_live_linux_daemon_retains_provider_service_and_rejects_stale_boot']='Native Linux daemon process identity contract'
    if target=='x86_64-pc-windows-msvc':
        result['test_cds_skill_inspect::test_posix_actual_space_path_and_exact_version_argument']='POSIX shell/shebang fixture; real Windows native execution is a separate acceptance check'
        result['test_cds_skill_inspect::test_posix_actual_offline_timeout_is_bounded']='POSIX shell/shebang timeout fixture; not Windows process execution evidence'
    if target!='x86_64-pc-windows-msvc':
        result['test_platforms::test_extended_paths_cannot_alias_codex_app_state']='Win32 extended-length directory aliases'
        for name in ['daemon.pid','app-server.pid']:
            result['test_platforms::test_live_windows_daemon_retains_service_and_rejects_stale_creation_time['+name+']']='Native Windows daemon creation FILETIME record'
        for parameters in ['0-1-SIGINT','1-3221225786-SIGBREAK']:
            result['test_windows_console::test_native_console_interrupt_keeps_original_signal_and_exit_status['+parameters+']']='Windows console control events; requires a Windows native package'
        for parameters in ['None-TimeoutError','reply1-RuntimeError','reply2-None']:
            result['test_windows_console::test_slow_socket_worker_handshake_keeps_identity_check['+parameters+']']='Windows socket ownership handshake'
        for name in ['test_exe_preserves_special_paths_long_environment_arguments_and_import_isolation',
                     'test_profile_recipe_does_not_replace_running_exe_and_repairs_immutably',
                     'test_locked_activation_fails_atomically_and_preserves_current_receipt']:
            result['test_windows_launchers::'+name]='Exercises Windows PE executable launchers'
    return result


def validate_method_platform_boundaries(report,root,native):
    """Verify the one fixed Windows restricted-token control boundary, never as positive execution."""
    from verify_remaining_local import (windows_command_boundary_detail_valid,WINDOWS_COMMAND_SCOPE,
                                       WINDOWS_STREAM_ERROR,WINDOWS_CONTROL_ERROR)
    target=native['target'];expected={'command/exec/terminate'} if target=='x86_64-pc-windows-msvc' else set()
    rows=report.get('methods',{})
    actual={name for name,row in rows.items() if row.get('status')=='platform_boundary_verified'}
    require(actual==expected,'Unexpected or missing platform/configuration boundary')
    verified={}
    for name in sorted(expected):
        row=rows[name];detail=row.get('detail')
        require(windows_command_boundary_detail_valid(detail,target),'Windows restricted-token boundary detail differs')
        evidence=(Path(root)/row['evidence']).resolve()
        require(comparison_path(evidence).is_relative_to(comparison_path(root)),'Boundary method evidence escaped its run')
        original=[item for item in load(evidence) if item.get('method')==name]
        require(len(original)==1 and original[0]=={k:v for k,v in row.items() if k!='evidence'},'Boundary method raw row differs')
        case=evidence.parent
        reference=Path(detail['reference']['path'])
        require(sha(reference)==detail['reference']['sha256'],'Official boundary reference changed')
        proof=load(reference)
        require(proof.get('passed') is True and proof.get('scope')==WINDOWS_COMMAND_SCOPE
                and proof.get('official_terminate_os_execution') is False,'Reference must describe only the compared configuration guard')
        source=proof.get('source',{})
        require(source.get('upstream_commit')==BASELINE_COMMIT and source.get('byte_identical') is True
                and source.get('sha256')=='772a643ee76491d9103698ad64872af85d273529a6da74e48e2af8861b557738'
                and sha(source['upstream_file'])==sha(source['patched_file'])==source['sha256'],'Native control guard source differs from the fixed official source')
        files={str(evidence):sha(evidence),str(reference):sha(reference),
               source['upstream_file']:sha(source['upstream_file']),source['patched_file']:sha(source['patched_file'])}
        for label in ['official','patched']:
            compared=proof['cases'][label]
            binary_expected=('8c24181c61c94ce8982ab6a5a9408a068f1385d1427c6d424edd5cd330fb6994'
                             if label=='official' else native['files']['bin/codex.exe'])
            require(compared['binary_sha256']==sha(compared['binary'])==binary_expected,'Boundary comparison binary identity differs')
            require(compared.get('setup_state_absent') is True,'Official guard comparison must not provision shared sandbox state')
            for part in ['response','config']:
                require(sha(compared[part])==compared[part+'_sha256'],'Boundary reference raw bytes changed')
                files[compared[part]]=compared[part+'_sha256']
            response=load(compared['response'])
            require(response.get('error')==WINDOWS_STREAM_ERROR and 'result' not in response,'Official/final streaming guard differs')
            config=load(compared['config']);config=config.get('config',config)
            require(config.get('sandbox_mode')=='workspace-write' and config.get('windows',{}).get('sandbox')=='elevated','Compared native configuration is not the restricted Windows scope')
        traces=list(case.glob('client-requests.jsonl'));event_files=list(case.glob('events-*.json'))
        require(len(traces)==len(event_files)==1,'Boundary must retain one unambiguous native session')
        requests=[json.loads(line) for line in traces[0].read_text(encoding='utf-8').splitlines()]
        events=load(event_files[0]);paired={}
        for req in requests:
            if 'method' not in req or 'id' not in req:continue
            key=(type(req['id']).__name__,req['id'])
            require(key not in paired,'Duplicate boundary RPC request identity')
            replies=[v for v in events if 'method' not in v and type(v.get('id')) is type(req['id']) and v.get('id')==req['id']]
            require(len(replies)==1,'Boundary request lacks exactly one native reply')
            paired[key]=(req,replies[0])
        def single(method,predicate):
            found=[pair for pair in paired.values() if pair[0]['method']==method and predicate(pair[0]['params'])]
            require(len(found)==1,'Boundary operation is ambiguous or missing: '+method)
            return found[0]
        stream,stream_reply=single('command/exec',lambda v:v.get('processId')=='stream-boundary')
        active,active_reply=single('command/exec',lambda v:v.get('processId')=='native-windows-boundary')
        terminate,terminate_reply=single(name,lambda v:v.get('processId')=='native-windows-boundary')
        require(stream_reply.get('error')==WINDOWS_STREAM_ERROR and terminate_reply.get('error')==WINDOWS_CONTROL_ERROR,'Current native guard reply differs')
        for req in [stream,active]:
            policy=req['params'].get('sandboxPolicy',{})
            require(policy==detail['sandbox_policy'] and policy.get('networkAccess') is False
                    and req['params'].get('cwd')==detail['workspace'],'Boundary must retain the declared workspace without elevated execution')
        command=detail['active_command'];ready=Path(active['params']['cwd'])/('control-ready-'+command['ready_nonce']+'.json')
        require(sha(ready)==command['ready_file_sha256'],'Actual restricted child readiness changed')
        ready_value=load(ready)
        require(ready_value.get('nonce')==command['ready_nonce'] and ready_value.get('is_admin') is False
                and ready_value.get('sid')==command['sid'],'Restricted child identity differs')
        require(active_reply.get('result',{}).get('exitCode')==0
                and active_reply['result'].get('stdout','').strip()==command['ready_nonce'],'Restricted command must complete naturally after rejected termination')
        files.update({str(traces[0]):sha(traces[0]),str(event_files[0]):sha(event_files[0]),str(ready):sha(ready)})
        verified[name]={'boundary_id':detail['boundary_id'],'scope':detail['scope'],'positive_execution_claimed':False,
                        'reason':'Fixed upstream legacy command/exec control is unavailable with Windows restricted-token sandbox; process APIs have separate positive tests.',
                        'evidence_sha256':files}
    return verified


def validate_offline_collection(cases,collection):
    require(collection.get('schema_version')==1 and collection.get('deselected')==0,'Offline collection was filtered or lacks the supported receipt')
    items=collection.get('items',[])
    require(items and len({row['nodeid'] for row in items})==len(items),'Offline collection is empty or contains duplicate node IDs')
    expected=Counter((row['classname'],row['name']) for row in items)
    actual=Counter((case.get('classname'),case.get('name')) for case in cases)
    require(actual==expected,'Offline JUnit does not contain exactly the collected test cases')
    require(not any(case.find('failure') is not None or case.find('error') is not None for case in cases),'Offline JUnit contains a failure or error')


def qa_python_hashes(source):
    source=Path(source)
    return {path.relative_to(source).as_posix():sha(path) for path in [*source.glob('*.py'),*(source/'tests').rglob('*.py'),*(source/'src').rglob('*.py')]}


def normalized_hashes(values):
    result={name.replace('\\','/'):value for name,value in values.items()}
    require(len(result)==len(values),'Ambiguous verifier path aliases')
    return result


def validate_regression_source(artifact,installation,snapshot=None,*,terminal_revalidated=False):
    recorded=normalized_hashes(artifact.get('verifier_source_sha256',{}));current=qa_python_hashes(SOURCE)
    if snapshot is None:
        require(recorded==current,'Verifier inventory changed after standard gates; an explicit audited producer snapshot and current offline run are required')
        return {'producer':'current_checkout','producer_python_sha256':hashlib.sha256(json.dumps(recorded,sort_keys=True).encode()).hexdigest(),
                'consumer_sha256':sha(SOURCE/'assemble_release_acceptance.py'),'changed_qa_files':[]}
    snapshot=Path(snapshot).resolve();receipt_path=snapshot.parent/(snapshot.name+'.manifest.json');receipt=load(receipt_path)
    files=receipt.get('files',{});require(files and receipt.get('source_id')==installation['source_id'],'Regression snapshot product identity missing')
    digest=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
    require(receipt.get('qa_sha256')==digest,'Regression snapshot manifest digest differs')
    actual={}
    for path in snapshot.rglob('*'):
        if not path.is_file() or any(part in {'__pycache__','.pytest_cache'} for part in path.relative_to(snapshot).parts):continue
        require(comparison_path(path).is_relative_to(comparison_path(snapshot)),'Regression snapshot file escaped its root')
        actual[path.relative_to(snapshot).as_posix()]=sha(path)
    require(actual==files,'Regression snapshot full file inventory/hash differs')
    require(all(files.get(name)==value for name,value in installation['source_sha256'].items()),'Regression snapshot changed installed product source')
    require(recorded==qa_python_hashes(snapshot),'Original standard gates do not match this producer snapshot')
    changed=sorted(name for name in set(recorded)|set(current) if recorded.get(name)!=current.get(name))
    reporting={'assemble_release_acceptance.py','verify_rpc_observations.py','verify_remaining_methods.py','verify_remaining_local.py',
               'verify_complex_runtime.py'}
    if terminal_revalidated:reporting|={'verify_tui_audit.py','verify_tui_interrupt.py'}
    require(all(name in reporting or name.startswith('tests/') for name in changed),
            'A standard gate verifier/shared helper changed; the producer snapshot cannot replace its revalidation: '+repr(changed))
    return {'producer':'explicit_frozen_snapshot','producer_source':str(snapshot),'producer_snapshot_sha256':digest,
            'producer_manifest_sha256':sha(receipt_path),'producer_python_sha256':hashlib.sha256(json.dumps(recorded,sort_keys=True).encode()).hexdigest(),
            'consumer_sha256':sha(SOURCE/'assemble_release_acceptance.py'),'changed_qa_files':changed,
            'unchanged_installed_product_source_files':len(installation['source_sha256'])}


def scope_source_identity(path):
    """Only the statically named applicability function dependencies used by methods."""
    import ast
    tree=ast.parse(Path(path).read_text(encoding='utf-8'))
    functions={'qualification_scope','load','sha','require'}
    constants={'TARGETS','BASELINE_COMMIT','BASELINE_MATRIX_SHA256','REMOTE','LOCAL_ACCOUNT','WINDOWS_SANDBOX'}
    selected={}
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in functions:
            selected[node.name]=ast.dump(node,include_attributes=False)
        elif isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in constants:selected[target.id]=ast.dump(node.value,include_attributes=False)
    require(set(selected)==functions|constants,'Method scope helper dependency inventory differs')
    return hashlib.sha256(json.dumps(selected,sort_keys=True).encode()).hexdigest()


def validate_standard_gates(artifact,verdict,replacement=None):
    require(verdict.get('entry_unchanged') is True and verdict.get('verifier_source_unchanged') is True
            and verdict.get('native_probes_unchanged') is True,'Original standard run artifact/source/probes changed')
    if artifact.get('external_offline') is not None:
        require(verdict.get('external_offline_unchanged') is True,'Referenced offline evidence changed during the standard run')
    rows=verdict.get('gates',[]);selected=artifact.get('selected_gates',[])
    require(len(rows)==len(selected)==len(GATES) and set(selected)==GATES=={row.get('gate') for row in rows},
            'Original run must contain the exact distinct thirty gates')
    require(all(type(row.get('exit')) is int for row in rows),'Original standard gate exit status is invalid')
    failed={row['gate'] for row in rows if row['exit']!=0}
    require(verdict.get('passed') is (not failed),'Original standard run aggregate status contradicts gate results')
    if replacement is None:
        require(not failed,'Standard regression did not fully pass')
        return rows,None
    require(failed<=TERMINAL_GATES,'Non-terminal standard gate failure cannot be replaced')
    root=Path(replacement);new=load(root/'artifact.json');after=load(root/'verdict.json')
    for key in ['entry','entry_sha256','release','installation','native_manifest','target','tui_sandbox_mode','required_local_methods','baseline_matrix_sha256']:
        require(key in artifact and new.get(key)==artifact[key],'Terminal replacement installed target/launch identity differs: '+key)
    require(new.get('controller_environment',{}).get('optimize')==0,'Terminal replacement disabled assertions')
    require(after.get('passed') is True and all(after.get(flag) is True for flag in ['entry_unchanged','verifier_source_unchanged','native_probes_unchanged']),
            'Terminal replacement did not pass immutable artifact checks')
    newrows=after.get('gates',[]);newselected=new.get('selected_gates',[])
    require(len(newrows)==len(newselected)==2 and set(newselected)==TERMINAL_GATES=={row.get('gate') for row in newrows}
            and all(type(row.get('exit')) is int and row['exit']==0 for row in newrows),'Terminal replacement must contain exactly both passing terminal gates')
    recorded=normalized_hashes(new.get('verifier_source_sha256',{}));current=qa_python_hashes(SOURCE)
    # Unrelated report collectors and their tests do not execute either TUI.
    # Bind the execution dependency closure plus every installed adapter module;
    # current complete offline acceptance remains mandatory at the caller.
    required=TERMINAL_EXECUTION_SOURCES|set(new['installation'].get('adapter_source_sha256',{}))
    require(required<=set(recorded) and all(recorded[name]==current.get(name) for name in required),
            'Terminal replacement execution source is not current')
    by_name={row['gate']:row for row in newrows}
    effective=[by_name.get(row['gate'],row) for row in rows]
    lineage={'replacement_root':str(root.resolve()),'original_failed_gates':sorted(failed),
             'original_terminal_gates':[row for row in rows if row['gate'] in TERMINAL_GATES],
             'replacement_gates':newrows,'replacement_artifact_sha256':sha(root/'artifact.json'),
             'replacement_verdict_sha256':sha(root/'verdict.json'),'original_results_preserved':True,
             'execution_source_sha256':{name:recorded[name] for name in sorted(required)},
             'other_qa_changes_rechecked_by_current_offline':sorted(name for name in set(recorded)|set(current)
                                                                   if recorded.get(name)!=current.get(name))}
    return effective,lineage


def superseded_terminal_roots(root):
    root=Path(root).resolve();result={root/name for name in TERMINAL_GATES}
    for name in TERMINAL_GATES:
        text=(root/(name+'.log')).read_text(encoding='utf-8')
        for value in re.findall(r'^EVIDENCE (.+)$',text,re.MULTILINE):
            path=Path(value.strip()).resolve()
            require(comparison_path(path).is_relative_to(comparison_path(root)),'Superseded terminal evidence escaped its original run')
            result.add(path)
    return result


def validate_method_verifiers(artifact,snapshot=None,method_snapshot=None):
    hashes=artifact.get('verifier_sha256',{});expected={'verify_remaining_methods.py','verify_remaining_local.py','verify_live.py',
                                                    'assemble_release_acceptance.py','process_acceptance.py','install.py','windows_acceptance_setup.py','qa_workspace.py'}
    require(set(hashes)==expected,'Per-method producer/helper inventory differs')
    legacy=None
    for name,digest in hashes.items():
        if sha(SOURCE/name)==digest:continue
        selected=method_snapshot or snapshot
        require(name=='assemble_release_acceptance.py' and selected is not None
                and sha(Path(selected)/name)==digest,'Per-method verifier changed: '+name)
        if method_snapshot:
            validate_regression_source({'verifier_source_sha256':qa_python_hashes(Path(selected))},artifact['installation'],selected,terminal_revalidated=True)
            require(scope_source_identity(Path(selected)/name)==scope_source_identity(SOURCE/name),'Method applicability implementation changed')
        legacy={'file':name,'producer_source':str(Path(selected).resolve()),'producer_sha256':digest,'consumer_sha256':sha(SOURCE/name)}
    return {'five_execution_producers_current':True,'scope_helper_from_regression_snapshot':legacy}


def offline_file_hashes(root):
    return {name:sha(Path(root)/name) for name in OFFLINE_FILES}


def validate_windows_controller(controller,*,elevated):
    token=controller.get('windows_token',{})
    require(isinstance(token,dict) and isinstance(token.get('sid'),str)
            and re.fullmatch(r'S-1-\d+(?:-\d+)+',token['sid']) is not None
            and token.get('elevated') is elevated,'Windows controller token identity/elevation is missing or wrong')


def validate_offline_run(root,installation,native,scope,*,current=False,terminal_revalidated=False):
    root=Path(root);artifact=load(root/'artifact.json');verdict=load(root/'verdict.json')
    external=artifact.get('external_offline')
    if external is not None:
        require(not current,'A standalone offline receipt cannot import another receipt')
        validate_windows_controller(artifact.get('controller_environment',{}),elevated=False)
        proof=external_offline_receipt(external['root'],installation,native,scope)
        require(external==proof,'External offline execution provenance changed')
        require(normalized_hashes(artifact.get('verifier_source_sha256',{}))==proof['verifier_source_sha256'],
                'Runtime and referenced offline execution used different QA source')
        require(verdict.get('external_offline_unchanged') is True,'External offline evidence was not stable through the runtime run')
        require(load(root/'results.json')==verdict.get('gates'),'Runtime result rows differ from the terminal verdict')
        for name in OFFLINE_FILES:
            require(sha(root/name)==proof['files_sha256'][name],'Imported offline evidence bytes differ: '+name)
    require(artifact.get('installation')==installation and artifact.get('native_manifest')==native
            and artifact.get('target')==scope['target'] and artifact.get('baseline_matrix_sha256')==BASELINE_MATRIX_SHA256,
            'Offline run belongs to another installed target')
    failures={row.get('gate') for row in verdict.get('gates',[]) if row.get('exit')!=0}
    aggregate_ok=verdict.get('passed') is True or (terminal_revalidated and not current and failures and failures<=TERMINAL_GATES)
    require(artifact.get('controller_environment',{}).get('optimize')==0 and aggregate_ok
            and verdict.get('entry_unchanged') is True and verdict.get('verifier_source_unchanged') is True,'Offline run integrity failed')
    offline=[row for row in verdict.get('gates',[]) if row.get('gate')=='offline']
    require(len(offline)==1 and offline[0].get('exit')==0,'Original/current offline gate did not pass')
    if external is not None:
        require(offline[0]=={**proof['gate'],'execution':'separate_complete_offline_run','external_root':proof['root']},
                'Imported offline gate lost or changed its actual execution origin')
        require(verdict.get('offline_files_sha256')==offline_file_hashes(root),'Imported offline files changed after runtime completion')
    elif verdict.get('offline_files_sha256') is not None:
        require(verdict['offline_files_sha256']==offline_file_hashes(root),'Offline files changed after execution')
    if current:
        require(artifact.get('selected_gates')==['offline'] and len(verdict.get('gates',[]))==1
                and verdict['gates'][0].get('gate')=='offline' and verdict['gates'][0].get('exit')==0,'Current offline supplement must contain exactly the passing offline gate')
        require(normalized_hashes(artifact.get('verifier_source_sha256',{}))==qa_python_hashes(SOURCE),'Current offline supplement source differs')
    cases=ET.parse(root/'offline.xml').getroot().findall('.//testcase');collection=root/'offline-collection.json'
    require(verdict.get('offline_collection_sha256')==sha(collection),'Offline collection receipt changed after regression')
    validate_offline_collection(cases,load(collection));allowed=expected_offline_skips(scope['target']);skips={}
    for case in cases:
        skipped=case.find('skipped')
        if skipped is None:continue
        name=case.get('classname','').split('.')[-1]+'::'+str(case.get('name'))
        require(name in allowed and skipped.get('type')=='pytest.skip' and skipped.get('message')==allowed[name],
                'An unexpected offline case was skipped: '+name)
        require(name not in skips,'Duplicate skipped case: '+name);skips[name]=skipped.get('message')
    require(skips==allowed,'Expected platform-specific tests are missing or misclassified')
    result={'tests_passed':len(cases)-len(skips),'platform_inapplicable':skips,
            'artifact_sha256':sha(root/'artifact.json'),'verdict_sha256':sha(root/'verdict.json'),
            'collection_sha256':sha(collection),'junit_sha256':sha(root/'offline.xml')}
    if external is not None:result['external_execution']=external
    return result


def external_offline_receipt(root,installation,native,scope):
    """Windows symlink tests need a capable token; runtime gates keep their own token."""
    root=Path(root).resolve()
    require(scope['target']=='x86_64-pc-windows-msvc','External offline execution is a Windows-only qualification path')
    names=['artifact.json','verdict.json','results.json',*OFFLINE_FILES]
    require(all((root/name).is_file() and not (root/name).is_symlink()
                and comparison_path(root/name).parent==comparison_path(root) for name in names),
            'External offline evidence must be ordinary files within its own run')
    before={name:sha(root/name) for name in names}
    artifact=load(root/'artifact.json');verdict=load(root/'verdict.json')
    require('external_offline' not in artifact,'Nested offline evidence is not allowed')
    validate_windows_controller(artifact.get('controller_environment',{}),elevated=True)
    expected_entry=PureWindowsPath(installation['release_environment'])/'Scripts'/'codex-deepseek.exe'
    require(PureWindowsPath(artifact.get('entry',''))==expected_entry
            and artifact.get('entry_sha256')==installation.get('console_script_sha256')
            and bool(artifact.get('entry_sha256')),'External offline execution used another installed launcher')
    require(load(root/'results.json')==verdict.get('gates'),'External offline result rows differ from its terminal verdict')
    require(verdict.get('offline_files_sha256')=={name:before[name] for name in OFFLINE_FILES},
            'External offline raw files were not bound at original execution completion')
    checked=validate_offline_run(root,installation,native,scope,current=True)
    require(verdict.get('native_probes_unchanged') is True,'External offline probe identity changed')
    gate=verdict['gates'][0]
    require(gate.get('log')=='offline.log' and type(gate.get('exit')) is int
            and 'execution' not in gate and 'external_root' not in gate,'External offline gate is not an original local execution')
    require(before=={name:sha(root/name) for name in names},'External offline files changed during validation')
    return {'root':str(root),'execution':'separate_complete_offline_run',
            'controller_environment':artifact['controller_environment'],
            'verifier_source_sha256':normalized_hashes(artifact['verifier_source_sha256']),
            'gate':gate,'validation':checked,'files_sha256':before}


def validate_native_source_audit(proof,native):
    # Reconstruct the pinned archive inventory and target recipe, then recheck
    # the exact source tree and shipped package rather than trusting counts.
    from verify_native_source import validate_audit
    try:
        return validate_audit(proof,native)
    except (OSError,ValueError,KeyError,TypeError) as error:
        raise SystemExit('Native source/package audit failed: '+str(error)) from error


def validate_linux_resources(path,installation,native):
    require(native.get('target')=='x86_64-unknown-linux-gnu','Linux resource evidence cannot qualify another target')
    artifact=load(path/'artifact.json');proof=load(path/'verdict.json')
    require(artifact.get('installation')==installation==proof.get('installation') and artifact.get('native_manifest')==native,
            'Linux resource evidence belongs to another installation')
    require(artifact.get('target')==proof.get('target')==native['target'],'Linux resource target identity mismatch')
    require(artifact.get('entry_sha256')==installation['console_script_sha256']
            and artifact.get('binary_sha256')==native['files']['bin/codex']
            and artifact.get('bwrap_sha256')==native['files'].get('codex-resources/bwrap')
            and artifact.get('bwrap_sha256'),'Linux resource entry/CLI/bwrap identity mismatch')
    require(artifact.get('verifier_sha256')==sha(SOURCE/'verify_linux_resources.py'),'Linux resource verifier changed')
    fields=['passed','bundled_resource_forced','native_inside_write','native_outside_write_denied',
            'tampered_bwrap_rejected_before_execution','verifier_and_artifact_unchanged']
    require(all(proof.get(name) is True for name in fields),'Linux native resource or enforcement check incomplete')
    require(type(proof.get('native_exit_code')) is int and proof['native_exit_code']==0
            and type(proof.get('tampered_exit_code')) is int and proof['tampered_exit_code']!=0,
            'Linux sandbox execution/tamper exit status missing or incorrect')
    require('NATIVE_OUTSIDE_DENIED' in (path/'native-sandbox.txt').read_text(encoding='utf-8')
            and 'bundled bubblewrap digest mismatch' in (path/'native-digest-rejection.txt').read_text(encoding='utf-8')
            and (path/'outside.txt').read_bytes()==b'PRESERVE' and not (path/'work/inside.txt').exists(),
            'Linux resource raw denial/digest/effect evidence differs from its verdict')
    return {'bundled_resource_forced':True,'native_inside_write':True,'native_outside_write_denied':True,
            'tampered_bwrap_rejected_before_execution':True,'bwrap_sha256':artifact['bwrap_sha256']}


def gate_evidence(regression,gate):
    regression=regression.resolve()
    paths=re.findall(r'^EVIDENCE (.+)$',(regression/(gate+'.log')).read_text(encoding='utf-8'),re.MULTILINE)
    require(len(paths)==1,'Expected exactly one terminal evidence root for '+gate)
    path=Path(paths[0].strip()).resolve()
    require(comparison_path(path).is_relative_to(comparison_path(regression)),'Terminal evidence escaped its regression run')
    return path


def validate_terminal_qa(path,entry,installation,native,mode,*,interrupt=False):
    qa=load(path/'qa-source.json')
    names={'verify_tui_audit.py','terminal_acceptance.py','credential_acceptance.py','verify_live.py','windows_acceptance_setup.py','qa_workspace.py'}
    if interrupt:names|={'verify_tui_interrupt.py','process_acceptance.py'}
    files=qa.get('qa_files_sha256',{})
    require(set(files)==names and all(sha(SOURCE/name)==digest for name,digest in files.items()),'Terminal QA source identity changed or is incomplete')
    require(qa.get('qa_snapshot_sha256')==hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),'Terminal QA snapshot digest mismatch')
    require(comparison_path(qa.get('entry',''))==comparison_path(entry) and qa.get('entry_sha256')==installation['console_script_sha256']
            and qa.get('adapter_source_id')==installation['source_id'] and qa.get('native_archive_sha256')==installation['native_archive_sha256'],
            'Terminal QA belongs to a different installed artifact')
    config=tomllib.loads((path/'home/config.toml').read_text(encoding='utf-8'))
    require(config.get('sandbox_mode')==mode,'Terminal sandbox mode differs from the regression invocation')
    expected_terminal='conpty' if native['target']=='x86_64-pc-windows-msvc' else 'pty'
    from verify_tui_audit import rendered_reply,session_log_complete
    def phase(row,label,reply):
        require(row.get('passed') is True and row.get('terminal')==expected_terminal and row.get('backend') in {'shared','embedded'},'Native terminal phase failed or used a simulated terminal')
        require(native['target']=='x86_64-pc-windows-msvc' or row['backend']=='shared','Non-Windows TUI lost its native shared daemon')
        require(row.get('thread_id') and row.get('native_rollout_trace_files_changed',0)>0 and row.get('rendered_reply')==reply,'Native terminal current phase trace/reply evidence is missing')
        require(rendered_reply((path/(label+'.pty')).read_bytes(),reply),'Native terminal assistant cell is missing from the raw transcript')
        records=[json.loads(line) for line in (path/(label+'-native-session.jsonl')).read_text(encoding='utf-8').splitlines()]
        require(session_log_complete(records),'Native terminal session log is incomplete')
    if not interrupt:
        rows=load(path/'results.json')
        require(len(rows)==2 and [r.get('name') for r in rows]==['initial','cold-resume'],'Native TUI must complete both initial and cold-resume phases')
        for row,label,reply in zip(rows,['initial','cold-resume'],['TUI_VERIFIED_INITIAL_5271','TUI_VERIFIED_RESUMED_5271']):phase(row,label,reply)
        require(rows[0]['thread_id']==rows[1]['thread_id'] and rows[0]['tui_pid']!=rows[1]['tui_pid'] and rows[0]['backend']==rows[1]['backend'],
                'Cold TUI resume did not preserve the same native thread across separate processes')
        require(rows[0]['backend']!='shared' or rows[0].get('daemon_pid') and rows[1].get('daemon_pid') and rows[0]['daemon_pid']!=rows[1]['daemon_pid'],
                'Cold TUI resume did not replace its shared daemon')
        trace_files=[file for file in (path/'native-rollout-traces').rglob('*') if file.is_file()]
        require(trace_files and rows[1].get('native_rollout_trace_files')==len(trace_files) and any(file.stat().st_size for file in trace_files),
                'Native TUI rollout trace files are missing or differ from the receipt')
        require((path/'work/tui-first.txt').read_bytes()==b'TUI_FIRST_5271' and (path/'work/tui-resumed.txt').read_bytes()==b'TUI_FIRST_5271_RESUMED','Native TUI file effects differ from the verdict')
        return {'passed':True,'terminal':expected_terminal,'backend':rows[0]['backend'],'same_thread_cold_resume':True,'sandbox_mode':mode}
    proof=load(path/'results.json')
    require(proof.get('passed') is True and proof.get('terminal')==expected_terminal and proof.get('qa_snapshot_sha256')==qa['qa_snapshot_sha256']
            and proof.get('sandbox_mode')==mode and comparison_path(proof.get('entry',''))==comparison_path(entry)
            and proof.get('backend') in ({'shared','embedded'} if native['target']=='x86_64-pc-windows-msvc' else {'shared'}),
            'Native terminal interruption identity/terminal proof failed')
    require(proof.get('background_retained_after_interrupt') is True and proof.get('native_stop_terminated_fixture') is True
            and not proof.get('failed_run_cleanup_required') and proof.get('terminal_exit_code')==0,'Native interrupt/stop lifecycle did not finish through the native UI')
    require(proof.get('interrupted_turn_id') and proof.get('continued_turn_id') and proof['interrupted_turn_id']!=proof['continued_turn_id'],'Native TUI did not complete a different turn after interruption')
    content=(path/'work/after-interrupt.txt').read_bytes()
    require(content==b'AFTER_CTRL_C_2719' and hashlib.sha256(content).hexdigest()==proof.get('continued_file_sha256')
            and rendered_reply((path/'transcript.pty').read_bytes(),'TUI_INTERRUPT_RECOVERED_2719'),'TUI continuation lacks its actual file effect or rendered answer')
    rollouts=[];rollout_paths=[]
    for file in (path/'home/sessions').rglob('*.jsonl'):
        rows=[json.loads(line) for line in file.read_text(encoding='utf-8').splitlines()]
        if rows and rows[0].get('type')=='session_meta' and rows[0].get('payload',{}).get('id')==proof.get('thread_id'):rollouts.append(rows);rollout_paths.append(file)
    require(len(rollouts)==1,'Native interruption has no unique matching session rollout')
    from verify_tui_interrupt import yielded_background_session
    background=yielded_background_session(rollout_paths[0],'interrupt_fixture.py')
    require(background is not None and proof.get('background_session')==background,'Native interruption lacks a yielded background session before Ctrl-C')
    events=[row['payload'] for row in rollouts[0] if row.get('type')=='event_msg']
    aborted=[i for i,row in enumerate(events) if row.get('type')=='turn_aborted' and row.get('turn_id')==proof['interrupted_turn_id'] and row.get('reason')=='interrupted']
    complete=[i for i,row in enumerate(events) if row.get('type')=='task_complete' and row.get('turn_id')==proof['continued_turn_id'] and row.get('last_agent_message')=='TUI_INTERRUPT_RECOVERED_2719']
    require(len(aborted)==len(complete)==1 and aborted[0]<complete[0],'Native interrupted turn and subsequent completion are not ordered in the same session')
    records=[json.loads(line) for line in (path/'native-session.jsonl').read_text(encoding='utf-8').splitlines()]
    require(session_log_complete(records)
            and proof.get('native_session_log_records')==len(records) and proof.get('transcript_bytes')==(path/'transcript.pty').stat().st_size
            and any(row.get('dir')=='from_tui' and row.get('kind')=='op' and row.get('payload')=='CleanBackgroundTerminals' for row in records),
            'Native /stop cleanup operation was not recorded')
    from codex_deepseek.processes import matches
    require(proof.get('fixture_pid') and proof.get('fixture_birth') and not matches(proof['fixture_pid'],proof['fixture_birth']),
            'Native /stop fixture is still running or its kernel identity is absent')
    return {'passed':True,'terminal':expected_terminal,'backend':proof['backend'],'same_session_continued_after_ctrl_c':True,
            'background_retained_until_native_stop':True,'native_stop_kernel_cleanup_verified':True,'sandbox_mode':mode}


def validate_hook_trust_cases(root, rows):
    """Reconstruct UI, exact turn, trust hashes and effects from immutable raw files."""
    from verify_hook_trust import CASE_NAMES, case_contract, one_fixture_hook
    root=Path(root).resolve()
    require(isinstance(rows,list) and [row.get('name') for row in rows]==list(CASE_NAMES),
            'Hook trust requires all three distinct native lifecycle cases')
    all_files={};previous_calls=[];declarations=[];hashes=[];nonces=[]
    for row in rows:
        name=row['name']
        require(row.get('passed') is True and row.get('root')==name,'Hook trust case failed or escaped its scope')
        case=root/name;recorded=row.get('evidence_sha256')
        require(isinstance(recorded,dict) and recorded,'Hook trust raw inventory missing')
        actual={}
        for path in case.rglob('*'):
            require(not path.is_symlink() and not getattr(path,'is_junction',lambda:False)(),
                    'Hook trust evidence contains a link')
            if path.is_file():
                require(comparison_path(path).is_relative_to(comparison_path(case)),'Hook trust evidence escaped case')
                actual[path.relative_to(case).as_posix()]=sha(path)
        require(actual==recorded,'Hook trust raw evidence inventory/hash differs')
        required={'expectations.json','hooks-before.json','hooks-after.json','config-before.toml','config-after.toml',
                  'hook-calls-before.json','hook-calls-after.json','shell-effect.json','terminal.pty','inputs.json',
                  'completion.json','native-thread.json','cleanup.json','hooks-declaration.json',
                  'native-session.jsonl','rollout.jsonl'}
        require(required<=set(actual),'Hook trust lifecycle raw files missing')
        expected=load(case/'expectations.json')
        require(expected.get('name')==name,'Hook trust expectations belong to another case')
        before=one_fixture_hook(load(case/'hooks-before.json'))
        after=one_fixture_hook(load(case/'hooks-after.json'))
        require(before==expected['before_hook'] and after==expected['after_hook'],'Hook trust raw discovery differs')
        calls_before=load(case/'hook-calls-before.json');calls_after=load(case/'hook-calls-after.json')
        require(calls_before==previous_calls,'Hook execution history is discontinuous between cases')
        cleanup=load(case/'cleanup.json')
        require(cleanup.get('passed') is True and cleanup.get('errors')==[],'Hook terminal/process cleanup failed')
        case_contract(name,before,after,(case/'config-before.toml').read_bytes(),
                      (case/'config-after.toml').read_bytes(),calls_before,calls_after,
                      expected['hook_nonce'],load(case/'shell-effect.json'),expected['shell_effect'],
                      (case/'terminal.pty').read_bytes(),load(case/'inputs.json'),
                      load(case/'completion.json'),load(case/'native-thread.json'))
        previous_calls=calls_after;declarations.append((case/'hooks-declaration.json').read_bytes())
        hashes.append(before['currentHash']);nonces.append(expected['shell_effect']['nonce'])
        all_files.update({name+'/'+rel:digest for rel,digest in actual.items()})
    require(declarations[0]==declarations[1] and declarations[2]!=declarations[1],
            'Hook declaration did not change only at retrust case')
    require(hashes[0]==hashes[1] and hashes[2]!=hashes[1],'Native changed-declaration hash was not observed')
    require(len(set(nonces))==3,'Hook lifecycle reused a shell-effect identity')
    return {'passed':True,'cases':list(CASE_NAMES),'raw_reconstructed':True,'evidence_sha256':all_files}


def validate_hook_trust(root,installation,native):
    from verify_hook_trust import CASE_NAMES, VERIFIERS
    root=Path(root)
    identity=load(root/'artifact.json');proof=load(root/'verdict.json')
    installation_lineage(installation,native,identity)
    require(proof.get('installation')==installation and proof.get('target')==native['target'],
            'Hook trust verdict installation differs')
    require(proof.get('passed') is True and proof.get('verifier_and_artifact_unchanged') is True
            and proof.get('native_trust_ui_exercised') is True and proof.get('bypass_hook_trust_used') is False
            and proof.get('failure') is None,'Native hook trust was not fully verified')
    require(identity.get('controller_optimize')==0 and identity.get('cases')==list(CASE_NAMES),
            'Hook trust controller contract differs')
    verifiers=identity.get('verifier_sha256',{})
    require(set(verifiers)==set(VERIFIERS) and all(sha(SOURCE/name)==digest for name,digest in verifiers.items()),
            'Hook trust verifier dependency inventory differs')
    require(load(root/'results.json')==proof.get('cases'),'Hook trust result rows differ from verdict')
    return validate_hook_trust_cases(root,proof['cases'])


def main():
    p=argparse.ArgumentParser()
    for flag in ['entry','regression','methods','local-account','browser','browser-protocol','protocol-recovery','native-lifetime','user-verification','schema','native-source-audit','fixtures','rpc-observations']:
        p.add_argument('--'+flag,type=Path,required=True)
    p.add_argument('--windows-sandbox',type=Path)
    p.add_argument('--fault-boundaries',type=Path,help='Required Windows native network/auth/non-idempotent effect fault comparison')
    p.add_argument('--hook-trust',type=Path,required=True,help='Actual native first trust, skip and changed-hook UI lifecycle evidence')
    p.add_argument('--linux-resources',type=Path)
    p.add_argument('--regression-source',type=Path,help='Explicit frozen producer QA directory with a same-name .manifest.json sidecar')
    p.add_argument('--offline-regression',type=Path,help='Current-source complete offline-only acceptance run')
    p.add_argument('--complex-runtime',type=Path,help='Independent raw runtime proofs; application test failures remain reported separately')
    p.add_argument('--methods-source',type=Path,help='Explicit frozen methods producer snapshot; execution verifiers and scope implementation must match')
    p.add_argument('--terminal-regression',type=Path,help='Current-source passing tui+tui-interrupt run, replacing only those original gates')
    p.add_argument('--output',type=Path);args=p.parse_args()
    entry=args.entry.resolve();release=entry.parent.parent.parent;metadata=load(release/'installation.json');native=load(release/'native-runtime.json')
    scope=qualification_scope(native)
    require(entry==console_entry(release/'venv'),'Expected immutable installed entry')
    require(valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native),'Installation integrity failed')
    source_id,source_hashes=source_identity()
    require(source_id==metadata['source_id'] and source_hashes==metadata['source_sha256'],'Full installed source identity differs from the current checkout')
    for name,digest in metadata['source_sha256'].items():require(sha(SOURCE/name)==digest,'Installed runtime source differs: '+name)
    version=tomllib.loads((SOURCE/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    evidence={}
    def record(label,path):evidence[label]=sha(path)
    def same_install(a,label):require(a['installation']==metadata,'Mixed runtime installations in '+label)
    hook_trust=validate_hook_trust(args.hook_trust,metadata,native)
    for name in ['artifact.json','verdict.json','results.json']:
        record('hook_trust_'+name,args.hook_trust/name)
    for name,expected in hook_trust.pop('evidence_sha256').items():
        record('hook_trust_raw_'+name,args.hook_trust/name)
        require(evidence['hook_trust_raw_'+name]==expected,'Hook evidence changed during assembly')
    native_source=validate_native_source_audit(load(args.native_source_audit),native)
    record('native_source_audit',args.native_source_audit)
    linux_resources=None
    if scope['target']=='x86_64-unknown-linux-gnu':
        require(args.linux_resources is not None,'Linux requires bundled native sandbox resource/enforcement evidence')
        linux_resources=validate_linux_resources(args.linux_resources,metadata,native)
        for name in ['artifact.json','verdict.json','native-sandbox.txt','native-digest-rejection.txt','outside.txt']:
            record('linux_resources_'+name,args.linux_resources/name)
    else:require(args.linux_resources is None,'Linux resource evidence cannot qualify another target')
    regression=load(args.regression/'verdict.json');artifact=load(args.regression/'artifact.json');same_install(artifact,'regression')
    require(artifact.get('controller_environment',{}).get('optimize')==0,'Acceptance controller did not attest active Python assertions')
    require(artifact.get('target')==scope['target'] and artifact.get('required_local_methods')==scope['local_methods'] and artifact.get('baseline_matrix_sha256')==BASELINE_MATRIX_SHA256,'Standard regression uses another native applicability baseline')
    require(args.terminal_regression is None or (args.regression_source and args.offline_regression),
            'Terminal replacement requires an explicit original producer snapshot and current offline supplement')
    effective_gates,terminal_lineage=validate_standard_gates(artifact,regression,args.terminal_regression)
    for gate in regression['gates']:
        log=(args.regression/gate['log']).resolve()
        require(comparison_path(log).is_relative_to(comparison_path(args.regression)),'Standard gate log escaped its run')
        record('standard_gate_log_'+gate['gate'],log)
    probes=artifact.get('native_probes',{})
    for name in ['revocation','keyring']:
        probe=probes.get(name,{})
        require(probe.get('path') and probe.get('sha256') and sha(probe['path'])==probe['sha256'],
                'Native '+name+' probe identity is missing or changed')
    require(regression.get('native_probes_unchanged') is True,'Native acceptance probe changed during regression')
    require(bool(args.regression_source)==bool(args.offline_regression),'Explicit regression producer snapshot and current offline supplement must be supplied together')
    qa_lineage=validate_regression_source(artifact,metadata,args.regression_source,terminal_revalidated=args.terminal_regression is not None)
    qa_lineage['terminal_revalidation']=terminal_lineage
    if args.terminal_regression:
        for name in ['artifact.json','verdict.json','tui.log','tui-interrupt.log']:record('terminal_replacement_'+name,args.terminal_regression/name)
    record('regression_artifact',args.regression/'artifact.json');record('regression_verdict',args.regression/'verdict.json')
    methods=load(args.methods/'verdict.json');method_artifact=load(args.methods/'artifact.json');same_install(method_artifact,'per-method acceptance')
    require(method_artifact.get('target')==scope['target'] and method_artifact.get('baseline_matrix_sha256')==BASELINE_MATRIX_SHA256,'Per-method platform/baseline identity missing')
    platform_boundaries=validate_method_platform_boundaries(methods,args.methods,native)
    require(methods['passed'] and methods['verifier_and_artifact_unchanged'] and methods['positive_local_methods']==60-len(platform_boundaries)
            and methods['backend_boundaries_verified']==4 and methods.get('platform_boundaries_verified',0)==len(platform_boundaries)
            and not methods['missing'] and not methods['unexpected'] and not methods['invalid'],'Per-method acceptance incomplete')
    expected=set(load(SOURCE/'reports/runtime-v0.3-unexercised-local.json')['methods'])
    require(set(methods['methods'])==expected,'Per-method names differ from the frozen gap inventory')
    require({g['group'] for g in methods['groups']}==set(method_artifact['groups']) and len(methods['groups'])==7 and all(g['exit']==0 for g in methods['groups']),'Per-method group did not complete')
    qa_lineage['method_producers']=validate_method_verifiers(method_artifact,args.regression_source,args.methods_source)
    for name,row in methods['methods'].items():
        path=(args.methods/row['evidence']).resolve()
        require(comparison_path(path).is_relative_to(comparison_path(args.methods)),'Method evidence escaped its run')
        original=[r for r in load(path) if r['method']==name]
        require(len(original)==1 and original[0]=={k:v for k,v in row.items() if k!='evidence'},'Per-method postcondition receipt differs: '+name)
        require(row['status']==('backend_boundary_verified' if name in REMOTE else 'platform_boundary_verified' if name in platform_boundaries else 'passed'),'Per-method status differs: '+name)
    require({n for n,r in methods['methods'].items() if r['status']=='backend_boundary_verified'}==REMOTE,'Incorrect backend scope classification')
    record('methods_artifact',args.methods/'artifact.json');record('methods_verdict',args.methods/'verdict.json')
    lifecycle_lineage={}
    def local_lifecycle(path,names,label,*,relocated_windows=False):
        proof=load(path/'verdict.json');identity=load(path/'artifact.json')
        lifecycle_lineage[label]=installation_lineage(metadata,native,identity,relocated_windows=relocated_windows)
        require(proof.get('installation')==identity['installation'],label+' verdict and artifact installations differ')
        require(proof.get('passed') is True and proof.get('target')==scope['target'] and identity.get('target')==scope['target'],label+' platform/lifecycle failed')
        require(proof.get('verifier_and_artifact_unchanged') is True,label+' verifier/artifact immutability was not verified')
        producer='verify_local_account.py' if label=='local_account' else 'verify_windows_sandbox.py'
        validate_peer_verifiers(identity,{producer,'install.py'},label)
        rows=proof['methods'];require(len(rows)==len(names) and {r['method'] for r in rows}==names and all(r['status']=='passed' for r in rows),label+' method postconditions incomplete')
        require(load(path/'results.json')==rows,label+' raw results differ from verdict')
        for filename in ['artifact.json','verdict.json','results.json']:record(label+'_'+filename,path/filename)
        return proof
    account=local_lifecycle(args.local_account,LOCAL_ACCOUNT,'local_account')
    require(account.get('model_requests')==0,'Local account verifier submitted inference')
    if scope['target']=='x86_64-pc-windows-msvc':
        require(args.windows_sandbox is not None,'Windows requires positive isolated native sandbox lifecycle evidence')
        sandbox=local_lifecycle(args.windows_sandbox,WINDOWS_SANDBOX,'windows_sandbox',relocated_windows=True)
        require(sandbox.get('positive_lifecycle_exercised') is True,'Windows sandbox was only observed at an unavailable/setup boundary')
    elif args.windows_sandbox is not None:
        raise SystemExit('Windows sandbox evidence cannot qualify another target')
    tui_mode=artifact.get('tui_sandbox_mode')
    require(tui_mode=='workspace-write' or (scope['target']=='x86_64-pc-windows-msvc' and tui_mode=='danger-full-access' and 'windows_sandbox' in lifecycle_lineage),
            'Native terminal sandbox mode is missing or lacks matching guest enforcement evidence')
    tui_proofs={}
    for gate in ['tui','tui-interrupt']:
        path=gate_evidence(args.terminal_regression or args.regression,gate)
        tui_proofs[gate]=validate_terminal_qa(path,entry,metadata,native,tui_mode,interrupt=gate=='tui-interrupt')
        for name in ['qa-source.json','results.json','home/config.toml']:
            record(gate+'_'+name,path/name)
        patterns=['transcript.pty','native-session.jsonl','work/after-interrupt.txt','home/sessions/**/*.jsonl'] if gate=='tui-interrupt' else ['initial.pty','cold-resume.pty','initial-native-session.jsonl','cold-resume-native-session.jsonl','work/tui-first.txt','work/tui-resumed.txt']
        for pattern in patterns:
            for file in path.glob(pattern):record(gate+'_'+file.relative_to(path).as_posix(),file)
    browser=load(args.browser/'results.json');browser_artifact=load(args.browser/'artifact.json');browser_verdict=load(args.browser/'verdict.json')
    same_install(browser_artifact,'native browser');same_install(browser_verdict,'native browser verdict')
    require(browser_verdict.get('passed') is True and browser_verdict.get('verifier_and_artifact_unchanged') is True,'Native browser artifact/verifier changed or scenarios incomplete')
    require(browser_artifact.get('native_manifest')==native and browser_artifact.get('target')==scope['target'],'Native browser target/manifest mismatch')
    require(browser_artifact.get('sandbox_mode')==browser_verdict.get('sandbox_mode') and
            (browser_artifact.get('sandbox_mode')=='workspace-write' or
             (scope['target']=='x86_64-pc-windows-msvc' and browser_artifact.get('sandbox_mode')=='danger-full-access' and 'windows_sandbox' in lifecycle_lineage)),
            'Native browser sandbox mode is missing or lacks a matching positive native Windows sandbox lifecycle')
    validate_peer_verifiers(browser_artifact,{'verify_browser_runtime.py','install.py','acceptance_assertions.py'},'Native browser')
    required_browser={'native_direct_mcp_actual_open_find_click','cold_native_mcp_page_reference_recovery','model_native_browse_find_follow_and_file_effect','bundled_public_browser_actual_https_find_and_link','slow_native_browser_request_actually_started','native_interrupt_preserves_official_mcp_lifetime','browser_remains_usable_after_turn_interruption','native_owner_shutdown_closes_browser_http_request'}
    require(required_browser<={r['name'] for r in browser} and all(r['passed'] for r in browser),'Native browser acceptance incomplete')
    record('browser_artifact',args.browser/'artifact.json');record('browser_results',args.browser/'results.json');record('browser_verdict',args.browser/'verdict.json')
    protocol=load(args.browser_protocol/'verdict.json');same_install(protocol,'browser protocol')
    require(protocol['passed'] and protocol['http_cancelled_by_mcp'] and protocol['anticipated_error_readable'],'Browser protocol did not pass')
    require(protocol.get('verifier_and_artifact_unchanged') is True and protocol.get('target')==scope['target'] and protocol.get('native_manifest')==native,'Browser protocol artifact/verifier identity failed')
    require(protocol.get('verifier_sha256'),'Browser protocol verifier identity missing')
    for name,digest in protocol['verifier_sha256'].items():require(sha(SOURCE/name)==digest,'Browser protocol verifier changed: '+name)
    record('browser_protocol',args.browser_protocol/'verdict.json')
    recovery=[]
    for path in args.protocol_recovery.glob('*/verdict.json'):
        row=validate_protocol_recovery(path.parent,metadata,native)
        recovery.append(row);record('protocol_recovery_'+row['kind'],path)
        record('protocol_recovery_'+row['kind']+'_artifact',path.parent/'artifact.json')
        for name in row['evidence_sha256']:record('protocol_recovery_'+row['kind']+'_'+name,path.parent/name)
    require({r['kind'] for r in recovery}==RECOVERY_KINDS and len(recovery)==len(RECOVERY_KINDS),'Every protocol fault case is required exactly once')
    lifetime=load(args.native_lifetime/'verdict.json');uv=load(args.user_verification/'verdict.json');schema=load(args.schema)
    same_install(lifetime,'native MCP browser interpreter')
    require(lifetime.get('target')==scope['target'],'Native MCP interpreter target differs')
    expected_native=sha(Path(metadata['native_package'])/'bin'/('codex.exe' if scope['target']=='x86_64-pc-windows-msvc' else 'codex'))
    for row,label in [(lifetime,'native_lifetime'),(uv,'user_verification')]:
        require(row['passed'] and row['binary_sha256']['patched']==expected_native,'Native comparison mismatch: '+label)
        require(row['binary_sha256']['official']==schema.get('official_binary_sha256'),'Native comparisons did not use the same archive-verified official binary: '+label)
        require(row.get('binary_and_verifier_unchanged') is True and row.get('verifier_sha256'),'Native comparison artifact/verifier identity missing: '+label)
        expected={'verify_native_mcp_lifetime.py','install.py','src/codex_deepseek/cli.py'} if label=='native_lifetime' else {'verify_user_verification_boundary.py','src/codex_deepseek/cli.py'}
        validate_peer_verifiers(row,expected,label)
    require(uv['positive_credential_lifecycle_exercised'] is False,'Do not conflate account guard with real biometric enrollment')
    require(browser_artifact['native_lifetime_proof_sha256']==sha(args.native_lifetime/'verdict.json'),'Browser interruption witness used another native lifetime baseline')
    require(schema['passed'] and schema['schemas_compared']==scope['native_schema_count'] and not schema['differences'] and schema['patched_binary_sha256']==expected_native,'Native schema comparison incomplete')
    require(schema.get('binary_and_verifier_unchanged') is True and schema.get('target')==scope['target'] and schema.get('native_manifest')==native and schema.get('verifier_sha256'),'Native schema provenance incomplete')
    for name,digest in schema['verifier_sha256'].items():require(sha(SOURCE/name)==digest,'Native schema verifier changed: '+name)
    record('native_lifetime',args.native_lifetime/'verdict.json');record('user_verification_boundary',args.user_verification/'verdict.json');record('schema_comparison',args.schema)
    native_faults=None
    if scope['target']=='x86_64-pc-windows-msvc':
        require(args.fault_boundaries is not None,'Windows requires native network/auth/tool-effect fault boundary evidence')
        native_faults=validate_native_faults(args.fault_boundaries,entry,metadata,native,schema['official_binary_sha256'])
        for name,digest in native_faults.pop('evidence_sha256').items():evidence['native_fault_'+name]=digest
    else:require(args.fault_boundaries is None,'Windows native fault evidence cannot qualify another platform')
    real={}
    for name in ['jinja','httpcore']:
        root=args.fixtures/name;row=load(root/'results.json');stage=root/row['followup']['evidence'] if row.get('followup') else root;real_artifact=load(stage/'artifact.json');same_install(real_artifact,name+' final real-task pass')
        expected={'verify_real_work.py','acceptance_assertions.py','host_execution_acceptance.py','real_work_imports.py'}
        if 'verify_real_work_followup.py' in real_artifact.get('verifier_sha256',{}):expected.add('verify_real_work_followup.py')
        validate_peer_verifiers(real_artifact,expected,name+' real task')
        complex_proof=None
        if args.complex_runtime:
            complex_path=args.complex_runtime/(name+'.json')
            complex_proof=validate_complex_runtime(complex_path,root,entry,metadata)
            record(name+'_complex_runtime',complex_path)
        else:require(row['passed'] and real_work_passed(row),name+' real task incomplete')
        sandbox_mode=row.get('sandbox_mode')
        require(sandbox_mode=='workspace-write' or
                (scope['target']=='x86_64-pc-windows-msvc' and sandbox_mode=='danger-full-access' and 'windows_sandbox' in lifecycle_lineage),
                name+' real-task sandbox mode is missing or lacks its matching native sandbox lifecycle proof')
        if complex_proof is None:require(all(v['exit']==0 for v in row['validation']),name+' external regression failed')
        package='jinja2' if name=='jinja' else 'httpcore';tested_root=root/'work/src' if name=='jinja' else root/'work'
        for suite in row['validation']:
            imports=root/('final-'+suite['suite']+'-imports.json');proof=load(imports)
            require(proof==suite.get('source_imports') and proof.get('passed') is True and proof.get('package')==package
                    and comparison_path(proof.get('expected_root',''))==comparison_path(tested_root) and not proof.get('invalid_modules'),
                    name+' independent suite import receipt differs')
            modules=proof.get('modules',{});require(package in modules,name+' suite did not load the tested package')
            for module,identity in modules.items():
                require((module==package or module.startswith(package+'.')) and comparison_path(identity['file']).is_relative_to(comparison_path(tested_root))
                        and sha(identity['file'])==identity['sha256'],name+' suite loaded foreign or subsequently changed production code')
            record(name+'_imports_'+suite['suite'],imports)
        require({'open','find','click'}<=set(row['web_research']['native_completed_actions']) and row['web_research']['distinct_actual_urls']>=2,name+' actual multi-page browsing incomplete')
        events=[]
        for index,path in enumerate(sorted(root.glob('events-*.json'))):events.extend(load(path));record(name+'_native_events_'+str(index),path)
        current=current_browser_evidence(events,row['thread'],turn=row.get('final_turn_id'))
        require(current['passed'],name+' final real-task turn lacks matched current native browsing evidence')
        if complex_proof is None:require(current==row['web_research']['current_turn'],name+' browser summary differs from native evidence')
        require((row.get('followup') or row)['source_id']==metadata['source_id'],name+' final real task used another source version')
        if name=='httpcore':require(row['restarted'] and {'real-tcp-0','real-tcp-1','real-tcp-2','tls-socket','independent-cancellation'}<={v['suite'] for v in row['validation']},'HTTPcore cold recovery or stress missing')
        record(name+'_final_results',root/'results.json');record(name+'_final_artifact',stage/'artifact.json');record(name+'_final_patch',root/'agent.patch')
        real[name]={'passed':True,'sandbox_mode':sandbox_mode,'native_sandbox_lifecycle_verified_separately':sandbox_mode=='danger-full-access','cold_resume_after_source_edit':row.get('restarted',False),'fresh_independent_native_review':True,'unchanged_oracle':True,'validation':[{'suite':v['suite'],'exit':v['exit'],'tail':v.get('tail','')[-350:].replace(str(root),'<fixture>')} for v in row['validation']],'web_research':{'actions':row['web_research']['native_completed_actions'],'distinct_urls':row['web_research']['distinct_actual_urls'],'pages':row['web_research']['pages']},'task_patch_sha256':sha(root/'agent.patch'),'initial_failures_preserved':True,'final_source_id':metadata['source_id']}
        if complex_proof is not None:
            real[name].update(passed=row.get('passed') is True,
                              runtime_mechanisms_passed=True,
                              application_task_outcome=complex_proof['application_task_outcome'],
                              runtime_mechanisms=complex_proof['runtime_mechanisms'])
    observed=load(args.rpc_observations)
    from verify_rpc_observations import NON_RPC_PRODUCERS
    require(observed.get('non_rpc_producers')==NON_RPC_PRODUCERS,'RPC producer classification differs')
    same_install(observed,'positive RPC observations')
    require(observed['passed'] and observed['source_id']==metadata['source_id']
            and observed.get('target')==scope['target'] and observed.get('baseline_matrix_sha256')==BASELINE_MATRIX_SHA256
            and not observed['missing'] and not observed['trace_errors'] and set(observed['methods'])==set(scope['local_methods'])-set(platform_boundaries)
            and observed.get('configuration_boundaries',{})==platform_boundaries,
            'Positive RPC observations do not cover the exact target-local method inventory')
    rpc_roots={args.regression:(str(entry),metadata['console_script_sha256']),args.methods:(str(entry),metadata['console_script_sha256']),
               args.local_account:(str(entry),metadata['console_script_sha256'])}
    excluded_rpc_roots=superseded_terminal_roots(args.regression) if args.terminal_regression else ()
    if args.terminal_regression:rpc_roots[args.terminal_regression]=(str(entry),metadata['console_script_sha256'])
    require(observed.get('terminal_revalidation')==terminal_lineage,'RPC terminal replacement lineage differs')
    if args.windows_sandbox:
        identity=load(args.windows_sandbox/'artifact.json');rpc_roots[args.windows_sandbox]=(identity['entry'],identity['entry_sha256'])
    rpc_reconstruction=validate_rpc_witnesses(observed,rpc_roots,scope['target'],sorted(set(scope['local_methods'])-set(platform_boundaries)),excluded_roots=excluded_rpc_roots)
    record('current_local_rpc_observations',args.rpc_observations)
    matrix=scope['matrix']
    for row in matrix:
        method=row['method'];row['target']=scope['target']
        if method in methods['methods']:
            result=methods['methods'][method]
            row.update(observed_in_passing_acceptance=result['status']=='passed',execution_status=result['status'],execution_evidence='Current installed-artifact per-method postconditions; see remaining-local-methods.json')
            if method in REMOTE:row['upstream_source_note']=result['reason']
            if method in platform_boundaries:
                row.update(configuration_boundary_verified=True,upstream_source_note=platform_boundaries[method]['reason'])
        elif method in LOCAL_ACCOUNT:
            row.update(observed_in_passing_acceptance=True,execution_status='local_variant_passed',execution_evidence='Current native local account lifecycle with nonfunctional fixture credentials; see local_account evidence receipts.')
        elif method in WINDOWS_SANDBOX and scope['target']=='x86_64-pc-windows-msvc':
            row.update(observed_in_passing_acceptance=True,execution_status='passed',execution_evidence='Current isolated native Windows sandbox setup and readiness lifecycle.')
        elif method.startswith('userVerification/'):
            row.update(execution_status='account_unavailable_boundary_verified',execution_evidence='Same-version official/native account gate compared; no real OS credential enrollment or deletion.',upstream_source_note='Native identity() requires a ChatGPT account user ID before creating its platform credential provider.')
        elif row['scope']=='local_runtime_surface':
            row.update(observed_in_passing_acceptance=bool(observed['methods'].get(method)),execution_status='observed_in_native_scenarios',execution_evidence='Current matched native RPC response plus the complete frozen scenario gate set.')
        else:row['execution_status']='not_applicable_to_frozen_local_scope'
    local=[r for r in matrix if r['scope']=='local_runtime_surface']
    require({r['method'] for r in local}==set(scope['local_methods']) and all(r['observed_in_passing_acceptance'] or r['method'] in platform_boundaries for r in local),'Local method matrix still has an unverified entry')
    original_offline=validate_offline_run(args.regression,metadata,native,scope,terminal_revalidated=args.terminal_regression is not None)
    selected_offline=validate_offline_run(args.offline_regression,metadata,native,scope,current=True) if args.offline_regression else original_offline
    qa_lineage['original_offline']=original_offline;qa_lineage['current_offline']=selected_offline
    if original_offline.get('external_execution'):
        external=original_offline['external_execution']
        for name,expected in external['files_sha256'].items():
            record('external_offline_'+name,Path(external['root'])/name)
            require(evidence['external_offline_'+name]==expected,'External offline file changed during assembly: '+name)
    for label,directory in [('original_offline',args.regression),('current_offline',args.offline_regression or args.regression)]:
        for name in ['artifact.json','verdict.json','offline-collection.json','offline.xml']:record(label+'_'+name,directory/name)
    offline_count=selected_offline['tests_passed'];skips=selected_offline['platform_inapplicable']
    summary={'status':'accepted_for_frozen_target_local_runtime_scope','version':version,'source_id':metadata['source_id'],
             'target':scope['target'],'platform':scope['platform'],'upstream_version':native['upstream_version'],
             'upstream_commit':native['upstream_commit'],'baseline_matrix_sha256':BASELINE_MATRIX_SHA256,'model':metadata['model'],
             'native_archive_sha256':metadata['native_archive_sha256'],'adapter_source_sha256':metadata['adapter_source_sha256'],
             'native_schemas_compared':scope['native_schema_count'],'native_schema_differences':[],'client_methods':len(matrix),
             'native_source_audit':native_source,'browser_sandbox_mode':browser_artifact['sandbox_mode'],
             'linux_bundled_sandbox_resources':linux_resources,
             'native_fault_boundaries':native_faults,
             'native_terminal_lifecycles':tui_proofs,'native_hook_trust_lifecycle':hook_trust,
             'local_methods_positive_evidence':len(local)-len(platform_boundaries),'local_configuration_boundaries':platform_boundaries,'new_local_methods_individually_passed':methods['positive_local_methods'],
             'rpc_raw_witness_reconstruction':rpc_reconstruction,
             'rpc_non_rpc_producers':observed['non_rpc_producers'],
             'local_account_variants_verified':sorted(LOCAL_ACCOUNT),'windows_sandbox_lifecycle_verified':scope['target']=='x86_64-pc-windows-msvc',
             'supplemental_lifecycle_lineage':lifecycle_lineage,'native_probes':probes,
             'corrected_backend_entries':len(REMOTE),'user_verification_account_boundaries':5,'all_174_positive_lifecycles_claimed':False,
             'offline_tests_passed':offline_count,'offline_platform_inapplicable':skips,
             'qa_producer_consumer_lineage':qa_lineage,
             'acceptance_gates':[{'name':r['gate'],'exit':r['exit'],'seconds':r['seconds'],
                                  'producer':'current_terminal_revalidation' if args.terminal_regression and r['gate'] in TERMINAL_GATES else 'original_frozen_run'} for r in effective_gates],
             'additional_acceptance':{'method_gap_list':True,'local_account_lifecycle':True,'native_browser':True,'browser_mcp_cancellation':True,
                                     'native_mcp_lifetime_comparison':True,'native_hook_trust_lifecycle':True,'protocol_fault_recovery':True,'os_user_verification_account_boundary':True,'complex_real_tasks':True},
             'real_work':real,
             'limits':['Qualification applies to the frozen '+scope['platform']+' target and its current artifact identities.',
                       'Model judgment and cloud/account entitlements are outside runtime equivalence.',
                       'Local account RPC evidence covers API-key storage/read/logout and no-active-login cancellation, not hosted OAuth.',
                       'Cached/indexed OpenAI search, location bias and image-search backend semantics remain explicit compatibility errors on Exa.',
                       'Document browsing does not execute JavaScript or use authenticated website sessions.',
                       'Native MCP operations can outlive turn interruption; explicit MCP cancellation and owner shutdown have separate passing evidence.',
                       'Local methods have positive evidence for declared scenarios or an explicitly verified fixed-platform configuration boundary; neither is exhaustive fault-space coverage.',
                       'The prediction endpoint is declared but unimplemented in frozen upstream.'],
             'evidence_sha256':evidence,'qualification_protocol':'One frozen installed target; exact standard gates plus per-method postconditions, local account and applicable sandbox lifecycles, complex saved tasks, actual browsing and protocol fault recovery. Initial failures remain preserved.',
             'stable_solution':'docs/STABLE_PLAN.zh-CN.md','historical_release_reports':'reports/history/v0.3.0/'}
    if args.complex_runtime:
        summary['qualification_protocol']+=' Third-party application outcomes remain separate from independently reconstructed runtime mechanism proofs.'
        summary['limits'].append('Runtime acceptance does not assert that every model-generated third-party code change passed its business tests; each original application outcome is retained.')
    output=(args.output or SOURCE/'reports/platforms'/scope['target']).resolve();output.mkdir(parents=True,exist_ok=True)
    previous=load(output/'acceptance.json').get('version') if (output/'acceptance.json').exists() else None
    history=output/'history'/('v'+(previous or '0.3.0'));history.mkdir(parents=True,exist_ok=True)
    for path in list(output.glob('*.json')):
        dest=history/path.name
        if not dest.exists():shutil.copy2(path,dest)
    write=lambda name,value:(output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    write('runtime-method-matrix.json',matrix);write('remaining-local-methods.json',{'status':'passed','baseline_gap_count':64,'positive_local_methods':methods['positive_local_methods'],'platform_boundary_count':len(platform_boundaries),'backend_boundary_count':4,'methods':{name:{'status':row['status'],'scope':'backend' if name in REMOTE else 'platform_configuration_boundary' if name in platform_boundaries else 'local','reason':row.get('reason'),'evidence_sha256':sha(args.methods/row['evidence'])} for name,row in methods['methods'].items()}})
    write('native-schema-comparison.json',schema);write('runtime-qualification.json',summary);write('acceptance.json',summary)
    write('qualification-lineage.json',{'version':version,'source_id':metadata['source_id'],'runtime_sources_immutable':True,'evidence_sha256':evidence,'initial_failures_preserved':True,'no_automatic_adapter_tool_replay':True,'note':'Native retry and explicit saved-task continuations are separately recorded. Real-task source fixes belong to test workspaces, not the runtime implementation.'})
    print(json.dumps({'passed':True,'source_id':metadata['source_id'],'target':scope['target'],'positive_local_methods':len(local),'standard_gates':len(GATES),'output':str(output)}),flush=True)


if __name__=='__main__':main()
