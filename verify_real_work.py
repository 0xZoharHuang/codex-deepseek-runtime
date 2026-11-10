"""Paid, isolated native development workflows against pinned real upstream defects.

Fixtures and independent oracle tests live outside the agent workspace. No App IPC.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import hashlib
import os
from pathlib import Path
import subprocess
import shutil
import sys
import sqlite3
from contextlib import closing
import threading
import time
import xml.etree.ElementTree as ET
from verify_live import Peer,qa_sandbox_mode,prepare_workspace,workspace_path
from acceptance_assertions import current_browser_evidence,complete_work_patch,OracleGuard,real_work_passed,real_work_input_hashes,native_host_execution,real_work_tools_binding
from install import python_entry,_support,valid_release

BASE = Path(__file__).resolve().parent.parent / 'delivery/real-work'
COMPACTION_LIMIT = 500000
PRINT_LOCK = threading.Lock()
REQUIRE_WEB_RESEARCH = False
SANDBOX_MODE = 'workspace-write'
RUNTIME_HOME = None
TOOLS_ROOT = None
CONTROLS = None


def tool_environment(root):
    return TOOLS_ROOT/root.name/'tools311' if TOOLS_ROOT is not None else root/'tools311'


def tool_python(root):
    return python_entry(tool_environment(root)).resolve(strict=True)



def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def source_inventory(directory):
    result={}
    for path in sorted(Path(directory).rglob('*')):
        if any(part in {'.git','__pycache__','.pytest_cache'} for part in path.relative_to(directory).parts):
            continue
        if path.is_file():
            if path.is_symlink() or path.stat().st_nlink!=1:
                raise ValueError('Linked real-work source control')
            result[path.relative_to(directory).as_posix()]=file_sha(path)
    return result


def load_work_controls(root,name,tools,*,initial=True):
    """Read controller-pinned originals before model access; never derive from final tests."""
    from acceptance_assertions import pytest_execution_evidence,pytest_inventory_coverage
    if CONTROLS is None:
        raise ValueError('--controls is required; final results cannot supply their own test inventory')
    path=(CONTROLS/name/'qualification-controls.json').resolve(strict=True)
    forbidden=[root.resolve(),Path(workspace_path(root)).resolve(),
               Path(RUNTIME_HOME).resolve() if RUNTIME_HOME is not None else (root/'home').resolve()]
    if any(path.is_relative_to(directory) for directory in forbidden):
        raise ValueError('Qualification controls must remain outside the task fixture, writable workspace and runtime home')
    expected=os.environ.get('CODEX_DEEPSEEK_QA_CONTROLS_SHA256_'+name.upper())
    if not expected or file_sha(path)!=expected:
        raise ValueError('Qualification controls require an independent prelaunch SHA pin')
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('case')!=name or value.get('schema_version')!=1:
        raise ValueError('Qualification controls belong to a different task or schema')
    if real_work_input_hashes(root,name)!=value['fixture_input_sha256']:
        raise ValueError('Fixture drivers and dependency pins differ from the frozen originals')
    baselines=value['pytest_baselines']
    if set(baselines)!={'workspace','upstream-heldout'}:
        raise ValueError('Both original pytest inventories are required')
    for suite,baseline in baselines.items():
        original=value['original_xml'][suite]
        coverage=pytest_inventory_coverage(pytest_execution_evidence(Path(original['path'])),baseline)
        if (file_sha(original['path'])!=original['sha256']
            or baseline['source_junit_sha256']!=original['sha256']
            or baseline['source_sha256']!=value['input_source_sha256'][suite]
            or baseline['tools_sha256']!=value['tools_sha256']
            or not coverage['passed'] or coverage['added_count']!=0):
            raise ValueError('Original pytest inventory does not match its pinned provenance')
        source_files=value['source_files_sha256'][suite]
        if hashlib.sha256(json.dumps(source_files,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()!=value['input_source_sha256'][suite]:
            raise ValueError('Original source manifest does not match the baseline')
    oracle=source_inventory(root/'oracle')
    if oracle!=value['oracle_files_sha256']:
        raise ValueError('Independent oracle differs from the frozen original')
    if initial:
        original=source_inventory(root/'work')
        combined={'work/'+key:sha for key,sha in original.items()}
        combined.update({'oracle/'+key:sha for key,sha in oracle.items() if key.startswith('tests/')})
        if (original!=value['source_files_sha256']['workspace']
            or combined!=value['source_files_sha256']['upstream-heldout']):
            raise ValueError('Initial task source differs from the frozen original')
    selected=tools['paths']['selected']
    if hashlib.sha256(json.dumps(value['tools_files_sha256'],sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()!=value['tools_sha256']:
        raise ValueError('Original tools manifest does not match the baseline')
    if (not tools['same_bytes'] or selected['files_sha256']!=value['tools_files_sha256']
        or value['interpreter_binding']['launcher']['path']!=str(tool_python(root))
        or value['interpreter_binding']['launcher']['sha256']!=selected['python_sha256']):
        raise ValueError('Selected interpreter differs from the original tools')
    for binding in value['interpreter_binding'].values():
        if file_sha(binding['path'])!=binding['sha256']:
            raise ValueError('Pinned interpreter environment has changed')
    return {**value,'control_path':str(path),'control_sha256':expected}


def observed_host_execution(events,root,controls,release):
    """Join native events with the external controller's retained OS handle evidence."""
    if sys.platform!='win32':
        return native_host_execution(events,tool_python(root),sys.platform)
    from codex_deepseek.processes import identity
    snapshot=os.environ.get('CODEX_DEEPSEEK_QA_PROCESS_EVIDENCE')
    from ci.qualify_native import read_process_evidence_bytes
    raw=read_process_evidence_bytes(Path(snapshot)) if snapshot else None
    evidence=json.loads(raw) if raw is not None else None
    retained=root/('controller-process-evidence-'+str(time.time_ns())+'.json') if raw is not None else None
    if retained is not None:
        with retained.open('xb') as stream:stream.write(raw)
    metadata=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    manifest=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
    native=(Path(metadata['native_package'])/'bin/codex.exe').resolve(strict=True)
    expected=manifest['files']['bin/codex.exe']
    if not isinstance(expected,str):
        expected=expected['sha256']
    if file_sha(native)!=expected:
        raise ValueError('Owned native image differs from its installed manifest')
    roots=[]
    if isinstance(evidence,dict):
        rows=evidence.get('observed',[])
        owner_key=(os.getpid(),identity(os.getpid()))
        by_key={(row.get('pid'),row.get('birth')):row for row in rows}
        if len(by_key)!=len(rows) or owner_key not in by_key:
            raise ValueError('Process evidence does not belong to this verifier instance')
        def own_branch(row):
            visited=set()
            while (key:=(row.get('pid'),row.get('birth')))!=owner_key:
                if (key in visited or row.get('ownership')!='stable_direct_parent_edge'
                    or row.get('stable') is not True or row.get('birth_after')!=row.get('birth')
                    or row.get('ppid')!=row.get('parent_pid')):
                    return False
                visited.add(key)
                row=by_key.get((row.get('parent_pid'),row.get('parent_birth')))
                if row is None:return False
            return True
        roots=[{'pid':row['pid'],'birth':row['birth'],'exe':str(native),'sha256':expected}
               for row in rows if isinstance(row.get('exe'),str)
               and _support.comparison_path(row['exe'])==_support.comparison_path(native)
               and own_branch(row)]
    result=native_host_execution(events,tool_python(root),sys.platform,
        process_evidence=evidence,native_roots=roots,
        python_sha256=controls['interpreter_binding']['launcher']['sha256'],
        interpreter_binding=controls['interpreter_binding'])
    result['controller_evidence']={'live_snapshot_path':snapshot,'retained_path':str(retained) if retained is not None else None,
        'sha256':hashlib.sha256(raw).hexdigest() if raw is not None else None}
    return result


