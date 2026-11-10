"""Optional Windows observer proof uses retained OS handles, never missing PIDs."""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import pytest
from ci import qualify_native as qualify


@pytest.mark.skipif(sys.platform!='win32',reason='Windows retained OS process handles')
@pytest.mark.parametrize('exit_code',[0,7])
def test_real_windows_child_exit_is_snapshot_before_owner_finishes(tmp_path,exit_code):
    marker='QA_REAL_CHILD_EXIT_'+str(exit_code)
    child='import sys,time;time.sleep(0.8);sys.exit('+str(exit_code)+')'
    program='import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-I","-B","-c",'+repr(child)+','+repr(marker)+']);p.wait();time.sleep(0.7)'
    snapshot=tmp_path/'process-evidence.json';result={};errors=[]
    def run():
        try:
            with (tmp_path/'process.log').open('wb') as log:
                result.update(qualify.run_observed([sys.executable,'-I','-B','-c',program],
                    cwd=tmp_path,env=os.environ.copy(),log=log,timeout=10,process_evidence_path=snapshot))
        except BaseException as error:errors.append(type(error).__name__)
    thread=threading.Thread(target=run);thread.start();live_exit_seen=False
    deadline=time.monotonic()+15
    while thread.is_alive() and time.monotonic()<deadline:
        if snapshot.exists():
            value=json.loads(snapshot.read_bytes())
            children={(r['pid'],r['birth']) for r in value['observed'] if marker in (r.get('argv') or [])}
            if any((r['pid'],r['birth']) in children and r['exit_code']==exit_code for r in value['exits']):
                live_exit_seen=True
        time.sleep(.025)
    thread.join(timeout=1)
    assert not thread.is_alive() and errors==[] and result['passed']
    assert result['returncode']==0 and result['process_evidence_errors']==[]
    value=json.loads(snapshot.read_bytes());assert value['exit_evidence_source']=='retained_os_process_handles'
    children=[r for r in value['observed'] if marker in (r.get('argv') or [])]
    assert children and live_exit_seen
    exits={(r['pid'],r['birth']):r['exit_code'] for r in value['exits']}
    assert all(exits.get((r['pid'],r['birth']))==exit_code for r in children)
    assert all(r.get('sha256')==hashlib.sha256(Path(r['exe']).read_bytes()).hexdigest() for r in children)
    assert result['forced_cleanup']==result['remaining_after_cleanup']==result['unknown_live']==[]
    assert not list(tmp_path.glob('*.tmp-*'))


@pytest.mark.skipif(sys.platform!='win32',reason='Windows retained OS process handles')
def test_wrong_instance_birth_never_becomes_an_exit_or_retained_handle(tmp_path):
    collector=qualify._WindowsProcessEvidence(tmp_path/'snapshot.json')
    key=(os.getpid(),'windows:1');seen={key:{'pid':key[0],'birth':key[1]}}
    try:
        collector.sample(seen,force=True)
        assert not collector.handles and not collector.exits
        assert collector.unretained[key]['reason']=='opened_instance_birth_mismatch'
    finally:collector.close()


@pytest.mark.skipif(sys.platform!='win32',reason='Windows retained OS process handles')
def test_snapshot_write_failure_is_explicit_and_cannot_be_success(tmp_path):
    collector=qualify._WindowsProcessEvidence(tmp_path/'missing-parent'/'snapshot.json')
    try:
        collector.sample({},force=True)
        assert collector.errors and any(r['reason']=='snapshot_write_failed' for r in collector.errors.values())
        assert not collector.path.exists() and not collector.exits
    finally:collector.close()


@pytest.mark.parametrize('same_instance',[True,False])
def test_retained_exit_reconciles_uncertain_only_for_exact_pid_birth(monkeypatch,tmp_path,same_instance):
    import io
    from test_qualification_scope import _observed_process_graph
    module,graph,_,signals,_,root=_observed_process_graph(monkeypatch)
    graph[300]['alive']=False
    observe=module._observe_process_family
    def uncertain_child(seen,uncertain,rejected,errors):
        observe(seen,uncertain,rejected,errors)
        row=seen.pop((200,'child-20'),None)
        if row:uncertain[(200,'child-20','test')]={**row,'reason':'test'}
    monkeypatch.setattr(module,'_observe_process_family',uncertain_child)
    poll=root.poll
    def finish():
        value=poll()
        if value==0:graph[200]['birth']=None
        return value
    monkeypatch.setattr(root,'poll',finish)
    class Evidence:
        def __init__(self,path):
            self.path=path;self.errors={};self.exits={(200,'child-20' if same_instance else 'other-birth'):
                {'pid':200,'birth':'child-20' if same_instance else 'other-birth','exit_code':0}}
        def sample(self,*args,**kwargs):pass
        def close(self):pass
    monkeypatch.setattr(module,'_WindowsProcessEvidence',Evidence)
    result=module.run_observed(['synthetic'],cwd=tmp_path,env={},log=io.StringIO(),timeout=30,
        process_evidence_path=tmp_path/'synthetic.json')
    assert signals==[] and result['unknown_live']==([] if same_instance else [200])
    assert result['passed'] is same_instance
