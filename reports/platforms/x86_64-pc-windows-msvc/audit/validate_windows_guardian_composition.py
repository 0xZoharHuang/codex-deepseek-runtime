"""Read-only fixed Windows full-run + Guardian supplement composition.

Run as the existing standard QA user to read its private request/receipt evidence.
This never changes a historical verdict or accepts mixed installations.
"""
import os
from pathlib import Path
import argparse,ast,hashlib,importlib.util,json,sqlite3,sys,time
sys.dont_write_bytecode=True
SOURCE_ID='3a59a782f017693be2b0'
OLD_QA='fd3217ee050e32c7a3b7f71b28b261e2d16b0abbae0e7af229f5814b94930eb8'
NEW_QA='31be6cbec0be0b6ca34969a330264d402c2f222edc95542e4f3a246c4eff591b'
ROOT=Path('D:/CodexDeepSeekQA/20261021')
FULL=Path('D:/CDSQA069/f4/r/46fe03c7b45c')
SUPP=Path('D:/CDSQA069/g3/r/5295d601b398')
OFFLINE=ROOT/'offline-3a59a782f017693be2b0-31/r/3a720c0be6a4'
FD=ROOT/'final-qa-fd3217ee050e32c7'
CURRENT=ROOT/'final-qa-31be6cbec0be0b6c'
STD_SID=os.environ['CODEX_DEEPSEEK_QA_STANDARD_SID']
ADMIN_SID=os.environ['CODEX_DEEPSEEK_QA_ADMIN_SID']
RAW={}
def require(value,message):
    if not value:raise RuntimeError(message)
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def bind(path):
    path=Path(path);digest=sha(path)
    require(str(path) not in RAW or RAW[str(path)]==digest,'Raw evidence changed during validation')
    RAW[str(path)]=digest;return path
def read(path):return json.loads(bind(path).read_text(encoding='utf-8'))
def lines(path):return [json.loads(x) for x in bind(path).read_text(encoding='utf-8').splitlines() if x.strip()]
def module(root,filename,name):
    spec=importlib.util.spec_from_file_location(name,root/filename);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
def function_ast(path,name):
    nodes=[n for n in ast.parse(Path(path).read_text()).body if isinstance(n,ast.FunctionDef) and n.name==name]
    require(len(nodes)==1,'Missing/ambiguous assertion function');return ast.dump(nodes[0],include_attributes=False)
def frozen(root,manifest_path,digest):
    m=read(manifest_path);require(m['qa_sha256']==digest and m['source_id']==SOURCE_ID,'Wrong frozen manifest')
    require(hashlib.sha256(json.dumps(m['files'],sort_keys=True).encode()).hexdigest()==digest,'Frozen QA manifest digest mismatch')
    actual={p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file() and not any(x in {'__pycache__','.pytest_cache'} for x in p.relative_to(root).parts)}
    require(actual==m['files'],'Full frozen QA inventory differs')
    archive=ROOT/(root.name+'.tar.gz');require(sha(archive)==m['archive_sha256'],'Frozen QA archive changed')
    return m

def gate_root(root):
    paths=[Path(line[9:]) for line in bind(root/'guardian.log').read_text(encoding='utf-8').splitlines() if line.startswith('EVIDENCE ')]
    require(len(paths)==1 and paths[0].resolve().is_relative_to((root/'guardian').resolve()),'Guardian evidence root escaped its original run')
    return paths[0]
def guardian_raw(root,entry,entry_sha):
    requests=lines(root/'client-requests.jsonl');event_files=list(root.glob('events-*.json'));launch_files=list(root.glob('launch-*.json'))
    require(len(event_files)==len(launch_files)==1,'Guardian RPC lifecycle is ambiguous')
    events=read(event_files[0]);launch=read(launch_files[0]);policies=lines(root/'qa-thread-sandbox.jsonl');results=read(root/'results.json')
    require(Path(launch['resolved_entry'])==entry and launch['entry_sha256']==entry_sha and Path(launch['runtime_home'])==root/'home' and launch['server_args']==['app-server','--strict-config'],'Guardian native launch identity differs')
    require(launch['fixture_sandbox_mode']=='danger-full-access','Original suite fixture mode changed')
    replies={}
    for event in events:
        if 'id' in event and 'method' not in event:
            require(event['id'] not in replies,'Duplicate native RPC reply');replies[event['id']]=event
    for request in requests:
        if 'id' not in request:continue
        reply=replies.get(request['id'],{})
        require('result' in reply and not reply.get('error'),'A native Guardian RPC failed or has no reply')
    starts=[r for r in requests if r.get('method')=='thread/start'];turns=[r for r in requests if r.get('method')=='turn/start']
    for request,policy in zip(starts,policies):
        result=replies[request['id']]['result']
        require(policy['thread']==result['thread']['id'] and policy['requested_sandbox_mode']==request['params']['sandbox'] and policy['native_response_sandbox']==result['sandbox'],'Thread policy evidence differs from actual native RPC reply')
    require(len(starts)==len(policies),'Missing native thread policy evidence')
    return {'requests':requests,'events':events,'replies':replies,'policies':policies,'results':results,'starts':starts,'turns':turns}

