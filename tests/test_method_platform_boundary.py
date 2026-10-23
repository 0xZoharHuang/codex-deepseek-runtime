"""Evidence consumer must verify a native configuration limit without counting execution."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
import assemble_release_acceptance as acceptance
from verify_remaining_local import WINDOWS_COMMAND_SCOPE, WINDOWS_STREAM_ERROR, WINDOWS_CONTROL_ERROR

def fixture(tmp_path,monkeypatch):
    root=tmp_path/'run';case=root/'processes';case.mkdir(parents=True)
    work=tmp_path/'work';work.mkdir()
    def save(path,value):
        path.write_text(json.dumps(value),encoding='utf-8')
        return path
    original_sha=acceptance.sha
    fixed={}
    def fixed_file(name,digest):
        path=tmp_path/name;path.write_text(name);fixed[str(path)]=digest;return str(path)
    monkeypatch.setattr(acceptance,'sha',lambda path:fixed.get(str(path)) or original_sha(path))
    source_hash='772a643ee76491d9103698ad64872af85d273529a6da74e48e2af8861b557738'
    native={'target':'x86_64-pc-windows-msvc','files':{'bin/codex.exe':'final-native'}}
    policy={'type':'workspaceWrite','writableRoots':[str(work)],'networkAccess':False,'excludeTmpdirEnvVar':True,'excludeSlashTmp':True}
    nonce='a'*32;sid='S-1-5-21-1-2-3-1002'
    ready=save(work/('control-ready-'+nonce+'.json'),{'nonce':nonce,'sid':sid,'is_admin':False})
    proof={'passed':True,'scope':WINDOWS_COMMAND_SCOPE,'official_terminate_os_execution':False,
           'source':{'upstream_commit':acceptance.BASELINE_COMMIT,'byte_identical':True,'sha256':source_hash,
                     'upstream_file':fixed_file('upstream.rs',source_hash),'patched_file':fixed_file('patched.rs',source_hash)},'cases':{}}
    for label,digest in [('official','8c24181c61c94ce8982ab6a5a9408a068f1385d1427c6d424edd5cd330fb6994'),('patched','final-native')]:
        resp=save(tmp_path/(label+'-reply.json'),{'id':2,'error':WINDOWS_STREAM_ERROR})
        cfg=save(tmp_path/(label+'-config.json'),{'config':{'sandbox_mode':'workspace-write','windows':{'sandbox':'elevated'}}})
        proof['cases'][label]={'binary':fixed_file(label+'.exe',digest),'binary_sha256':digest,'setup_state_absent':True,
                              'response':str(resp),'response_sha256':original_sha(resp),'config':str(cfg),'config_sha256':original_sha(cfg)}
    reference=save(tmp_path/'reference.json',proof)
    detail={'target':native['target'],'boundary_id':'windows_restricted_token_command_exec_control_unsupported',
            'scope':WINDOWS_COMMAND_SCOPE,'workspace':str(work),'sandbox_policy':policy,
            'streaming_error':WINDOWS_STREAM_ERROR,'terminate_error':WINDOWS_CONTROL_ERROR,
            'reference':{'path':str(reference),'sha256':original_sha(reference)},
            'active_command':{'process_id':'native-windows-boundary','ready_nonce':nonce,'ready_file_sha256':original_sha(ready),
                              'exit_code':0,'stdout':nonce+'\n','sid':sid,'owner_sid':'S-1-5-21-1-2-3-1001','is_admin':False}}
    raw={'method':'command/exec/terminate','status':'platform_boundary_verified','detail':detail}
    report={'methods':{raw['method']:{**raw,'evidence':'processes/results.json'}}}
    requests=[{'id':1,'method':'command/exec','params':{'processId':'stream-boundary','cwd':str(work),'sandboxPolicy':deepcopy(policy)}},
              {'id':2,'method':'command/exec','params':{'processId':'native-windows-boundary','cwd':str(work),'sandboxPolicy':deepcopy(policy)}},
              {'id':3,'method':raw['method'],'params':{'processId':'native-windows-boundary'}}]
    events=[{'id':1,'error':WINDOWS_STREAM_ERROR},{'id':2,'result':{'exitCode':0,'stdout':nonce+'\n'}},{'id':3,'error':WINDOWS_CONTROL_ERROR}]
    def persist():
        save(case/'results.json',[raw]);save(case/'events-100.json',events)
        (case/'client-requests.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in requests),encoding='utf-8')
    persist()
    return root,native,report,requests,events,raw,proof,reference,persist,save

def test_verified_boundary_is_never_positive_execution(tmp_path,monkeypatch):
    root,native,report,*_=fixture(tmp_path,monkeypatch)
    proof=acceptance.validate_method_platform_boundaries(report,root,native)
    assert set(proof)=={'command/exec/terminate'}
    assert proof['command/exec/terminate']['positive_execution_claimed'] is False
    assert len(proof['command/exec/terminate']['evidence_sha256'])==11

@pytest.mark.parametrize('mutation',['missing_boundary','extra_boundary','wrong_platform','missing_reply','duplicate_reply',
                                    'success_instead_of_guard','command_failed','different_workspace','widened_roots',
                                    'child_admin','claimed_official_execution','reference_tampered','source_changed'])
def test_boundary_consumer_rejects_false_or_unmatched_evidence(tmp_path,monkeypatch,mutation):
    root,native,report,requests,events,raw,proof,reference,persist,save=fixture(tmp_path,monkeypatch)
    if mutation=='missing_boundary':report['methods']={}
    elif mutation=='extra_boundary':report['methods']['command/exec/resize']=deepcopy(raw)
    elif mutation=='wrong_platform':native['target']='x86_64-unknown-linux-gnu'
    elif mutation=='missing_reply':events.pop()
    elif mutation=='duplicate_reply':events.append(deepcopy(events[-1]))
    elif mutation=='success_instead_of_guard':events[-1]={'id':3,'result':{}}
    elif mutation=='command_failed':events[1]['result']['exitCode']=1
    elif mutation=='different_workspace':requests[1]['params']['cwd']=str(tmp_path/'elsewhere')
    elif mutation=='widened_roots':requests[1]['params']['sandboxPolicy']['writableRoots'].append('C:/')
    elif mutation=='child_admin':
        ready=Path(raw['detail']['workspace'])/('control-ready-'+raw['detail']['active_command']['ready_nonce']+'.json')
        data=json.loads(ready.read_text());data['is_admin']=True;save(ready,data)
        raw['detail']['active_command']['ready_file_sha256']=acceptance.sha(ready)
    elif mutation=='claimed_official_execution':
        proof['official_terminate_os_execution']=True;save(reference,proof);raw['detail']['reference']['sha256']=acceptance.sha(reference)
    elif mutation=='reference_tampered':reference.write_text('{}')
    elif mutation=='source_changed':
        proof['source']['upstream_commit']='wrong';save(reference,proof);raw['detail']['reference']['sha256']=acceptance.sha(reference)
    persist()
    with pytest.raises(SystemExit):acceptance.validate_method_platform_boundaries(report,root,native)

def test_other_platform_has_no_windows_boundary(tmp_path):
    assert acceptance.validate_method_platform_boundaries({'methods':{}},tmp_path,{'target':'x86_64-unknown-linux-gnu'})=={}
