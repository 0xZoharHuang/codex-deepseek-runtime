"""Persistent native daemon, goals, queue and provider worker lifecycle effects."""
import json
import base64
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid
import argparse

from codex_deepseek.processes import matches,terminate
from codex_deepseek.platforms import comparison_path
from codex_deepseek.service import healthy,read_descriptor
from verify_live import Peer,text
from verify_tui_audit import expected_backend
from process_acceptance import python_command

BASE=Path(__file__).resolve().parent.parent
ROOT=BASE/'delivery/independent-audit/daemon-lab'
ENTRY=BASE/'delivery/independent-audit/package-source-entry'


def is_daemon_package_file(path,home):
    if path is None:return False
    found=comparison_path(path)
    return found.is_file() and found.is_relative_to(comparison_path(Path(home)/'packages/app-server-daemon'))


def main():
    global ROOT,ENTRY
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,default=ENTRY);parser.add_argument('--output',type=Path,default=BASE/'delivery/independent-audit/daemon');args=parser.parse_args()
    if os.name=='nt' and expected_backend()=='embedded':
        raise SystemExit('Positive native Windows daemon acceptance requires a non-elevated terminal; this administrator token is native-ineligible. Run this gate in the disposable guest with its non-elevated account.')
    ENTRY=args.entry.resolve();ROOT=args.output.resolve()/str(uuid.uuid4())
    (ROOT/'home').mkdir(parents=True,mode=0o700)
    (ROOT/'home/config.toml').write_text('model_reasoning_effort="low"\nsandbox_mode="workspace-write"\napproval_policy="on-request"\n[features]\ncode_mode=true\ncode_mode_host=true\nmulti_agent_v2=true\n', encoding='utf-8')
    (ROOT/'work').mkdir(exist_ok=True);results=[];peer=None
    def management(command):
        result=subprocess.run([str(ENTRY),'app-server','daemon',command],env={**os.environ,'CODEX_DEEPSEEK_HOME':str(ROOT/'home')},capture_output=True,text=True,encoding='utf-8',timeout=45)
        (ROOT/f'daemon-{command}-{time.time_ns()}.txt').write_text(result.stdout+result.stderr, encoding='utf-8')
        assert result.returncode==0,(command,result.stdout,result.stderr)
        return result
    def connect():return Peer(ROOT,command=[str(ENTRY)],server_args=['app-server','proxy'])
    def passed(name,condition,detail):
        record={'name':name,'passed':bool(condition),'detail':detail};results.append(record)
        (ROOT/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False), encoding='utf-8');print(json.dumps(record,ensure_ascii=False),flush=True)
        if not condition:raise AssertionError(name)
    try:
        management('start')
        peer=connect()
        actual_home=peer.events[0].get('result',{}).get('codexHome')
        passed('native_daemon_proxy_is_own_home',comparison_path(actual_home)==comparison_path(ROOT/'home'),actual_home)
        descriptor=read_descriptor(ROOT/'home')
        # Cross the server's 16 MiB unfragmented-frame budget in both directions.
        payload=(b'Native fragmented transport '+uuid.uuid4().hex.encode()+b'\n')*300000
        large=ROOT/'work/large-transport.bin'
        peer.call('fs/writeFile',{'path':str(large),'dataBase64':base64.b64encode(payload).decode()})
        readback=peer.call('fs/readFile',{'path':str(large)})
        actual=base64.b64decode(readback['dataBase64'])
        passed('native_proxy_fragmented_large_files_roundtrip',len(payload)>16*1024*1024 and actual==payload,
               {'bytes':len(payload),'sha256':hashlib.sha256(actual).hexdigest()})
        config=peer.call('config/read',{'includeLayers':False})
        (ROOT/'effective-config.json').write_text(json.dumps(config,indent=2), encoding='utf-8')
        started=peer.call('thread/start',{'cwd':str(ROOT/'work'),'sandbox':'workspace-write','modelProvider':'openai',
            'config':{'model_providers.deepseek_runtime.base_url':'http://127.0.0.1:1/must-not-be-used'}})
        thread=started['thread']['id']
        passed('native_daemon_request_overrides_cannot_escape_required_provider',started.get('modelProvider')=='deepseek_runtime',{'thread':thread,'effective_provider':started.get('modelProvider')})
        try:peer.call('config/value/write',{'keyPath':'model_provider','value':'openai','mergeStrategy':'replace'})
        except RuntimeError as error:rejected=str(error)
        else:rejected=''
        passed('native_daemon_config_write_respects_managed_binding',bool(rejected),rejected)
        rg=peer.call('command/exec',{'command':python_command('import shutil;print(shutil.which("rg") or "")'),'cwd':str(ROOT/'work'),'timeoutMs':10000})
        found=Path(rg['stdout'].strip()) if rg['stdout'].strip() else None
        passed('daemon_uses_owned_bundled_ripgrep',rg['exitCode']==0 and is_daemon_package_file(found,ROOT/'home'),rg)
        objective='Audit persistent goal metadata '+uuid.uuid4().hex
        goal=peer.call('thread/goal/set',{'threadId':thread,'objective':objective,'status':'paused','tokenBudget':30000,'origin':'user'})['goal']
        read=peer.call('thread/goal/get',{'threadId':thread})['goal']
        passed('native_goal_paused_budget_and_objective',read['objective']==objective and read['status']=='paused' and read['tokenBudget']==30000,read)
        items=peer.turn(thread,'Do not delegate or search the web. Reply exactly DAEMON_CHECKPOINT and remember this thread for the queue acceptance.')
        passed('daemon_model_call_after_launcher_exit',text(items).strip()=='DAEMON_CHECKPOINT',text(items))
        peer.close();peer=None;management('restart');peer=connect()
        loaded=peer.call('thread/loaded/list',{})
        passed('daemon_restart_restores_native_loaded_threads',thread in loaded.get('data',[]),loaded)
        peer.call('thread/archive',{'threadId':thread});peer.call('thread/unarchive',{'threadId':thread})
        loaded=peer.call('thread/loaded/list',{})
        passed('native_archive_unarchive_retains_saved_unloaded_thread',thread not in loaded.get('data',[]),loaded)
        def queued(message):
            return peer.call('thread/queue/add',{'threadId':thread,'clientUserMessageId':str(uuid.uuid4()),'input':[{'type':'text','text':message}]})['queuedSubmission']
        first=queued('Do not execute this obsolete message. It will be deleted by the queue fixture.')
        nonce='QUEUED_'+uuid.uuid4().hex;target=ROOT/'work/queued-result.txt'
        second=queued('This placeholder will be updated before execution.')
        task=f'Do not delegate or search the web. Using a native tool, create {target.name} in the workspace containing exactly {nonce} and no newline. Read the file back to verify it, then reply QUEUE_FINISHED.'
        peer.call('thread/queue/update',{'threadId':thread,'queuedSubmissionId':second['id'],'input':[{'type':'text','text':task}]})
        peer.call('thread/queue/reorder',{'threadId':thread,'queuedSubmissionIds':[second['id'],first['id']]})
        listed=peer.call('thread/queue/list',{'threadId':thread})['data']
        peer.call('thread/queue/delete',{'threadId':thread,'queuedSubmissionId':first['id']})
        remaining=peer.call('thread/queue/list',{'threadId':thread})['data']
        passed('native_queue_crud_on_unloaded_thread', [item['id'] for item in listed]==[second['id'],first['id']] and len(remaining)==1 and remaining[0]['input'][0]['text']==task and not target.exists(),{'ordered_ids':[item['id'] for item in listed],'remaining':remaining})
        peer.close();peer=None
        before=read_descriptor(ROOT/'home')
        assert matches(before['worker_pid'],before['worker_birth']),'Provider worker kernel identity changed before the crash probe'
        if os.name=='nt':terminate(before['worker_pid'],before['worker_birth'])
        else:os.kill(before['worker_pid'],signal.SIGKILL)
        end=time.monotonic()+10
        while time.monotonic()<end:
            after=read_descriptor(ROOT/'home')
            if after.get('worker_pid')!=before['worker_pid'] and healthy(after):break
            time.sleep(.05)
        passed('gateway_worker_restart_keeps_native_daemon_endpoint',after['worker_pid']!=before['worker_pid'] and after['port']==before['port'] and after['token']==before['token'] and healthy(after),{'old_worker':before['worker_pid'],'new_worker':after['worker_pid'],'stable_port':after['port']})
        peer=connect();index=len(peer.events)
        resumed=peer.call('thread/resume',{'threadId':thread,'cwd':str(ROOT/'work'),'sandbox':'workspace-write','approvalPolicy':'on-request'})
        passed('cold_proxy_resume_uses_declared_workspace_permissions',resumed.get('sandbox',{}).get('type')=='workspaceWrite' and resumed.get('approvalPolicy')=='on-request' and comparison_path(resumed['cwd'])==comparison_path(ROOT/'work'),{'sandbox':resumed.get('sandbox'),'approvalPolicy':resumed.get('approvalPolicy'),'cwd':resumed.get('cwd')})
        items=peer.complete(thread,index)
        queue=peer.call('thread/queue/list',{'threadId':thread})['data']
        passed('queued_task_executes_after_cold_proxy_resume_and_worker_crash',target.is_file() and target.read_text(encoding='utf-8')==nonce and not queue and any(item.get('type') in {'commandExecution','fileChange'} for item in items),{'answer':text(items),'queue_remaining':queue,'tool_item_types':[item.get('type') for item in items]})
        read=peer.call('thread/goal/get',{'threadId':thread})['goal']
        passed('native_goal_survives_daemon_restart',read['objective']==objective and read['status']=='paused',read)
        peer.call('thread/goal/set',{'threadId':thread,'status':'complete','origin':'user'})
        peer.call('thread/goal/clear',{'threadId':thread,'origin':'user'})
        passed('native_goal_complete_and_clear',peer.call('thread/goal/get',{'threadId':thread}).get('goal') is None,{})
    finally:
        if peer:peer.close()
        management('stop')
        print('EVIDENCE',ROOT,flush=True)


if __name__=='__main__':main()
