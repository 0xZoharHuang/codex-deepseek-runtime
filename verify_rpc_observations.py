"""Reconstruct exact-target RPC coverage from paired current-artifact evidence."""
import argparse
from qa_workspace import evidence_files
import hashlib
import json
from pathlib import Path,PureWindowsPath
import re

from assemble_release_acceptance import (qualification_scope,installation_lineage,require,sha,load,validate_standard_gates,
    validate_regression_source,validate_offline_run,validate_terminal_qa,gate_evidence,superseded_terminal_roots,oauth_evidence_roots,evidence_io_root,TERMINAL_GATES,OAUTH_GATES,validate_method_platform_boundaries,validate_windows_command_controls)
from install import console_entry,valid_release,_support

comparison_path=_support.comparison_path
VERIFIERS=('verify_rpc_observations.py','assemble_release_acceptance.py','install.py','qa_workspace.py')
NON_RPC_PRODUCERS={'search':'Direct Gateway/NativeTransport search, HTTP and citation checks; no native Peer or JSON-RPC producer. Its standard gate remains required.'}


def _same_entry(left,right,target):
    if _support.WINDOWS:return comparison_path(left)==comparison_path(right)
    if target=='x86_64-pc-windows-msvc':return PureWindowsPath(left)==PureWindowsPath(right)
    return left==right


def trace_observations(trace,expected_entry,expected_sha,target):
    """Reject ambiguous IDs/segments rather than letting dict overwrite prove a call."""
    trace=Path(trace);observed={};errors=[];sessions=0
    def error(reason):errors.append({'trace':str(trace),'reason':reason})
    snapshots={}
    def read(path):
        if not comparison_path(path).is_relative_to(comparison_path(trace.parent)):raise ValueError('Native RPC companion evidence escaped its session directory')
        data=path.read_bytes();snapshots[path]=hashlib.sha256(data).hexdigest();return json.loads(data)
    try:
        raw=trace.read_bytes();snapshots[trace]=hashlib.sha256(raw).hexdigest()
        segments=[]
        for line in raw.decode('utf-8').splitlines():
            request=json.loads(line)
            if not isinstance(request,dict):raise ValueError('Non-object client request')
            if request.get('method')=='initialize':segments.append([])
            if not segments:raise ValueError('Client traffic precedes native initialization')
            segments[-1].append(request)
        events=sorted(trace.parent.glob('events-*.json'));launches=sorted(trace.parent.glob('launch-*.json'))
        if not segments or len(segments)!=len(events) or len(events)!=len(launches):
            raise ValueError('Request segments, event files and launch files must pair one-to-one')
        for index,(sent,event_file,launch_file) in enumerate(zip(segments,events,launches)):
            launch=read(launch_file)
            if not _same_entry(launch.get('resolved_entry',''),expected_entry,target):continue
            if launch.get('entry_sha256')!=expected_sha:raise ValueError('Launch entry hash differs from the selected installation')
            requests={}
            def key(value):
                if type(value) not in (int,str):raise ValueError('RPC request ID must be a string or integer')
                return (type(value).__name__,value)
            for request in sent:
                if 'id' not in request or 'method' not in request:continue
                identity=key(request['id'])
                if identity in requests:raise ValueError('Duplicate native client request ID within one session')
                requests[identity]=request
            received={};rows=read(event_file)
            if not isinstance(rows,list):raise ValueError('Native event receipt is not a list')
            for response in rows:
                if not isinstance(response,dict):raise ValueError('Non-object native event')
                if 'id' not in response or 'method' in response:continue
                identity=key(response['id'])
                if identity in received:raise ValueError('Duplicate native response ID within one session')
                if identity not in requests:raise ValueError('Native response has no matching request in its session')
                received[identity]=response
            sessions+=1
            for identity,request in requests.items():
                response=received.get(identity,{})
                if 'result' not in response or 'error' in response:continue
                witness={'requests':str(trace),'requests_sha256':snapshots[trace],
                         'events':str(event_file),'events_sha256':snapshots[event_file],
                         'launch':str(launch_file),'launch_sha256':snapshots[launch_file],
                         'session':index,'request_id':request['id'],
                         'result_sha256':hashlib.sha256(json.dumps(response['result'],sort_keys=True).encode()).hexdigest()}
                observed.setdefault(request['method'],[]).append(witness)
        if any(sha(path)!=digest for path,digest in snapshots.items()):raise ValueError('Native RPC evidence changed during reconstruction')
    except (OSError,ValueError,KeyError,TypeError) as failure:
        error(str(failure));observed={}
    return observed,sessions,errors


