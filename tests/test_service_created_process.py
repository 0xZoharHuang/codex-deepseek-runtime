"""Created provider processes retain a waiter or are reaped on launch failure."""
import subprocess
import threading
import time
from pathlib import Path
import pytest
from codex_deepseek import service
from codex_deepseek.processes import identity, matches, terminate

@pytest.mark.parametrize("failure",["identity","waiter_start","retain"])
def test_new_provider_failure_reaps_the_exact_created_process(tmp_path,monkeypatch,failure):
    home=tmp_path/"profile"
    catalog=Path(__file__).resolve().parents[1]/"catalog.json"
    real_popen=subprocess.Popen;created=[]
    def launch(*args,**kwargs):
        process=real_popen(*args,**kwargs)
        created.append((process,identity(process.pid)))
        return process
    monkeypatch.setattr(service.subprocess,"Popen",launch)
    if failure=="identity":
        monkeypatch.setattr(service,"identity",lambda pid:None)
        expected="identity is unavailable"
    elif failure=="waiter_start":
        start=threading.Thread.start
        def fail_waiter(thread,*args,**kwargs):
            if thread.name=="cds-provider-reap":raise RuntimeError("fixture waiter start failed")
            return start(thread,*args,**kwargs)
        monkeypatch.setattr(threading.Thread,"start",fail_waiter)
        expected="fixture waiter start failed"
    else:
        expected="Provider client owner is no longer alive"
    try:
        with pytest.raises(RuntimeError,match=expected):
            service.ensure(home,catalog,owner="nonexistent-fixture-owner" if failure=="retain" else None)
        assert len(created)==1
        process,birth=created[0]
        assert process.poll() is not None and not matches(process.pid,birth)
        descriptor=service.read_descriptor(home)
        deadline=time.monotonic()+5
        while matches(descriptor.get("worker_pid"),descriptor.get("worker_birth")) and time.monotonic()<deadline:
            time.sleep(.02)
        assert not matches(descriptor.get("worker_pid"),descriptor.get("worker_birth"))
    finally:
        for process,birth in created:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        descriptor=service.read_descriptor(home)
        if matches(descriptor.get("worker_pid"),descriptor.get("worker_birth")):
            terminate(descriptor["worker_pid"],descriptor["worker_birth"],timeout=2)
