"""Read-only positive RPC reconstruction for the fixed composed Windows acceptance.

Host-only output keeps the two guest sandbox methods explicitly missing. A final
mapping requires the independently passed positive guest lifecycle evidence.
"""
import os
from pathlib import Path
import argparse,hashlib,importlib.util,json,sys,time
sys.dont_write_bytecode=True
ROOT=Path('D:/CodexDeepSeekQA/20261021')
QA=ROOT/'final-qa-8ec26d0b9d492b57'
GUARDIAN_QA=ROOT/'final-qa-31be6cbec0be0b6c'
METHODS=Path('D:/CDSQA069/m4/m/52a58213-d827-4243-a835-397804dbd143')
OFFLINE=ROOT/'offline-3a59a782f017693be2b0-8e/r/2526e5a356a7'
ACCOUNT=Path('D:/CDSQA069/f4/r/46fe03c7b45c/local-account/1fe8bf8d-bdf6-4de3-b0ed-3bf63f01c58b')
STANDARD=ROOT/'host-user/data/guardian-composition-3a59-r3.json'
HASH='8ec26d0b9d492b57b191a98c00c22af46573946053bf5b7e1ace498f0b4d501f'
STANDARD_SHA='2bbfaed96936431255f2f6bd8a48ebb9650e2de4b46b2e3b514258d229a76ad9'
RAW={}
def require(v,why):
    if not v:raise RuntimeError(why)
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def read(path):
    path=Path(path);RAW[str(path)]=sha(path);return json.loads(path.read_text(encoding='utf-8'))
