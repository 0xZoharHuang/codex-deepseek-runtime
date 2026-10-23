"""Exercise native hooks, skills, images, schemas, sandbox policy and steering."""

import argparse
import json
import os
import re
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import uuid
import zlib

from verify_live import Peer, prepare, text
from process_acceptance import shell_command,python_command
from host_execution_acceptance import command_mentions_path


def permission_denied(value):
    value=str(value).lower()
    return (any(word in value for word in ['operation not permitted','permission denied','read-only','access is denied','unauthorizedaccessexception','permissiondenied','os error 5','拒绝访问'])
            or ('access to the path' in value and 'denied' in value))


def code_mode_denials(rollouts,thread,turn,outside):
    denied=[]
    for records in rollouts:
        if not records or records[0].get('type')!='session_meta' or records[0].get('payload',{}).get('id')!=thread:continue
        pending={};current_turn=None
        for event in records:
            payload=event.get('payload',{})
            if event.get('type')=='turn_context':current_turn=payload.get('turn_id')
            if event.get('type')!='response_item':continue
            item_turn=payload.get('internal_chat_message_metadata_passthrough',{}).get('turn_id',current_turn)
            if item_turn!=turn:continue
            if (payload.get('type')=='custom_tool_call' and payload.get('name')=='exec'
                    and isinstance(payload.get('input'),str) and 'exec_command' in payload['input']
                    and command_mentions_path(payload['input'],outside)):
                pending[payload.get('call_id')]=payload
            if payload.get('type')!='custom_tool_call_output' or payload.get('call_id') not in pending:continue
            output=payload.get('output')
            parts=output if isinstance(output,list) else [{'text':output}]
            for part in parts:
                content=part.get('text') if isinstance(part,dict) else None
                if not isinstance(content,str):continue
                # A successful command quoting a denial is not a denied native
                # execution. Code Mode must carry the real exec result/exit.
                try:result=json.loads(content)
                except ValueError:continue
                if (isinstance(result,dict) and type(result.get('exit_code')) is int and result['exit_code']!=0
                        and isinstance(result.get('output'),str) and permission_denied(result['output'])
                        and command_mentions_path(result['output'],outside)):
                    denied.append({'call':pending[payload['call_id']],'output':payload,'execution':result})
    return denied


def sandbox_denial_evidence(events,client_messages,rollouts,thread,turn,outside):
    current=[event for event in events if event.get('params',{}).get('threadId')==thread and event.get('params',{}).get('turnId')==turn]
    commands=[event['params']['item'] for event in current if event.get('method')=='item/completed'
              and event['params'].get('item',{}).get('type')=='commandExecution']
    attempted=[item for item in commands if isinstance(item.get('command'),str) and command_mentions_path(item['command'],outside)]
    failed=[item for item in attempted if item.get('status')=='failed' and item.get('exitCode') not in (None,0)
            and permission_denied(item.get('aggregatedOutput',''))]
    approvals=[]
    for item in attempted:
        if item.get('status')!='declined':continue
        for event in current:
            params=event.get('params',{})
            if (event.get('method')=='item/commandExecution/requestApproval' and params.get('itemId')==item.get('id')
                    and params.get('command')==item.get('command')
                    and any(reply.get('id')==event.get('id') and reply.get('result',{}).get('decision')=='decline' for reply in client_messages)):
                approvals.append({'request':event,'command':item,'decision':'decline'})
    code=code_mode_denials(rollouts,thread,turn,outside)
    return {'passed':not outside.exists() and bool(failed or approvals or code),
            'thread':thread,'turn':turn,'sandbox_mode':'workspace-write','file_created':outside.exists(),
            'native_failed_commands':failed,'native_declined_approvals':approvals,'native_code_mode_denials':code,
            'scope':'Native policy/approval refusal and absent file effect. Positive Windows OS sandbox enforcement is qualified separately in the isolated guest.'}


