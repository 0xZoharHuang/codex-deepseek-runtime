"""Per-method native effects, with failures preserved rather than counted as coverage."""
from install import python_entry
import argparse
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import socket
import threading
import time
import traceback
import uuid
from urllib.parse import unquote
from verify_live import Peer,text,prepare_workspace,workspace_path
from process_acceptance import python_command,shell_command


class Audit:
    def __init__(self,output,entry):
        self.root=output.resolve()/str(uuid.uuid4())
        (self.root/'home').mkdir(parents=True);(self.root/'work').mkdir()
        (self.root/'home/config.toml').write_text('model_reasoning_effort="low"\nweb_search="disabled"\n[features]\napps=false\nplugins=false\nmemories=false\nmulti_agent=false\n')
        self.peer=Peer(self.root,command=[str(entry)],server_args=['-c','mcp_servers={}','app-server','--strict-config'])
        self.results=[];self.cleanups=[]

    def verify(self,method,operation,assertion,success_status='passed'):
        started=time.monotonic()
        try:
            detail=operation()
            if not assertion(detail):raise AssertionError('Postcondition failed: '+repr(detail)[:1000])
            row={'method':method,'status':success_status,'detail':detail}
        except Exception as e:row={'method':method,'status':'failed','error':str(e),'traceback':traceback.format_exc()}
        row['seconds']=round(time.monotonic()-started,2);self.results.append(row)
        (self.root/'results.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in row.items() if k not in {'detail','traceback'}},ensure_ascii=False),flush=True)
        return row

    def call(self,method,params):return self.peer.call(method,params)
    def backend_boundary(self,method,operation,assertion,reason):
        row=self.verify(method,operation,assertion,success_status='backend_boundary_verified')
        if row['status']=='backend_boundary_verified':row.update(reason=reason)
        (self.root/'results.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2))
    def close(self):
        self.peer.close()
        for cleanup in reversed(self.cleanups):cleanup()
        print('EVIDENCE',self.root,flush=True)


def catalog(a):
    a.verify('server/diagnostics',lambda:a.call('server/diagnostics',{}),lambda r:r['process']['id']>0 and r['process']['residentMemoryBytes']>0)
    a.verify('model/list',lambda:a.call('model/list',{'includeHidden':True}),lambda r:any(x.get('id')=='deepseek/deepseek-v4.1-flash-1017' or x.get('model')=='deepseek/deepseek-v4.1-flash-1017' for x in r['data']))
    a.verify('modelProvider/capabilities/read',lambda:a.call('modelProvider/capabilities/read',{}),lambda r:bool(r))
    a.verify('experimentalFeature/list',lambda:a.call('experimentalFeature/list',{}),lambda r:any(x['name']=='code_mode' for x in r['data']))
    def features():
        changed=a.call('experimentalFeature/enablement/set',{'enablement':{'mentions_v2':True}})
        after=a.call('experimentalFeature/list',{});return {'changed':changed,'features':after['data']}
    a.verify('experimentalFeature/enablement/set',features,lambda r:r['changed']['enablement']=={'mentions_v2':True} and any(x['name']=='mentions_v2' and x['enabled'] is True for x in r['features']))
    a.verify('permissionProfile/list',lambda:a.call('permissionProfile/list',{'cwd':str(a.root/'work')}),lambda r:bool(r['data']))
    a.verify('collaborationMode/list',lambda:a.call('collaborationMode/list',{}),lambda r:bool(r['data']))
    a.verify('mock/experimentalMethod',lambda:a.call('mock/experimentalMethod',{'value':'ROUNDTRIP_'+a.root.name}),lambda r:r['echoed']=='ROUNDTRIP_'+a.root.name)
    a.verify('configRequirements/read',lambda:a.call('configRequirements/read',{}),lambda r:r['requirements']['modelProvider']=='deepseek_runtime' and unquote(r['requirements']['sqliteHome'])==unquote((a.root/'home').as_uri()))
    def config():
        result=a.call('config/batchWrite',{'edits':[{'keyPath':'model_reasoning_effort','value':'medium','mergeStrategy':'replace'},{'keyPath':'history.max_bytes','value':1234567,'mergeStrategy':'replace'}]})
        import tomllib
        return {'result':result,'config':tomllib.loads((a.root/'home/config.toml').read_text())}
    a.verify('config/batchWrite',config,lambda r:r['config']['model_reasoning_effort']=='medium' and r['config']['history']['max_bytes']==1234567)
    a.verify('memory/status',lambda:a.call('memory/status',{'minConsolidatedThreads':1}),lambda r:r['v2ConsolidatedThreads']==0 and r['v2Ready'] is False)
    memory=a.root/'home/memories';memory.mkdir(exist_ok=True);(memory/'memory_summary.md').write_text('DISPOSABLE_MEMORY_'+a.root.name)
    a.verify('memory/reset',lambda:a.call('memory/reset',{}),lambda r:not (memory/'memory_summary.md').exists())
    hooks=a.root/'home/hooks.json'
    hooks.write_text(json.dumps({'hooks':{'PreToolUse':[{'matcher':'Bash','hooks':[{'type':'command','command':'printf HOOK_ENUMERATED'}]}]}}))
    a.verify('hooks/list',lambda:a.call('hooks/list',{'cwds':[str(a.root/'work')]}),lambda r:'HOOK_ENUMERATED' in json.dumps(r))
    extra=a.root/'extra-skills';skill=extra/'extra-root-audit';skill.mkdir(parents=True)
    (skill/'SKILL.md').write_text('---\nname: extra-root-audit\ndescription: Exact additional-root discovery fixture.\n---\nRead-only audit.\n')
    def roots():
        result=a.call('skills/extraRoots/set',{'extraRoots':[str(extra)]})
        listed=a.call('skills/list',{'cwds':[str(a.root/'work')],'forceReload':True})
        return {'result':result,'skills':listed}
    a.verify('skills/extraRoots/set',roots,lambda r:'extra-root-audit' in json.dumps(r['skills']))
    def skill_config():
        result=a.call('skills/config/write',{'path':str(skill/'SKILL.md'),'enabled':False})
        listed=a.call('skills/list',{'cwds':[str(a.root/'work')],'forceReload':True})
        return {'result':result,'skills':listed}
    a.verify('skills/config/write',skill_config,lambda r:any(x.get('name')=='extra-root-audit' and x.get('enabled') is False for group in r['skills']['data'] for x in group['skills']))
    target=a.root/'work/distinctive_runtime_fixture_672.txt';target.write_text('FUZZY_ACTUAL_FILE')
    a.verify('fuzzyFileSearch',lambda:a.call('fuzzyFileSearch',{'query':'distinctive_runtime_fixture','roots':[str(a.root/'work')]}),lambda r:'distinctive_runtime_fixture_672.txt' in json.dumps(r['files']))
    sid='session_'+a.root.name
    a.verify('fuzzyFileSearch/sessionStart',lambda:a.call('fuzzyFileSearch/sessionStart',{'sessionId':sid,'roots':[str(a.root/'work')]}),lambda r:r=={})
    def update():
        start=len(a.peer.events);r=a.call('fuzzyFileSearch/sessionUpdate',{'sessionId':sid,'query':'distinctive_runtime_fixture'})
        event=a.peer.wait(lambda e:e.get('method')=='fuzzyFileSearch/sessionUpdated' and 'distinctive_runtime_fixture_672.txt' in json.dumps(e),start,20)
        return {'response':r,'event':event}
    a.verify('fuzzyFileSearch/sessionUpdate',update,lambda r:'distinctive_runtime_fixture_672.txt' in json.dumps(r['event']))
    def stop():
        r=a.call('fuzzyFileSearch/sessionStop',{'sessionId':sid})
        try:a.call('fuzzyFileSearch/sessionUpdate',{'sessionId':sid,'query':'distinctive'});rejected=False
        except RuntimeError as e:rejected='not found' in str(e)
        return {'response':r,'subsequent_update_rejected':rejected}
    a.verify('fuzzyFileSearch/sessionStop',stop,lambda r:r['subsequent_update_rejected'])


def threads(a):
    thread=a.peer.new(historyMode='paginated');nonce='TRANSCRIPT_'+uuid.uuid4().hex
    a.peer.turn(thread,'Do not use tools or delegate. Reply exactly '+nonce)
    a.verify('thread/increment_elicitation',lambda:a.call('thread/increment_elicitation',{'threadId':thread}),lambda r:r['count']==1 and r['paused'] is True)
    a.verify('thread/decrement_elicitation',lambda:a.call('thread/decrement_elicitation',{'threadId':thread}),lambda r:r['count']==0 and r['paused'] is False)
    imported={}
    def project_import():
        result=a.call('project/import',{'idempotencyKey':a.root.name,'name':'Imported audit project','roots':[{'path':str(a.root/'work')}],'threads':[thread]})
        imported.update(result['project']);return {'result':result,'thread':a.call('thread/read',{'threadId':thread})['thread']}
    a.verify('project/import',project_import,lambda r:r['thread']['projectId']==r['result']['project']['id'])
    second=a.call('project/create',{'idempotencyKey':uuid.uuid4().hex,'name':'Other project','roots':[{'path':str(a.root/'work')} ]})['project']['id']
    def project_move():
        result=a.call('project/move',{'projectId':imported['id'],'beforeProjectId':second})
        projects=a.call('project/list',{})['data'];return {'result':result,'ids':[x['id'] for x in projects]}
    a.verify('project/move',project_move,lambda r:r['ids'].index(imported['id'])<r['ids'].index(second))
    section=a.call('threadSection/create',{'name':'Actual listed section'})['section']
    a.verify('threadSection/list',lambda:a.call('threadSection/list',{}),lambda r:any(x['id']==section['id'] for x in r['data']))
    def metadata():
        result=a.call('thread/metadata/update',{'threadId':thread,'projectId':imported['id'],'daybreakEnabled':True,'gitInfo':{'branch':'audit-branch','sha':'a'*40}})
        return {'result':result,'thread':a.call('thread/read',{'threadId':thread})['thread']}
    a.verify('thread/metadata/update',metadata,lambda r:r['thread']['gitInfo']['branch']=='audit-branch' and r['thread']['daybreakEnabled'] is True)
    a.verify('thread/search',lambda:a.call('thread/search',{'searchTerm':nonce}),lambda r:thread in json.dumps(r['data']))
    a.verify('thread/searchOccurrences',lambda:a.call('thread/searchOccurrences',{'threadId':thread,'searchTerm':nonce}),lambda r:nonce in json.dumps(r))
    def read_state():
        snapshot=a.call('thread/read',{'threadId':thread})['readState']
        unread=a.call('thread/readState/update',{'threadId':thread,'expectedRevision':snapshot['revision'],'operation':{'type':'unread'}})['readState']
        restored=a.call('thread/readState/update',{'threadId':thread,'expectedRevision':unread['revision'],'operation':{'type':'read'}})['readState']
        return {'unread':unread,'read':restored}
    a.verify('thread/readState/update',read_state,lambda r:r['unread']['firstUnread'] is not None and r['read']['firstUnread'] is None)
    turns=a.call('thread/turns/list',{'threadId':thread})['data'];first=turns[0]['id']
    entries=a.call('thread/items/list',{'threadId':thread,'turnId':first})['data']
    identifiers=[x['id'] if 'id' in x else x['item']['id'] for x in entries]
    a.verify('thread/items/read',lambda:a.call('thread/items/read',{'threadId':thread,'turnId':first,'itemIds':identifiers}),lambda r:nonce in json.dumps(r))
    a.verify('thread/timeline/list',lambda:a.call('thread/timeline/list',{'threadId':thread}),lambda r:nonce in json.dumps(r))
    injected='INJECTED_'+uuid.uuid4().hex
    def inject():
        result=a.call('thread/inject_items',{'threadId':thread,'items':[{'type':'message','role':'user','content':[{'type':'input_text','text':'The injected marker is '+injected}]}]})
        items=a.peer.turn(thread,'Without using tools or delegating, repeat the injected marker from the preceding user message.')
        return {'result':result,'answer':text(items)}
    a.verify('thread/inject_items',inject,lambda r:injected in r['answer'])
    target=a.root/'work/shell-effect.txt'
    writer=a.root/'work/shell_effect_writer.py'
    writer.write_text('from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text("SHELL_EFFECT")\n',encoding='utf-8')
    def shell():
        import shlex
        start=len(a.peer.events)
        result=a.call('thread/shellCommand',{'threadId':thread,'command':shell_command([python_command('')[0],'-I',str(writer),str(target)]),'timeoutMs':10000})
        end=time.monotonic()+10
        while not target.exists() and time.monotonic()<end:time.sleep(.02)
        return {'result':result,'content':target.read_text() if target.exists() else None}
    a.verify('thread/shellCommand',shell,lambda r:r['content']=='SHELL_EFFECT')
    def revert():
        before=a.call('thread/turns/list',{'threadId':thread})['data'];last=before[-1]['id']
        result=a.call('thread/revert',{'threadId':thread,'beforeTurnId':last})
        after=a.call('thread/turns/list',{'threadId':thread})['data']
        return {'result':result,'before':[x['id'] for x in before],'after':[x['id'] for x in after],'file':target.read_text()}
    a.verify('thread/revert',revert,lambda r:len(r['after'])<len(r['before']) and r['before'][-1] not in r['after'] and r['file']=='SHELL_EFFECT')
    def unsubscribe():
        result=a.call('thread/unsubscribe',{'threadId':thread})
        return {'result':result,'second':a.call('thread/unsubscribe',{'threadId':thread}),'loaded':a.call('thread/loaded/list',{})}
    a.verify('thread/unsubscribe',unsubscribe,lambda r:r['result']['status']=='unsubscribed' and r['second']['status']=='notSubscribed')
    def delete():
        a.call('thread/archive',{'threadId':thread})
        result=a.call('thread/delete',{'threadId':thread})
        listed=a.call('thread/list',{'archived':True,'modelProviders':['deepseek_runtime']})
        try:a.call('thread/read',{'threadId':thread});missing=False
        except RuntimeError:missing=True
        return {'result':result,'missing':missing,'archived':listed}
    a.verify('thread/delete',delete,lambda r:r['missing'] and thread not in json.dumps(r['archived']))


def process_stdout_lines(events,handle):
    chunks=[]
    for event in events:
        params=event.get('params',{})
        if event.get('method')=='process/outputDelta' and params.get('processHandle')==handle and params.get('stream')=='stdout':
            chunks.append(base64.b64decode(params['deltaBase64'],validate=True))
    # ConPTY emits terminal control sequences even for a plain Python print.
    # Strip complete OSC strings first (titles are not stdout text), then CSI
    # controls, after joining chunks so a split escape cannot become evidence.
    data=b''.join(chunks)
    data=re.sub(rb'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)',b'',data)
    data=re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]',b'',data)
    return data.splitlines()


def scoped_migration_items(result,work):
    return [item for item in (result.get('items') or result.get('migrationItems') or [])
            if item.get('itemType')=='AGENTS_MD' and item.get('cwd')==str(work)]


def migration_history_recorded(result,work,recorded):
    return any(row.get('providerId')=='isolated-native-audit' and not row.get('failures')
               and any(item.get('itemType')=='AGENTS_MD' and item.get('cwd')==str(work)
                       and item.get('source')==str(work/'CLAUDE.md')
                       and item.get('target')==str(work/'AGENTS.md')
                       and item.get('title')==recorded for item in row.get('successes',[]))
               for row in result.get('data',[]))



WINDOWS_COMMAND_BOUNDARY_ID='windows_restricted_token_command_exec_control_unsupported'
WINDOWS_COMMAND_TARGET='x86_64-pc-windows-msvc'
WINDOWS_COMMAND_SCOPE={'sandbox_mode':'workspace-write','windows_backend':'elevated','network_access':False}
WINDOWS_STREAM_ERROR={'code':-32600,'message':'streaming command/exec is not supported with windows sandbox'}
WINDOWS_CONTROL_ERROR={'code':-32600,'message':'command/exec/write, command/exec/terminate, and command/exec/resize are not supported for windows sandbox processes'}
WINDOWS_COMMAND_REFERENCE='CODEX_DEEPSEEK_TEST_WINDOWS_COMMAND_BOUNDARY_REFERENCE'
WINDOWS_COMMAND_REFERENCE_SHA='CODEX_DEEPSEEK_TEST_WINDOWS_COMMAND_BOUNDARY_REFERENCE_SHA256'


def windows_command_boundary_detail_valid(detail,target):
    if not isinstance(detail,dict) or target!=WINDOWS_COMMAND_TARGET:return False
    if (detail.get('target')!=target or detail.get('boundary_id')!=WINDOWS_COMMAND_BOUNDARY_ID
        or detail.get('scope')!=WINDOWS_COMMAND_SCOPE or detail.get('scope',{}).get('network_access') is not False or detail.get('streaming_error')!=WINDOWS_STREAM_ERROR
        or detail.get('terminate_error')!=WINDOWS_CONTROL_ERROR):return False
    active=detail.get('active_command',{});reference=detail.get('reference',{})
    workspace=detail.get('workspace');policy=detail.get('sandbox_policy')
    if not isinstance(workspace,str) or not workspace or policy!={'type':'workspaceWrite','writableRoots':[workspace],'networkAccess':False,'excludeTmpdirEnvVar':True,'excludeSlashTmp':True}:return False
    if policy.get('networkAccess') is not False:return False
    return (isinstance(active,dict) and active.get('process_id')=='native-windows-boundary'
        and isinstance(active.get('ready_nonce'),str) and re.fullmatch('[0-9a-f]{32}',active['ready_nonce']) is not None
        and type(active.get('exit_code')) is int and active['exit_code']==0 and active.get('stdout','').strip()==active['ready_nonce']
        and isinstance(active.get('ready_file_sha256'),str) and re.fullmatch('[0-9a-f]{64}',active['ready_file_sha256']) is not None
        and active.get('is_admin') is False and isinstance(active.get('sid'),str)
        and re.fullmatch(r'S-1-5-21-\d+-\d+-\d+-\d+',active['sid']) is not None
        and isinstance(active.get('owner_sid'),str) and active['owner_sid']!=active['sid']
        and re.fullmatch(r'S-1-5-21-\d+-\d+-\d+-\d+',active['owner_sid']) is not None
        and isinstance(reference,dict) and isinstance(reference.get('path'),str)
        and isinstance(reference.get('sha256'),str) and re.fullmatch('[0-9a-f]{64}',reference['sha256']) is not None)


def exact_rpc_error(peer,method,params,expected):
    start=len(peer.events)
    try:peer.call(method,params)
    except RuntimeError:
        events=[e for e in peer.events[start:] if 'id' in e and 'error' in e]
        if len(events)!=1 or events[0]['error']!=expected:raise
        return events[0]['error']
    raise AssertionError(method+' unexpectedly succeeded')


def windows_command_control_boundary(a,work):
    cfg=a.call('config/read',{'includeLayers':False})['config']
    assert cfg['sandbox_mode']=='workspace-write' and cfg['windows']['sandbox']=='elevated'
    policy={'type':'workspaceWrite','writableRoots':[str(work)],'networkAccess':False,'excludeTmpdirEnvVar':True,'excludeSlashTmp':True}
    reference=Path(os.environ[WINDOWS_COMMAND_REFERENCE]);reference_sha=os.environ[WINDOWS_COMMAND_REFERENCE_SHA]
    assert hashlib.sha256(reference.read_bytes()).hexdigest()==reference_sha
    proof=json.loads(reference.read_text());assert proof['passed'] is True and proof['scope']==WINDOWS_COMMAND_SCOPE
    assert proof['official_terminate_os_execution'] is False and proof['source']['byte_identical'] is True
    stream=exact_rpc_error(a.peer,'command/exec',{'command':python_command('print("unreachable")'),'processId':'stream-boundary','cwd':str(work),'sandboxPolicy':policy,'streamStdoutStderr':True,'timeoutMs':5000},WINDOWS_STREAM_ERROR)
    nonce=uuid.uuid4().hex;ready=work/('control-ready-'+nonce+'.json')
    owner_sid=re.search(r'S-1-5-21-\d+-\d+-\d+-\d+',subprocess.check_output(['whoami.exe','/user','/fo','csv','/nh'],text=True)).group()
    script='import ctypes,json,os,re,subprocess,time;from pathlib import Path;sid=re.search(r"S-1-5-21-\\d+-\\d+-\\d+-\\d+",subprocess.check_output(["whoami.exe","/user","/fo","csv","/nh"],text=True)).group();Path('+repr(str(ready))+').write_text(json.dumps({"nonce":'+repr(nonce)+',"pid":os.getpid(),"sid":sid,"is_admin":bool(ctypes.windll.shell32.IsUserAnAdmin())}));time.sleep(4);print('+repr(nonce)+',flush=True)'
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(a.call,'command/exec',{'command':python_command(script),'processId':'native-windows-boundary','cwd':str(work),'sandboxPolicy':policy,'timeoutMs':30000})
        deadline=time.monotonic()+15
        while not ready.exists() and time.monotonic()<deadline:
            if future.done():future.result();raise AssertionError('Native command ended before its readiness receipt')
            time.sleep(.02)
        assert ready.exists(),'Native restricted command readiness missing'
        observed=json.loads(ready.read_text());assert observed['nonce']==nonce and observed['is_admin'] is False
        terminate=exact_rpc_error(a.peer,'command/exec/terminate',{'processId':'native-windows-boundary'},WINDOWS_CONTROL_ERROR)
        result=future.result(20)
    detail={'boundary_id':WINDOWS_COMMAND_BOUNDARY_ID,'target':WINDOWS_COMMAND_TARGET,'scope':WINDOWS_COMMAND_SCOPE,
            'sandbox_policy':policy,'workspace':str(work),'streaming_error':stream,'terminate_error':terminate,'reference':{'path':str(reference),'sha256':reference_sha},
            'active_command':{'process_id':'native-windows-boundary','ready_nonce':nonce,'ready_file_sha256':hashlib.sha256(ready.read_bytes()).hexdigest(),
                              'exit_code':result['exitCode'],'stdout':result['stdout'],'sid':observed['sid'],'owner_sid':owner_sid,'is_admin':observed['is_admin']}}
    assert windows_command_boundary_detail_valid(detail,WINDOWS_COMMAND_TARGET)
    return detail


def wait_command_output_or_error(peer,start,future):
    if future.done():future.result()
    event=peer.wait(lambda e:e.get('method')=='command/exec/outputDelta' or ('id' in e and 'error' in e),start,15)
    if 'error' in event:
        future.result(15)
        raise AssertionError('Native command returned an unexpected error')
    return event


def processes(a):
    work=Path(workspace_path(a.root))
    start=len(a.peer.events);handle='resize-and-kill'
    a.call('process/spawn',{'command':python_command('import os,time;print("PTY_READY",flush=True);input();s=os.get_terminal_size();print(s.lines,s.columns,flush=True);time.sleep(120)'),'cwd':str(work),'processHandle':handle,'tty':True,'size':{'rows':24,'cols':80},'streamStdin':True,'streamStdoutStderr':True,'timeoutMs':150000})
    a.peer.wait(lambda e:e.get('method')=='process/outputDelta' and e['params']['processHandle']==handle,start,15)
    def resize():
        result=a.call('process/resizePty',{'processHandle':handle,'size':{'rows':37,'cols':111}})
        a.call('process/writeStdin',{'processHandle':handle,'deltaBase64':base64.b64encode(b'check-size\n').decode()})
        a.peer.wait(lambda _:b'37 111' in process_stdout_lines(a.peer.events[start:],handle),start,15)
        return {'response':result,'events':a.peer.events[start:]}
    a.verify('process/resizePty',resize,lambda r:b'37 111' in process_stdout_lines(r['events'],handle))
    def kill():
        result=a.call('process/kill',{'processHandle':handle})
        ended=a.peer.wait(lambda e:e.get('method')=='process/exited' and e['params']['processHandle']==handle,start,15)
        return {'response':result,'exit':ended}
    a.verify('process/kill',kill,lambda r:r['exit']['params'].get('exitCode')!=0)
    if os.name=='nt':
        a.verify('command/exec/terminate',lambda:windows_command_control_boundary(a,work),
                 lambda r:windows_command_boundary_detail_valid(r,WINDOWS_COMMAND_TARGET),
                 success_status='platform_boundary_verified')
    else:
        with ThreadPoolExecutor(1) as pool:
            start=len(a.peer.events)
            future=pool.submit(a.call,'command/exec',{'command':python_command('import time;print("TERMINATE_READY",flush=True);time.sleep(120)'),'processId':'terminate-command','cwd':str(work),'streamStdoutStderr':True,'timeoutMs':150000})
            wait_command_output_or_error(a.peer,start,future)
            def terminate():
                result=a.call('command/exec/terminate',{'processId':'terminate-command'})
                return {'response':result,'command_exit':future.result(15)}
            a.verify('command/exec/terminate',terminate,lambda r:r['command_exit']['exitCode']!=0)
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    import secrets,hashlib
    token=secrets.token_urlsafe(32);token_hash=hashlib.sha256(token.encode()).hexdigest()
    entry=Path(a.peer.process.args[0]);log=(a.root/'executor.log').open('w')
    env={**os.environ,'CODEX_DEEPSEEK_HOME':str(a.root/'executor-home')}
    executor=subprocess.Popen([str(entry),'exec-server','--listen',f'ws://127.0.0.1:{port}','--ws-auth','capability-token','--ws-token-sha256',token_hash],cwd=work,env=env,stdin=subprocess.PIPE,stdout=log,stderr=log)
    try:
        end=time.monotonic()+20
        while time.monotonic()<end:
            if executor.poll() is not None:raise RuntimeError('Owned native executor exited: '+(a.root/'executor.log').read_text())
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',port))==0:break
            time.sleep(.05)
        eid='owned-native-executor'
        def add_environment():
            result=a.call('environment/add',{'environmentId':eid,'execServerUrl':f'ws://127.0.0.1:{port}','authBearerToken':token,'connectTimeoutMs':10000})
            return {'result':result,'info':a.call('environment/info',{'environmentId':eid})}
        a.verify('environment/add',add_environment,lambda r:unquote(r['info']['cwd'])==unquote((work).as_uri()))
        a.verify('environment/info',lambda:a.call('environment/info',{'environmentId':eid}),lambda r:bool(r['shell']['path']) and unquote(r['cwd'])==unquote((work).as_uri()))
        a.verify('environment/status',lambda:a.call('environment/status',{'environmentId':eid}),lambda r:r['status']=='ready')
    finally:
        executor.stdin.close();executor.terminate()
        try:executor.wait(8)
        except subprocess.TimeoutExpired:executor.terminate();executor.wait(5)
        log.close()
    thread=a.peer.new()
    fixture=a.root/'work/background_audit.py'
    fixture.write_text('import time\nprint("BACKGROUND_READY",flush=True)\ntime.sleep(120)\n')
    def start_background():
        return a.peer.turn(thread,'Do not delegate. Use native exec_command to run '+shell_command([python_command('')[0],'-I',str(fixture)])+' with yield_time_ms=1000. Once it reports a running session, finish your turn without waiting or polling. This is an explicitly authorized disposable background process.')
    start_background()
    terminals={}
    def list_terminals():
        result=a.call('thread/backgroundTerminals/list',{'threadId':thread})
        terminals.update(result);return result
    a.verify('thread/backgroundTerminals/list',list_terminals,lambda r:any('background_audit.py' in t['command'] for t in r['data']))
    if terminals.get('data'):
        pid=terminals['data'][0]['processId']
        def terminate_terminal():
            result=a.call('thread/backgroundTerminals/terminate',{'threadId':thread,'processId':pid})
            end=time.monotonic()+10
            while time.monotonic()<end:
                listed=a.call('thread/backgroundTerminals/list',{'threadId':thread})
                if not any(t['processId']==pid for t in listed['data']):break
                time.sleep(.05)
            return {'result':result,'remaining':listed}
        a.verify('thread/backgroundTerminals/terminate',terminate_terminal,lambda r:r['result']['terminated'] and not any(t['processId']==pid for t in r['remaining']['data']))
    start_background()
    def clean_terminals():
        before=a.call('thread/backgroundTerminals/list',{'threadId':thread})['data']
        result=a.call('thread/backgroundTerminals/clean',{'threadId':thread})
        end=time.monotonic()+10
        while time.monotonic()<end:
            after=a.call('thread/backgroundTerminals/list',{'threadId':thread})['data']
            if not after:break
            time.sleep(.05)
        return {'before':before,'response':result,'after':after}
    a.verify('thread/backgroundTerminals/clean',clean_terminals,lambda r:bool(r['before']) and not r['after'])


def plugins(a):
    entry=Path(a.peer.process.args[0]);a.peer.close()
    config=a.root/'home/config.toml';config.write_text(config.read_text().replace('plugins=false','plugins=true'))
    a.peer=Peer(a.root,command=[str(entry)],server_args=['-c','mcp_servers={}','app-server','--strict-config'])
    origin=a.root/'marketplace-origin';(origin/'.agents/plugins').mkdir(parents=True)
    marketplace=origin/'.agents/plugins/marketplace.json'
    marketplace.write_text(json.dumps({'name':'completion-market','plugins':[{'name':'completion-plugin','source':{'source':'local','path':'./completion-plugin'}}]}))
    plugin=origin/'completion-plugin';(plugin/'.codex-plugin').mkdir(parents=True)
    meta=plugin/'.codex-plugin/plugin.json'
    meta.write_text(json.dumps({'name':'completion-plugin','version':'1.0.0','description':'Actual local marketplace lifecycle audit'}))
    skill=plugin/'skills/completion-skill';skill.mkdir(parents=True)
    (skill/'SKILL.md').write_text('---\nname: completion-skill\ndescription: Local plugin audit skill.\n---\nReturn LOCAL_PLUGIN_V1.\n')
    subprocess.run(['git','init','-q','-b','main',str(origin)],check=True)
    def commit(message):
        subprocess.run(['git','-C',str(origin),'add','.'],check=True)
        subprocess.run(['git','-C',str(origin),'-c','user.name=Runtime Audit','-c','user.email=runtime-audit@example.invalid','commit','--no-gpg-sign','-qm',message],check=True)
        subprocess.run(['git','-C',str(origin),'update-server-info'],check=True)
    commit('initial fixture')
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(a.root)))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    def close_server():server.shutdown();server.server_close();worker.join(3)
    a.cleanups.append(close_server)
    git_url='http://127.0.0.1:'+str(server.server_port)+'/marketplace-origin/.git'
    registered={}
    def add():
        result=a.call('marketplace/add',{'source':git_url,'refName':'main'});registered.update(result)
        return {'result':result,'manifest_exists':(Path(result['installedRoot'])/'.agents/plugins/marketplace.json').exists()}
    a.verify('marketplace/add',add,lambda r:r['result']['marketplaceName']=='completion-market' and r['manifest_exists'])
    a.verify('plugin/list',lambda:a.call('plugin/list',{'cwds':[str(a.root/'work')],'forceRefetch':False}),lambda r:'completion-plugin' in json.dumps(r['marketplaces']))
    a.verify('plugin/search',lambda:a.call('plugin/search',{'searchTerm':'completion-plugin','cwds':[str(a.root/'work')]}),lambda r:'completion-plugin' in json.dumps(r['data']))
    installed_manifest=Path(registered['installedRoot'])/'.agents/plugins/marketplace.json'
    a.call('plugin/install',{'marketplacePath':str(installed_manifest),'pluginName':'completion-plugin','installAttemptId':uuid.uuid4().hex})
    def installed_plugins():
        result=a.call('plugin/installed',{'cwds':[str(a.root/'work')]})
        skills=a.call('skills/list',{'cwds':[str(a.root/'work')],'forceReload':True})
        return {'result':result,'skills':skills}
    a.verify('plugin/installed',installed_plugins,lambda r:'completion-plugin' in json.dumps(r['result']['marketplaces']) and 'completion-skill' in json.dumps(r['skills']))
    meta.write_text(json.dumps({'name':'completion-plugin','version':'2.0.0','description':'Upgraded actual fixture'}));commit('upgrade fixture')
    def upgrade():
        result=a.call('marketplace/upgrade',{'marketplaceName':'completion-market'})
        materialized=json.loads((Path(registered['installedRoot'])/'completion-plugin/.codex-plugin/plugin.json').read_text())
        return {'result':result,'materialized':materialized}
    a.verify('marketplace/upgrade',upgrade,lambda r:not r['result']['errors'] and r['result']['upgradedRoots'] and r['materialized']['version']=='2.0.0')
    a.backend_boundary('plugin/reconcile',lambda:a.call('plugin/reconcile',{'reason':'isolated-local-runtime-audit'}),lambda r:r=={'changedPlugins':[],'failedRemotePluginIds':[],'failedMaterializationRemotePluginIds':[]},'Frozen upstream reconciles remote installed bundles only with Codex-backend authentication; local marketplace upgrades use marketplace/upgrade.')
    def remote_skill():
        a.call('config/batchWrite',{'edits':[{'keyPath':'features.plugins','value':False,'mergeStrategy':'replace'}],'reloadUserConfig':True})
        try:a.call('plugin/skill/read',{'remoteMarketplaceName':'completion-market','remotePluginId':'fixture-remote-plugin','skillName':'completion-skill'});return {'rejected':False}
        except RuntimeError as e:return {'rejected':True,'error':str(e)}
    a.backend_boundary('plugin/skill/read',remote_skill,lambda r:r['rejected'] and 'remote plugin skill read is not enabled' in r['error'],'Frozen upstream method fetches remote service plugin skill details; local skills remain accessible through native skills/list and filesystem tools.')
    a.call('plugin/uninstall',{'pluginId':'completion-plugin@completion-market'})
    def remove():
        result=a.call('marketplace/remove',{'marketplaceName':'completion-market'})
        listed=a.call('plugin/list',{'cwds':[str(a.root/'work')],'forceRefetch':False})
        return {'result':result,'listed':listed,'installed_root_exists':Path(registered['installedRoot']).exists()}
    a.verify('marketplace/remove',remove,lambda r:'completion-market' not in json.dumps(r['listed']['marketplaces']) and not r['installed_root_exists'])


