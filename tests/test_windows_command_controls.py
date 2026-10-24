"""Three fixed Windows control boundaries require their own live native comparison."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
import assemble_release_acceptance as acceptance

ACTOR = 'import ctypes as C,ctypes.wintypes as W,json,os,sys,time\nfrom pathlib import Path\nready=Path(sys.argv[1]);outside=Path(sys.argv[2]);nonce=sys.argv[3]\nk=C.WinDLL("kernel32",use_last_error=True);a=C.WinDLL("advapi32",use_last_error=True)\nk.GetCurrentProcess.restype=W.HANDLE;k.CloseHandle.argtypes=[W.HANDLE]\na.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,C.POINTER(W.HANDLE)]\na.IsTokenRestricted.argtypes=[W.HANDLE];a.IsTokenRestricted.restype=W.BOOL\na.GetTokenInformation.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.POINTER(W.DWORD)]\na.ConvertSidToStringSidW.argtypes=[C.c_void_p,C.POINTER(W.LPWSTR)]\nk.LocalFree.argtypes=[C.c_void_p]\ntoken=W.HANDLE();assert a.OpenProcessToken(k.GetCurrentProcess(),8,C.byref(token))\nsize=W.DWORD();a.GetTokenInformation(token,1,None,0,C.byref(size));buf=C.create_string_buffer(size.value)\nassert a.GetTokenInformation(token,1,buf,size.value,C.byref(size));sid=W.LPWSTR()\nassert a.ConvertSidToStringSidW(C.cast(buf,C.POINTER(C.c_void_p))[0],C.byref(sid))\nsid_text=sid.value;k.LocalFree(sid)\nrestricted=bool(a.IsTokenRestricted(token));admin=bool(C.windll.shell32.IsUserAnAdmin());k.CloseHandle(token)\ntry:outside.write_text("MUST_NOT_WRITE");denied=False\nexcept PermissionError:denied=True\nvalue={"nonce":nonce,"pid":os.getpid(),"sid":sid_text,"is_token_restricted":restricted,"is_admin":admin,"outside_write_denied":denied,"time":time.time()}\npending=ready.with_suffix(".pending");pending.write_text(json.dumps(value));os.replace(pending,ready)\nprint("READY "+nonce,flush=True);time.sleep(6);print("DONE "+nonce,flush=True)\n'
METHODS=('command/exec/write','command/exec/resize','command/exec/terminate')
GUARD='772a643ee76491d9103698ad64872af85d273529a6da74e48e2af8861b557738'
OFFICIAL='8c24181c61c94ce8982ab6a5a9408a068f1385d1427c6d424edd5cd330fb6994'
ERROR={'code':-32600,'message':'command/exec/write, command/exec/terminate, and command/exec/resize are not supported for windows sandbox processes'}

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')

@pytest.fixture
def proof_fixture(tmp_path,monkeypatch):
    root=tmp_path/'proof';root.mkdir()
    original_sha=acceptance.sha
    def sha(path):
        data=Path(path).read_bytes()
        if data==b'FIXTURE_OFFICIAL':return OFFICIAL
        if data==b'FIXTURE_GUARD':return GUARD
        return original_sha(path)
    monkeypatch.setattr(acceptance,'sha',sha)
    for name,data in [('official.exe',b'FIXTURE_OFFICIAL'),('candidate.exe',b'candidate'),('python.exe',b'python'),
                      ('upstream.rs',b'FIXTURE_GUARD'),('candidate.rs',b'FIXTURE_GUARD'),('worker.py',b'frozen fixture controller')]:
        (root/name).write_bytes(data)
    resources={name:{'owned':True} for name in ['namespace','expected_resources','local','wfp','registry']}
    save(root/'resources-before.json',resources);save(root/'resources-after.json',resources)
    native={'target':'x86_64-pc-windows-msvc','upstream_commit':acceptance.BASELINE_COMMIT,'files':{'bin/codex.exe':sha(root/'candidate.exe')}}
    proof={'passed':True,'scope':{'sandbox_mode':'workspace-write','windows_backend':'unelevated','network_access':False},
           'model_requests':0,'resource_snapshot_unchanged':True,'worker_sha256':sha(root/'worker.py'),'finished':110.0,
           'source':{'upstream':str(root/'upstream.rs'),'candidate':str(root/'candidate.rs'),'sha256':GUARD,'guard_byte_identical':True},
           'python':{'path':str(root/'python.exe'),'sha256':sha(root/'python.exe')},'cases':{}}
    raw={}
    for ordinal,label in enumerate(['official','candidate'],1):
        case_root=root/label;work=case_root/'work';work.mkdir(parents=True)
        nonce=str(ordinal)*32;pid='control-'+nonce;binary=str(root/(label+'.exe'))
        config={'sandbox_mode':'workspace-write','windows':{'sandbox':'unelevated'},'approval_policy':'on-request'}
        command={'command':[str(root/'python.exe'),'-I','-u','-c',ACTOR,str(work/'command-ready.json'),str(case_root/'outside-canary.txt'),nonce],
                 'cwd':str(work),'processId':pid,'sandboxPolicy':{'type':'workspaceWrite','writableRoots':[str(work)],'networkAccess':False,
                 'excludeTmpdirEnvVar':True,'excludeSlashTmp':True},'timeoutMs':30000}
        marker={'nonce':nonce,'pid':ordinal*10+1,'sid':'S-1-5-21-1-2-3-1001','is_token_restricted':True,'is_admin':False,'outside_write_denied':True,'time':100.0}
        result={'exitCode':0,'stdout':'READY '+nonce+'\r\nDONE '+nonce+'\r\n','stderr':''}
        calls=[{'id':1,'method':'initialize','params':{}},{'method':'initialized','params':{}},
               {'id':2,'method':'config/read','params':{}},{'id':3,'method':'config/read','params':{}},
               {'id':4,'method':'command/exec','params':command}]
        events=[{'id':1,'result':{}},{'id':2,'result':{'config':config}},{'id':3,'result':{'config':config}}]
        controls=[]
        for index,method in enumerate(METHODS,5):
            params={'processId':pid}
            if method.endswith('/write'):params.update(deltaBase64='Qk9VTkRBUllfV1JJVEUK',closeStdin=False)
            if method.endswith('/resize'):params['size']={'rows':32,'cols':120}
            calls.append({'id':index,'method':method,'params':params});events.append({'id':index,'error':deepcopy(ERROR)})
            controls.append({'method':method,'params':params,'response':str(case_root/(method.rsplit('/',1)[-1]+'-response.json')),
                             'rpc_id':index,'requested_at':float(101+index-5),'command_was_active':True})
        events.append({'id':4,'result':result})
        launch={'command':[binary],'server_args':['-c','mcp_servers={}','app-server','--strict-config'],'resolved_entry':binary,
                'entry_sha256':sha(binary),'runtime_home':str(case_root/'home'),'physical_workspace':str(work),'fixture_sandbox_mode':'workspace-write'}
        save(case_root/'launch-1.json',launch);(case_root/'outside-canary.txt').write_text('UNCHANGED')
        case={'binary':binary,'binary_sha256':sha(binary),'native_identity':{'pid':ordinal*10,'birth':'windows:'+str(ordinal*10)},
              'process_id':pid,'controls':controls,'command_request':command,'ready':marker,'command_identity':{'pid':marker['pid'],'birth':'windows:'+str(marker['pid'])},
              'command_response':str(case_root/'command-response.json'),'command_rpc_id':4,'command_exit_code':0,'native_exit_code':0,
              'command_gone':True,'native_gone':True,'peer_reader_stopped':True,'raw_client_requests':str(case_root/'client-requests.jsonl'),'setup_state_absent':True}
        proof['cases'][label]=case;raw[label]={'calls':calls,'events':events,'config':config,'marker':marker,'result':result}
    def sync():
        for label,case in proof['cases'].items():
            p=root/label;data=raw[label]
            save(p/'config-readback.json',data['config']);case['config_sha256']=sha(p/'config-readback.json')
            save(p/'command-request.json',case['command_request']);save(p/'work/command-ready.json',data['marker'])
            save(p/'command-response.json',data['result']);case['command_response_sha256']=sha(p/'command-response.json')
            (p/'client-requests.jsonl').write_text('\n'.join(json.dumps(row) for row in data['calls'])+'\n',encoding='utf-8')
            case['raw_client_requests_sha256']=sha(p/'client-requests.jsonl')
            save(p/'events-1.json',data['events']);case['raw_native_event_files']={str(p/'events-1.json'):sha(p/'events-1.json')}
            for control in case['controls']:
                reply=next((row for row in data['events'] if row.get('id')==control['rpc_id']),{'id':control['rpc_id']})
                save(Path(control['response']),reply);control['response_sha256']=sha(control['response'])
            save(p/'case-result.json',case)
        save(root/'reference.json',proof)
    sync()
    return root,proof,native,raw,sync,{'command/exec/terminate':{'scope':{'windows_backend':'elevated'},'positive_execution_claimed':False}}

@pytest.mark.parametrize('change',['none','missing_proof','missing_role','wrong_mode','inference','wrong_binary','guard_changed',
    'wrong_config','wrong_launch','missing_control','duplicate_id','missing_reply','duplicate_reply','positive_reply',
    'early_completion','no_live_command','not_restricted','admin','outside_write','wrong_actor','wrong_nonce','unknown_rpc',
    'foreign_file','nonzero_command','nonzero_owner','running_owner','resources_changed','missing_legacy'])
def test_fixed_native_controls_never_become_implicit_positive_or_blanket_exemptions(proof_fixture,change):
    root,proof,native,raw,sync,legacy=proof_fixture;case=proof['cases']['candidate'];data=raw['candidate']
    if change=='missing_role':proof['cases'].pop('official')
    elif change=='wrong_mode':proof['scope']['windows_backend']='elevated'
    elif change=='inference':proof['model_requests']=1
    elif change=='wrong_binary':(root/'official.exe').write_bytes(b'foreign binary')
    elif change=='guard_changed':(root/'upstream.rs').write_bytes(b'foreign guard')
    elif change=='wrong_config':data['config']['windows']['sandbox']='disabled'
    elif change=='wrong_launch':
        p=root/'candidate/launch-1.json';value=json.loads(p.read_text());value['server_args'].append('--full-access');save(p,value)
    elif change=='missing_control':case['controls'].pop()
    elif change=='duplicate_id':data['calls'][-1]['id']=5
    elif change=='missing_reply':data['events'].pop(3)
    elif change=='duplicate_reply':data['events'].append(deepcopy(data['events'][3]))
    elif change=='positive_reply':data['events'][3]={'id':5,'result':{}}
    elif change=='early_completion':data['events'].insert(3,data['events'].pop())
    elif change=='no_live_command':case['controls'][0]['command_was_active']=False
    elif change=='not_restricted':data['marker']['is_token_restricted']=False
    elif change=='admin':data['marker']['is_admin']=True
    elif change=='outside_write':(root/'candidate/outside-canary.txt').write_text('changed')
    elif change=='wrong_actor':case['command_request']['command'][4]='fake ready writer'
    elif change=='wrong_nonce':case['command_request']['command'][-1]='f'*32
    elif change=='unknown_rpc':
        data['calls'].append({'id':99,'method':'turn/start','params':{}});data['events'].append({'id':99,'result':{}})
    elif change=='foreign_file':case['controls'][0]['response']=str(root.parent/'foreign.json')
    elif change=='nonzero_command':data['result']['exitCode']=1
    elif change=='nonzero_owner':case['native_exit_code']=1
    elif change=='running_owner':case['native_gone']=False
    elif change=='resources_changed':
        values=json.loads((root/'resources-before.json').read_text());values['local']={'changed':True};save(root/'resources-after.json',values)
    elif change=='missing_legacy':legacy={}
    sync()
    if change=='none':
        result=acceptance.validate_windows_command_controls(root/'reference.json',native,legacy)
        assert set(result)==set(METHODS) and all(row['positive_execution_claimed'] is False for row in result.values())
        assert result[METHODS[-1]]['additional_elevated_terminate_evidence']==legacy[METHODS[-1]]
    else:
        with pytest.raises(SystemExit):
            acceptance.validate_windows_command_controls(None if change=='missing_proof' else root/'reference.json',native,legacy)

@pytest.mark.parametrize('target',['aarch64-apple-darwin','x86_64-unknown-linux-gnu'])
def test_non_windows_scope_is_unchanged_and_refuses_windows_proof(tmp_path,target):
    assert acceptance.validate_windows_command_controls(None,{'target':target},{})=={}
    with pytest.raises(SystemExit):acceptance.validate_windows_command_controls(tmp_path/'unrelated',{'target':target},{})
