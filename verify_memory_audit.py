"""Native persistent memory extraction, consolidation, cold read and sampling."""
from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path,PureWindowsPath
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import uuid

from verify_live import Peer,text,qa_sandbox_mode,prepare_workspace,workspace_path
from process_acceptance import shell_command
from host_execution_acceptance import host_execution_evidence


def model_quality_observation(answer,nonce):
    """Record wording/recall without making either a native-runtime gate."""
    return {'user_identifier_retained':nonce in answer,
            'integer_word_present':'integer' in answer.lower(),
            'idempotency_word_present':'idempoten' in answer.lower(),
            'outside_runtime_pass_criterion':True}


def literal_posix_tokens(command):
    """Small allowlist grammar, not a shell evaluator or a substring search."""
    if any(char in command for char in '\r\n\0'):return None
    tokens=[];word=[];started=False;quote=None;index=0
    while index<len(command):
        char=command[index]
        if quote=="'":
            if char==quote:quote=None
            else:word.append(char)
        elif quote=='"':
            if char==quote:quote=None
            elif char in '$`':return None
            elif char=='\\':
                index+=1
                if index==len(command):return None
                if command[index] not in '$`"\\':word.append('\\')
                word.append(command[index])
            else:word.append(char)
        elif char in ' \t':
            if started:tokens.append(('word',''.join(word)));word=[];started=False
        elif char in "'\"":quote=char;started=True
        elif char=='\\':
            index+=1
            if index==len(command):return None
            word.append(command[index]);started=True
        elif char=='&' and command[index:index+2]=='&&':
            if started:tokens.append(('word',''.join(word)));word=[];started=False
            tokens.append(('operator','&&'));index+=1
        elif char in '$`;&|()<>*?[]{}!#~':return None
        else:word.append(char);started=True
        index+=1
    if quote:return None
    if started:tokens.append(('word',''.join(word)))
    return tokens


def literal_powershell_arguments(command):
    # Preserve the exact host command emitted by shell_command(), including
    # literal quoted paths. No expression, pipeline, redirection or statements.
    if any(char in command for char in '\r\n\0'):return None
    if not command.startswith('& '):return None
    command=command[2:];arguments=[]
    token=re.compile(r"'(?:[^']|'')*'|\"(?:[^\"$`]|\"\")*\"|[A-Za-z0-9_./:\\\-]+")
    while command:
        found=token.match(command)
        if not found:return None
        value=found.group()
        if value.startswith("'"):value=value[1:-1].replace("''", "'")
        elif value.startswith('"'):value=value[1:-1].replace('""','"')
        arguments.append(value)
        command=command[found.end():]
        if command and not command[0].isspace():return None
        command=command.lstrip(' \t')
    return arguments


def verification_command_shape(command,interpreter,verifier,workspace,cwd=None):
    """Accept only the exact verifier, optionally after reading this ledger."""
    if not isinstance(command,str):return None
    expected=[str(interpreter),'-I',str(verifier),str(workspace)]
    windows=PureWindowsPath(str(interpreter)).is_absolute()
    try:outer=shlex.split(command)
    except ValueError:return None
    if windows:
        if outer and PureWindowsPath(outer[0]).name.lower() in {'powershell.exe','pwsh.exe'}:
            if (not PureWindowsPath(outer[0]).is_absolute() or len(outer)<3
                    or outer[-2].lower()!='-command'
                    or any(flag.lower() not in {'-noprofile','-nologo','-noninteractive'} for flag in outer[1:-2])):return None
            command=outer[-1]
        arguments=literal_powershell_arguments(command)
        if (not arguments or len(arguments)!=4 or arguments[1]!='-I'
                or any(PureWindowsPath(arguments[index])!=PureWindowsPath(expected[index]) for index in (0,2,3))):return None
        return {'ledger_read_prefix':False}
    shells={'/bin/bash','/bin/zsh','/bin/sh','/usr/bin/bash','/usr/bin/zsh','/usr/bin/sh'}
    if outer and outer[0] in shells:
        if len(outer)!=3 or outer[1] not in {'-c','-lc'}:return None
        command=outer[2]
    tokens=literal_posix_tokens(command)
    verifier_tokens=[('word',value) for value in expected]
    if tokens==verifier_tokens:return {'ledger_read_prefix':False}
    # One literal file read followed by && is allowed. No other command or
    # conditional can skip/replace the verifier while still yielding exit 0.
    if tokens and tokens[-5:]==[('operator','&&'),*verifier_tokens]:
        prefix=tokens[:-5]
        allowed_paths={'ledger.py','./ledger.py',str(Path(workspace)/'ledger.py')}
        if (len(prefix)==2 and prefix[0]==('word','cat') and prefix[1][0]=='word'
                and prefix[1][1] in allowed_paths and str(cwd)==str(workspace)):
            return {'ledger_read_prefix':True}
    return None