def mcp(a):
    entry=Path(a.peer.process.args[0]);a.peer.close()
    nonce='MCP_RESOURCE_'+uuid.uuid4().hex;source=a.root/'resource-source.txt';source.write_text(nonce)
    effect=a.root/'tool-count.txt';effect.write_text('0')
    fixture=a.root/'fixture_server.py'
    fixture.write_text('''from mcp.server import MCPServer
from pathlib import Path
import sys
m=MCPServer('local-acceptance')
source=Path(sys.argv[1]);effect=Path(sys.argv[2])
@m.resource('audit://local/nonce')
def resource()->str:return source.read_text()
@m.tool()
def increment_counter()->dict:
 n=int(effect.read_text())+1;effect.write_text(str(n));return {'count':n}
m.run(transport='stdio')
''')
    with (a.root/'home/config.toml').open('a') as f:
        f.write('\n[mcp_servers.local_fixture]\ncommand='+json.dumps(str(python_entry(entry.parent.parent)))+'\nargs='+json.dumps(['-I',str(fixture),str(source),str(effect)])+'\ndefault_tools_approval_mode="approve"\n')
    a.peer=Peer(a.root,command=[str(entry)]);thread=a.peer.new()
    def resource():
        first=a.call('mcpServer/resource/read',{'threadId':thread,'server':'local_fixture','uri':'audit://local/nonce'})
        source.write_text(nonce+'_FRESH')
        second=a.call('mcpServer/resource/read',{'threadId':thread,'server':'local_fixture','uri':'audit://local/nonce'})
        return {'first':first,'second':second}
    a.verify('mcpServer/resource/read',resource,lambda r:nonce in json.dumps(r['first']) and nonce+'_FRESH' in json.dumps(r['second']))
    a.verify('mcpServer/tool/call',lambda:a.call('mcpServer/tool/call',{'threadId':thread,'server':'local_fixture','tool':'increment_counter','arguments':{}}),lambda r:effect.read_text()=='1' and '1' in json.dumps(r))
    def stream_start():
        try:a.call('mcpServer/event/stream/start',{'threadId':thread,'server':'local_fixture','name':'fixture','arguments':{},'subscriptionId':'native-boundary'});return {'rejected':False}
        except RuntimeError as e:return {'rejected':True,'error':str(e)}
    a.backend_boundary('mcpServer/event/stream/start',stream_start,lambda r:r['rejected'] and 'only supported for hosted apps' in r['error'],'Frozen upstream explicitly restricts this endpoint to hosted Apps; ordinary local MCP subscriptions are not this API contract.')
    a.backend_boundary('mcpServer/event/stream/stop',lambda:a.call('mcpServer/event/stream/stop',{'subscriptionId':'native-boundary'}),lambda r:r=={},'Idempotent stop branch executed; positive hosted-App event-stream lifecycle requires the excluded backend.')


