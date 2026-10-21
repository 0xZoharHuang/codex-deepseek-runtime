"""Kill the actual provider owner with SIGKILL; verify child cleanup and cold resume."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import signal
import sqlite3
import subprocess
import time
import uuid
from verify_live import Peer,text,qa_sandbox_mode,qa_command_sandbox
from process_acceptance import python_command,evidence_root,fixture_identity,write_process_tree_fixture
from codex_deepseek.processes import identity,matches,terminate
root=evidence_root('native-crash')/str(uuid.uuid4())
(root/'home').mkdir(parents=True);(root/'work').mkdir();(root/'home/config.toml').write_text('model_reasoning_effort="high"\nsandbox_mode='+json.dumps(qa_sandbox_mode())+'\n')
peer=Peer(root);result={}
try:
    thread=peer.new();peer.turn(thread,'Do not use tools or delegate. Remember checkpoint RECOVERY-98371 and reply SAVED.')
    pidfile=root/'work/owned-pids.json';fixture=root/'work/owned-process-tree.py'
    write_process_tree_fixture(fixture,pidfile)
    peer.send({'id':8001,'method':'command/exec','params':{'command':[python_command('')[0],'-I','-u',str(fixture)],'cwd':str(root/'work'),'sandboxPolicy':qa_command_sandbox(root/'work'),'processId':'crash-child','timeoutMs':130000}})
    until=time.monotonic()+10
    while not pidfile.exists() and time.monotonic()<until:time.sleep(.1)
    assert pidfile.exists(),[e for e in peer.events if e.get('id')==8001 and 'method' not in e]
    markers=json.loads(pidfile.read_text(encoding='utf-8'));assert len(markers)==2
    births=dict(fixture_identity(marker,fixture) for marker in markers);assert len(births)==2
    pids=list(births)
    _,start=peer.start(thread,'Do not use tools or delegate. Write a detailed 6000-word explanation of implementing a durable queue. Continue until the explanation is complete.')
    state=root/'home/provider-state/adapter.sqlite'
    until=time.monotonic()+20
    active=[]
    while time.monotonic()<until:
        with sqlite3.connect(state) as db:active=db.execute("SELECT id FROM receipts WHERE status='running'").fetchall()
        if active and any(e.get('method')=='item/agentMessage/delta' for e in peer.events[start:]):break
        time.sleep(.1)
    assert active,'No active provider request was observed'
    peer.process.kill();peer.process.wait(timeout=5)
    until=time.monotonic()+10
    alive=[]
    while time.monotonic()<until:
        alive=[pid for pid in pids if matches(pid,births[pid])]
        if not alive:break
        time.sleep(.1)
    result.update({'owned_pids':pids,'remaining_pids':alive,'killed_during_stream':True,'thread':thread})
    peer.close();peer=Peer(root)
    with sqlite3.connect(state) as db:result['crash_receipts']=db.execute('SELECT id,status,response_id FROM receipts WHERE id IN ('+','.join('?' for _ in active)+')',[a[0] for a in active]).fetchall()
    peer.call('thread/resume',{'threadId':thread})
    items=peer.turn(thread,'The client was restarted after an intentional crash. Do not continue the long explanation. Without tools or delegation, repeat the checkpoint marker you saved before it.')
    result['answer']=text(items)
    result['passed']=not alive and 'RECOVERY-98371' in result['answer'] and bool(result['crash_receipts']) and all(row[1] in {'owner_lost','cancelled'} for row in result['crash_receipts'])
finally:
    peer.close()
    for pid,birth in locals().get('births',{}).items():terminate(pid,birth)
    (root/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));print('EVIDENCE',root)
if not result.get('passed'):raise AssertionError('Native crash recovery acceptance failed')
