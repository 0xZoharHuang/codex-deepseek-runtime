"""Fixture readers must always see a complete published OAuth event snapshot."""
import importlib.util
import json
from pathlib import Path
import threading
import subprocess
import sys
import os
import urllib.request
import urllib.error
import time
import pytest

spec=importlib.util.spec_from_file_location("oauth_atomic_fixture",Path(__file__).with_name("oauth_fixture.py"))
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)


def test_old_snapshot_remains_visible_until_atomic_publication(monkeypatch,tmp_path):
    path=tmp_path/"events.json";path.write_text('[{"old":true}]')
    entered=threading.Event();release=threading.Event();errors=[]
    original=fixture.replace_file
    def delayed(source,target):
        entered.set();assert release.wait(5)
        original(source,target)
    monkeypatch.setattr(fixture,"replace_file",delayed)
    def write():
        try:fixture.publish_json(path,[{"new":True}])
        except BaseException as error:errors.append(error)
    worker=threading.Thread(target=write);worker.start()
    try:
        assert entered.wait(5)
        assert json.loads(path.read_text())==[{"old":True}]
    finally:release.set();worker.join(5)
    assert not worker.is_alive() and not errors
    assert json.loads(path.read_text())==[{"new":True}]


def test_live_http_snapshots_remain_complete_during_concurrent_event_publication(tmp_path):
    root=tmp_path/"fixture";root.mkdir()
    with (root/"server.log").open("wb") as log:
        server=subprocess.Popen([sys.executable,str(Path(fixture.__file__)),str(root)],
                                stdout=log,stderr=log,env={**os.environ,"PYTHONPATH":
                                str(Path(__import__("codex_deepseek").__file__).resolve().parent.parent)})
    errors=[];done=threading.Event();reads=0;counts=[]
    try:
        deadline=time.monotonic()+10
        while not (root/"ready.json").exists() and time.monotonic()<deadline:
            assert server.poll() is None
            time.sleep(.01)
        ready=json.loads((root/"ready.json").read_text());base=ready["base"]
        snapshot=urllib.request.Request(base+"/events",
                    headers={"Authorization":"Bearer "+ready["events_token"]})
        for request in [base+"/events",urllib.request.Request(base+"/events",
                        headers={"Authorization":"Bearer WRONG_FIXTURE_CONTROLLER"})]:
            with pytest.raises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(request,timeout=5)
            assert denied.value.code==403
            denied.value.close()
        with urllib.request.urlopen(snapshot,timeout=5) as response:assert json.load(response)==[]
        def write():
            try:
                for revision in range(100):
                    request=urllib.request.Request(base+"/register",
                        data=json.dumps({"redirect_uris":["http://localhost/"+str(revision)]}).encode(),
                        headers={"Content-Type":"application/json"},method="POST")
                    with urllib.request.urlopen(request,timeout=5) as response:
                        assert response.status==200
            except BaseException as error:errors.append(error)
            finally:done.set()
        worker=threading.Thread(target=write);worker.start();deadline=time.monotonic()+15
        try:
            while not done.is_set() and time.monotonic()<deadline:
                with urllib.request.urlopen(snapshot,timeout=5) as response:value=json.load(response)
                counts.append(len(value));reads+=1
                assert all(row=={"kind":"register","redirect_uris":["http://localhost/"+str(i)]}
                           for i,row in enumerate(value))
        finally:worker.join(10)
        assert done.is_set() and not worker.is_alive() and not errors and reads>0
        with urllib.request.urlopen(snapshot,timeout=5) as response:final=json.load(response)
        assert len(final)==100 and counts==sorted(counts)
    finally:
        server.terminate();server.wait(timeout=10)
    # After the server stops, durable evidence exactly matches the final snapshot.
    assert json.loads((root/"fixture-events.json").read_text())==final


def test_failed_publication_preserves_last_complete_snapshot(monkeypatch,tmp_path):
    path=tmp_path/"events.json";path.write_text('[{"previous":true}]')
    def fail(*args):raise OSError("fixture publish failed")
    monkeypatch.setattr(fixture,"replace_file",fail)
    with pytest.raises(OSError,match="fixture publish failed"):
        fixture.publish_json(path,[{"replacement":True}])
    assert json.loads(path.read_text())==[{"previous":True}]
