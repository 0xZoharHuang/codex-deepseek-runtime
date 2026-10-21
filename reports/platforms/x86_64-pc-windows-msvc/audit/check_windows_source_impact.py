"""Read-only, exact-scope 069 -> Windows OAuth canonicalization impact check.

This is a source/asset guard, not a substitute for runtime acceptance.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,sys
sys.dont_write_bytecode=True
BASE_SOURCE='069803795d9ad78421fb'
ALLOWED={'native/windows-transport.patch','native/windows-source-changes.json','native/runtime-x86_64-pc-windows-msvc.json'}
OVERLAY='d2745c0e0c8b498fab05f3a830081dda3e3502aac6da80a705311702df351f5c'
COMBINED='8e5f6f809e6945080974e4762de3e0fef3503d1e488ec7c1c9b682ee34f06c57'
COMMON='b13ac1bd8c9d66c7b25cdc50cb726e2e1ae5e25aa24b25020bad6063595827f3'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def require(value,message):
    if not value:raise RuntimeError(message)
def identity(root,name):
    spec=importlib.util.spec_from_file_location(name,root/'install.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module.source_identity()
def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--native-source-audit',type=Path);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    require(not args.output.exists(),'Preserving existing impact receipt; choose a new output')
    old=args.baseline.resolve();new=args.source.resolve();old_id,old_files=identity(old,'baseline_installer');new_id,new_files=identity(new,'candidate_installer')
    require(old_id==BASE_SOURCE and old_files.keys()==new_files.keys(),'Unexpected baseline/product source inventory')
    changed={k for k in old_files if old_files[k]!=new_files[k]}
    require(changed<=ALLOWED and {'native/windows-transport.patch','native/windows-source-changes.json'}<=changed,'Unreviewed product change outside the Windows overlay/manifest')
    require(sha(new/'native/codex-runtime.patch')==COMMON==sha(old/'native/codex-runtime.patch'),'Common patch changed')
    require(sha(new/'native/windows-transport.patch')==OVERLAY,'Windows overlay differs from the reviewed minimal correction')
    extra=load(new/'native/windows-source-changes.json');previous=load(old/'native/windows-source-changes.json')
    require(extra['base_patch_sha256']==COMMON and extra['combined_patch_sha256']==COMBINED and extra['external_dependency_versions_unchanged'] is True,'Overlay provenance differs')
    prior={r['path']:r for r in previous['files']};current={r['path']:r for r in extra['files']};oauth='codex-rs/rmcp-client/src/oauth.rs'
    require(set(current)==set(prior)|{oauth} and all(current[k]==v for k,v in prior.items()),'Additional Windows native changes appeared')
    require(current[oauth]=={'path':oauth,'common_sha256':'eb558232d2f54cccc02d55de3eb56ebf3276a856c3e0c89e28d4f53c557df2cd','fork_sha256':'234dd51e5f1d2b85eb3e7994ff5bdb2bc2e7bfc233125b47df0c6b79cd70c44b'},'OAuth correction differs')
    for n in ['native/runtime.json','native/runtime-x86_64-unknown-linux-gnu.json','requirements.lock','catalog.json']:
        require(old_files[n]==new_files[n],'Mac/Linux asset or common dependency/model identity changed')
    native=load(new/'native/runtime-x86_64-pc-windows-msvc.json');before=load(old/'native/runtime-x86_64-pc-windows-msvc.json')
    manifest_ready=(native['patch_sha256']==COMBINED and native['archive']['sha256']!=before['archive']['sha256'])
    require(native['target']=='x86_64-pc-windows-msvc' and native['upstream_commit']==before['upstream_commit'] and native['upstream_version']==before['upstream_version'],'Upstream target/version changed')
    require(native['archive']['filename']==before['archive']['filename'],'Unexpected release asset rename/version change')
    audit=None
    if args.native_source_audit:
        require(manifest_ready,'Native manifest has not been replaced by the rebuilt package')
        sys.path[:0]=[str(new),str(new/'src')]
        import assemble_release_acceptance as acceptance
        require(acceptance.SOURCE==new,'Wrong source-audit consumer imported')
        audit=acceptance.validate_native_source_audit(load(args.native_source_audit),native)
    result={'source_scope_verified':True,'candidate_native_artifact_ready':manifest_ready,'full_native_source_audit_checked':audit is not None,'release_acceptance_claimed':False,'baseline_source_id':old_id,'candidate_source_id':new_id,'changed_product_files':{k:{'before':old_files[k],'after':new_files[k]} for k in sorted(changed)},'mac_linux_execution_and_assets_unchanged':True,'common_dependency_and_catalog_unchanged':True,'native_source_audit':audit,'native_source_audit_sha256':sha(args.native_source_audit) if args.native_source_audit else None,'remaining':['Actual rebuilt-Windows Direct and Secrets OAuth lifecycle proof','Current standard regression and any exact-scope supplements','Final installed identities and component-specific lineage assembly']}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ['source_scope_verified','candidate_native_artifact_ready','full_native_source_audit_checked','release_acceptance_claimed','candidate_source_id']}))
if __name__=='__main__':main()