def inventory(root):return {p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file() and not any(x in {'__pycache__','.pytest_cache'} for x in p.relative_to(root).parts)}
def main():
    p=argparse.ArgumentParser();p.add_argument('--methods-manifest',type=Path,required=True);p.add_argument('--guardian-manifest',type=Path,required=True);p.add_argument('--windows-sandbox',type=Path);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    require(not args.output.exists(),'Preserve prior host/final mapping output')
    m=read(args.methods_manifest);g=read(args.guardian_manifest)
    require(m['qa_sha256']==HASH and hashlib.sha256(json.dumps(m['files'],sort_keys=True).encode()).hexdigest()==HASH and inventory(QA)==m['files'],'Full methods QA snapshot identity differs')
    require(g['qa_sha256']=='31be6cbec0be0b6ca34969a330264d402c2f222edc95542e4f3a246c4eff591b' and inventory(GUARDIAN_QA)==g['files'],'Guardian snapshot identity differs')
    require(m['source_id']==g['source_id']=='3a59a782f017693be2b0' and m['product_files']==g['product_files'],'Methods QA changed product')
    require({k for k in set(m['files'])|set(g['files']) if m['files'].get(k)!=g['files'].get(k)}=={'verify_remaining_local.py','tests/test_platform_verifiers.py'},'Methods QA delta exceeds the reviewed two files')
    sys.path[:0]=[str(QA),str(QA/'src')]
    import assemble_release_acceptance as a
    import verify_rpc_observations as rpc
    import run_acceptance as runner
    import install
    require(a.SOURCE==QA,'Wrong mapping predicates')
    require(runner.windows_token_identity()=={'sid':os.environ['CODEX_DEEPSEEK_QA_STANDARD_SID'],'elevated':False},'Mapping must read private evidence as original standard QA user')
    require(sha(STANDARD)==STANDARD_SHA,'Strict Guardian composition receipt changed')
    standard=read(STANDARD)
    require(standard['standard_mechanisms_composition_qualified'] is True and standard['single_run_all_passed'] is False and standard['original_verdict_passed'] is False and standard['original_failed_gates']==['guardian'],'Original failed regression was erased or composition unqualified')
    require(standard['current_qa_sha256']==g['qa_sha256'] and standard['source_id']==m['source_id'],'Standard/method producer identity differs')
    require(sha(ROOT/'host-user/validate_windows_guardian_composition.py')==standard['validator_sha256'],'Strict standard composer changed after verification')
    for path,digest in standard['raw_sha256'].items():require(sha(path)==digest,'A bound standard/Guardian source artifact changed')
    entry=Path(standard['entry']);release=entry.parent.parent.parent;metadata=read(release/'installation.json');native=read(release/'native-runtime.json');scope=a.qualification_scope(native)
    require(metadata==standard['installation'] and native==standard['native_manifest'] and sha(entry)==standard['entry_sha256']==metadata['console_script_sha256'],'Mapping installation differs from strict composition')
    require(install.valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native),'Current immutable installed release failed integrity')
    fresh=a.external_offline_receipt(OFFLINE,metadata,native,scope)
    require(fresh['controller_environment']['windows_token']=={'sid':os.environ['CODEX_DEEPSEEK_QA_ADMIN_SID'],'elevated':True},'Latest complete offline token differs')
    for name in ['artifact.json','verdict.json','results.json',*a.OFFLINE_FILES]:RAW[str(OFFLINE/name)]=sha(OFFLINE/name)
    artifact=read(METHODS/'artifact.json');verdict=read(METHODS/'verdict.json')
    require(artifact['installation']==metadata and artifact['native_manifest']==native and Path(artifact['entry'])==entry and artifact['entry_sha256']==sha(entry) and artifact['target']==scope['target'] and artifact['baseline_matrix_sha256']==a.BASELINE_MATRIX_SHA256,'Methods use a different installed target')
    require(verdict['passed'] is True and verdict['verifier_and_artifact_unchanged'] is True and verdict['positive_local_methods']==60 and verdict['backend_boundaries_verified']==4 and not verdict['missing'] and not verdict['unexpected'] and not verdict['invalid'],'Complete 64-method acceptance did not pass')
    expected=set(read(QA/'reports/runtime-v0.3-unexercised-local.json')['methods'])
    require(set(verdict['methods'])==expected and len(expected)==64,'Per-method inventory differs')
    require({r['group'] for r in verdict['groups']}==set(artifact['groups']) and len(verdict['groups'])==7 and all(r['exit']==0 for r in verdict['groups']),'Seven method groups did not complete')
    method_sources=a.validate_method_verifiers(artifact)
    for name,row in verdict['methods'].items():
        path=(METHODS/row['evidence']).resolve();require(a.comparison_path(path).is_relative_to(a.comparison_path(METHODS)),'Method postcondition escaped its run')
        records=[r for r in read(path) if r['method']==name]
        require(records==[{k:v for k,v in row.items() if k!='evidence'}] and row['status']==('backend_boundary_verified' if name in a.REMOTE else 'passed'),'Per-method raw postcondition differs')
    require({name for name,row in verdict['methods'].items() if row['status']=='backend_boundary_verified'}==a.REMOTE,'Backend scope classification differs')
    roots={};excluded=[a.comparison_path(Path(x)) for x in standard['rpc_evidence_sources']['excluded_roots']]
    require(excluded==[a.comparison_path(Path(standard['original_full_run'])/'guardian')],'Failed Guardian root was not explicitly excluded')
    for source in standard['rpc_evidence_sources']['roots']:
        root=Path(source['root']);artifact0=read(root/'artifact.json');verdict0=read(root/'verdict.json')
        require(artifact0['installation']==metadata and artifact0['native_manifest']==native,'RPC source changed installation')
        names=set(source['passing_gates']);passing=[r for r in verdict0['gates'] if r['gate'] in names]
        require(len(passing)==len(names) and all(r['exit']==0 for r in passing) and (names==a.GATES-{'guardian'} if root==Path(standard['original_full_run']) else names=={'guardian'}),'RPC source admits a failed gate')
        for selected in rpc.regression_roots(root,artifact0,{'gates':passing}):roots[selected]=(str(entry),sha(entry))
    roots[METHODS]=(str(entry),sha(entry))
    def lifecycle(path,names,relocated=False):
        identity=read(path/'artifact.json');proof=read(path/'verdict.json')
        lineage=a.installation_lineage(metadata,native,identity,relocated_windows=relocated)
        require(proof['installation']==identity['installation'] and proof['passed'] is True and proof['target']==scope['target'] and identity['target']==scope['target'] and proof['verifier_and_artifact_unchanged'] is True,'Lifecycle acceptance failed')
        require(identity.get('verifier_sha256') and all(sha(QA/name)==digest for name,digest in identity['verifier_sha256'].items()),'Lifecycle execution verifier changed')
        rows=proof['methods'];require(len(rows)==len(names) and {r['method'] for r in rows}==names and all(r['status']=='passed' for r in rows) and read(path/'results.json')==rows,'Lifecycle raw method postconditions differ')
        if relocated:require(proof['positive_lifecycle_exercised'] is True,'Guest sandbox is only an unavailable boundary')
        else:require(proof['model_requests']==0,'Local account lifecycle submitted inference')
        roots[path.resolve()]=(identity['entry'],identity['entry_sha256'])
        return lineage
    account_lineage=lifecycle(ACCOUNT,a.LOCAL_ACCOUNT)
    guest_lineage=lifecycle(args.windows_sandbox,a.WINDOWS_SANDBOX,True) if args.windows_sandbox else None
    traces={}
    for root,identity in sorted(roots.items(),key=lambda x:len(x[0].parts)):
        for trace in root.rglob('client-requests.jsonl'):
            canonical=a.comparison_path(trace)
            require(canonical.is_relative_to(a.comparison_path(root)),'RPC trace escaped its declared source')
            if any(canonical.is_relative_to(x) for x in excluded):continue
            if canonical in traces:require(traces[canonical][1]==identity,'RPC trace has conflicting installation identity')
            traces[canonical]=(trace,identity)
    observations={};sessions=0;errors=[]
    for _,(trace,identity) in sorted(traces.items()):
        found,count,failures=rpc.trace_observations(trace,*identity,scope['target']);sessions+=count;errors.extend(failures)
        for method,rows in found.items():observations.setdefault(method,[]).extend(rows)
    required=set(scope['local_methods']);missing=sorted(required-observations.keys())
    require(len(required)==131,'Unexpected Windows local RPC method scope')
    require(not errors,'Paired RPC reconstruction found ambiguous or invalid native evidence')
    require(set(missing)==(set() if args.windows_sandbox else a.WINDOWS_SANDBOX),'Unexpected missing methods, or host-only mapping pretends guest coverage')
    for path,digest in RAW.items():require(sha(path)==digest,'Mapping source evidence changed while read')
    require(sha(entry)==standard['entry_sha256'] and read(release/'installation.json')==metadata,'Installed launcher changed during mapping')
    report={'passed':not missing,'host_mapping_qualified':True,'full_local_rpc_mapping_complete':not missing,'release_acceptance_claimed':False,'single_run_all_passed':False,'source_id':metadata['source_id'],'entry':str(entry),'entry_sha256':sha(entry),'installation':metadata,'target':scope['target'],'baseline_matrix_sha256':scope['baseline_matrix_sha256'],'standard_composition':{'path':str(STANDARD),'sha256':sha(STANDARD),'original_failed_verdict_preserved':True},'methods_qa_sha256':HASH,'guardian_qa_sha256':g['qa_sha256'],'methods_root':str(METHODS),'method_execution_producers':method_sources,'methods_positive_local':60,'backend_boundaries_verified':4,'latest_complete_offline_validation':fresh['validation'],'supplemental_lifecycle_lineage':{'local_account':account_lineage,'windows_sandbox':guest_lineage},'excluded_failed_roots':[str(x) for x in excluded],'local_methods_observed':len(required&observations.keys()),'local_methods_expected':len(required),'missing':missing,'trace_errors':errors,'native_client_sessions':sessions,'scope':'Exact successful paired native RPC replies. Standard gates, 64 method postconditions and lifecycle postconditions remain independently bound; host-only stage retains the two guest methods as missing.','methods':{name:observations[name] for name in sorted(required) if name in observations},'input_sha256':RAW,'validator_sha256':sha(__file__),'trace_observations_sha256':sha(QA/'verify_rpc_observations.py'),'assembly_predicates_sha256':sha(QA/'assemble_release_acceptance.py'),'validated_at':time.time()}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps({'host_mapping_qualified':True,'full_local_rpc_mapping_complete':not missing,'local_methods_observed':report['local_methods_observed'],'local_methods_expected':131,'missing':missing,'native_client_sessions':sessions,'trace_errors':len(errors)}),flush=True)
if __name__=='__main__':main()