def migration(a):
    work=Path(workspace_path(a.root));marker='SCOPED_IMPORT_'+uuid.uuid4().hex
    subprocess.run(['git','init','-q',str(work)],check=True)
    (work/'CLAUDE.md').write_text('Use integer cents for this test project. '+marker+'\n')
    detected={}
    def detect():
        result=a.call('externalAgentConfig/detect',{'includeHome':False,'cwds':[str(work)],'maxSessions':0})
        detected.update(result);return result
    a.verify('externalAgentConfig/detect',detect,lambda r:bool(scoped_migration_items(r,work)))
    def import_configuration():
        selected=scoped_migration_items(detected,work)
        if not selected:raise AssertionError('No scoped AGENTS_MD migration candidate: '+repr(detected))
        start=len(a.peer.events)
        response=a.call('externalAgentConfig/import',{'migrationItems':selected,'providerId':'isolated-native-audit','source':'runtime-fixture'})
        event=a.peer.wait(lambda e:e.get('method')=='externalAgentConfig/import/completed',start,30)
        files=[p for p in work.rglob('*') if p.is_file() and p.name!='CLAUDE.md']
        imported=[str(p.relative_to(work)) for p in files if marker in p.read_text(errors='replace')]
        return {'response':response,'event':event,'imported_files':imported}
    a.verify('externalAgentConfig/import',import_configuration,lambda r:bool(r['imported_files']))
    recorded='RECORDED_'+uuid.uuid4().hex
    def history_record():
        result=a.call('externalAgentConfig/import/recordHistory',{'providerId':'isolated-native-audit','source':'runtime-fixture','itemTypeResults':[{'itemType':'AGENTS_MD','successes':[{'itemType':'AGENTS_MD','cwd':str(work),'source':str(work/'CLAUDE.md'),'target':str(work/'AGENTS.md'),'title':recorded}],'failures':[]}]})
        histories=a.call('externalAgentConfig/import/readHistories',{})
        return {'result':result,'histories':histories}
    a.verify('externalAgentConfig/import/recordHistory',history_record,lambda r:migration_history_recorded(r['histories'],work,recorded))
    a.verify('externalAgentConfig/import/readHistories',lambda:a.call('externalAgentConfig/import/readHistories',{}),lambda r:migration_history_recorded(r,work,recorded))


