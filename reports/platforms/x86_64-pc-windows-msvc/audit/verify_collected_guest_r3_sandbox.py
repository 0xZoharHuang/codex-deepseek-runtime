from pathlib import Path,PureWindowsPath
import hashlib,json,runpy,sys,tomllib
sys.dont_write_bytecode=True
BASE=Path(__file__).resolve().parent;DEST=BASE/'windows-r3-sandbox-final-evidence';RAW=next(p for p in DEST.iterdir() if p.is_dir());QA=BASE/'final-qa-fd3217ee050e32c7'
def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def require(value,message):
 if not value:raise AssertionError(message)
transfer=load(DEST/'transfer-and-current-readback.json');a=load(RAW/'artifact.json');v=load(RAW/'verdict.json');results=load(RAW/'results.json');manifest=load(BASE/'final-qa-windows-oauth-candidate.json')
for name,receipt in transfer['files'].items():require(sha(DEST/receipt['local_relative_path'])==receipt['local_sha256']==receipt['original_sha256'],'Copied source bytes differ: '+name)
checks=transfer['checks'];require(transfer['remote_exit_code']==0 and checks['original_files_unchanged_during_read'],'Readback was incomplete or changed mid-transfer')
require(a['installation']==v['installation'] and a['installation']['source_id']=='3a59a782f017693be2b0' and a['target']==v['target']=='x86_64-pc-windows-msvc','Wrong installed target or source')
source_id,source_files=runpy.run_path(str(QA/'install.py'))['source_identity']()
require(source_id==manifest['source_id']==a['installation']['source_id'] and source_files==a['installation']['source_sha256'],'Guest full product source map differs from frozen QA')
require(a['native_manifest']==load(QA/'native/runtime-x86_64-pc-windows-msvc.json'),'Guest native manifest differs from the frozen source')
require(checks['installed_metadata_equals_artifact'] and checks['installed_native_manifest_equals_artifact'],'Installed metadata changed after the gate')
require(checks['entry_sha256']==a['entry_sha256']==a['installation']['console_script_sha256'],'Actual guest launcher changed')
expected_verifiers={name:sha(QA/name) for name in ['verify_windows_sandbox.py','verify_live.py','install.py']}
require(a['verifier_sha256']==checks['verifier_sha256']==expected_verifiers,'Guest verifier source differs from its fixed producer')
require(v['passed'] is True and v['positive_lifecycle_exercised'] is True and v['verifier_and_artifact_unchanged'] is True and v['model_requests']==0,'Original gate is incomplete')
require(results==v['methods'] and len(results)==2 and {r['method'] for r in results}=={'windowsSandbox/setupStart','windowsSandbox/readiness'} and all(r['status']=='passed' for r in results),'Raw results differ from method verdict')
require(checks['model_receipt_counts']=={'unelevated':0,'elevated':0} and not checks['failure_file_exists'],'Unexpected inference or failure marker')
require(checks['guest_hostname']=='CDS-QA' and a['guest']=='CDS-QA','Wrong guest')
job=load(RAW/'job/job-result.json');start=load(RAW/'job/job-start.json')
require(job['exit']==0 and job['session_id']==1 and job['user'].casefold()=='administrator' and job['install_exit']==0 and job['activated'] is False and job['inference_key_supplied'] is False,'Console job did not complete successfully')
require(job['pid']==start['pid'] and job['entry']==a['entry'],'Job/source identity mismatch')
modes=next(r for r in results if r['method']=='windowsSandbox/setupStart')['detail']['modes'];require([r['mode'] for r in modes]==['unelevated','elevated'],'Both sandbox modes are required')
witnesses=[];origin=PureWindowsPath(transfer['original_guest_root'])
for mode,row in zip(['unelevated','elevated'],modes):
 case=RAW/mode;work=origin/mode/'work';home=origin/mode/'home';outside=origin/mode/'outside.txt';allowed=work/'allowed.txt'
 requests=[json.loads(line) for line in (case/'client-requests.jsonl').read_text().splitlines()];sessions=[]
 for request in requests:
  if request.get('method')=='initialize':sessions.append([])
  require(bool(sessions),'Request before initialization');sessions[-1].append(request)
 events_paths=sorted(case.glob('events-*.json'),key=lambda p:int(p.stem.split('-')[1]));launch_paths=sorted(case.glob('launch-*.json'),key=lambda p:int(p.stem.split('-')[1]))
 require(len(sessions)==len(events_paths)==len(launch_paths)==2,'Expected setup plus separate cold-start native session')
 paired=[]
 for index,(session,event_path,launch_path) in enumerate(zip(sessions,events_paths,launch_paths)):
  events=load(event_path);launch=load(launch_path)
  require(int(launch_path.stem.split('-')[1])<int(event_path.stem.split('-')[1]),'Session closed before launch')
  if index:require(int(events_paths[index-1].stem.split('-')[1])<int(launch_path.stem.split('-')[1]),'Cold session started before prior native peer closed')
  require(PureWindowsPath(launch['resolved_entry'])==PureWindowsPath(a['entry']) and launch['entry_sha256']==a['entry_sha256'] and PureWindowsPath(launch['runtime_home'])==home,'Native session belongs to another entry/home')
  require(launch['command']==[a['entry']] and launch['server_args']==['app-server','--strict-config'],'Unexpected native launch override')
  queries={r['id']:r for r in session if 'id' in r and 'method' in r};require(len(queries)==sum('id' in r and 'method' in r for r in session),'Duplicate request ID within native session')
  replies={e['id']:e for e in events if 'id' in e and ('result' in e or 'error' in e)}
  require(len(replies)==sum('id' in e and ('result' in e or 'error' in e) for e in events),'Duplicate response ID')
  require(set(queries)==set(replies) and all('error' not in r for r in replies.values()),'Unpaired or failed native response')
  ordered=[q['method'] for q in queries.values()]
  expected=['initialize','config/read','windowsSandbox/readiness','windowsSandbox/setupStart'] if index==0 else ['initialize','config/read','windowsSandbox/readiness','config/read','command/exec']
  require(ordered==expected,'Unexpected ordered setup/cold native method sequence')
  configs=[(q,replies[i]['result']) for i,q in queries.items() if q['method']=='config/read']
  require(all(q['params']=={'includeLayers':False} and result['config']['sandbox_mode']=='workspace-write' for q,result in configs),'Native fixture mode differs from workspace-write')
  if index:require(configs[0][1]==configs[1][1] and configs[0][1]['config']['windows']['sandbox']==mode,'Cold native config reads disagree')
  by_method={q['method']:(q,replies[i]['result']) for i,q in queries.items()}
  init=by_method['initialize'][1];require(PureWindowsPath(init['codexHome'])==home and init['platformFamily']=='windows','Native initialized a foreign platform/home')
  paired.append((by_method,events))
 before,setup_events=paired[0];after,_=paired[1]
 require(set(before)=={'initialize','config/read','windowsSandbox/readiness','windowsSandbox/setupStart'} and set(after)=={'initialize','windowsSandbox/readiness','config/read','command/exec'},'Unexpected setup or cold-phase method set')
 require(before['windowsSandbox/readiness'][1]==row['before']=={'status':'notConfigured'},'Initial readiness is not the saved unconfigured state')
 setup_request,setup_reply=before['windowsSandbox/setupStart'];require(setup_request['params']=={'mode':mode,'cwd':str(work)} and setup_reply=={'started':True},'Native setup request/acknowledgment differs')
 notifications=[e['params'] for e in setup_events if e.get('method')=='windowsSandbox/setupCompleted'];require(notifications==[{'mode':mode,'success':True,'error':None}]==[row['setup']],'Native setup completion missing or failed')
 require(after['windowsSandbox/readiness'][1]==row['cold_readiness']=={'status':'ready'},'Cold native readiness failed')
 require(after['config/read'][1]['config']['windows']['sandbox']==mode and tomllib.loads((case/'home/config.toml').read_text())['windows']['sandbox']==mode,'Selected sandbox mode was not persisted')
 request,response=after['command/exec'];params=request['params'];quote=lambda p:"'"+str(p).replace("'","''")+"'"
 expected_script='$ErrorActionPreference="Stop";[IO.File]::WriteAllText('+quote(allowed)+',"SANDBOX_ALLOWED");try{[IO.File]::WriteAllText('+quote(outside)+',"ESCAPED");exit 91}catch{if($_.Exception -is [UnauthorizedAccessException] -or $_.Exception.InnerException -is [UnauthorizedAccessException]){Write-Output "OUTSIDE_WRITE_DENIED"}else{throw}}'
 require(PureWindowsPath(params['command'][0])==PureWindowsPath('C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe') and params['command'][1:]==['-NoProfile','-NonInteractive','-Command',expected_script],'Enforcement command does not bind denial to UnauthorizedAccessException')
 require(params['cwd']==str(work) and params['sandboxPolicy']=={'type':'workspaceWrite','writableRoots':[str(work)],'networkAccess':False} and params['timeoutMs']==120000,'Enforcement policy changed')
 require(response==row['result'] and response['exitCode']==0 and response['stdout'].strip()=='OUTSIDE_WRITE_DENIED' and response['stderr']=='','Raw native enforcement result differs or failed')
 require((case/'work/allowed.txt').read_bytes()==b'SANDBOX_ALLOWED' and (case/'outside.txt').read_bytes()==b'PRESERVE','Real file postcondition failed')
 require(checks['inside_bytes'][mode] and checks['outside_bytes_preserved'][mode] and row['inside_write_verified'] and row['outside_write_denied'],'Guest current file readback differs')
 witnesses.append({'mode':mode,'native_sessions':2,'paired_native_methods':[sorted(x[0]) for x in paired],'setup_completed':True,'cold_readiness_and_config_persistence':True,'inside_write_actual_bytes':True,'outside_UnauthorizedAccess_verified_by_exact_command_and_result':True,'outside_original_bytes_preserved':True,'model_requests':0,'source_files':[str(p.relative_to(RAW)) for p in events_paths+launch_paths]+[mode+'/client-requests.jsonl',mode+'/home/config.toml',mode+'/outside.txt',mode+'/work/allowed.txt']})
proof={'passed':True,'source_id':source_id,'target':a['target'],'guest':checks['guest_hostname'],'guest_os_observed':checks['platform'],'original_guest_root':str(origin),'artifact_sha256':sha(RAW/'artifact.json'),'verdict_sha256':sha(RAW/'verdict.json'),'results_sha256':sha(RAW/'results.json'),'transfer_receipt_sha256':sha(DEST/'transfer-and-current-readback.json'),'verifier_source_sha256':expected_verifiers,'full_native_manifest_matches_frozen_r3':True,'actual_entry_and_installation_metadata_readback_matches':True,'native_binaries_rehashed_in_this_readback':False,'original_unchanged_gate_performed_full_native_integrity_before_and_after':True,'current_model_receipt_counts':checks['model_receipt_counts'],'modes':witnesses,'new_model_or_sandbox_execution':False,'read_only':True,'scope':'Native sandbox positive lifecycle on Windows Server 2025 guest; not a claim of all Windows runtime gates or every Windows edition.'}
(DEST/'independent-verdict.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'passed':True,'source_id':source_id,'guest_os':checks['platform'],'modes':[w['mode'] for w in witnesses],'raw_files':len(transfer['files']),'model_requests':0}))
