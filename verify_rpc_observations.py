"""Reconstruct positive RPC observations from request/response pairs, never matrix flags."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def main():
    p=argparse.ArgumentParser();p.add_argument('--regression',type=Path,required=True);p.add_argument('--methods',type=Path,required=True);p.add_argument('--entry',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    source=Path(__file__).resolve().parent;workspace=source.parent;entry=args.entry.resolve()
    roots={args.methods.resolve()}
    verdict=json.loads((args.regression/'verdict.json').read_text());assert verdict['passed']
    for gate in verdict['gates']:
        assert gate['exit']==0
        log=(args.regression/gate['log']).read_text()
        for found in re.findall(r'^EVIDENCE (.+)$',log,re.MULTILINE):
            root=Path(found.strip()).resolve();assert root.is_relative_to(workspace/'delivery');roots.add(root)
    traces={path for root in roots for path in root.rglob('client-requests.jsonl')}
    observations={};sessions=0;errors=[]
    for trace in sorted(traces):
        requests=[]
        for line in trace.read_text().splitlines():
            request=json.loads(line)
            if request.get('method')=='initialize':requests.append([])
            if requests:requests[-1].append(request)
        events=sorted(trace.parent.glob('events-*.json'));launches=sorted(trace.parent.glob('launch-*.json'))
        if len(requests)!=len(events) or len(events)!=len(launches):
            errors.append({'root':str(trace.parent),'segments':len(requests),'event_files':len(events),'launches':len(launches)});continue
        for index,(sent,event_file,launch_file) in enumerate(zip(requests,events,launches)):
            launch=json.loads(launch_file.read_text())
            if launch['resolved_entry']!=str(entry):continue
            assert launch['entry_sha256']==hashlib.file_digest(entry.open('rb'),'sha256').hexdigest()
            received={r['id']:r for r in json.loads(event_file.read_text()) if 'id' in r and 'method' not in r}
            sessions+=1
            for request in sent:
                if 'method' not in request or 'id' not in request:continue
                response=received.get(request['id'],{})
                if 'result' not in response or 'error' in response:continue
                method=request['method'];witness={'requests':str(trace.relative_to(workspace)),'events':str(event_file.relative_to(workspace)),
                    'session':index,'request_id':request['id'],'result_sha256':hashlib.sha256(json.dumps(response['result'],sort_keys=True).encode()).hexdigest()}
                observations.setdefault(method,[]).append(witness)
    matrix=json.loads((source/'reports/runtime-method-matrix.json').read_text())
    local={r['method'] for r in matrix if r['scope']=='local_runtime_surface'}
    missing=sorted(local-observations.keys())
    report={'passed':not missing and not errors,'entry':str(entry),'source_id':json.loads((entry.parent.parent.parent/'installation.json').read_text())['source_id'],
            'scope':'Matched successful native RPC responses; scenario and per-method postconditions are separate required evidence.',
            'local_methods_observed':len(local-observations.keys()),'local_methods_expected':len(local),'missing':missing,'trace_errors':errors,'native_client_sessions':sessions,
            'methods':{m:observations[m] for m in sorted(local) if m in observations}}
    report['local_methods_observed']=len(local & observations.keys())
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in {'methods','trace_errors'}}));assert report['passed'],errors


if __name__=='__main__':main()