def native_verification_commands(items,interpreter,verifier,workspace):
    accepted=[];marker='NATIVE_LEDGER_VERIFIED'
    for item in items:
        if (item.get('type')!='commandExecution' or item.get('status')!='completed'
                or item.get('exitCode')!=0 or not isinstance(item.get('aggregatedOutput'),str)):continue
        shape=verification_command_shape(item.get('command'),interpreter,verifier,workspace,item.get('cwd'))
        if shape is None:continue
        prefix=''
        if shape['ledger_read_prefix']:
            try:prefix=(Path(workspace)/'ledger.py').read_text(encoding='utf-8')
            except (OSError,UnicodeError):continue
            if prefix and not prefix.endswith('\n'):continue
        output=item['aggregatedOutput'].replace('\r\n','\n')
        if output in {prefix+marker,prefix+marker+'\n'}:accepted.append(item)
    return accepted


def publish_memory_verifier(root,work,source):
    if work==root/'work':return source,None
    # Only a read-only input is published; its private original stays preserved.
    published=work.parent/(work.name+'-verify_ledger_fixture.py')
    with published.open('xb') as stream:stream.write(source.read_bytes())
    binding={'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
             'scope':'Owned QA executable input outside workspace writable roots',
             'files':{}}
    for name,path in [('source',source),('published',published)]:
        info=path.stat()
        binding['files'][name]={'path':str(path),'identity':[info.st_dev,info.st_ino]}
    (root/'qa-readonly-fixture.json').write_text(json.dumps(binding,indent=2),encoding='utf-8')
    return published,binding


def memory_source_options(root, verifier, sandbox_mode):
    """Give the owned verifier a native read grant without granting writes."""
    if sys.platform != 'win32' or sandbox_mode != 'workspace-write':
        return {'sandbox': sandbox_mode}
    profile = 'cds-memory-verification'
    config = root / 'home/config.toml'
    with config.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write('\n[permissions.' + profile + ']\nextends = ":workspace"\n'
                     '[permissions.' + profile + '.filesystem]\n'
                     + json.dumps(str(verifier)) + ' = "read"\n')
    return {'sandbox': None, 'permissions': profile}


def memory_verifier_binding_unchanged(binding):
    if binding is None:return True
    try:
        for item in binding['files'].values():
            path=Path(item['path']);info=path.lstat()
            if (path.is_symlink() or getattr(info,'st_file_attributes',0)&0x400 or info.st_nlink!=1
                    or [info.st_dev,info.st_ino]!=item['identity']
                    or hashlib.sha256(path.read_bytes()).hexdigest()!=binding['sha256']):return False
        return set(binding['files'])=={'source','published'}
    except (OSError,KeyError,TypeError):return False


