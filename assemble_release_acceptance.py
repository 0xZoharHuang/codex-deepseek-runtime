"""Publish aggregate facts only after checking every private acceptance receipt."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
from install import valid_release

SOURCE=Path(__file__).resolve().parent
GATES={'tui','native-revocation','offline','live','plugin','features','modes','agents-nested','agents-full','agents-none','guardian','runtime-contract','plan-review','daemon','profiles','transports','local-methods','auth-stores','oauth-file','oauth-keyring','oauth-auto','oauth-secrets','memory-v1','memory-v2','goal-sample','crash-recovery','search','routes'}
REMOTE={'mcpServer/event/stream/start','mcpServer/event/stream/stop','plugin/skill/read','plugin/reconcile'}


def load(path):return json.loads(Path(path).read_text())
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def require(value,message):
    if not value:raise SystemExit(message)


def main():
    p=argparse.ArgumentParser()
    for flag in ['entry','regression','methods','browser','browser-protocol','protocol-recovery','native-lifetime','user-verification','schema','fixtures']:
        p.add_argument('--'+flag,type=Path,required=True)
    p.add_argument('--output',type=Path,default=SOURCE/'reports');args=p.parse_args()
    entry=args.entry.resolve();release=entry.parent.parent.parent;metadata=load(release/'installation.json');native=load(release/'native-runtime.json')
    require(entry==release/'venv/bin/codex-deepseek','Expected immutable installed entry')
    require(valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native),'Installation integrity failed')
    for name,digest in metadata['source_sha256'].items():require(sha(SOURCE/name)==digest,'Installed runtime source differs: '+name)
    evidence={}
    def record(label,path):evidence[label]=sha(path)
    def same_install(a,label):require(a['installation']==metadata,'Mixed runtime installations in '+label)
    regression=load(args.regression/'verdict.json');artifact=load(args.regression/'artifact.json');same_install(artifact,'regression')
    require(regression['passed'] and regression['entry_unchanged'] and regression['verifier_source_unchanged'],'Standard regression did not fully pass frozen-artifact checks')
    require(set(artifact['selected_gates'])==GATES=={r['gate'] for r in regression['gates']} and all(r['exit']==0 for r in regression['gates']),'Standard gate set missing, duplicated or failed')
    require(len(regression['gates'])==len(GATES),'Duplicate standard gates')
    for name,digest in artifact['verifier_source_sha256'].items():require(sha(SOURCE/name)==digest,'Verifier changed after standard gates: '+name)
    record('regression_artifact',args.regression/'artifact.json');record('regression_verdict',args.regression/'verdict.json')
    methods=load(args.methods/'verdict.json');same_install(load(args.methods/'artifact.json'),'per-method acceptance')
    require(methods['passed'] and methods['positive_local_methods']==60 and methods['backend_boundaries_verified']==4 and not methods['missing'] and not methods['invalid'],'Per-method acceptance incomplete')
    require({n for n,r in methods['methods'].items() if r['status']=='backend_boundary_verified'}==REMOTE,'Incorrect backend scope classification')
    record('methods_artifact',args.methods/'artifact.json');record('methods_verdict',args.methods/'verdict.json')
    browser=load(args.browser/'results.json');same_install(load(args.browser/'artifact.json'),'native browser')
    required_browser={'native_direct_mcp_actual_open_find_click','cold_native_mcp_page_reference_recovery','model_native_browse_find_follow_and_file_effect','bundled_public_browser_actual_https_find_and_link','slow_native_browser_request_actually_started','native_interrupt_preserves_official_mcp_lifetime','browser_remains_usable_after_turn_interruption','native_owner_shutdown_closes_browser_http_request'}
    require(required_browser<={r['name'] for r in browser} and all(r['passed'] for r in browser),'Native browser acceptance incomplete')
    record('browser_artifact',args.browser/'artifact.json');record('browser_results',args.browser/'results.json')
    protocol=load(args.browser_protocol/'verdict.json');same_install(protocol,'browser protocol')
    require(protocol['passed'] and protocol['http_cancelled_by_mcp'] and protocol['anticipated_error_readable'],'Browser protocol did not pass')
    record('browser_protocol',args.browser_protocol/'verdict.json')
    recovery=[]
    for path in args.protocol_recovery.glob('*/verdict.json'):
        row=load(path);same_install(row,'protocol recovery');require(row['passed'],'Protocol recovery failed')
        require(row['requests']>=2 and row['continuation_context_observed'] and not row['client_tool_executions'] and row['native_retry_errors'],'Native retry recovery lacks effects evidence')
        recovery.append(row);record('protocol_recovery_'+row['kind'],path)
    require({r['kind'] for r in recovery}=={'undeclared-tool','schema'} and len(recovery)==2,'Both protocol fault cases are required exactly once')
    lifetime=load(args.native_lifetime/'verdict.json');uv=load(args.user_verification/'verdict.json');schema=load(args.schema)
    expected_native=sha(Path(metadata['native_package'])/'bin/codex')
    for row,label in [(lifetime,'native_lifetime'),(uv,'user_verification')]:
        require(row['passed'] and row['binary_sha256']['patched']==expected_native,'Native comparison mismatch: '+label)
    require(uv['positive_credential_lifecycle_exercised'] is False,'Do not conflate account guard with real biometric enrollment')
    require(schema['passed'] and schema['schemas_compared']==456 and not schema['differences'] and schema['patched_binary_sha256']==expected_native,'Native schema comparison incomplete')
    record('native_lifetime',args.native_lifetime/'verdict.json');record('user_verification_boundary',args.user_verification/'verdict.json');record('schema_comparison',args.schema)
    real={}
    for name in ['jinja','httpcore']:
        root=args.fixtures/name;row=load(root/'results.json');stage=root/row['followup']['evidence'];same_install(load(stage/'artifact.json'),name+' final real-task pass')
        require(row['passed'] and row['turn_status']=='completed' and row['fresh_review'] and row['oracle_guard']['source_unchanged'] and not row.get('error') and row['diff_check']==0,name+' real task incomplete')
        require(all(v['exit']==0 for v in row['validation']),name+' external regression failed')
        require({'open','find','click'}<=set(row['web_research']['native_completed_actions']) and row['web_research']['distinct_actual_urls']>=2,name+' actual multi-page browsing incomplete')
        require(row['followup']['source_id']==metadata['source_id'],name+' final real task used another source version')
        if name=='httpcore':require(row['restarted'] and {'real-tcp-0','real-tcp-1','real-tcp-2','tls-socket','independent-cancellation'}<={v['suite'] for v in row['validation']},'HTTPcore cold recovery or stress missing')
        record(name+'_final_results',root/'results.json');record(name+'_final_artifact',stage/'artifact.json');record(name+'_final_patch',root/'agent.patch')
        real[name]={'passed':True,'cold_resume_after_source_edit':row.get('restarted',False),'fresh_independent_native_review':True,'unchanged_oracle':True,'validation':[{'suite':v['suite'],'exit':v['exit'],'tail':v.get('tail','')[-350:].replace(str(root),'<fixture>')} for v in row['validation']],'web_research':{'actions':row['web_research']['native_completed_actions'],'distinct_urls':row['web_research']['distinct_actual_urls'],'pages':row['web_research']['pages']},'task_patch_sha256':sha(root/'agent.patch'),'initial_failures_preserved':True,'final_source_id':metadata['source_id']}
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    history=output/'history/v0.3.0';history.mkdir(parents=True,exist_ok=True)
    for path in list(output.glob('*.json')):
        if path.name=='runtime-v0.3-unexercised-local.json':continue
        dest=history/path.name
        if not dest.exists():shutil.copy2(path,dest)
    matrix=load(history/'runtime-method-matrix.json')
    for row in matrix:
        method=row['method']
        if method in methods['methods']:
            result=methods['methods'][method]
            row.update(observed_in_passing_acceptance=result['status']=='passed',execution_status=result['status'],execution_evidence='v0.4.0 installed-artifact per-method postconditions; see remaining-local-methods.json')
            if method in REMOTE:row.update(scope='backend_or_account_dependent_native_surface',upstream_source_note=result['reason'])
        elif method.startswith('userVerification/'):
            row.update(scope='backend_or_account_dependent_native_surface',execution_status='account_unavailable_boundary_verified',execution_evidence='Same-version official/native account gate compared; no real OS credential enrollment or deletion.',upstream_source_note='Native identity() requires a ChatGPT account user ID before creating its platform credential provider.')
        else:row['execution_status']='observed_in_native_scenarios' if row['observed_in_passing_acceptance'] else 'not_applicable_to_frozen_local_scope'
    local=[r for r in matrix if r['scope']=='local_runtime_surface'];require(len(local)==125 and all(r['observed_in_passing_acceptance'] for r in local),'Local method matrix still has an unverified entry')
    offline=(args.regression/'offline.log').read_text();require('127 passed' in offline,'Unexpected offline regression count')
    summary={'status':'accepted_for_frozen_macos_local_runtime_scope','version':'0.4.0','source_id':metadata['source_id'],'upstream_version':native['upstream_version'],'upstream_commit':native['upstream_commit'],'model':metadata['model'],'platform':'macOS arm64','native_archive_sha256':metadata['native_archive_sha256'],'adapter_source_sha256':metadata['adapter_source_sha256'],'native_schemas_compared':456,'native_schema_differences':[],'client_methods':len(matrix),'local_methods_positive_evidence':125,'new_local_methods_individually_passed':60,'corrected_backend_entries':4,'user_verification_account_boundaries':5,'all_174_positive_lifecycles_claimed':False,'offline_tests_passed':127,'acceptance_gates':[{'name':r['gate'],'exit':r['exit'],'seconds':r['seconds']} for r in regression['gates']],'additional_acceptance':{'method_gap_list':True,'native_browser':True,'browser_mcp_cancellation':True,'native_mcp_lifetime_comparison':True,'protocol_fault_recovery':True,'os_user_verification_account_boundary':True,'complex_real_tasks':True},'real_work':real,'limits':['Frozen upstream prerelease; macOS arm64 verified, no Windows/Linux platform parity claim.','Model judgment and cloud/account entitlements are outside runtime equivalence.','Cached/indexed OpenAI search, location bias and image-search backend semantics remain explicit compatibility errors on Exa.','Document browsing does not execute JavaScript or use authenticated website sessions.','Native MCP operations can outlive turn interruption; explicit MCP cancellation and owner shutdown have separate passing evidence.','Official macOS native SIGKILL may leave command descendants; unchanged upstream limitation.','All 125 local methods have positive evidence for specified scenarios, not exhaustive input or fault-space coverage.','The prediction endpoint is declared but unimplemented in frozen upstream.'],'evidence_sha256':evidence,'qualification_protocol':'One frozen installed release; 28 standard gates plus individual local-method acceptance, real saved-task continuation with independent feedback, actual browsing and protocol fault recovery. Initial failures are preserved, not relabeled successful.','stable_solution':'docs/stable-solution.zh.md','historical_release_reports':'reports/history/v0.3.0/'}
    write=lambda name,value:(output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    write('runtime-method-matrix.json',matrix);write('remaining-local-methods.json',{'status':'passed','baseline_gap_count':64,'positive_local_methods':60,'backend_boundary_count':4,'methods':{name:{'status':row['status'],'scope':'backend' if name in REMOTE else 'local','reason':row.get('reason'),'evidence_sha256':sha(args.methods/row['evidence'])} for name,row in methods['methods'].items()}})
    write('native-schema-comparison.json',schema);write('runtime-qualification.json',summary);write('acceptance.json',summary)
    write('qualification-lineage.json',{'version':'0.4.0','source_id':metadata['source_id'],'runtime_sources_immutable':True,'evidence_sha256':evidence,'initial_failures_preserved':True,'no_automatic_adapter_tool_replay':True,'note':'Native retry and explicit saved-task continuations are separately recorded. Real-task source fixes belong to test workspaces, not the runtime implementation.'})
    print(json.dumps({'passed':True,'source_id':metadata['source_id'],'positive_local_methods':125,'standard_gates':28,'output':str(output)}),flush=True)


if __name__=='__main__':main()