def main():
    p=argparse.ArgumentParser();p.add_argument('--fd-manifest',type=Path,required=True);p.add_argument('--current-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    require(not args.output.exists(),'Preserve existing composition output')
    old_m=frozen(FD,args.fd_manifest,OLD_QA);new_m=frozen(CURRENT,args.current_manifest,NEW_QA)
    changed={k for k in set(old_m['files'])|set(new_m['files']) if old_m['files'].get(k)!=new_m['files'].get(k)}
    require(changed=={'verify_safety.py','tests/test_qa_safety.py'},'Unreviewed QA changes invalidate this fixed supplement')
    require(old_m['product_files']==new_m['product_files'],'Guardian repair changed product source')
    require(function_ast(FD/'verify_safety.py','approval_execution_evidence')==function_ast(CURRENT/'verify_safety.py','approval_execution_evidence'),'Approval/execution binding was weakened or changed')
    sys.path[:0]=[str(CURRENT),str(CURRENT/'src')]
    import assemble_release_acceptance as current
    import verify_safety as safety
    import run_acceptance as runner
    import install
    import psutil
    old=module(FD,'assemble_release_acceptance.py','original_fd_assembler')
    require(current.SOURCE==CURRENT and old.SOURCE==FD and current.GATES==old.GATES and len(current.GATES)==30,'Wrong source consumer or gate scope')
    reader=runner.windows_token_identity();require(reader=={'sid':STD_SID,'elevated':False},'Private Guardian evidence must be read as its original standard QA user')
    a=read(FULL/'artifact.json');v=read(FULL/'verdict.json');b=read(SUPP/'artifact.json');w=read(SUPP/'verdict.json');c=read(OFFLINE/'artifact.json');z=read(OFFLINE/'verdict.json')
    entry=Path(a['entry']);release=entry.parent.parent.parent;installation=read(release/'installation.json');native=read(release/'native-runtime.json')
    require(installation['source_id']==SOURCE_ID and installation['source_sha256']==new_m['product_files'],'Current installed product identity differs')
    require(install.source_identity()==(SOURCE_ID,new_m['product_files']),'Current QA product identity differs')
    require(install.valid_release(release,installation['adapter_source_sha256'],installation['model_catalog_sha256'],native),'Current installed/native immutable integrity failed')
    require(native['target']=='x86_64-pc-windows-msvc','Wrong target')
    scope=current.qualification_scope(native)
    for artifact,verdict,path,source in [(a,v,FULL,FD),(b,w,SUPP,CURRENT),(c,z,OFFLINE,CURRENT)]:
        require(artifact['installation']==installation and artifact['native_manifest']==native and Path(artifact['entry'])==entry and artifact['entry_sha256']==installation['console_script_sha256']==sha(entry),'Mixed installation/native/entry is forbidden')
        require(artifact['controller_environment']['optimize']==0 and artifact['target']==native['target'] and artifact['baseline_matrix_sha256']==current.BASELINE_MATRIX_SHA256,'Controller assertions or applicability identity differs')
        require(all(verdict[key] is True for key in ['entry_unchanged','verifier_source_unchanged','native_probes_unchanged']),'Original execution integrity failed')
        require(read(path/'results.json')==verdict['gates'],'Raw gate rows differ from final verdict')
        require(current.normalized_hashes(artifact['verifier_source_sha256'])==current.qa_python_hashes(source),'Wrong original/current QA execution source')
        require(all(sha(row['path'])==row['sha256'] for row in artifact['native_probes'].values()),'Native probe identity changed')
        for row in verdict['gates']:
            require(type(row['exit']) is int and row['log']==row['gate']+'.log','Unexpected gate exit/log identity');bind(path/row['log'])
    for artifact in (a,b):
        current.validate_windows_controller(artifact['controller_environment'],elevated=False)
        require(artifact['controller_environment']['windows_token']['sid']==STD_SID and artifact['qa_default_sandbox_mode']=='danger-full-access','Runtime gates did not execute as the fixed standard user/default mode')
    current.validate_windows_controller(c['controller_environment'],elevated=True)
    require(c['controller_environment']['windows_token']['sid']==ADMIN_SID,'Offline symlink-capable controller identity differs')
    require(v['passed'] is False and v['all_gates_selected'] is True and len(v['gates'])==len(a['selected_gates'])==30 and set(a['selected_gates'])=={r['gate'] for r in v['gates']}==current.GATES,'Original full run is not the exact complete scope')
    require({r['gate'] for r in v['gates'] if r['exit']!=0}=={'guardian'},'An additional failed standard gate cannot be replaced')
    require(w['passed'] is True and b['selected_gates']==['guardian'] and len(w['gates'])==1 and w['gates'][0]['gate']=='guardian' and w['gates'][0]['exit']==0,'Supplement must contain exactly one passing Guardian gate')
    external=a['external_offline'];old_offline=old.external_offline_receipt(external['root'],installation,native,scope)
    require(external==old_offline and v['external_offline_unchanged'] is True and v['external_offline_error'] is None,'Original separate offline provenance differs')
    require(v['offline_files_sha256']==old.offline_file_hashes(FULL),'Original imported offline files changed')
    require(all(sha(FULL/n)==external['files_sha256'][n] for n in old.OFFLINE_FILES),'Imported offline differs from original complete admin run')
    old_offline_row=next(r for r in v['gates'] if r['gate']=='offline')
    require(old_offline_row=={**external['gate'],'execution':'separate_complete_offline_run','external_root':external['root']},'Original offline row hides its separate execution')
    fresh_offline=current.external_offline_receipt(OFFLINE,installation,native,scope)
    require(fresh_offline['validation']['tests_passed']==665 and len(fresh_offline['validation']['platform_inapplicable'])==1,'Unexpected complete current offline scope')
    for directory in [FULL,Path(external['root']),OFFLINE]:
        for name in ['artifact.json','verdict.json','results.json',*current.OFFLINE_FILES]:bind(directory/name)
    original_root=gate_root(FULL);new_root=gate_root(SUPP)
    original=guardian_raw(original_root,entry,installation['console_script_sha256']);fresh=guardian_raw(new_root,entry,installation['console_script_sha256'])
    require(len(original['starts'])==len(original['turns'])==len(original['results'])==1,'Original failed Guardian scope differs')
    require(original['results'][0]['name']=='native_automatic_approval' and original['results'][0]['passed'] is False,'Original failure was not the reviewed missing-approval condition')
    original_policy=original['policies'][0]
    require(original_policy['requested_sandbox_mode']=='danger-full-access' and original_policy['native_response_sandbox']=={'type':'dangerFullAccess'},'Original native permission context differs')
    original_detail=original['results'][0]['detail'];original_check=safety.approval_execution_evidence(original['events'],original_detail['thread'],original_detail['turn'],'APPROVAL_NATIVE_OK')
    require(original_check['passed'] is False and original_check['approved_executions']==[],'Historical failed approval must remain failed')
    original_items=[e['params']['item'] for e in original['events'] if e.get('method')=='item/completed' and e.get('params',{}).get('threadId')==original_detail['thread'] and e['params'].get('turnId')==original_detail['turn']]
    require(any(i.get('type')=='commandExecution' and i.get('status')=='completed' and i.get('exitCode')==0 and i.get('aggregatedOutput','').strip()=='APPROVAL_NATIVE_OK' for i in original_items),'Original successful command without approval was not preserved')
    require(not any(e.get('method')=='item/autoApprovalReview/completed' for e in original['events']),'Original missing-approval diagnosis differs')
    names=['native_automatic_approval','native_guardian_v2_classifier_uses_selected_deepseek','native_guardian_v2_completed_classification_and_applied_score','native_turn_interrupt_status','native_owner_shutdown_cleans_process_tree']
    rows=fresh['results'];require([r['name'] for r in rows]==names and all(r['passed'] is True for r in rows),'Guardian five-stage lifecycle did not pass')
    require([r['sandbox_mode'] for r in rows]==['read-only']*3+['danger-full-access']*2 and all(r['fixture_sandbox_mode']=='danger-full-access' for r in rows),'Guardian report conflates approval and cancellation permissions')
    require(len(fresh['starts'])==len(fresh['turns'])==len(fresh['policies'])==2,'Supplement thread/turn scope differs')
    policies=fresh['policies'];require(policies[0]['requested_sandbox_mode']=='read-only' and policies[0]['native_response_sandbox']=={'type':'readOnly','networkAccess':False} and policies[1]['requested_sandbox_mode']=='danger-full-access' and policies[1]['native_response_sandbox']=={'type':'dangerFullAccess'},'Actual native thread permissions differ')
    require(fresh['starts'][0]['params']['approvalsReviewer']=='auto_review' and all(r['params']['approvalPolicy']=='on-request' for r in fresh['starts']),'Native approval reviewer/policy differs')
    detail=rows[0]['detail'];approval=safety.approval_execution_evidence(fresh['events'],detail['thread'],detail['turn'],'APPROVAL_NATIVE_OK')
    require(approval['passed'] is True and all(detail[k]==v for k,v in approval.items()),'Real approved execution differs from recorded binding')
    require(detail['thread']==policies[0]['thread'],'Approval belongs to another thread')
    for request,policy in zip(fresh['turns'],policies):
        require(request['params']['threadId']==policy['thread'],'Turn belongs to another native thread')
    samples=[]
    for path in (new_root/'adapter-traces').glob('*-request.json'):
        payload=json.loads(path.read_text(encoding='utf-8'))
        if str(payload.get('prompt_cache_key','')).startswith('guardian-v2:'):
            bind(path);samples.append({'receipt':path.name.removesuffix('-request.json'),'model':payload.get('model'),'provider':payload.get('provider')})
    require(samples and sorted(samples,key=lambda x:x['receipt'])==sorted(rows[1]['detail'],key=lambda x:x['receipt']),'Actual classifier requests differ from recorded DeepSeek evidence')
    require(all(x['model']=='deepseek/deepseek-v4.1-flash-1017' and x['provider']=={'only':['deepseek'],'allow_fallbacks':False,'require_parameters':True} for x in samples),'Classifier used another model or route')
    database=new_root/'home/provider-state/adapter.sqlite';bind(database)
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        receipt_rows=[db.execute('SELECT status,upstream_status,provider FROM receipts WHERE id=?',(s['receipt'],)).fetchone() for s in samples]
    require(all(r==('completed',200,'deepseek') for r in receipt_rows),'Actual classifier provider receipts did not complete')
    require([list(r[:2]) for r in receipt_rows]==rows[2]['detail']['receipts'],'Recorded classifier receipts differ')
    scores=[]
    for path in (new_root/'home/sessions').rglob('*.jsonl'):
        records=lines(path);scores.extend(r for r in records if r.get('type')=='security_risk_score')
    require(scores==rows[2]['detail']['scores'] and scores,'Actual native applied scores differ')
    for accepted in approval['approved_executions']:
        command=accepted['command'];actions={x.get('command') for x in command.get('commandActions',[])}
        require(any(s['payload'].get('call_id')==command['id'] and s['payload'].get('action',{}).get('tool')=='exec_command' and s['payload']['action'].get('cmd') in actions and s['payload']['action'].get('sandbox_permissions')=='require_escalated' and s['payload'].get('scores') for s in scores),'Applied native score is not bound to the actually approved command')
    cancel_thread=policies[1]['thread'];cancel_turn=fresh['replies'][fresh['turns'][1]['id']]['result']['turn']['id']
    interrupts=[r for r in fresh['requests'] if r.get('method')=='turn/interrupt']
    require(len(interrupts)==1 and interrupts[0]['params']=={'threadId':cancel_thread,'turnId':cancel_turn},'Cancellation targeted another native turn')
    terminals=[e['params']['turn'] for e in fresh['events'] if e.get('method')=='turn/completed' and e['params'].get('threadId')==cancel_thread and e['params']['turn'].get('id')==cancel_turn]
    require(len(terminals)==1 and terminals[0]['status']=='interrupted' and terminals[0].get('error') is None and rows[3]['detail']=='interrupted','Actual cancellation terminal differs')
    fixture=new_root/'work/cancellable.py';bind(fixture);markers=read(new_root/'work/processes.json');pids=[r['pid'] for r in markers]
    require(len(pids)==len(set(pids))==2 and all(type(pid) is int and pid>0 for pid in pids),'Process-tree fixture PID receipt differs')
    require(rows[4]['detail']=={'fixture_pids':pids,'still_alive':[]},'Original owner shutdown postcondition did not pass')
    started=[e for e in fresh['events'] if e.get('method')=='item/started' and e.get('params',{}).get('threadId')==cancel_thread and e['params'].get('turnId')==cancel_turn and e['params']['item'].get('type')=='commandExecution']
    require(started and any(str(fixture).replace('\\','/').lower() in json.dumps(e['params']['item']).replace('\\\\','/').lower() for e in started),'Disposable process tree was not started in the cancelled native turn')
    require(w['gates'][0]['seconds']<120,'Process cleanup could be confused with fixture natural timeout')
    live_fixture=[]
    for pid in pids:
        try:command_line=psutil.Process(pid).cmdline()
        except psutil.NoSuchProcess:continue
        if str(fixture) in command_line:live_fixture.append(pid)
    require(not live_fixture,'Disposable Guardian fixture remains alive')
    # Historical birth identities were used by the frozen verifier before it
    # wrote still_alive=[]; they were not serialized. Do not invent them now.
    gate_origins=[]
    for gate in sorted(current.GATES):
        origin=SUPP if gate=='guardian' else OFFLINE if gate=='offline' else FULL
        original_row=(w['gates'][0] if gate=='guardian' else z['gates'][0] if gate=='offline' else next(r for r in v['gates'] if r['gate']==gate))
        gate_origins.append({'gate':gate,'original_run':str(origin),'original_gate_row':original_row,'original_artifact_sha256':sha(origin/'artifact.json'),'original_verdict_sha256':sha(origin/'verdict.json'),'producer_qa_sha256':NEW_QA if gate in {'guardian','offline'} else OLD_QA,'source_id':SOURCE_ID})
    rpc_sources={'roots':[{'root':str(FULL),'entry':str(entry),'entry_sha256':sha(entry),'passing_gates':[r['gate'] for r in v['gates'] if r['gate']!='guardian']},{'root':str(SUPP),'entry':str(entry),'entry_sha256':sha(entry),'passing_gates':['guardian']}],'excluded_roots':[str(FULL/'guardian')],'scope':'Only validated original passing gates and the successful Guardian supplement; every paired RPC trace must still be reconstructed by trace_observations.'}
    for path,digest in RAW.items():require(sha(path)==digest,'Raw evidence mutated while composing qualification')
    result={'standard_mechanisms_composition_qualified':True,'release_acceptance_claimed':False,'single_run_all_passed':False,'source_id':SOURCE_ID,'target':native['target'],'current_qa_sha256':NEW_QA,'original_qa_sha256':OLD_QA,'original_full_run':str(FULL),'original_verdict_passed':False,'original_passing_gates':29,'original_failed_gates':['guardian'],'supplement_run':str(SUPP),'supplement_selected_gates':['guardian'],'supplement_guardian_stages':names,'same_installation_native_entry':True,'installation':installation,'native_manifest':native,'entry':str(entry),'entry_sha256':sha(entry),'baseline_matrix_sha256':scope['baseline_matrix_sha256'],'required_local_methods':scope['local_methods'],'effective_standard_gate_sources':gate_origins,'rpc_evidence_sources':rpc_sources,'exact_qa_changed_files':sorted(changed),'approval_assertion_ast_unchanged':True,'reader_windows_token':reader,'runtime_controller':b['controller_environment']['windows_token'],'offline_controller':c['controller_environment']['windows_token'],'original_external_offline_validation':old_offline['validation'],'current_complete_offline_validation':fresh_offline['validation'],'guardian_raw':{'original':str(original_root),'supplement':str(new_root),'native_thread_policies':policies,'approved_execution_count':len(approval['approved_executions']),'actual_deepseek_classifier_requests':len(samples),'actual_applied_native_score_count':len(scores),'shutdown':{'historical_verifier_postcondition_preserved':True,'original_kernel_birth_ids_serialized':False,'current_live_fixture_processes':live_fixture}},'remaining_separate_qualification':['remaining64 and final131 RPC witnesses','native schema and source audit','browser/lifetime/protocol recovery','guest new-native sandbox'],'raw_sha256':RAW,'validator_sha256':sha(__file__),'validated_at':time.time()}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps({'standard_mechanisms_composition_qualified':True,'release_acceptance_claimed':False,'single_run_all_passed':False,'offline_passed':fresh_offline['validation']['tests_passed'],'raw_files_bound':len(RAW)}),flush=True)
if __name__=='__main__':main()