def source_work_evidence(items,verified,ledger,ledger_hash,fixtures,interpreter,verifier,nonce,verifier_binding=None):
    """Bind native tool execution to independently checked filesystem effects."""
    fixture_hashes={str(path):hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None for path in fixtures}
    unchanged=all(fixture_hashes[str(path)]==expected for path,expected in fixtures.items())
    binding_ok=memory_verifier_binding_unchanged(verifier_binding)
    unchanged=unchanged and binding_ok
    current_ledger_hash=hashlib.sha256(ledger.read_bytes()).hexdigest() if ledger.is_file() else None
    changed=current_ledger_hash is not None and current_ledger_hash!=ledger_hash
    commands=native_verification_commands(items,interpreter,verifier,ledger.parent)
    file_changes=[item for item in items if item.get('type')=='fileChange' and item.get('status')=='completed']
    # Shell edits are also native tool work. A fileChange notification is useful
    # evidence, but the hard checks are a real command, a changed file and the
    # independent oracle against unmodified fixtures.
    return {'passed':verified.returncode==0 and verified.stdout.strip()=='NATIVE_LEDGER_VERIFIED' and unchanged and changed and bool(commands),
            'answer':text(items),'verification_fixture_unchanged':unchanged,
            'fixture_sha256':fixture_hashes,'readonly_verifier_binding':verifier_binding,'readonly_verifier_unchanged':binding_ok,'verification_exit_code':verified.returncode,
            'verification_stdout':verified.stdout,'verification_stderr':verified.stderr,
            'ledger_changed':changed,'ledger_sha256_before':ledger_hash,'ledger_sha256_after':current_ledger_hash,
            'native_verification_commands':commands,'native_completed_file_changes':file_changes,
            'model_quality_observation':model_quality_observation(text(items),nonce)}


def message_texts(body,roles):
    return [content['text'] for item in body.get('input',[])
            if isinstance(item,dict) and item.get('type')=='message' and item.get('role') in roles and isinstance(item.get('content'),list)
            for content in item['content'] if isinstance(content,dict) and isinstance(content.get('text'),str)]


def turn_metadata(body):
    value=body.get('client_metadata',{}).get('x-codex-turn-metadata','{}')
    try:decoded=json.loads(value)
    except (TypeError,ValueError):return {}
    return decoded if isinstance(decoded,dict) else {}


