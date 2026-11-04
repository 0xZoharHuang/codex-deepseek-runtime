"""Real occupied-port selection; never starts a provider or reads credentials."""
from contextlib import closing
import socket
import pytest
from codex_deepseek import service

def exclusive_listener():
    value=socket.socket()
    value.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE if service.WINDOWS else socket.SO_REUSEADDR,1)
    value.bind(("127.0.0.1",0));value.listen(1);value.settimeout(2)
    return value

def test_occupied_previous_port_selects_new_listener_without_touching_occupant(tmp_path,monkeypatch):
    monkeypatch.setattr(service,"native_active",lambda home:False)
    descriptor=tmp_path/"gateway.json";descriptor.write_bytes(b"original descriptor")
    with closing(exclusive_listener()) as occupant:
        old=occupant.getsockname()[1]
        with closing(service._provider_listener(tmp_path,{"port":old})) as selected:
            assert selected.getsockname()[1] not in {0,old}
            assert selected.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)==1
            with socket.create_connection(("127.0.0.1",old),timeout=2) as client:
                peer,_=occupant.accept()
                with closing(peer):
                    client.sendall(b"OWN_OCCUPANT_UNCHANGED")
                    assert peer.recv(128)==b"OWN_OCCUPANT_UNCHANGED"
        assert descriptor.read_bytes()==b"original descriptor"

def test_active_profile_does_not_move_stable_port_and_closes_failed_socket(tmp_path,monkeypatch):
    monkeypatch.setattr(service,"native_active",lambda home:True)
    original_socket=socket.socket;created=[]
    with closing(exclusive_listener()) as occupant:
        def create(*args,**kwargs):
            value=original_socket(*args,**kwargs);created.append(value);return value
        monkeypatch.setattr(service.socket,"socket",create)
        with pytest.raises(RuntimeError,match="stable provider port is unavailable"):
            service._provider_listener(tmp_path,{"port":occupant.getsockname()[1]})
        assert len(created)==1 and created[0].fileno()==-1
        assert occupant.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)==1

def test_fresh_profile_returns_actual_ephemeral_listener(tmp_path,monkeypatch):
    def no_activity_query(home):raise AssertionError("A successful fresh bind does not need activity recovery")
    monkeypatch.setattr(service,"native_active",no_activity_query)
    with closing(service._provider_listener(tmp_path,{})) as selected:
        assert selected.getsockname()[1]>0
        assert selected.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)==1

def test_total_bind_failure_never_inspects_unbound_socket_and_closes(tmp_path,monkeypatch):
    class FailedSocket:
        closed=False
        def setsockopt(self,*args):pass
        def bind(self,*args):raise OSError(10013,"controlled bind denied")
        def getsockname(self):raise AssertionError("unbound Winsock address must not be queried")
        def listen(self,*args):raise AssertionError("unbound socket must not listen")
        def close(self):self.closed=True
    selected=FailedSocket()
    monkeypatch.setattr(service.socket,"socket",lambda:selected)
    monkeypatch.setattr(service,"native_active",lambda home:False)
    with pytest.raises(OSError,match="controlled bind denied"):
        service._provider_listener(tmp_path,{})
    assert selected.closed


def test_normal_supervisor_recovers_stale_occupied_port_without_native_or_model(tmp_path,monkeypatch):
    import json,time
    from pathlib import Path
    from codex_deepseek.platforms import private_runtime_home
    from codex_deepseek.processes import matches,terminate
    monkeypatch.setenv("OPENROUTER_API_KEY","SYNTHETIC-NONFUNCTIONAL-PORT-RECOVERY")
    home=tmp_path/"private-home";private_runtime_home(home)
    catalog=tmp_path/"catalog.json";catalog.write_text(json.dumps({"data":[]}))
    first=None;recovered=None
    try:
        first=service.ensure(home,catalog)
        assert service.healthy(first) and not service.native_active(home)
        terminate(first["pid"],first["birth"],timeout=8)
        deadline=time.monotonic()+6
        while matches(first.get("worker_pid"),first.get("worker_birth")) and time.monotonic()<deadline:
            time.sleep(.05)
        with closing(exclusive_listener()) as occupant:
            port=occupant.getsockname()[1]
            stale=dict(first);stale["port"]=port
            service.atomic_json(service.descriptor_path(home),stale)
            recovered=service.ensure(home,catalog)
            assert service.healthy(recovered)
            assert recovered["port"] not in {0,port}
            assert recovered["token"]==first["token"]
            assert recovered["pid"]!=first["pid"] or recovered["birth"]!=first["birth"]
            assert occupant.getsockname()[1]==port
            assert occupant.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)==1
            assert not service.native_active(home)
            from contextlib import closing as close_db
            import sqlite3
            with close_db(sqlite3.connect(home/"provider-state/adapter.sqlite")) as db:
                assert db.execute("select count(*) from receipts").fetchone()==(0,)
    finally:
        for record in [recovered,first]:
            if record:terminate(record["pid"],record["birth"],timeout=8)
