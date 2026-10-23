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


def tool_environment(root):
    return TOOLS_ROOT/root.name/'tools311' if TOOLS_ROOT is not None else root/'tools311'


def tool_python(root):
    return python_entry(tool_environment(root)).resolve(strict=True)


def log(name, value):
    with PRINT_LOCK:
        print(json.dumps({'case': name, **value}, ensure_ascii=False), flush=True)


def run_test(root, label, args, pythonpath):
    start = time.monotonic()
    imports=root/f'final-{label}-imports.json';package='jinja2' if root.name=='jinja' else 'httpcore'
    if imports.exists():imports.unlink()
    junit=root/f'final-{label}.junit.xml'
    if junit.exists():junit.unlink()
    p = subprocess.run([str(tool_python(root)),'-B',str(Path(__file__).with_name('real_work_imports.py')),
                        '--package',package,'--root',str(pythonpath),'--receipt',str(imports),'pytest','--junitxml='+str(junit),*args], cwd=workspace_path(root),
                       env={**os.environ, 'PYTHONPATH': str(pythonpath),'PYTHONOPTIMIZE':'','PYTHONDONTWRITEBYTECODE':'1'}, capture_output=True, text=True, timeout=180)
    (root/f'final-{label}.txt').write_text(p.stdout+p.stderr, encoding='utf-8')
    proof=json.loads(imports.read_text(encoding='utf-8')) if imports.is_file() else {'passed':False}
    from acceptance_assertions import pytest_execution_evidence
    executed=pytest_execution_evidence(junit) if junit.is_file() else {}
    return {'suite': label, 'exit': p.returncode if p.returncode else (0 if proof.get('passed') is True else 1), 'seconds': round(time.monotonic()-start, 2),
            'source_imports':proof,'pytest_execution':executed,'tail': (p.stdout+p.stderr)[-2200:]}


def run_script(root,label,filename):
    imports=root/f'final-{label}-imports.json'
    if imports.exists():imports.unlink()
    p=subprocess.run([str(tool_python(root)),'-B',str(Path(__file__).with_name('real_work_imports.py')),
                      '--package','httpcore','--root',str(root/'work'),'--receipt',str(imports),'script',str(root/filename)],cwd=workspace_path(root),
                     env={**os.environ,'PYTHONPATH':str(root/'work'),'PYTHONOPTIMIZE':'','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=60)
    (root/f'final-{label}.txt').write_text(p.stdout+p.stderr, encoding='utf-8')
    proof=json.loads(imports.read_text(encoding='utf-8')) if imports.is_file() else {'passed':False}
    return {'suite':label,'exit':p.returncode if p.returncode else (0 if proof.get('passed') is True else 1),
            'source_imports':proof,'tail':(p.stdout+p.stderr)[-2200:]}


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
    rows=[]
    with closing(sqlite3.connect(next(home.glob('state_*.sqlite')))) as db, db:
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


def fresh_reviewer_completed(reviews,parent_turn,previous=None):
    previous=previous or {}
    return any(completion['root_turn_id']==parent_turn
               and completion['turn_id'] not in previous.get(row['thread'],set())
               for row in reviews for completion in row['verified_completions'])


def scenario(name):
    root = BASE/name
    work=prepare_workspace(root)
    tools_before=real_work_tools_binding(root,tool_environment(root))
    if not tools_before['same_bytes']:raise ValueError('Selected real-work tools differ from the pinned original')
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
    (root/'task.txt').write_text(common+task, encoding='utf-8')
    try:
        oracle_guard.lock()
        peer=Peer(root,runtime_home=RUNTIME_HOME,environment={'PYTHONDONTWRITEBYTECODE':'1'})
        thread = peer.new(sandbox=SANDBOX_MODE)
        report['thread'] = thread
        turn, index = peer.start(thread, common+task)
        report['final_turn_id']=turn
        last_count = 0
        end = time.monotonic()+1800
        while time.monotonic() < end:
            completed = [e for e in peer.events[index:] if e.get('method')=='turn/completed' and e.get('params',{}).get('threadId')==thread]
            if completed:
                report['turn_status'] = completed[-1]['params']['turn']['status']
                break
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
    finally:
        try:
            if peer:peer.close()
        finally:oracle_guard.restore()
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
    report['reviewers']=reviewer_evidence(root,report.get('thread'),home=home)
    report['fresh_review']=fresh_reviewer_completed(report['reviewers'],report.get('final_turn_id'))
    browser=home/'web-cache/pages.sqlite';pages=[]
    if browser.exists():
        with closing(sqlite3.connect(browser)) as db, db:
            pages=[{key:page.get(key) for key in ['url','source_sha256','retrieved_at']} for (raw,) in db.execute('SELECT payload FROM pages') for page in [json.loads(raw)]]
    persisted_events=[event for path in root.glob('events-*.json') for event in json.loads(path.read_text(encoding='utf-8'))]
    report['host_platform']=sys.platform
    report['native_host_execution']=native_host_execution(persisted_events,py,sys.platform)
    browser_calls=[event['params']['item'] for event in persisted_events if event.get('method')=='item/completed' and event.get('params',{}).get('item',{}).get('type')=='mcpToolCall' and event['params']['item'].get('server')=='codex_web']
    completed_actions=sorted({call['tool'] for call in browser_calls if call.get('status')=='completed' and not call.get('error')})
    report['web_research']={'required':REQUIRE_WEB_RESEARCH,'pages':pages,'distinct_actual_urls':len({p['url'] for p in pages}),'native_completed_actions':completed_actions}
    report['web_research']['current_turn']=current_browser_evidence(peer.events if peer else [],report.get('thread'),index,turn=turn)
    current_inputs=real_work_input_hashes(root,name)
    report['fixture_inputs']={'before':controlled_inputs,'after':current_inputs,'unchanged':controlled_inputs==current_inputs}
    tools_after=real_work_tools_binding(root,tool_environment(root))
    report['tool_environment']={'before':tools_before,'after':tools_after,'unchanged':tools_before==tools_after}
    report['passed']=real_work_passed(report) and tools_before==tools_after
    (root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2), encoding='utf-8')
    log(name,report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--fixtures',type=Path,default=BASE)
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