def log(name, value):
    with PRINT_LOCK:
        print(json.dumps({'case': name, **value}, ensure_ascii=False), flush=True)


def job_git_preflight(root):
    """Check this job's existing Git before creating a native peer; never edit PATH."""
    result={'passed':False,'checked_before_peer':True,'path_modified':False,
            'path':None,'sha256':None,'os_exit_code':None}
    try:
        found=shutil.which('git')
        if not found:raise FileNotFoundError('Git is unavailable in the job PATH')
        selected=Path(found).resolve(strict=True)
        result.update(path=str(selected),sha256=file_sha(selected))
        expected=os.environ.get('CODEX_DEEPSEEK_QA_GIT_SHA256')
        result['expected_sha256']=expected
        if expected is not None and result['sha256']!=expected:
            raise ValueError('Job Git differs from the controller pin')
        version=subprocess.run([str(selected),'--version'],capture_output=True,timeout=10)
        result.update(os_exit_code=version.returncode,stdout=_output_text(version.stdout),stderr=_output_text(version.stderr))
        if version.returncode!=0:raise RuntimeError('Git --version failed')
        result['passed']=True
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as error:
        result.update(error_type=type(error).__name__,error=str(error))
    path=root/('job-git-preflight-'+str(time.time_ns())+'.json')
    result['evidence_path']=str(path)
    with path.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    return result


def _turn_terminal(peer,thread,start,turn):
    return next((event for event in reversed(peer.events[start:])
                 if event.get('method')=='turn/completed'
                 and event.get('params',{}).get('threadId')==thread
                 and event.get('params',{}).get('turn',{}).get('id')==turn),None)


