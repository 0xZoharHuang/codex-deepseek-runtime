"""Read-only exact-install source/schema/browser/recovery component verification."""
import os
from pathlib import Path
import argparse,hashlib,json,sys,time
sys.dont_write_bytecode=True
ROOT=Path('D:/CodexDeepSeekQA/20261021');QA=ROOT/'final-qa-8ec26d0b9d492b57';X=Path('D:/CDSQA069/x3')
RAW={}
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def read(path):
    path=Path(path);RAW[str(path)]=sha(path);return json.loads(path.read_text(encoding='utf-8'))
def need(value,why):
    if not value:raise RuntimeError(why)
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();need(not args.output.exists(),'Preserve existing verification output')
    sys.path[:0]=[str(QA),str(QA/'src')]
    import assemble_release_acceptance as a
    import install
    import run_acceptance as runner
    from verify_rpc_observations import trace_observations
    need(a.SOURCE==QA and runner.windows_token_identity()=={'sid':os.environ['CODEX_DEEPSEEK_QA_STANDARD_SID'],'elevated':False},'Wrong verifier source or reading user')
    release=ROOT/'host-user/data/runtime/releases/3a59a782f017693be2b0';metadata=read(release/'installation.json');native=read(release/'native-runtime.json');entry=release/'venv/Scripts/codex-deepseek.exe';scope=a.qualification_scope(native)
    need(metadata['source_id']=='3a59a782f017693be2b0' and install.valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native),'Current installed artifact is not valid')
    def same_install(obj):need(obj.get('installation')==metadata,'A component used another installation')
    def current_verifier(obj):
        need(obj.get('verifier_sha256'),'Component verifier inventory is missing')
        for name,digest in obj['verifier_sha256'].items():need(sha(QA/name)==digest,'Component verifier source changed: '+name)
    source_path=X/'src/verdict.json';source=read(source_path);source_result=a.validate_native_source_audit(source,native)
    schema_path=X/'s/2ea36822-966a-4e3b-afe7-520b9c3c8c46/verdict.json';schema=read(schema_path);binary=Path(metadata['native_package'])/'bin/codex.exe'
    need(schema['passed'] is True and schema['schemas_compared']==schema['official_schema_count']==schema['patched_schema_count']==schema['expected_schema_count']==456 and schema['differences']==[],'Schema comparison incomplete')
    need(schema['binary_and_verifier_unchanged'] is True and schema['native_manifest']==native and schema['target']==schema['official_target']==native['target']=='x86_64-pc-windows-msvc','Schema target or native provenance differs')
    need(schema['patched_binary_sha256']==sha(binary)==native['files']['bin/codex.exe'],'Schema used another native executable');current_verifier(schema)
    official=ROOT/'official-baselines/windows/codex-x86_64-pc-windows-msvc.exe';archive=ROOT/'official-baselines/codex-x86_64-pc-windows-msvc.exe.tar.gz'
    need(sha(official)==schema['official_binary_sha256'] and sha(archive)==schema['official_archive_sha256'],'Official comparison artifact changed')
    from verify_native_schema import compare_schemas
    actual_schema=compare_schemas(schema_path.parent/'official',schema_path.parent/'patched',456)
    need(all(schema[k]==value for k,value in actual_schema.items()),'Raw exported schema inventory differs from its comparison receipt')
    for label in ['official','patched']:
        for path in (schema_path.parent/label).rglob('*.json'):RAW[str(path)]=sha(path)
    need(len(schema['commands'])==2 and all(row['exit']==0 and row['version']=='codex-cli '+native['upstream_version'] for row in schema['commands']),'Native schema exports did not complete')
    lifetime_path=X/'l/3f2699e2-a23e-44ca-8f83-f74bab2dcf6e/verdict.json';lifetime=read(lifetime_path);same_install(lifetime);current_verifier(lifetime)
    need(lifetime['passed'] is True and lifetime['target']==native['target'] and lifetime['binary_and_verifier_unchanged'] is True and lifetime['binary_sha256']=={'official':schema['official_binary_sha256'],'patched':schema['patched_binary_sha256']},'Native MCP lifetime comparison differs')
    for label,binary_path in [('official',official),('patched',binary)]:
        raw=read(lifetime_path.parent/label/'result.json')
        need(raw==lifetime['results'][label] and raw['binary']==str(binary_path) and raw['version']=='codex-cli '+native['upstream_version'],'Lifetime raw scenario result differs')
        trace=lifetime_path.parent/label/'client-requests.jsonl'
        found,count,errors=trace_observations(trace,str(binary_path),sha(binary_path),native['target'])
        need(count==1 and not errors and {'thread/start','turn/start','turn/interrupt'}<=found.keys(),'Lifetime native RPC scenario is incomplete')
        for witnesses in found.values():
            for witness in witnesses:
                for name in ['requests','events','launch']:RAW[witness[name]]=witness[name+'_sha256']
    need(lifetime['results']['patched']['http_closed_on_owner_exit'] is True and all(lifetime['results']['official'][k]==lifetime['results']['patched'][k] for k in ['http_closed_on_turn_interrupt','http_closed_on_owner_exit']),'Native request ownership differs from official CLI')
    protocol_path=X/'bp/1ee4a29a-9600-407d-9cf8-ea813fc936f8/verdict.json';protocol=read(protocol_path);same_install(protocol);current_verifier(protocol)
    need(all(protocol[k] is True for k in ['passed','http_started','http_cancelled_by_mcp','anticipated_error_readable','verifier_and_artifact_unchanged']) and protocol['target']==native['target'] and protocol['native_manifest']==native,'Browser protocol acceptance is incomplete')
    browser_root=X/'b/82a005a3-94ce-4a40-a550-63160e1c04ce';browser=read(browser_root/'results.json');artifact=read(browser_root/'artifact.json');verdict=read(browser_root/'verdict.json');same_install(artifact);same_install(verdict);current_verifier(artifact)
    need(verdict['passed'] is True and verdict['verifier_and_artifact_unchanged'] is True and artifact['target']==verdict['target']==native['target'] and artifact['native_manifest']==native,'Browser runtime target or integrity differs')
    need(artifact['sandbox_mode']==verdict['sandbox_mode']=='danger-full-access','Original host browser mode changed')
    expected={'native_direct_mcp_actual_open_find_click','cold_native_mcp_page_reference_recovery','model_native_browse_find_follow_and_file_effect','bundled_public_browser_actual_https_find_and_link','slow_native_browser_request_actually_started','native_interrupt_preserves_official_mcp_lifetime','browser_remains_usable_after_turn_interruption','native_owner_shutdown_closes_browser_http_request'}
    need(len(browser)==len(expected)==8 and {r['name'] for r in browser}==expected and all(r['passed'] is True for r in browser),'Eight browser mechanisms did not pass')
    need(artifact['native_lifetime_proof_sha256']==sha(lifetime_path),'Browser used another MCP lifetime reference')
    browser_sessions=0;browser_rpc_methods=set()
    for path in browser_root.rglob('client-requests.jsonl'):
        found,count,errors=trace_observations(path,str(entry),sha(entry),native['target']);need(not errors,'Browser RPC evidence pairing failed');browser_sessions+=count;browser_rpc_methods.update(found)
        for method,witnesses in found.items():
            for witness in witnesses:
                for label in ['requests','events','launch']:RAW[witness[label]]=witness[label+'_sha256']
    need(browser_sessions>0 and {'thread/start','turn/start','turn/interrupt'}<=browser_rpc_methods,'Browser actual native lifecycle RPC evidence is missing')
    recovery=[]
    for path in sorted((X/'pr').glob('*/verdict.json')):
        proof=a.validate_protocol_recovery(path.parent,metadata,native);read(path);read(path.parent/'artifact.json')
        for name,digest in proof['evidence_sha256'].items():RAW[str(path.parent/name)]=digest
        recovery.append({'kind':proof['kind'],'path':str(path.parent),'verdict_sha256':sha(path),'requests':proof['requests'],'native_completed':True,'raw_protocol_reconstructed':True})
    need(len(recovery)==6 and {r['kind'] for r in recovery}==a.RECOVERY_KINDS,'Protocol recovery requires every exact fault case once')
    for path,digest in RAW.items():need(sha(path)==digest,'A component raw source changed while checking')
    report={'current_windows_components_verified':True,'release_acceptance_claimed':False,'qualified_source_id':metadata['source_id'],'target':native['target'],'same_installation_native_entry':True,'installation':metadata,'native_manifest':native,'native_source':{'path':str(source_path),'sha256':sha(source_path),**source_result},'native_schema':{'path':str(schema_path),'sha256':sha(schema_path),'schemas_compared':456,'differences':[],'actual_target':native['target'],'official_binary_sha256':schema['official_binary_sha256'],'patched_binary_sha256':schema['patched_binary_sha256'],'original_scope_caption':schema['scope'],'scope_caption_correction':'Actual target is Windows x86_64; the preserved generic Linux-libc sentence is not platform evidence.'},'native_mcp_lifetime':{'path':str(lifetime_path),'sha256':sha(lifetime_path),'passed':True},'browser_protocol':{'path':str(protocol_path),'sha256':sha(protocol_path),'passed':True,'http_cancelled_by_mcp':True,'anticipated_error_readable':True},'native_browser':{'path':str(browser_root),'stages':sorted(expected),'passed_stages':8,'native_client_sessions':browser_sessions,'native_rpc_pairing_reconstructed':True,'host_sandbox_mode':'danger-full-access','positive_guest_sandbox_required_separately':True},'protocol_recovery':recovery,'evidence_sha256':RAW,'validator_sha256':sha(__file__),'assembly_predicates_sha256':sha(QA/'assemble_release_acceptance.py'),'validated_at':time.time()}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps({'current_windows_components_verified':True,'native_source_files':source['source_files'],'protected_runtime_files':source['unchanged_protected_runtime_files'],'schemas':456,'browser_stages':8,'protocol_recovery_cases':len(recovery),'raw_files_bound':len(RAW)}),flush=True)
if __name__=='__main__':main()