def control(a):
    queue_ready=threading.Event();queue_send=a.peer.send
    def hold_queue(message):
        if any(x.get('text')=='QUEUE_PENDING' for x in message.get('result',{}).get('contentItems',[])):
            queue_ready.set();return
        return queue_send(message)
    a.peer.dynamic_answers[(None,'audit_queue_barrier')]='QUEUE_PENDING';a.peer.send=hold_queue
    queued=a.peer.new(historyMode='legacy',dynamicTools=[{'name':'audit_queue_barrier','description':'An explicitly authorized runtime synchronization point.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]);nonce='QUEUED_'+uuid.uuid4().hex
    try:
        paused_turn,paused_start=a.peer.start(queued,'Call audit_queue_barrier exactly once and wait for its result. Do not delegate or use other tools.')
        if not queue_ready.wait(60):raise AssertionError('Queue interruption barrier not reached')
        submission=a.call('thread/queue/add',{'threadId':queued,'clientUserMessageId':uuid.uuid4().hex,'input':[{'type':'text','text':'Do not use tools or delegate. Reply exactly '+nonce}]})
        a.call('turn/interrupt',{'threadId':queued,'turnId':paused_turn});a.peer.complete(queued,paused_start,expected_status='interrupted',turn_id=paused_turn)
    finally:a.peer.send=queue_send
    def queue_start():
        start=len(a.peer.events)
        response=a.call('thread/queue/start',{'threadId':queued,'queuedSubmissionId':submission['queuedSubmission']['id']})
        items=a.peer.complete(queued,start)
        remaining=a.call('thread/queue/list',{'threadId':queued})
        return {'response':response,'answer':text(items),'remaining':remaining}
    a.verify('thread/queue/start',queue_start,lambda r:nonce in r['answer'] and nonce not in json.dumps(r['remaining']))
    a.call('config/batchWrite',{'edits':[{'keyPath':'features.step_model_switching','value':True,'mergeStrategy':'replace'}],'reloadUserConfig':True})
    barrier=threading.Event();pending=[];original_send=a.peer.send
    def gated_send(message):
        if any(x.get('text')=='BARRIER_RELEASED' for x in message.get('result',{}).get('contentItems',[])):
            pending.append(message);barrier.set();return
        return original_send(message)
    a.peer.send=gated_send
    a.peer.dynamic_answers[(None,'audit_barrier')]='BARRIER_RELEASED'
    held=a.peer.new(dynamicTools=[{'name':'audit_barrier','description':'A harmless user-authorized synchronization point for the runtime audit. Call once when explicitly requested.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}}])
    turn,start=a.peer.start(held,'Do not delegate or search. Call audit_barrier exactly once, wait for its result, and then report the returned marker without other tools.')
    def update_turn():
        if not barrier.wait(60):raise AssertionError('Model did not reach the actual native dynamic-tool barrier')
        response=a.call('turn/settings/update',{'threadId':held,'turnId':turn,'effort':'high'})
        for message in pending:original_send(message)
        pending.clear();items=a.peer.complete(held,start)
        requests=[json.loads(p.read_text()) for p in (a.root/'adapter-traces').glob('*-request.json')]
        requests=[p for p in requests if p.get('client_metadata',{}).get('thread_id')==held]
        return {'response':response,'answer':text(items),'inference_efforts':[p.get('reasoning') for p in requests]}
    try:a.verify('turn/settings/update',update_turn,lambda r:r['response']['status']=='applied' and 'BARRIER_RELEASED' in r['answer'] and {'effort':'high'} in r['inference_efforts'])
    finally:
        a.peer.send=original_send
        for message in pending:original_send(message)
    guardian=a.peer.new();marker='GUARDIAN_ACK_'+uuid.uuid4().hex
    event={'id':'serialized-denial-fixture','status':'denied','action':{'type':'command','source':'unified_exec','command':'printf '+marker,'cwd':str(a.root/'work')}}
    def guardian_approval():
        response=a.call('thread/approveGuardianDeniedAction',{'threadId':guardian,'event':event})
        items=a.peer.turn(guardian,'Do not use tools, execute commands or delegate. Read the preceding Guardian approved-action context and quote the exact approved command. This checks context delivery only.')
        requests=[json.loads(p.read_text()) for p in (a.root/'adapter-traces').glob('*-request.json')]
        matching=[p for p in requests if p.get('client_metadata',{}).get('thread_id')==guardian]
        return {'response':response,'answer':text(items),'native_approved_context_forwarded':any(marker in json.dumps(p['input']) and 'The user has manually approved a specific action' in json.dumps(p['input']) for p in matching),'command_executed':any(x.get('type')=='commandExecution' for x in items),'fixture_kind':'valid serialized denial event; does not claim this test produced an automatic Guardian denial'}
    a.verify('thread/approveGuardianDeniedAction',guardian_approval,lambda r:marker in r['answer'] and r['native_approved_context_forwarded'] and not r['command_executed'])
    a.call('thread/archive',{'threadId':queued})
    candidates=[p for p in (a.root/'home/archived_sessions').rglob('*.jsonl') if queued in p.name]
    def compress():
        if len(candidates)!=1:raise AssertionError('Expected one owned cold legacy rollout: '+repr(candidates))
        path=candidates[0];before=path.read_bytes();old=time.time()-8*24*60*60;os.utime(path,(old,old))
        response=a.call('rollout/compress',{})
        compressed=path.with_suffix(path.suffix+'.zst');end=time.monotonic()+20
        while not compressed.exists() and time.monotonic()<end:time.sleep(.05)
        import zstandard,hashlib
        with compressed.open('rb') as f, zstandard.ZstdDecompressor().stream_reader(f) as reader:after=reader.read()
        read=a.call('thread/read',{'threadId':queued,'includeTurns':True})
        return {'response':response,'original_removed':not path.exists(),'before_sha256':hashlib.sha256(before).hexdigest(),'after_sha256':hashlib.sha256(after).hexdigest(),'native_read_preserved_nonce':nonce in json.dumps(read)}
    a.verify('rollout/compress',compress,lambda r:r['original_removed'] and r['before_sha256']==r['after_sha256'] and r['native_read_preserved_nonce'])


def main():
    p=argparse.ArgumentParser();p.add_argument('--entry',type=Path,default=Path.home()/'.local/bin/codex-deepseek');p.add_argument('--output',type=Path,required=True);p.add_argument('--group',choices=['catalog','threads','processes','plugins','mcp','migration','control'],default='catalog');args=p.parse_args()
    a=Audit(args.output,args.entry.resolve())
    try:globals()[args.group](a)
    finally:a.close()
    raise SystemExit(1 if any(x['status'] not in {'passed','backend_boundary_verified','platform_boundary_verified'} for x in a.results) else 0)


if __name__=='__main__':main()