def operator_interrupt(peer,root,thread,start,turn,*,deadline):
    """Use only the current native peer/turn for an external case-root request."""
    request=root/'operator-interrupt.request'
    if not request.exists():return None
    if (not request.is_file() or request.is_symlink() or request.stat().st_nlink!=1
        or request.resolve().is_relative_to(Path(workspace_path(root)).resolve())):
        raise ValueError('Operator interrupt request must be a plain external case-root file')
    result={'request_path':str(request),'request_sha256':file_sha(request),
            'thread_id':thread,'turn_id':turn,'native_confirmed':False}
    path=root/('operator-interrupt-'+str(time.time_ns())+'.json')
    result['evidence_path']=str(path)
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    try:
        peer.call('turn/interrupt',{'threadId':thread,'turnId':turn})
        result['rpc_acknowledged']=True
        peer.complete(thread,start,timeout=max(0,deadline-time.monotonic()),
                      expected_status='interrupted',turn_id=turn)
        event=_turn_terminal(peer,thread,start,turn)
        terminal=event.get('params',{}).get('turn',{}) if event else {}
        if terminal.get('status')!='interrupted' or terminal.get('error') is not None:
            raise RuntimeError('Requested native turn interruption is not confirmed')
        result.update(native_confirmed=True,native_terminal={
            key:terminal.get(key) for key in ('id','status','error','rootTurnId')})
    except Exception as error:
        result.update(error_type=type(error).__name__,error=str(error))
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def complete_real_work_turn(peer,root,thread,start,turn,*,timeout):
    """Poll only operator control while retaining the original native terminal check."""
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if getattr(peer,'protocol_error',None) is not None:
            raise RuntimeError(f'Native app-server protocol error: {peer.protocol_error}')
        if _turn_terminal(peer,thread,start,turn) is not None:
            return peer.complete(thread,start,timeout=max(0,deadline-time.monotonic()),turn_id=turn),None
        interrupted=operator_interrupt(peer,root,thread,start,turn,deadline=deadline)
        if interrupted is not None:return [],interrupted
        if getattr(peer,'exited',False) or peer.process.poll() is not None:raise RuntimeError('Native wrapper exited')
        time.sleep(min(.25,max(0,deadline-time.monotonic())))
    raise TimeoutError('Native acceptance event timed out')