def regression_roots(root,artifact,verdict):
    """Log pointers may only name declared roots, never arbitrary workspace files."""
    root=Path(root).resolve()
    allowed=[Path(value).resolve() for value in artifact.get('evidence_roots',[])]
    require(allowed and comparison_path(root) in [comparison_path(path) for path in allowed],'Regression must declare its evidence roots')
    roots={root}
    for gate in verdict['gates']:
        require(gate.get('exit')==0,'A failed gate cannot supply positive RPC coverage')
        path=(root/gate['log']).resolve()
        require(comparison_path(path).is_relative_to(comparison_path(root)),'Regression log escaped its run')
        log_text=path.read_text(encoding='utf-8')
        # This gate verifies the direct Gateway/NativeTransport search protocol.
        # It has no native Peer/RPC producer, so its document-fetch evidence is
        # not a source of RPC witnesses (and cannot expand the allowed roots).
        if gate.get('gate') in NON_RPC_PRODUCERS:continue
        for found in re.findall(r'^EVIDENCE (.+)$',log_text,re.MULTILINE):
            evidence=Path(found.strip()).resolve()
            require(any(comparison_path(evidence).is_relative_to(comparison_path(base)) for base in allowed),'Log evidence escaped its declared roots: '+str(evidence))
            require(evidence.is_dir(),'Declared completed evidence root is missing: '+str(evidence))
            roots.add(evidence)
    return roots


