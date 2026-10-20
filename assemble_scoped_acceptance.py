"""Compose explicitly scoped revalidation for an unchanged installed runtime.

Original full-run failures remain intact. Only the memory verifier scope may
change; bounded real-work attempts may continue through saved native sessions.
A runtime, shared helper, test or unrelated verifier change requires a full run.
"""
import argparse
import json
from pathlib import Path
import shutil
import uuid
from install import valid_release
from run_acceptance import digest


def load(path):return json.loads(Path(path).read_text())
def require(condition,message):
    if not condition:raise SystemExit(message)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base',type=Path,required=True);parser.add_argument('--rerun',type=Path,required=True,action='append');parser.add_argument('--previous-verifier',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--reason',required=True);args=parser.parse_args()
    base=args.base.resolve();source=Path(__file__).resolve().parent;old=load(base/'artifact.json');before=load(base/'verdict.json')
    require(before['all_gates_selected'] and before['entry_unchanged'] and before['verifier_source_unchanged'],'Original full run must be frozen and unchanged')
    failed={row['gate'] for row in before['gates'] if row['exit']}
    require(bool(failed) and failed<={'memory-v1','memory-v2','real-work'},'Unrelated failures require a fresh full run')
    replacements={};stages=[];new=None
    for directory in args.rerun:
        directory=directory.resolve();artifact=load(directory/'artifact.json');verdict=load(directory/'verdict.json')
        require(verdict['passed'] and verdict['entry_unchanged'] and verdict['verifier_source_unchanged'],'Revalidation must pass with frozen sources')
        for key in ['entry','entry_sha256','installation','native_manifest']:
            require(old[key]==artifact[key],'Installed runtime artifact changed: '+key)
        if new:require(new['verifier_source_sha256']==artifact['verifier_source_sha256'],'Revalidation stages used different verifier sources')
        new=artifact
        require(set(artifact['selected_gates'])=={row['gate'] for row in verdict['gates']},'Selected gates differ from actual results')
        for row in verdict['gates']:
            require(row['gate'] in failed and row['gate'] not in replacements,'Unexpected or duplicate revalidation gate')
            replacements[row['gate']]=(directory,row)
        stages.append({'artifact_sha256':digest(directory/'artifact.json'),'verdict_sha256':digest(directory/'verdict.json'),'gates':artifact['selected_gates'],'protocol':artifact.get('protocol','scoped installed-artifact gate rerun')})
    require(set(replacements)==failed,'Every original failure must be revalidated')
    require(valid_release(Path(new['release']),new['installation']['adapter_source_sha256'],new['installation']['model_catalog_sha256'],new['native_manifest']),'Installed native/Python integrity changed')
    require(digest(Path(new['entry']))==new['entry_sha256'],'Entrypoint changed')
    require(load(Path(new['release'])/'installation.json')==new['installation'],'Installation metadata changed')
    for name,value in new['installation']['adapter_source_sha256'].items():require(digest(source/name)==value,'Runtime source changed: '+name)
    old_hashes=old['verifier_source_sha256'];new_hashes=new['verifier_source_sha256']
    require(set(old_hashes)<=set(new_hashes),'Verifier or helper removed')
    changed={name for name in old_hashes if old_hashes[name]!=new_hashes[name]}
    require(changed=={'verify_memory_audit.py'} and bool(failed&{'memory-v1','memory-v2'}),'Unexpected verifier/helper changes: '+repr(changed))
    added=set(new_hashes)-set(old_hashes)
    require(added<={'assemble_scoped_acceptance.py','verify_real_work_resume.py'},'Unexpected added verification utility')
    require(digest(args.previous_verifier)==old_hashes['verify_memory_audit.py'],'Original verifier snapshot differs from the frozen full run')
    for name,value in new_hashes.items():require(digest(source/name)==value,'Verifier changed after revalidation: '+name)
    output=args.output.resolve()/str(uuid.uuid4());output.mkdir(parents=True,mode=0o700);rows=[]
    for row in before['gates']:
        directory,selected=replacements.get(row['gate'],(base,row));selected=dict(selected)
        shutil.copy2(directory/selected['log'],output/selected['log']);selected['evidence_stage']='scoped_revalidation' if row['gate'] in failed else 'frozen_full_run';rows.append(selected)
    qualification={'kind':'scoped_verifier_revalidation','reason':args.reason,'runtime_unchanged':True,'changed_verifiers':sorted(changed),'added_verification_utilities':sorted(added),'base_artifact_sha256':digest(base/'artifact.json'),'base_verdict_sha256':digest(base/'verdict.json'),'revalidation_stages':stages,'previous_verifier_sha256':digest(args.previous_verifier),'current_verifier_sha256':digest(source/'verify_memory_audit.py'),'failed_original_gates':sorted(failed),'original_failures_preserved':True}
    artifact={**new,'selected_gates':old['selected_gates'],'qualification':qualification};verdict={'passed':all(row['exit']==0 for row in rows),'all_gates_selected':{row['gate'] for row in rows}==set(old['selected_gates']),'entry_unchanged':True,'verifier_source_unchanged':True,'qualification_kind':qualification['kind'],'gates':rows}
    (output/'artifact.json').write_text(json.dumps(artifact,indent=2));(output/'verdict.json').write_text(json.dumps(verdict,indent=2));(output/'qualification.json').write_text(json.dumps(qualification,indent=2));print('EVIDENCE',output,flush=True)


if __name__=='__main__':main()