def validation_checkpoint(root,report):
    """A later external validation fault cannot erase the original task failure."""
    report.update(passed=False,validation_status='not_completed',validation=[])
    (root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


def _output_text(value):
    return value.decode('utf-8',errors='replace') if isinstance(value,bytes) else value or ''


def _validation_environment(root,label,pythonpath):
    private=root/'validation-temp'/label
    temp=private/'temp';temp.mkdir(parents=True,exist_ok=True)
    return ({**os.environ,'PYTHONPATH':str(pythonpath),'PYTHONOPTIMIZE':'',
             'PYTHONDONTWRITEBYTECODE':'1','TEMP':str(temp),'TMP':str(temp),'TMPDIR':str(temp)},
            private/'pytest')


def _validation_streams(root,label,stdout,stderr):
    saved={}
    for name,value in (('stdout',stdout),('stderr',stderr)):
        raw=value if isinstance(value,bytes) else _output_text(value).encode('utf-8')
        path=root/f'final-{label}.{name}.bin';path.write_bytes(raw)
        saved[name]={'path':str(path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    output=_output_text(stdout)+_output_text(stderr)
    (root/f'final-{label}.txt').write_text(output,encoding='utf-8')
    return output,saved


def _validation_process_failure(root,label,error,start,timeout):
    stdout=getattr(error,'stdout',None);stderr=getattr(error,'stderr',None)
    output,streams=_validation_streams(root,label,stdout,stderr)
    return {'suite':label,'exit':None,'os_exit_code':None,'seconds':round(time.monotonic()-start,2),
            'status':'timeout' if isinstance(error,subprocess.TimeoutExpired) else 'process_error',
            'timed_out':isinstance(error,subprocess.TimeoutExpired),'configured_timeout_seconds':timeout,
            'timeout_seconds':getattr(error,'timeout',None),'error_type':type(error).__name__,'error':str(error),
            'partial_stdout':_output_text(stdout),'partial_stderr':_output_text(stderr),'raw_streams':streams,
            'source_imports':{'passed':False,'status':'validation_did_not_complete'},
            'pytest_execution':{},'tail':output[-2200:]}


def _validation_imports(path):
    raw=None
    try:
        raw=path.read_bytes();value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('Import receipt must be an object')
        return value
    except (OSError,ValueError,UnicodeError) as error:
        return {'passed':False,'status':'invalid_or_missing','error_type':type(error).__name__,
                'error':str(error),'receipt_path':str(path),
                'receipt_sha256':hashlib.sha256(raw).hexdigest() if raw is not None else None}


def run_test(root, label, args, pythonpath):
    start=time.monotonic()
    imports=root/f'final-{label}-imports.json';package='jinja2' if root.name=='jinja' else 'httpcore'
    junit=root/f'final-{label}.junit.xml'
    test_root=Path(workspace_path(root)) if label=='workspace' else root/'oracle' if label=='upstream-heldout' else root
    try:
        if imports.exists():imports.unlink()
        if junit.exists():junit.unlink()
        environment,basetemp=_validation_environment(root,label,pythonpath)
        p=subprocess.run([str(tool_python(root)),'-B',str(Path(__file__).with_name('real_work_imports.py')),
            '--package',package,'--root',str(pythonpath),'--receipt',str(imports),'pytest',
            '--rootdir='+str(test_root),'--basetemp='+str(basetemp),'--junitxml='+str(junit),*args],
            cwd=workspace_path(root),env=environment,capture_output=True,timeout=180)
    except (subprocess.TimeoutExpired,OSError) as error:
        return _validation_process_failure(root,label,error,start,180)
    output,streams=_validation_streams(root,label,p.stdout,p.stderr)
    proof=_validation_imports(imports)
    from acceptance_assertions import pytest_execution_evidence
    try:
        executed=pytest_execution_evidence(junit)
        valid_xml=True
    except (ValueError,ET.ParseError,OSError,UnicodeError) as error:
        try:junit_sha256=file_sha(junit)
        except OSError:junit_sha256=None
        executed={'status':'invalid','error_type':type(error).__name__,'error':str(error),
                  'junit_path':str(junit),'junit_sha256':junit_sha256}
        valid_xml=False
    valid=proof.get('passed') is True and valid_xml
    return {'suite':label,'exit':p.returncode if p.returncode else (0 if valid else 1),
            'os_exit_code':p.returncode,'seconds':round(time.monotonic()-start,2),
            'status':'completed' if valid else 'invalid_validation_receipt','raw_streams':streams,
            'source_imports':proof,'pytest_execution':executed,'tail':output[-2200:]}


def run_script(root,label,filename):
    start=time.monotonic();imports=root/f'final-{label}-imports.json'
    try:
        if imports.exists():imports.unlink()
        environment,_=_validation_environment(root,label,root/'work')
        p=subprocess.run([str(tool_python(root)),'-B',str(Path(__file__).with_name('real_work_imports.py')),
            '--package','httpcore','--root',str(root/'work'),'--receipt',str(imports),'script',str(root/filename)],
            cwd=workspace_path(root),env=environment,capture_output=True,timeout=60)
    except (subprocess.TimeoutExpired,OSError) as error:
        return _validation_process_failure(root,label,error,start,60)
    output,streams=_validation_streams(root,label,p.stdout,p.stderr)
    proof=_validation_imports(imports)
    return {'suite':label,'exit':p.returncode if p.returncode else (0 if proof.get('passed') is True else 1),
            'os_exit_code':p.returncode,'seconds':round(time.monotonic()-start,2),'raw_streams':streams,
            'status':'completed' if proof.get('passed') is True else 'invalid_validation_receipt',
            'source_imports':proof,'tail':output[-2200:]}


def qualified_entry_release(entry, runtime_home=None):
    entry=Path(entry).resolve()
    if runtime_home is None:return entry.parent.parent.parent
    # Profile launchers live below the home, not under the venv.
    manifest=entry.parent/'manifest.json';record=json.loads(manifest.read_text(encoding='utf-8'))
    recipe=record['recipe'];home=Path(runtime_home).resolve()
    if (Path(recipe.get('home','')).resolve()!=home or set(record.get('files',{}))!={'codex.exe','codex-deepseek.exe'}
        or entry.name not in record['files'] or not all((entry.parent/name).is_file() and not (entry.parent/name).is_symlink()
            and hashlib.sha256((entry.parent/name).read_bytes()).hexdigest()==digest for name,digest in record['files'].items())):
        raise ValueError('Pinned profile entry does not match its home or immutable manifest')
    interpreter=Path(recipe['interpreter']).resolve();release=interpreter.parent.parent.parent
    if (interpreter!=python_entry(release/'venv').resolve()
        or Path(recipe['package_root']).resolve()!=(release/'venv/Lib/site-packages').resolve()):
        raise ValueError('Pinned profile interpreter/package do not belong to one release')
    metadata=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    native=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
    if not valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native):
        raise ValueError('Pinned profile installed release failed validation')
    return release


def native_reviewer_completions(rollout_events, native_events, child):
    """Require matching successful native terminal and a nonempty final review."""
    terminals={}
    for event in native_events:
        if event.get('method')!='turn/completed':continue
        params=event.get('params',{})
        if params.get('threadId')!=child:continue
        turn=params.get('turn',{})
        terminals.setdefault(turn.get('id'),[]).append(turn)
    completed={};invalid=set()
    for event in rollout_events:
        payload=event.get('payload',{})
        if event.get('type')!='event_msg' or payload.get('type')!='task_complete':continue
        turn_id=payload.get('turn_id');root_turn=payload.get('root_turn_id')
        message=payload.get('last_agent_message')
        matches=terminals.get(turn_id,[])
        valid=(isinstance(turn_id,str) and bool(turn_id)
               and isinstance(root_turn,str) and bool(root_turn)
               and isinstance(message,str) and bool(message.strip())
               and payload.get('error') is None and bool(matches)
               and all(t.get('status')=='completed' and 'error' in t and t['error'] is None
                       and t.get('rootTurnId')==root_turn for t in matches))
        if not valid:
            if isinstance(turn_id,str):invalid.add(turn_id)
            continue
        row={'turn_id':turn_id,'root_turn_id':root_turn,'final_message':message,
             'native_status':'completed','native_error':None}
        if turn_id in completed and completed[turn_id]!=row:invalid.add(turn_id)
        completed[turn_id]=row
    return [row for key,row in completed.items() if key not in invalid]


def reviewer_evidence(root,parent,*,home=None):
    home=Path(home) if home is not None else root/'home'
    native_events=[event for path in root.glob('events-*.json')
                   for event in json.loads(path.read_text(encoding='utf-8'))]
    rows=[];databases=list(home.glob('state_*.sqlite'))
    if len(databases)!=1:raise RuntimeError('Native reviewer evidence requires exactly one initialized state database')
    with closing(sqlite3.connect(_support.sqlite_filename(databases[0],read_only=True),uri=True)) as db, db:
        children=db.execute('SELECT id,source,rollout_path,agent_path FROM threads WHERE agent_path IS NOT NULL').fetchall()
    for child,source,path,agent in children:
        origin=json.loads(source) if source.startswith('{') else {}
        if origin.get('subagent',{}).get('thread_spawn',{}).get('parent_thread_id')!=parent:continue
        rollout=Path(path)
        if not _support.comparison_path(rollout).is_relative_to(_support.comparison_path(home)) or not rollout.is_file():continue
        events=[json.loads(line) for line in rollout.read_text(encoding='utf-8').splitlines()]
        complete=native_reviewer_completions(events,native_events,child)
        message=complete[-1]['final_message'] if complete else None
        rows.append({'thread':child,'agent_path':agent,'completed':bool(complete),
                     'completed_turns':len(complete),'completed_turn_ids':[r['turn_id'] for r in complete],
                     'verified_completions':complete,'final_message':message,'last_message':message})
    return rows


def collect_reviewer_evidence(report,root,*,home):
    """Keep the primary task failure when reviewer initialization never occurred."""
    try:
        report['reviewers']=reviewer_evidence(root,report.get('thread'),home=home)
        report['reviewer_evidence_status']={'status':'available'}
    except Exception as error:
        report['reviewers']=[]
        report['reviewer_evidence_status']={'status':'not_started' if not report.get('thread') else 'failed',
                                          'error_type':type(error).__name__,'error':str(error)}
        report.setdefault('error','Required native reviewer evidence is unavailable')
    report['fresh_review']=fresh_reviewer_completed(report['reviewers'],report.get('final_turn_id'))


def fresh_reviewer_completed(reviews,parent_turn,previous=None):
    previous=previous or {}
    return any(completion['root_turn_id']==parent_turn
               and completion['turn_id'] not in previous.get(row['thread'],set())
               for row in reviews for completion in row['verified_completions'])


def cleanup_real_work(report,peer,oracle_guard,root):
    """Retain the task failure, attempt both cleanups, and persist unsafe teardown."""
    errors=[]
    for phase,operation in [("peer.close",peer.close if peer is not None else None),
                            ("oracle.restore",oracle_guard.restore)]:
        if operation is None:continue
        try:operation()
        except Exception as error:
            errors.append({"phase":phase,"type":type(error).__name__,"error":str(error)})
    report['cleanup_errors']=errors
    if not errors:return True
    if not report.get('error'):report['error']='Real-work cleanup failed'
    report['passed']=False
    # evidence reads the protected probe. Never request it after failed cleanup.
    report['oracle_guard']={'restore_succeeded':not any(e['phase']=='oracle.restore' for e in errors),
                            'evidence_status':'not_verified_after_cleanup_failure',
                            'source_unchanged':False}
    (root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return False


def scenario(name):
    root = BASE/name
    work=prepare_workspace(root)
    tools_before=real_work_tools_binding(root,tool_environment(root))
    if not tools_before['same_bytes']:raise ValueError('Selected real-work tools differ from the pinned original')
    controls=load_work_controls(root,name,tools_before)
    home=Path(RUNTIME_HOME).resolve() if RUNTIME_HOME is not None else root/'home'
    controlled_inputs=real_work_input_hashes(root,name)
    if (root/'results.json').exists():
        raise RuntimeError(f'Existing acceptance evidence at {root}; prepare a fresh fixture directory')
    entry=Path(os.environ.get('CODEX_DEEPSEEK_TEST_ENTRY') or Path.home()/'.local/bin/codex-deepseek').resolve()
    release=qualified_entry_release(entry,RUNTIME_HOME)
    metadata=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    verifiers=[Path(__file__),Path(__file__).with_name('verify_live.py'),Path(__file__).with_name('acceptance_assertions.py'),Path(__file__).with_name('host_execution_acceptance.py'),Path(__file__).with_name('real_work_imports.py'),Path(__file__).with_name('windows_acceptance_setup.py'),Path(__file__).with_name('qa_workspace.py')]
    (root/'artifact.json').write_text(json.dumps({'entry':str(entry),'release':str(release),'runtime_home':str(home),'entry_sha256':hashlib.sha256(entry.read_bytes()).hexdigest(),'installation':metadata,'verifier_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in verifiers}},indent=2), encoding='utf-8')
    if RUNTIME_HOME is None:
        home.mkdir(exist_ok=True)
        (home/'config.toml').write_text(f'''sandbox_mode = {json.dumps(SANDBOX_MODE)}
model_reasoning_effort = "high"
model_auto_compact_token_limit = {COMPACTION_LIMIT}
[sandbox_workspace_write]
network_access = true
[features]
multi_agent_v2 = true
''', encoding='utf-8')
    else:
        import tomllib
        effective=tomllib.loads((home/'config.toml').read_text(encoding='utf-8'))
        if effective.get('sandbox_mode')!=SANDBOX_MODE or effective.get('windows',{}).get('sandbox')!='elevated':
            raise ValueError('Explicit Windows candidate profile must already select workspace sandbox and elevated backend')
    py = tool_python(root)
    common = f'''You are maintaining the real open-source {name} repository in the current workspace. Fix the production bug described below, add meaningful regression coverage, and run the full existing test suite. Python and dependencies are already provisioned: use {py} -B -m pytest with PYTHONPATH={work/('src' if name=='jinja' else '')}. Do not install dependencies, access sibling directories, fetch upstream code, look up existing patches, change git history, or push. All source edits and test edits must stay within this workspace. You are explicitly authorized to spawn one native subagent for independent read-only review of the relevant code and your final changes; no descendants. Actually use that reviewer and address its findings. Preserve public API and existing behavior. Use normal native tools and inspect actual output. Do not finish before testing. Explain the mechanism, final fix, and test results. '''
    if os.name=='nt':common+=' This is native Windows acceptance: run the provided Windows Python directly in PowerShell with & and quoted paths. Do not run WSL, Bash, containers or another operating system. These restrictions also apply to your reviewer. '
    if name == 'jinja':
        task = '''Production template compilation bug: when a template reads an incoming parameter in an if branch and then reassigns that same variable in every branch, the incoming parameter can incorrectly become undefined. Minimal example: {% if True %}{{ a.b }}{% set a = 1 %}{% elif False %}{% set a = 2 %}{% else %}{% set a = 3 %}{% endif %}{{ a }} with a={"b": 0} must render "01". Diagnose symbol tracking and branch semantics; cover multiple branch selection cases, nested conditionals, and preserve incoming values whenever needed. Run the complete tests directory.'''
    else:
        shutil.copyfile(root/'tls_socket_regression.py',root/'work/acceptance_tls_socket.py')
        task = '''Production async connection-pool cancellation bug: cancelling a request while an expired idle connection is being closed can leak the request in the queue and leave the pool unusable. A mock stream whose aclose awaits 50ms, keepalive_expiry=1ms, and a second request cancelled after 10ms reproduces it after the first response and an idle period. Repair cancellation safety across acquisition, connection cleanup, response-stream closure, and queue reassignment, considering both asyncio and trio and consistency with the sync implementation. Add real deterministic regressions, ensure no requests remain queued after cancellation, and verify a subsequent request succeeds. Preserve fairness and pool limits. Run the complete tests directory.'''
        task += ' Also fix cancellation during TLS initialization so the raw TCP stream is closed, not merely removed from the pool. A real-socket reproduction is supplied as acceptance_tls_socket.py in your workspace. Preserve its assertions and use the real network backend; add an appropriate regression and verify both asyncio and Trio.'
        if os.name=='nt':task+=' The pinned historical code also fails the existing synchronous and asynchronous immediate-expiry tests on Windows: keepalive_expiry=0 must always close the idle connection, including when the monotonic clock has not advanced between calls. Fix this production defect as part of the task, preserving those tests and using deterministic regression coverage rather than sleeping or changing the system clock.'
    if REQUIRE_WEB_RESEARCH:
        url='https://jinja.palletsprojects.com/en/stable/templates/' if name=='jinja' else 'https://docs.python.org/3/library/asyncio-task.html'
        phrase='Assignments' if name=='jinja' else 'Task cancellation'
        task+=' Before finalizing the fix, use the bundled codex_web MCP browser to actually open '+url+', use find to locate '+repr(phrase)+', and use click to follow a relevant link to a DIFFERENT documentation page. Read that page and relate the documentation to the fix. Cite both documentation URLs in your final explanation. Search excerpts and shell/curl fetches do not satisfy this browser acceptance. Official documentation reading is explicitly allowed; the prohibition on fetching upstream code or patches still applies.'
    oracle=root/'oracle'
    oracle_guard=OracleGuard(oracle)
    def oracle_sources():
        return {str(path.relative_to(oracle)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(oracle.rglob('*')) if path.is_file() and not any(part in {'__pycache__','.pytest_cache','.git'} for part in path.parts)}
    oracle_before=oracle_sources()
    peer = None
    turn = None
    index = 0
    started = time.monotonic()
    report = {'case': name, 'scope': 'native-runtime', 'source_id':metadata['source_id'],'started': time.time(), 'restarted': False,'sandbox_mode':SANDBOX_MODE}
    report['git_preflight']=job_git_preflight(root)
    (root/'task.txt').write_text(common+task, encoding='utf-8')
    try:
        if not report['git_preflight']['passed']:raise RuntimeError('Job Git preflight failed before peer creation')
        oracle_guard.lock()
        peer=Peer(root,runtime_home=RUNTIME_HOME,environment={'PYTHONDONTWRITEBYTECODE':'1'})
        thread = peer.new(sandbox=SANDBOX_MODE)
        report['thread'] = thread
        turn, index = peer.start(thread, common+task)
        report['final_turn_id']=turn
        last_count = 0
        end = time.monotonic()+1800
        while time.monotonic() < end:
            completed = [e for e in peer.events[index:] if e.get('method')=='turn/completed' and e.get('params',{}).get('threadId')==thread and e.get('params',{}).get('turn',{}).get('id')==turn]
            if completed:
                report['turn_status'] = completed[-1]['params']['turn']['status']
                break
            interrupted=operator_interrupt(peer,root,thread,index,turn,deadline=end)
            if interrupted is not None:
                report['operator_interrupt']=interrupted
                report['turn_status']='interrupted' if interrupted['native_confirmed'] else None
                raise RuntimeError('Operator requested native turn interruption' if interrupted['native_confirmed'] else 'Operator native interruption failed')
            if peer.process.poll() is not None:
                raise RuntimeError('Native wrapper exited')
            if name=='httpcore' and not report['restarted']:
                diff = subprocess.run(['git','diff','--name-only'], cwd=workspace_path(root),capture_output=True,text=True).stdout.splitlines()
                if any(p.startswith('httpcore/') for p in diff):
                    report['checkpoint_files'] = diff
                    peer.call('turn/interrupt', {'threadId': thread, 'turnId': turn})
                    peer.complete(thread,index,expected_status='interrupted',turn_id=turn)
                    peer.close()
                    peer = Peer(root,runtime_home=RUNTIME_HOME,environment={'PYTHONDONTWRITEBYTECODE':'1'})
                    peer.call('thread/resume', {'threadId': thread})
                    report['restarted'] = True
                    turn,index = peer.start(thread, 'The client was intentionally interrupted and restarted for recovery validation. Continue the original production fix from saved context and current uncommitted files. Inspect the current state first; do not assume a command finished. Reuse or close the previous reviewer as appropriate and finish full regression testing. Same original restrictions apply.')
                    report['final_turn_id']=turn
                    log(name, {'recovery': 'cold_resume_after_source_edits', 'files':diff})
            if len(peer.events) != last_count:
                items=[e['params']['item'] for e in peer.events[last_count:] if e.get('method')=='item/completed']
                short=[{'type':i.get('type'),'text':(i.get('text') or i.get('aggregatedOutput') or str(i.get('command','')))[-380:]} for i in items[-3:]]
                log(name, {'seconds':round(time.monotonic()-started),'events':len(peer.events),'recent':short})
                last_count=len(peer.events)
            time.sleep(5)
        else:
            peer.call('turn/interrupt',{'threadId':thread,'turnId':turn})
            raise TimeoutError('Real development workflow exceeded 30 minutes')
        report['final_messages']=[e['params']['item'].get('text','') for e in peer.events if e.get('method')=='item/completed' and e.get('params',{}).get('threadId')==thread and e['params']['item'].get('type')=='agentMessage']
    except Exception as exc:
        report['error']=str(exc)
        report['primary_error']={'type':type(exc).__name__,'error':str(exc)}
    finally:
        report['seconds']=round(time.monotonic()-started,2)
        cleanup_ok=cleanup_real_work(report,peer,oracle_guard,root)
    if not cleanup_ok:return report
    validation_checkpoint(root,report)
    if not report['git_preflight']['passed'] or report.get('operator_interrupt'):
        report['validation_status']='not_started_after_preflight_or_operator_stop'
        (root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report
    report['oracle_guard']={**oracle_guard.evidence,'source_unchanged':oracle_sources()==oracle_before,'source_files':len(oracle_before)}
    report['seconds']=round(time.monotonic()-started,2)
    work=Path(workspace_path(root));pp=work/'src' if name=='jinja' else work
    report['validation']=[run_test(root,'workspace',['-q','tests'],pp),run_test(root,'upstream-heldout',['-q',str(root/'oracle/tests')],pp)]
    if name=='httpcore':
        report['validation'].append(run_test(root,'independent-cancellation',['-q',str(root/'cancellation_regression.py')],pp))
        report['validation'].extend(run_script(root,'real-tcp-'+str(i),'network_stress.py') for i in range(3))
        report['validation'].append(run_script(root,'tls-socket','tls_socket_regression.py'))
    report['diff_check']=subprocess.run(['git','diff','--check'],cwd=work,capture_output=True,text=True).returncode
    report['diff_stat']=subprocess.run(['git','diff','--stat'],cwd=work,capture_output=True,text=True).stdout
    (root/'agent.patch').write_bytes(complete_work_patch(work))
    collect_reviewer_evidence(report,root,home=home)
    report['startup_failures']=[json.loads(path.read_text(encoding='utf-8')) for path in root.glob('startup-failure-*.json')]
    browser=home/'web-cache/pages.sqlite';pages=[]
    if browser.exists():
        with closing(sqlite3.connect(_support.sqlite_filename(browser,read_only=True),uri=True)) as db, db:
            pages=[{key:page.get(key) for key in ['url','source_sha256','retrieved_at']} for (raw,) in db.execute('SELECT payload FROM pages') for page in [json.loads(raw)]]
    persisted_events=[event for path in root.glob('events-*.json') for event in json.loads(path.read_text(encoding='utf-8'))]
    report['host_platform']=sys.platform
    report['native_host_execution']=observed_host_execution(persisted_events,root,controls,release)
    browser_calls=[event['params']['item'] for event in persisted_events if event.get('method')=='item/completed' and event.get('params',{}).get('item',{}).get('type')=='mcpToolCall' and event['params']['item'].get('server')=='codex_web']
    completed_actions=sorted({call['tool'] for call in browser_calls if call.get('status')=='completed' and not call.get('error')})
    report['web_research']={'required':REQUIRE_WEB_RESEARCH,'pages':pages,'distinct_actual_urls':len({p['url'] for p in pages}),'native_completed_actions':completed_actions}
    report['web_research']['current_turn']=current_browser_evidence(peer.events if peer else [],report.get('thread'),index,turn=turn)
    current_inputs=real_work_input_hashes(root,name)
    report['fixture_inputs']={'before':controlled_inputs,'after':current_inputs,'unchanged':controlled_inputs==current_inputs}
    tools_after=real_work_tools_binding(root,tool_environment(root))
    report['tool_environment']={'before':tools_before,'after':tools_after,'unchanged':tools_before==tools_after}
    report['qualification_controls']={'path':controls['control_path'],'sha256':controls['control_sha256'],
        'baseline_sha256':{suite:baseline['sha256'] for suite,baseline in controls['pytest_baselines'].items()}}
    controls_after=load_work_controls(root,name,tools_after,initial=False)
    report['validation_status']='completed'
    report['passed']=real_work_passed(report,pytest_baselines=controls['pytest_baselines']) and tools_before==tools_after and controls==controls_after
    (root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2), encoding='utf-8')
    log(name,report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--fixtures',type=Path,default=BASE)
    parser.add_argument('--controls',type=Path,required=True,help='External original source/test inventories pinned by the controller before model launch')
    parser.add_argument('--tools-root',type=Path,help='Explicit read-only copies under ROOT/<case>/tools311; original fixtures remain pinned')
    parser.add_argument('--case',choices=['jinja','httpcore'],help='Run one real project; required for an existing pinned runtime profile')
    parser.add_argument('--runtime-home',type=Path,help='Reuse an explicitly owned, prequalified profile without rewriting its config')
    parser.add_argument('--compaction-limit',type=int,default=COMPACTION_LIMIT,
                        help='Use 90000 only for deliberate repeated-compaction stress testing')
    parser.add_argument('--require-web-research',action='store_true')
    parser.add_argument('--sandbox-mode',choices=['workspace-write','danger-full-access'],default=qa_sandbox_mode(),
                        help='Full-access real-work acceptance must be paired with separate native sandbox enforcement evidence')
    options=parser.parse_args()
    BASE=options.fixtures.resolve()
    CONTROLS=options.controls.resolve(strict=True)
    TOOLS_ROOT=options.tools_root.resolve(strict=True) if options.tools_root else None
    RUNTIME_HOME=options.runtime_home.resolve() if options.runtime_home else None
    if RUNTIME_HOME is not None and not options.case:parser.error('--runtime-home requires one --case')
    COMPACTION_LIMIT=options.compaction_limit
    REQUIRE_WEB_RESEARCH=options.require_web_research
    SANDBOX_MODE=options.sandbox_mode
    if options.case:results=[scenario(options.case)]
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(scenario,['httpcore','jinja']))
    (BASE/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2), encoding='utf-8')
    raise SystemExit(0 if all(r['passed'] for r in results) else 1)
