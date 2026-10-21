"""Native client contract: PTY IO, approval callback, namespace discovery and durable history."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time
import uuid
from verify_live import Peer, text
from process_acceptance import python_command,shell_command,evidence_root

root=evidence_root('runtime-contract')/str(uuid.uuid4())
(root/'home').mkdir(parents=True);(root/'work').mkdir()
subprocess.run(['git','init','-q',str(root/'work')],check=True)
(root/'home/config.toml').write_text('model_reasoning_effort="high"\n[features]\ncode_mode=false\ncode_mode_only=false\ncode_mode_only_strict_3p_tools=false\n')
results=[]
def passed(name, condition, detail):
    results.append({'name':name,'passed':bool(condition),'sandbox_mode':peer.sandbox_mode,'detail':detail})
    (root/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
    print(json.dumps(results[-1],ensure_ascii=False),flush=True)
    if not condition:raise AssertionError(name)
peer=Peer(root)
try:
    with ThreadPoolExecutor(1) as pool:
        start=len(peer.events)
        future=pool.submit(peer.call,'command/exec',{'command':python_command('import os;print("READY",flush=True);value=input();size=os.get_terminal_size();print("SIZE:%s:%s"%(size.lines,size.columns));print("RESULT:"+value)'),'cwd':str(root/'work'),'processId':'native-pty','tty':True,'size':{'rows':24,'cols':80},'timeoutMs':15000})
        peer.wait(lambda e:e.get('method')=='command/exec/outputDelta' and e['params']['processId']=='native-pty',start,20)
        peer.call('command/exec/resize',{'processId':'native-pty','size':{'rows':32,'cols':120}})
        peer.call('command/exec/write',{'processId':'native-pty','deltaBase64':base64.b64encode(b'RUNTIME_PTY_9341\n').decode()})
        result=future.result(20)
        output=b''.join(base64.b64decode(e['params']['deltaBase64']) for e in peer.events[start:] if e.get('method')=='command/exec/outputDelta').decode()
        passed('native_pty_stdin_resize_output',result['exitCode']==0 and 'RESULT:RUNTIME_PTY_9341' in output and 'SIZE:32:120' in output,output)
    tools=[]
    for namespace in ['ledger_alpha','ledger_beta']:
        tools.append({'type':'namespace','name':namespace,'description':f'Independent {namespace} ledger.','tools':[{'type':'function','name':'read_record','deferLoading':True,'description':'Return the immutable ledger value.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]})
    peer.dynamic_answers={('ledger_alpha','read_record'):'LEDGER_ALPHA_98371',('ledger_beta','read_record'):'LEDGER_BETA_72649'}
    thread=peer.new(dynamicTools=tools)
    items=peer.turn(thread,'Do not delegate. Discover and call BOTH ledger_alpha.read_record and ledger_beta.read_record native dynamic tools. Do not simulate the tool output. Repeat both exact returned values and remember them for later.')
    calls=[e['params'] for e in peer.callbacks if e['method']=='item/tool/call']
    payloads=[json.loads(path.read_text()) for path in (root/'adapter-traces').glob('*-request.json')]
    native_outputs=[event['payload'] for path in (root/'home/sessions').rglob('*.jsonl') for line in path.read_text().splitlines() if (event:=json.loads(line)).get('type')=='response_item' and event.get('payload',{}).get('type')=='tool_search_output']
    discovered=any(item.get('execution')=='client' and bool(item.get('tools')) for item in native_outputs)
    passed('native_client_tool_search_actually_discovers_deferred_tools',discovered,{'requests':len(payloads)})
    passed('deferred_dynamic_tools_distinct_namespaces',{'ledger_alpha','ledger_beta'}=={c.get('namespace') for c in calls} and all(v in text(items) for v in peer.dynamic_answers.values()),{'calls':calls,'answer':text(items)})
    peer.call('thread/name/set',{'threadId':thread,'name':'runtime-contract-ledgers'})
    fork=peer.call('thread/fork',{'threadId':thread})['thread']['id']
    peer.call('thread/archive',{'threadId':thread})
    archived=peer.call('thread/list',{'archived':True,'limit':50,'modelProviders':['deepseek_runtime']})
    passed('native_archive_listing',thread in {x['id'] for x in archived['data']},[x['id'] for x in archived['data']])
    peer.call('thread/unarchive',{'threadId':thread})
    peer.close();peer=Peer(root)
    peer.dynamic_answers={('ledger_alpha','read_record'):'LEDGER_ALPHA_98371',('ledger_beta','read_record'):'LEDGER_BETA_72649'}
    peer.call('thread/resume',{'threadId':fork})
    start=len(peer.events);peer.call('thread/compact/start',{'threadId':fork});peer.complete(fork,start)
    items=peer.turn(fork,'Without calling tools or delegating, recall both exact ledger values from the inherited conversation.')
    passed('fork_cold_resume_compaction_tool_history',all(v in text(items) for v in peer.dynamic_answers.values()),text(items))
    target=root/'must-not-be-created.txt'
    fixture=root/'work/approval_write.py'
    fixture.write_text('from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text("APPROVED")\n',encoding='utf-8')
    command=shell_command([python_command('')[0],'-I',str(fixture),str(target)])
    approval=peer.new(approvalsReviewer='user',sandbox='read-only')
    turn,start=peer.start(approval,'Do not delegate. Actually call exec_command exactly once with the command below, sandbox_permissions=require_escalated, and justification="isolated native user approval fixture". The user authorizes requesting approval, not bypassing the reply. If declined, stop without another route. A statement of intent is not a tool call.\n```\n'+command+'\n```')
    items=peer.complete(approval,start)
    declines=[e for e in peer.callbacks if e['method']=='item/commandExecution/requestApproval'
              and e.get('params',{}).get('threadId')==approval and e.get('params',{}).get('turnId')==turn]
    replies=[json.loads(line) for line in (root/'client-requests.jsonl').read_text(encoding='utf-8').splitlines()]
    declined=[item for item in items if item.get('type')=='commandExecution' and item.get('status')=='declined']
    bound=[event for event in declines if any(reply.get('id')==event['id'] and reply.get('result',{}).get('decision')=='decline' for reply in replies)
           and any(item.get('id')==event['params'].get('itemId') and item.get('command')==event['params'].get('command') for item in declined)]
    passed('native_user_approval_decline_callback',bool(bound) and not target.exists(),{'callbacks':bound,'thread':approval,'turn':turn,'answer':text(items)})
finally:
    peer.close()
    print('EVIDENCE',root,flush=True)
