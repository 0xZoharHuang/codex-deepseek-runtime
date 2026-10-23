"""Kill the actual provider owner with SIGKILL; verify child cleanup and cold resume."""
from contextlib import closing
import json
from pathlib import Path
import sys
import sqlite3
import subprocess
import time
import uuid
from verify_live import Peer,text,qa_sandbox_mode,qa_command_sandbox
from process_acceptance import python_command,evidence_root,fixture_identity,write_process_tree_fixture
from codex_deepseek.processes import identity,matches,terminate
def running_turn_receipts(state, trace, thread, turn):
    with closing(sqlite3.connect(state.as_uri()+'?mode=ro',uri=True)) as db:
        active=db.execute("SELECT id FROM receipts WHERE status='running'").fetchall()
    bound=[]
    for (rid,) in active:
        request=trace/(rid+'-request.json')
        if not request.is_file():continue
        payload=json.loads(request.read_text(encoding='utf-8'))
        metadata=payload.get('client_metadata',{})
        if metadata.get('thread_id')==thread and metadata.get('turn_id')==turn:
            bound.append(rid)
    return bound


def crash_stream_evidence(events,thread,turn,active):
    if (not active or len(active)!=len(set(active))
            or any(not isinstance(rid,str) or not rid for rid in active)):
        return None
    matching=[event for event in events if event.get('params',{}).get('threadId')==thread
              and event.get('params',{}).get('turnId')==turn]
    if any(event.get('method')=='turn/completed' for event in matching):return None
    # turn/completed carries turn.id in the pinned native schema.
    if any(event.get('method')=='turn/completed'
           and event.get('params',{}).get('threadId')==thread
           and event.get('params',{}).get('turn',{}).get('id')==turn for event in events):
        return None
    deltas=[event for event in matching if event.get('method')=='item/agentMessage/delta'
            and isinstance(event['params'].get('delta'),str) and event['params']['delta']]
    if not deltas:return None
    return {'thread':thread,'turn':turn,'active_receipt_ids':list(active),
            'last_output_delta':deltas[-1],'observed_at':time.time()}


def recovered_receipts_match(active,receipts):
    return (bool(active) and len(active)==len(set(active))
            and len(receipts)==len(active)
            and {row[0] for row in receipts}==set(active)
            and all(row[1] in {'owner_lost','cancelled'} for row in receipts))


def finish_crash_evidence(root,peer,result,births):
    errors=[]
    try:peer.close()
    except BaseException as error:errors.append(('peer.close',error))
    for pid,birth in births.items():
        try:terminate(pid,birth)
        except BaseException as error:errors.append(('owned-process '+str(pid),error))
    if errors:
        result['passed']=False
        result['cleanup_errors']=[{'step':step,'type':type(error).__name__,'error':str(error)} for step,error in errors]
    (root/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True);print('EVIDENCE',root,flush=True)
    return errors[0][1] if errors else None


def main():
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
        turn,start=peer.start(thread,'Do not use tools or delegate. Write a detailed 6000-word explanation of implementing a durable queue. Continue until the explanation is complete.')
        state=root/'home/provider-state/adapter.sqlite'
        until=time.monotonic()+20
        active=[];stream=None
        while time.monotonic()<until:
            active=running_turn_receipts(state,root/'adapter-traces',thread,turn)
            stream=crash_stream_evidence(peer.events[start:],thread,turn,active)
            if stream:break
            time.sleep(.1)
        assert stream,'No current-turn active provider stream and nonempty native output were observed'
        result['stream_before_crash']=stream
        peer.process.kill();peer.process.wait(timeout=5)
        until=time.monotonic()+10
        alive=[]
        while time.monotonic()<until:
            alive=[pid for pid in pids if matches(pid,births[pid])]
            if not alive:break
            time.sleep(.1)
        result.update({'owned_pids':pids,'remaining_pids':alive,'killed_during_stream':True,'thread':thread})
        peer.close();peer=Peer(root)
        with closing(sqlite3.connect(state.as_uri()+'?mode=ro',uri=True)) as db:result['crash_receipts']=db.execute('SELECT id,status,response_id FROM receipts WHERE id IN ('+','.join('?' for _ in active)+')',active).fetchall()
        peer.call('thread/resume',{'threadId':thread})
        items=peer.turn(thread,'The client was restarted after an intentional crash. Do not continue the long explanation. Without tools or delegation, repeat the checkpoint marker you saved before it.')
        result['answer']=text(items)
        result['passed']=not alive and 'RECOVERY-98371' in result['answer'] and recovered_receipts_match(active,result['crash_receipts'])
    finally:
        original_error=sys.exc_info()[1]
        cleanup_error=finish_crash_evidence(root,peer,result,locals().get('births',{}))
        if cleanup_error is not None and original_error is None:raise cleanup_error
    if not result.get('passed'):raise AssertionError('Native crash recovery acceptance failed')


if __name__=='__main__':main()