def main():
    p=argparse.ArgumentParser()
    for name in ['regression','methods','local-account','entry','output']:p.add_argument('--'+name,type=Path,required=True)
    for name in ['regression-source','offline-regression']:p.add_argument('--'+name,type=Path)
    revalidation=p.add_mutually_exclusive_group()
    for name in ['terminal-regression','oauth-regression']:revalidation.add_argument('--'+name,type=Path)
    p.add_argument('--windows-sandbox',type=Path)
    p.add_argument('--windows-command-controls',type=Path)
    args=p.parse_args()
    entry=args.entry.resolve();release=entry.parent.parent.parent
    metadata=load(release/'installation.json');native=load(release/'native-runtime.json');scope=qualification_scope(native)
    require(entry==console_entry(release/'venv') and valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native),'Selected installation failed integrity checks')
    entry_sha=sha(entry)
    verifier_hashes={name:sha(Path(__file__).with_name(name)) for name in VERIFIERS}
    artifact=load(args.regression/'artifact.json');verdict=load(args.regression/'verdict.json')
    require(artifact.get('installation')==metadata and artifact.get('native_manifest')==native,'Regression belongs to another installation')
    replacement=args.terminal_regression or args.oauth_regression
    require(replacement is None or (args.regression_source and args.offline_regression),
            'Replacement requires an explicit original producer snapshot and current offline supplement')
    _,lineage=validate_standard_gates(artifact,verdict,replacement,revalidation_scope='oauth' if args.oauth_regression else 'terminal')
    terminal_lineage=None if args.oauth_regression else lineage;oauth_lineage=lineage if args.oauth_regression else None
    if replacement:
        validate_regression_source(artifact,metadata,args.regression_source,terminal_revalidated=args.terminal_regression is not None,oauth_revalidated=args.oauth_regression is not None)
        if args.oauth_regression:validate_offline_run(args.regression,metadata,native,scope,producer_snapshot=args.regression_source,oauth_revalidated=True)
        validate_offline_run(args.offline_regression,metadata,native,scope,current=True)
    if args.terminal_regression:
        for gate in TERMINAL_GATES:
            validate_terminal_qa(gate_evidence(args.terminal_regression,gate),entry,metadata,native,artifact['tui_sandbox_mode'],interrupt=gate=='tui-interrupt')
    require(artifact.get('target')==scope['target'] and artifact.get('required_local_methods')==scope['local_methods']
            and artifact.get('baseline_matrix_sha256')==scope['baseline_matrix_sha256'],'Regression applicability differs from frozen target scope')
    method_artifact=load(args.methods/'artifact.json');method_verdict=load(args.methods/'verdict.json')
    require(method_artifact.get('installation')==metadata and method_artifact.get('native_manifest')==native
            and method_artifact.get('target')==scope['target'] and method_artifact.get('baseline_matrix_sha256')==scope['baseline_matrix_sha256']
            and method_verdict.get('passed') is True and method_verdict.get('verifier_and_artifact_unchanged') is True,
            'Per-method frozen-artifact evidence does not match this target')
    replaced=OAUTH_GATES if args.oauth_regression else TERMINAL_GATES
    original_gates=[row for row in verdict['gates'] if not replacement or row['gate'] not in replaced]
    roots={root:(str(entry),entry_sha) for root in regression_roots(args.regression,artifact,{**verdict,'gates':original_gates})}
    excluded=superseded_terminal_roots(args.regression) if args.terminal_regression else ()
    if args.terminal_regression:
        after=load(args.terminal_regression/'verdict.json');replacement=load(args.terminal_regression/'artifact.json')
        roots.update({root:(str(entry),entry_sha) for root in regression_roots(args.terminal_regression,replacement,after)})
    if args.oauth_regression:
        excluded=oauth_evidence_roots(args.regression)
        roots.update({root:(str(entry),entry_sha) for root in oauth_evidence_roots(args.oauth_regression)})
    roots[args.methods.resolve()]=(str(entry),entry_sha)
    supplemental={}
    def lifecycle(path,label,relocated=False):
        identity=load(path/'artifact.json');proof=load(path/'verdict.json')
        supplemental[label]=installation_lineage(metadata,native,identity,relocated_windows=relocated)
        require(proof.get('passed') is True and proof.get('installation')==identity['installation']
                and proof.get('target')==scope['target'] and proof.get('verifier_and_artifact_unchanged') is True,
                label+' lifecycle frozen-artifact evidence failed')
        if relocated:require(proof.get('positive_lifecycle_exercised') is True,'Windows setup boundary cannot count as positive sandbox coverage')
        roots[path.resolve()]=(identity['entry'],identity['entry_sha256'])
    lifecycle(args.local_account,'local_account')
    if scope['target']=='x86_64-pc-windows-msvc':
        require(args.windows_sandbox is not None,'Windows target requires explicit positive sandbox lifecycle evidence')
        lifecycle(args.windows_sandbox,'windows_sandbox',True)
    else:require(args.windows_sandbox is None,'Windows sandbox evidence cannot qualify a non-Windows target')
    traces={}
    for root,identity in sorted(roots.items(),key=lambda item:len(item[0].parts)):
        for path in evidence_files(evidence_io_root(root),'client-requests.jsonl'):
            canonical=comparison_path(path)
            require(canonical.is_relative_to(comparison_path(root)),'RPC trace symlink escaped its declared root')
            if any(canonical.is_relative_to(comparison_path(value)) for value in excluded):continue
            # Alias spellings must not count the same native session twice.
            # Keep the original path for I/O, including genuinely long paths.
            traces[canonical]=(path,identity)
    observations={};sessions=0;errors=[]
    for _,(path,identity) in sorted(traces.items()):
        found,count,failures=trace_observations(path,*identity,scope['target']);sessions+=count;errors.extend(failures)
        for method,rows in found.items():observations.setdefault(method,[]).extend(rows)
    method_boundaries=validate_method_platform_boundaries(method_verdict,args.methods,native)
    boundaries=validate_windows_command_controls(args.windows_command_controls,native,method_boundaries)
    local=set(scope['local_methods'])-set(boundaries);missing=sorted(local-observations.keys())
    unchanged=(sha(entry)==entry_sha and load(release/'installation.json')==metadata
               and valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native)
               and all(sha(Path(__file__).with_name(name))==digest for name,digest in verifier_hashes.items()))
    report={'passed':not missing and not errors and unchanged,'entry':str(entry),'entry_sha256':entry_sha,'source_id':metadata['source_id'],
            'verifier_sha256':verifier_hashes,'verifier_and_artifact_unchanged':unchanged,
            'installation':metadata,'target':scope['target'],'baseline_matrix_sha256':scope['baseline_matrix_sha256'],
            'scope':'Matched successful native RPC responses; scenario and per-method postconditions remain separate required evidence.',
            'non_rpc_producers':NON_RPC_PRODUCERS,
            'terminal_revalidation':terminal_lineage,'oauth_revalidation':oauth_lineage,
            'supplemental_lifecycle_lineage':supplemental,'local_methods_observed':len(local & observations.keys()),
            'local_methods_expected':len(local),'configuration_boundaries':boundaries,'missing':missing,'trace_errors':errors,'native_client_sessions':sessions,
            'methods':{method:observations[method] for method in sorted(local) if method in observations}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key not in {'methods','trace_errors','installation','supplemental_lifecycle_lineage'}}))
    require(report['passed'],'Positive RPC coverage is incomplete; inspect '+str(args.output))


if __name__=='__main__':main()