def sample_context_evidence(paths,source,source_prompt,sample_prompt):
    evidence=[]
    for path in paths:
        body=json.loads(path.read_text(encoding='utf-8'));metadata=turn_metadata(body)
        messages=message_texts(body,{'user'})
        tool_outputs=[item for item in body.get('input',[]) if isinstance(item,dict) and item.get('type') in {'function_call_output','custom_tool_call_output'}]
        if (metadata.get('thread_source')=='thread_sample' and metadata.get('history_initialization')=='warm_fork'
                and metadata.get('forked_from_thread_id')==source
                and metadata.get('thread_id')==body.get('client_metadata',{}).get('thread_id')
                and metadata.get('thread_id') not in {None,source}
                and source_prompt in messages and messages[-1:]==[sample_prompt]
                and any('NATIVE_LEDGER_VERIFIED' in json.dumps(item.get('output')) for item in tool_outputs)):
            evidence.append({'request':path.name,'thread':metadata['thread_id'],'source_thread':source,
                             'request_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return evidence


def cold_memory_evidence(paths,reader,summary_snapshots):
    evidence=[]
    for path in paths:
        body=json.loads(path.read_text(encoding='utf-8'));metadata=turn_metadata(body)
        if (body.get('client_metadata',{}).get('thread_id')!=reader or metadata.get('thread_id')!=reader
                or metadata.get('history_initialization')!='new'):continue
        # Only native guidance counts. A later assistant reply or tool output
        # quoting a file does not establish memory injection on cold startup.
        guidance=message_texts(body,{'system','developer'})
        for name,summary in summary_snapshots.items():
            if summary.strip() and any(summary.strip() in value for value in guidance):
                evidence.append({'request':path.name,'thread':reader,'artifact':name,
                                 'artifact_sha256':hashlib.sha256(summary.encode()).hexdigest(),
                                 'request_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return evidence


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--version',choices=['v1','v2'],default='v2')
    parser.add_argument('--sandbox-mode',choices=['workspace-write','danger-full-access'],default=qa_sandbox_mode());args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    work=prepare_workspace(root)
    (root/'home/config.toml').write_text(f'sandbox_mode="{args.sandbox_mode}"\nmodel_reasoning_effort="low"\n[features]\nmemories=false\n[memories]\nversion="{args.version}"\nmin_rollout_idle_hours=1\nmax_rollouts_per_startup=4\nuse_memories=true\n', encoding='utf-8')
    nonce='MIGRATION_'+uuid.uuid4().hex
    (work/'ledger.py').write_text('import sqlite3\ndef credit(db,key,amount):\n    db.execute("INSERT INTO credits VALUES (?,?)",(key,amount))\n    db.commit()\n', encoding='utf-8')
    ledger_hash=hashlib.sha256((work/'ledger.py').read_bytes()).hexdigest()
    (work/'test_ledger.py').write_text('import sqlite3\nfrom ledger import credit\ndef test_repeated_credit_is_idempotent():\n    db=sqlite3.connect(":memory:")\n    db.execute("CREATE TABLE credits (key TEXT PRIMARY KEY, amount INTEGER NOT NULL)")\n    credit(db,"same",125)\n    credit(db,"same",125)\n    assert db.execute("SELECT sum(amount) FROM credits").fetchone()[0]==125\n', encoding='utf-8')
    test_hash=hashlib.sha256((work/'test_ledger.py').read_bytes()).hexdigest()
    verifier=root/'verify_ledger_fixture.py'
    private_verifier=verifier
    verifier.write_text('import sys\nsys.path.insert(0,sys.argv[1])\nfrom test_ledger import test_repeated_credit_is_idempotent\ntest_repeated_credit_is_idempotent()\nprint("NATIVE_LEDGER_VERIFIED")\n',encoding='utf-8')
    verifier,verifier_binding=publish_memory_verifier(root,work,verifier)
    verifier_hash=hashlib.sha256(verifier.read_bytes()).hexdigest()
    verification_command=shell_command([sys.executable,'-I',str(verifier),str(work)])
    results=[];peer=None
    def passed(name,condition,detail):
        record={'name':name,'passed':bool(condition),'sandbox_mode':args.sandbox_mode,'detail':detail};results.append(record)
        (root/'results.json').write_text(json.dumps(results,indent=2), encoding='utf-8');print(json.dumps(record),flush=True)
        if not condition:raise AssertionError(name)
    try:
        source_options=memory_source_options(root,verifier,args.sandbox_mode)
        peer=Peer(root);source=peer.new(**source_options)
        source_prompt=f'This is the persistent ledger project for {nonce}. My durable project preferences: store money as integer cents, use SQLite transactions, preserve existing customer data, and treat repeated identical idempotency keys as a successful no-op. Never replace those requirements with floating-point arithmetic or destructive migration. Fix only ledger.py so the supplied repeat-credit test passes. Do not modify test_ledger.py or the supplied verification fixture. Run this exact verification command using the installed native host Python; do not use WSL, Cygwin, MSYS, Docker, Podman or another interpreter/environment:\n```\n{verification_command}\n```\nDo not delegate, search, or write memory artifacts yourself. Explain the root cause, fix, and actual verification.'
        items=peer.turn(source,source_prompt)
        host_evidence=host_execution_evidence(items)
        observed_host=bool(native_verification_commands(items,sys.executable,verifier,work))
        passed('memory_source_uses_native_host_environment',host_evidence['passed'] and observed_host,{**host_evidence,'native_host_python_observed':observed_host,'declared_interpreter':sys.executable})
        verified=subprocess.run([sys.executable,'-I',str(verifier),str(work)],capture_output=True,text=True,encoding='utf-8')
        work_evidence=source_work_evidence(items,verified,work/'ledger.py',ledger_hash,{work/'test_ledger.py':test_hash,verifier:verifier_hash,private_verifier:verifier_hash},sys.executable,verifier,nonce,verifier_binding)
        passed('memory_source_real_tool_work',work_evidence['passed'],work_evidence)
        completed=[event['params']['turn']['id'] for event in peer.events if event.get('method')=='turn/completed' and event.get('params',{}).get('threadId')==source][-1]
        sample_prompt='Without tools, return the persistent project identifier and its integer-money convention.'
        before_sample=set((root/'adapter-traces').glob('*-request.json'))
        sample=peer.call('thread/sample',{'threadId':source,'expectedTurnId':completed,'prompt':sample_prompt})
        sample_context=sample_context_evidence(set((root/'adapter-traces').glob('*-request.json'))-before_sample,source,source_prompt,sample_prompt)
        passed('native_thread_sample_uses_loaded_deepseek_context',sample.get('sourceTurnId')==completed and bool(sample_context) and isinstance(sample.get('text'),str) and bool(sample.get('responseId')),
               {**sample,'native_loaded_context':sample_context,'model_quality_observation':model_quality_observation(sample.get('text') or '',nonce)})
        peer.close();peer=None
        # Native clamps the idle threshold to at least one hour. Follow the
        # upstream startup test's aged-candidate fixture; do not alter runtime
        # eligibility rules or wait an hour to test the background pipeline.
        database=next((root/'home').glob('state_*.sqlite'))
        with closing(sqlite3.connect(database,timeout=10)) as db, db:
            before=db.execute('SELECT updated_at,updated_at_ms FROM threads WHERE id=?',(source,)).fetchone()
            db.execute('UPDATE threads SET updated_at=updated_at-7200,updated_at_ms=updated_at_ms-7200000 WHERE id=?',(source,))
            after=db.execute('SELECT updated_at,updated_at_ms FROM threads WHERE id=?',(source,)).fetchone()
        (root/'fixture-clock.json').write_text(json.dumps({'reason':'upstream-style aged idle candidate','thread':source,'before':before,'after':after},indent=2), encoding='utf-8')
        config=root/'home/config.toml'
        config.write_text(config.read_text(encoding='utf-8').replace('memories=false','memories=true'), encoding='utf-8')
        peer=Peer(root);trigger=peer.new(sandbox=args.sandbox_mode)
        peer.turn(trigger,'Do not use tools or delegate. Reply exactly MEMORY_PIPELINE_TRIGGER.')
        end=time.monotonic()+300;selected=[];jobs=[]
        while time.monotonic()<end:
            databases=list((root/'home').glob('memories_v2_*.sqlite' if args.version=='v2' else 'memories_[0-9]*.sqlite'))
            if databases:
                with closing(sqlite3.connect(databases[0],timeout=10)) as db, db:
                    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'stage1_outputs' in tables:
                        db.row_factory=sqlite3.Row
                        selected=[dict(row) for row in db.execute('SELECT * FROM stage1_outputs') if row['thread_id']==source]
                    if 'jobs' in tables:jobs=[list(row) for row in db.execute('SELECT * FROM jobs')]
            memory_root=root/'home'/('memories_v2' if args.version=='v2' else 'memories')
            artifacts=list((memory_root/'rollout_summaries').glob('*.md')) if args.version=='v2' else list(memory_root.glob('MEMORY.md'))
            summaries=[path for path in (root/'home').rglob('memory_summary.md') if '.git' not in path.parts]
            if selected and any(row.get('selected_for_phase2') for row in selected) and artifacts and summaries:break
            time.sleep(.25)
        (root/'memory-db-evidence.json').write_text(json.dumps({'source':source,'selected':selected,'jobs':jobs},indent=2), encoding='utf-8')
        passed('native_memory_extraction_and_consolidation',bool(selected) and any(row.get('selected_for_phase2') for row in selected) and bool(artifacts) and bool(summaries),{'selected':selected,'artifacts':[str(path.relative_to(root)) for path in artifacts+summaries]})
        traces=[json.loads(path.read_text(encoding='utf-8')) for path in (root/'adapter-traces').glob('*-request.json')]
        passed('memory_auxiliary_models_remain_pinned_deepseek',bool(traces) and all(body['model']=='deepseek/deepseek-v4.1-flash-1017' for body in traces),{'requests':len(traces),'routes':sorted({body['provider']['only'][0] for body in traces})})
        summary_snapshots={str(path.relative_to(root)):path.read_text(encoding='utf-8') for path in summaries}
        peer.close();peer=Peer(root);reader=peer.new(sandbox=args.sandbox_mode)
        items=peer.turn(reader,'Consult the native memory guidance for this workspace. What are the ledger project identifier and durable money/idempotency conventions? Do not open project source files or delegate.')
        # Summary content selection is model quality. Runtime acceptance binds
        # actual generated artifacts to the new cold thread's native input.
        cold=cold_memory_evidence((root/'adapter-traces').glob('*-request.json'),reader,summary_snapshots)
        passed('native_memory_cold_read',bool(cold),{'native_exact_artifact_injection':cold,'answer':text(items),'model_quality_observation':model_quality_observation(text(items),nonce)})
    finally:
        if peer:peer.close()
        print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
