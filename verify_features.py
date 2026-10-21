"""Exercise native hooks, skills, images, schemas, sandbox policy and steering."""

import argparse
import json
import re
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import uuid
import zlib

from verify_live import Peer, prepare, text


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
    (root/'work/skill_fixture.py').write_text(f'import pathlib;pathlib.Path("skill-ran.txt").write_text({nonce!r});print({nonce!r})\n')
    skill=root/'work/.agents/skills/runtime-acceptance'
    skill.mkdir(parents=True)
    (skill/'SKILL.md').write_text('---\nname: runtime-acceptance\ndescription: Run the isolated acceptance fixture when explicitly requested.\n---\nRun `python3 skill_fixture.py` and repeat its exact output. Do not delegate.\n')
    hook=root/'fixture_hook.py'
    hook.write_text('import sys,json,pathlib\nx=json.load(sys.stdin)\nwith pathlib.Path(sys.argv[1]).open("a") as f:f.write(json.dumps({"tool_name":x.get("tool_name")})+"\\n")\nprint("{}")\n')
    command=' '.join(shlex.quote(s) for s in [sys.executable,str(hook),str(root/'hook-calls.jsonl')])
    (root/'home/hooks.json').write_text(json.dumps({'hooks':{'PreToolUse':[{'matcher':'Bash','hooks':[{'type':'command','command':command}]}]}}))
    image=root/'image.png'
    png(image)
    results=[]
    peer=Peer(root)
    def check(name,condition,detail):
        result={'name':name,'passed':bool(condition),'detail':detail}
        results.append(result)
        (root/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if not condition:raise AssertionError(name)
    try:
        # The only bypass is for the known, fixture-owned hook above. Production
        # profiles keep native hook trust intact.
        thread=peer.new(config={'bypass_hook_trust':True})
        items=peer.turn(thread,'$runtime-acceptance\nUse this explicitly invoked skill in the current workspace. Do not delegate.')
        check('native_skill',nonce in text(items) and (root/'work/skill-ran.txt').is_file() and (root/'work/skill-ran.txt').read_text()==nonce and any(item.get('type')=='commandExecution' for item in items),text(items))
        check('native_pretool_hook',(root/'hook-calls.jsonl').exists(),(root/'hook-calls.jsonl').read_text() if (root/'hook-calls.jsonl').exists() else 'missing')
        items=peer.turn(thread,'Describe the two colors in the image without tools or delegation. Answer in English.',input=[{'type':'text','text':'Describe the two colors in the image without tools or delegation. Answer in English.'},{'type':'localImage','path':str(image)}])
        answer=text(items).lower()
        check('native_image','black' in answer and 'white' in answer,answer)
        items=peer.turn(thread,f'Do not delegate. Call the native view_image tool on {image} with detail="original", then describe its two colors. Use the image returned by the tool, not a shell image parser.')
        answer=text(items).lower()
        original_requested=False
        for rollout in (root/'home/sessions').rglob('*.jsonl'):
            for line in rollout.read_text().splitlines():
                event=json.loads(line);payload=event.get('payload',{})
                source=payload.get('input','')
                if payload.get('type')=='custom_tool_call' and 'view_image' in source and re.search(r'''["']?detail["']?\s*:\s*["']original["']''',source):
                    original_requested=True
        check('native_original_detail_image_tool',original_requested and 'black' in answer and 'white' in answer,answer)
        schema={'type':'object','properties':{'marker':{'type':'string','enum':['STRUCTURED_NATIVE']},'value':{'type':'integer','enum':[437]}},'required':['marker','value'],'additionalProperties':False}
        items=peer.turn(thread,'Without tools or delegation, output marker STRUCTURED_NATIVE and integer value 437 in the required JSON schema.',outputSchema=schema)
        answer=json.loads(text(items))
        check('native_structured_output',answer=={'marker':'STRUCTURED_NATIVE','value':437},answer)
        outside=Path.home()/'.local/share/codex-deepseek'/('forbidden-'+uuid.uuid4().hex+'.txt')
        items=peer.turn(thread,f'Do not delegate. Attempt to write the string DENIED_TEST to {outside} using exec_command under the default sandbox. Do not request escalation or additional permissions. Report the actual result, including any denial.')
        commands=[item for item in items if item.get('type')=='commandExecution']
        attempted=[item for item in commands if str(outside) in str(item.get('command',''))]
        denied=any(item.get('exitCode') not in (None,0) and any(word in str(item).lower() for word in ['operation not permitted','permission denied','read-only']) for item in attempted)
        # Code Mode emits a custom call/output rather than commandExecution.
        # Bind the attempted path to its matching native call_id and output;
        # the agent's final prose alone is never denial evidence.
        code_mode_denials=[]
        for rollout in (root/'home/sessions').rglob('*.jsonl'):
            pending={}
            for line in rollout.read_text().splitlines():
                payload=json.loads(line).get('payload',{})
                if payload.get('type')=='custom_tool_call' and str(outside) in payload.get('input',''):
                    pending[payload['call_id']]=payload
                if payload.get('type')=='custom_tool_call_output' and payload.get('call_id') in pending:
                    content=str(payload.get('output','')).lower()
                    if str(outside).lower() in content and any(word in content for word in ['operation not permitted','permission denied','read-only']):
                        code_mode_denials.append({'call':pending[payload['call_id']],'output':payload})
        denied=denied or bool(code_mode_denials)
        check('native_sandbox_denial',not outside.exists() and denied,{'answer':text(items),'attempted_commands':attempted,'native_code_mode_denials':code_mode_denials})
        steering=peer.new(config={'bypass_hook_trust':True})
        turn,start=peer.start(steering,'Do not delegate. Run shell command `sleep 6; printf BACKGROUND_FINISHED` with a short yield, then poll that same command until it finishes. Report its output.')
        peer.wait(lambda x:x.get('method')=='item/started' and x.get('params',{}).get('threadId')==steering and x.get('params',{}).get('item',{}).get('type')=='commandExecution',start)
        peer.call('turn/steer',{'threadId':steering,'expectedTurnId':turn,'input':[{'type':'text','text':'Keep waiting for that same command. Your final answer must contain STEERED and its actual output.'}]})
        items=peer.complete(steering,start)
        check('native_steering_and_background','STEERED' in text(items) and 'BACKGROUND_FINISHED' in text(items),text(items))
    finally:
        peer.close()
        print('EVIDENCE',str(root),flush=True)


if __name__=='__main__':main()
