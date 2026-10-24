"""Build release evidence from installed-artifact gates; refuse incomplete acceptance."""
import argparse
from qa_workspace import evidence_files
import hashlib
import json
from pathlib import Path
import re


def load(path):return json.loads(Path(path).read_text())
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--acceptance',type=Path,required=True);parser.add_argument('--schemas',type=Path,required=True);parser.add_argument('--installation',type=Path,required=True);parser.add_argument('--fixtures',type=Path,required=True);parser.add_argument('--client-schema',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    acceptance=args.acceptance.resolve();verdict=load(acceptance/'verdict.json');artifact=load(acceptance/'artifact.json');schemas=load(args.schemas);installation=load(args.installation)
    if not verdict['passed'] or not verdict['all_gates_selected'] or not verdict['entry_unchanged']:raise SystemExit('All installed-artifact gates must pass before assembling a release claim')
    if not schemas['passed'] or not installation['passed']:raise SystemExit('Schema equivalence and real installation repair must pass')
    fixtures=load(args.fixtures/'results.json')
    if len(fixtures)!=2 or not all(case['passed'] for case in fixtures):raise SystemExit('Both real-work workflows must pass independently')
    required_cases={'httpcore','jinja'}
    actual_cases={case.get('case') or case.get('project') or case.get('name') for case in fixtures}
    if not required_cases<=actual_cases:
        # Existing real-work reports identify the case by their containing path.
        for name in required_cases:
            case=load(args.fixtures/name/'results.json')
            if not case['passed']:raise SystemExit('Real-work fixture is incomplete: '+name)
    for name in required_cases:
        baseline=load(args.fixtures/name/'baseline.json')
        if baseline[0]['exit']!=0 or baseline[-1]['exit']!=0:raise SystemExit('Broken baseline/oracle for '+name)
    if load(args.fixtures/'httpcore/baseline-independent.json')[0]['exit']!=1:raise SystemExit('HTTPcore defect must fail before the fix')
    if load(args.fixtures/'jinja/baseline.json')[1]['exit']!=1:raise SystemExit('Jinja defect must fail before the fix')
    roots={acceptance}
    for row in verdict['gates']:
        for line in (acceptance/row['log']).read_text().splitlines():
            if line.startswith('EVIDENCE '):
                path=Path(line.removeprefix('EVIDENCE '))
                if path.is_dir():roots.add(path)
    observed=set()
    for root in roots:
        for path in evidence_files(root,'client-requests.jsonl'):
            for line in path.read_text().splitlines():
                try:request=json.loads(line)
                except ValueError:continue
                if request.get('method'):observed.add(request['method'])
    schema=load(args.client_schema);methods=[item['properties']['method']['enum'][0] for item in schema['oneOf']]
    cloud_prefixes=('account/','app/','remoteControl/','thread/realtime/','feedback/','plugin/share/')
    matrix=[]
    for method in methods:
        if method=='thread/prediction/request':scope='declared_but_unimplemented_in_frozen_upstream'
        elif method.startswith(cloud_prefixes):scope='backend_or_account_dependent_native_surface'
        elif method.startswith('windowsSandbox/'):scope='other_platform_native_surface'
        elif method.startswith('userVerification/'):scope='native_os_user_verification_surface'
        else:scope='local_runtime_surface'
        matrix.append({'method':method,'scope':scope,'native_schema_identical':True,'upstream_source_note':'Native message_processor.rs returns method_not_found for this method' if method=='thread/prediction/request' else None,'observed_in_passing_acceptance':method in observed,'execution_evidence':'passing scenario logs and native events' if method in observed else 'unchanged native handler inherited; not individually exercised'})
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    public={'status':'accepted_for_frozen_local_runtime_scope','version':'0.3.0','upstream_version':artifact['native_manifest']['upstream_version'],'upstream_commit':artifact['native_manifest']['upstream_commit'],'model':artifact['installation']['model'],'platform':'macOS arm64','native_archive_sha256':artifact['native_manifest']['archive']['sha256'],'adapter_source_sha256':artifact['installation']['adapter_source_sha256'],'native_schemas_compared':schemas['schemas_compared'],'native_schema_differences':schemas['differences'],'client_methods':len(methods),'methods_observed_in_passing_scenarios':len(observed&set(methods)),'acceptance_gates':[{'name':row['gate'],'exit':row['exit'],'seconds':row['seconds']} for row in verdict['gates']],'real_work':[{'name':name,'passed':load(args.fixtures/name/'results.json')['passed'],'validation':[{'suite':row['suite'],'exit':row['exit'],'tail':row['tail']} for row in load(args.fixtures/name/'results.json')['validation']]} for name in sorted(required_cases)],'limits':['Model quality and backend/cloud entitlements are outside runtime equivalence.','Official upstream native SIGKILL on macOS can leave command descendants; no adapter-specific guarantee is claimed for that path.','Every method schema is identical, but the method matrix distinguishes live scenarios from inherited handlers; no claim that all 174 methods were individually executed.','Upstream prerelease behavior is frozen; updates require the same acceptance gates.'],'evidence_hashes':{'verdict':sha(acceptance/'verdict.json'),'artifact':sha(acceptance/'artifact.json'),'schema_comparison':sha(args.schemas),'installation_lab':sha(args.installation)},'method_matrix':'runtime-method-matrix.json'}
    # Public aggregate text must not contain private workspace paths or credentials.
    serialized=json.dumps(public,indent=2,ensure_ascii=False)
    serialized=re.sub(r'/Users/[^\s\"\'<>]+','<private-path>',serialized)
    if re.search(r'sk-or-v1-[A-Za-z0-9]+',serialized):raise SystemExit('Credential detected; refusing publication')
    (output/'acceptance.json').write_text(serialized+'\n');(output/'runtime-method-matrix.json').write_text(json.dumps(matrix,indent=2)+'\n')
    print(json.dumps({'report':str(output/'acceptance.json'),'methods':len(methods),'observed':len(observed&set(methods))},indent=2))


if __name__=='__main__':main()