def png(path):
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    raw=b''.join(b'\0'+b'\0\0\0'*48+b'\xff\xff\xff'*48 for _ in range(96))
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',96,96,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4())
    prepare(root)
    subprocess.run(['git','init','-q',str(root/'work')],check=True)
    nonce='SKILL_'+uuid.uuid4().hex[:12]
    (root/'work/skill_fixture.py').write_text(f'import pathlib;pathlib.Path("skill-ran.txt").write_text({nonce!r});print({nonce!r})\n', encoding='utf-8')
    skill=root/'work/.agents/skills/runtime-acceptance'
    skill.mkdir(parents=True)
    skill_command=shell_command([sys.executable,'-I',str(root/'work/skill_fixture.py')])
    (skill/'SKILL.md').write_text('---\nname: runtime-acceptance\ndescription: Run the isolated acceptance fixture when explicitly requested.\n---\nRun this exact native shell command and repeat its exact output. Do not delegate.\n```\n'+skill_command+'\n```\n', encoding='utf-8')
    hook=root/'fixture_hook.py'
    hook.write_text('import sys,json,pathlib\nx=json.load(sys.stdin)\nwith pathlib.Path(sys.argv[1]).open("a",encoding="utf-8") as f:f.write(json.dumps({"tool_name":x.get("tool_name"),"tool_input":x.get("tool_input"),"fixture_nonce":'+repr(nonce)+'})+"\\n")\nprint("{}")\n', encoding='utf-8')
    hook_argv=[sys.executable,'-I','-X','utf8',str(hook),str(root/'hook-calls.jsonl')]
    # Upstream hooks/engine/command_runner.rs uses COMSPEC /C on Windows;
    # native exec_command separately uses PowerShell. Match each real contract.
    command=subprocess.list2cmdline(hook_argv) if os.name=='nt' else shlex.join(hook_argv)
    (root/'home/hooks.json').write_text(json.dumps({'hooks':{'PreToolUse':[{'matcher':'Bash','hooks':[{'type':'command','command':command}]}]}}), encoding='utf-8')
    image=root/'image.png'
    png(image)
    results=[]
    peer=Peer(root)
    def check(name,condition,detail):
        result={'name':name,'passed':bool(condition),'qa_default_sandbox_mode':peer.sandbox_mode,'detail':detail}
        results.append(result)
        (root/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2), encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if not condition:raise AssertionError(name)
    try:
        # The only bypass is for the known, fixture-owned hook above. Production
        # profiles keep native hook trust intact.
        thread=peer.new(config={'bypass_hook_trust':True})
        items=peer.turn(thread,'$runtime-acceptance\nUse this explicitly invoked skill in the current workspace. Do not delegate.')
        check('native_skill',nonce in text(items) and (root/'work/skill-ran.txt').is_file() and (root/'work/skill-ran.txt').read_text(encoding='utf-8')==nonce and any(item.get('type')=='commandExecution' for item in items),text(items))
        hook_rows=[json.loads(line) for line in (root/'hook-calls.jsonl').read_text(encoding='utf-8').splitlines()] if (root/'hook-calls.jsonl').exists() else []
        check('native_pretool_hook',any(row.get('fixture_nonce')==nonce and row.get('tool_name') and 'skill_fixture.py' in json.dumps(row.get('tool_input')) for row in hook_rows),hook_rows)
        items=peer.turn(thread,'Describe the two colors in the image without tools or delegation. Answer in English.',input=[{'type':'text','text':'Describe the two colors in the image without tools or delegation. Answer in English.'},{'type':'localImage','path':str(image)}])
        answer=text(items).lower()
        check('native_image','black' in answer and 'white' in answer,answer)
        items=peer.turn(thread,f'Do not delegate. Call the native view_image tool on {image} with detail="original", then describe its two colors. Use the image returned by the tool, not a shell image parser.')
        answer=text(items).lower()
        original_requested=False
        for rollout in (root/'home/sessions').rglob('*.jsonl'):
            for line in rollout.read_text(encoding='utf-8').splitlines():
                event=json.loads(line);payload=event.get('payload',{})
                source=payload.get('input','')
                if payload.get('type')=='custom_tool_call' and 'view_image' in source and re.search(r'''["']?detail["']?\s*:\s*["']original["']''',source):
                    original_requested=True
        check('native_original_detail_image_tool',original_requested and 'black' in answer and 'white' in answer,answer)
        schema={'type':'object','properties':{'marker':{'type':'string','enum':['STRUCTURED_NATIVE']},'value':{'type':'integer','enum':[437]}},'required':['marker','value'],'additionalProperties':False}
        items=peer.turn(thread,'Without tools or delegation, output marker STRUCTURED_NATIVE and integer value 437 in the required JSON schema.',outputSchema=schema)
        answer=json.loads(text(items))
        check('native_structured_output',answer=={'marker':'STRUCTURED_NATIVE','value':437},answer)
        outside=root/'outside-workspace'/('forbidden-'+uuid.uuid4().hex+'.txt');outside.parent.mkdir()
        # This deliberately narrower thread retains the policy/approval refusal
        # check even when ordinary host QA uses danger-full-access. A mode name
        # does not establish an active Windows OS sandbox backend.
        denial_thread=peer.new(sandbox='workspace-write',approvalPolicy='on-request',config={'bypass_hook_trust':True})
        # WinPS 5 native argument passing strips embedded double quotes from -c.
        # A real fixture file keeps Python source bytes out of that argv boundary.
        denial_fixture=root/'work/denial_fixture.py'
        denial_fixture.write_text('import pathlib,sys\npathlib.Path(sys.argv[1]).write_text("DENIED_TEST",encoding="utf-8")\n',encoding='utf-8')
        denial_command=shell_command([python_command('')[0],'-I','-u',str(denial_fixture),str(outside)])
        client_path=root/'client-requests.jsonl';client_start=len(client_path.read_text(encoding='utf-8').splitlines())
        denial_turn,denial_start=peer.start(denial_thread,'Do not delegate. Run the exact native-host Python command below using exec_command under this workspace-write sandbox. Do not request escalation or additional permissions. If using Code Mode, emit the actual returned exec result as JSON. Report the actual result, including any denial.\n```\n'+denial_command+'\n```')
        items=peer.complete(denial_thread,denial_start)
        rollouts=[]
        for rollout in (root/'home/sessions').rglob('*.jsonl'):
            rollouts.append([json.loads(line) for line in rollout.read_text(encoding='utf-8').splitlines()])
        client_messages=[json.loads(line) for line in client_path.read_text(encoding='utf-8').splitlines()[client_start:]]
        denial=sandbox_denial_evidence(peer.events[denial_start:],client_messages,rollouts,denial_thread,denial_turn,outside)
        check('native_sandbox_denial',denial['passed'],{**denial,'answer':text(items)})
        steering=peer.new(config={'bypass_hook_trust':True})
        background=root/'work/background_fixture.py';background.write_text('import time;time.sleep(6);print("BACKGROUND_FINISHED",flush=True)\n',encoding='utf-8')
        background_command=shell_command([sys.executable,'-I','-u',str(background)])
        turn,start=peer.start(steering,'Do not delegate. Run this native shell command with a short yield, then poll that same command until it finishes. Report its output.\n```\n'+background_command+'\n```')
        peer.wait(lambda x:x.get('method')=='item/started' and x.get('params',{}).get('threadId')==steering and x.get('params',{}).get('item',{}).get('type')=='commandExecution',start)
        peer.call('turn/steer',{'threadId':steering,'expectedTurnId':turn,'input':[{'type':'text','text':'Keep waiting for that same command. Your final answer must contain STEERED and its actual output.'}]})
        items=peer.complete(steering,start)
        check('native_steering_and_background','STEERED' in text(items) and 'BACKGROUND_FINISHED' in text(items),text(items))
    finally:
        peer.close()
        print('EVIDENCE',str(root),flush=True)


if __name__=='__main__':main()
