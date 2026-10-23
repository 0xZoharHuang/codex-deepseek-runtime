"""A running receipt alone is not evidence that a turn was interrupted mid-stream."""
from contextlib import closing
import json
import sqlite3
import pytest
from verify_crash_recovery import crash_stream_evidence,running_turn_receipts,recovered_receipts_match


def delta():
    return {'method':'item/agentMessage/delta',
            'params':{'threadId':'thread','turnId':'turn','itemId':'msg','delta':'streaming'}}


@pytest.mark.parametrize('mutation',['missing','foreign-thread','foreign-turn','empty','non-string','completed','completed-nested','no-receipt','duplicate-receipt'])
def test_incomplete_or_foreign_stream_cannot_authorize_crash(mutation):
    event=delta();events=[event];active=['receipt']
    if mutation=='missing':events=[]
    elif mutation=='foreign-thread':event['params']['threadId']='other'
    elif mutation=='foreign-turn':event['params']['turnId']='other'
    elif mutation=='empty':event['params']['delta']=''
    elif mutation=='non-string':event['params']['delta']=17
    elif mutation=='completed':events.append({'method':'turn/completed','params':{'threadId':'thread','turnId':'turn'}})
    elif mutation=='completed-nested':events.append({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'completed'}}})
    elif mutation=='no-receipt':active=[]
    elif mutation=='duplicate-receipt':active=['receipt','receipt']
    assert crash_stream_evidence(events,'thread','turn',active) is None


def test_exact_active_native_stream_preserves_observation():
    event=delta()
    result=crash_stream_evidence([event],'thread','turn',['receipt'])
    assert result['last_output_delta']==event
    assert result['thread']=='thread' and result['turn']=='turn'
    assert result['active_receipt_ids']==['receipt'] and result['observed_at']>0


def test_receipts_require_running_status_and_exact_trace_identity(tmp_path):
    state=tmp_path/'state.sqlite';trace=tmp_path/'trace';trace.mkdir()
    rows=[('exact','running'),('finished','completed'),('foreign','running'),('unwritten','running')]
    with closing(sqlite3.connect(state)) as db,db:
        db.execute('CREATE TABLE receipts(id TEXT,status TEXT)')
        db.executemany('INSERT INTO receipts VALUES (?,?)',rows)
    for rid,thread,turn in [('exact','thread','turn'),('finished','thread','turn'),('foreign','thread','other')]:
        (trace/(rid+'-request.json')).write_text(json.dumps({'client_metadata':{'thread_id':thread,'turn_id':turn}}))
    assert running_turn_receipts(state,trace,'thread','turn')==['exact']
    assert running_turn_receipts(state,trace,'thread','missing')==[]
    # A leaked Windows connection prevents exclusive file retirement.
    state.unlink()
    assert not state.exists()


@pytest.mark.parametrize('rows',[
    [],[('r1','cancelled',None)],[('r1','cancelled',None),('r1','owner_lost',None)],
    [('r1','cancelled',None),('foreign','owner_lost',None)],
    [('r1','cancelled',None),('r2','running',None)],
    [('r1','cancelled',None),('r2','completed','resp')]
])
def test_partial_duplicate_foreign_or_completed_receipts_are_not_recovery(rows):
    assert not recovered_receipts_match(['r1','r2'],rows)


def test_every_observed_receipt_must_reconcile_to_loss_or_cancellation():
    assert recovered_receipts_match(['r1','r2'],[('r2','owner_lost',None),('r1','cancelled','resp')])

def test_peer_close_failure_cannot_skip_owned_cleanup_or_failure_receipt(tmp_path,monkeypatch):
    import verify_crash_recovery as audit
    failure=RuntimeError('protocol reader failed');called=[]
    class Peer:
        def close(self):raise failure
    monkeypatch.setattr(audit,'terminate',lambda pid,birth:called.append((pid,birth)))
    result={'passed':True,'original_failure':'stream was interrupted'}
    assert audit.finish_crash_evidence(tmp_path,Peer(),result,{1:'birth-a',2:'birth-b'}) is failure
    saved=json.loads((tmp_path/'results.json').read_text())
    assert called==[(1,'birth-a'),(2,'birth-b')] and saved['passed'] is False
    assert saved['original_failure']=='stream was interrupted'
    assert saved['cleanup_errors'][0]['step']=='peer.close'


def test_one_owned_cleanup_failure_does_not_skip_next_pid_or_receipt(tmp_path,monkeypatch):
    import verify_crash_recovery as audit
    called=[];failure=RuntimeError('owned process cleanup failed')
    class Peer:
        def close(self):pass
    def terminate(pid,birth):
        called.append((pid,birth))
        if pid==1:raise failure
    monkeypatch.setattr(audit,'terminate',terminate)
    assert audit.finish_crash_evidence(tmp_path,Peer(),{'passed':True},{1:'a',2:'b'}) is failure
    saved=json.loads((tmp_path/'results.json').read_text())
    assert called==[(1,'a'),(2,'b')] and saved['passed'] is False
    assert saved['cleanup_errors'][0]['step']=='owned-process 1'
