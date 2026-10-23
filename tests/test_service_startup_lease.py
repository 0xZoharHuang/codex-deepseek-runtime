from contextlib import closing
from pathlib import Path
import json,sqlite3,subprocess,sys,time
import pytest
from codex_deepseek.state import Store,SERVICE_CLIENT_PREFIX
from codex_deepseek.service import native_active,idle_retirement,ensure,healthy,read_descriptor
from codex_deepseek.processes import matches,terminate

@pytest.fixture
def state(tmp_path):
    home=tmp_path/'profile';store=Store(home/'provider-state/adapter.sqlite')
    try:yield home,store
    finally:store.close()

def test_unmarked_provider_store_does_not_retain_itself(state):
    home,store=state
    assert not native_active(home)

def test_live_launcher_retains_before_native_is_bound(state):
    home,store=state
    Store.retain_service(store.path,store.owner)
    assert store.db.execute('select native_pid from runtime_owners where owner_id=?',(store.owner,)).fetchone()==(None,)
    assert native_active(home)
    assert idle_retirement(home) is None

def test_closed_launcher_releases_lease_and_removes_marker(tmp_path):
    home=tmp_path/'profile';store=Store(home/'provider-state/adapter.sqlite')
    Store.retain_service(store.path,store.owner);owner=store.owner;store.close()
    assert not native_active(home)
    with closing(sqlite3.connect(home/'provider-state/adapter.sqlite')) as db, db:
        assert db.execute('select value from adapter_metadata where key=?',(SERVICE_CLIENT_PREFIX+owner,)).fetchone() is None

def test_lost_owner_releases_lease(state):
    home,store=state
    Store.retain_service(store.path,store.owner)
    Store.owner_lost(store.path,store.owner)
    assert not native_active(home)

def test_unknown_owner_cannot_retain_service(state):
    home,store=state
    with pytest.raises(RuntimeError,match='no longer alive'):Store.retain_service(store.path,'absent-owner')
    assert not native_active(home)

def test_changed_kernel_birth_is_not_a_live_lease(state):
    home,store=state
    Store.retain_service(store.path,store.owner)
    store.db.execute('update runtime_owners set birth=? where owner_id=?',('foreign-birth',store.owner));store.db.commit()
    assert not native_active(home)

def test_dead_owner_cannot_acquire_lease(state):
    home,store=state
    store.db.execute('update runtime_owners set birth=? where owner_id=?',('foreign-birth',store.owner));store.db.commit()
    with pytest.raises(RuntimeError,match='no longer alive'):Store.retain_service(store.path,store.owner)
    assert not native_active(home)

def test_wrong_role_marker_cannot_keep_provider_alive(state):
    home,store=state
    store.db.execute('insert into adapter_metadata values (?,?)',(SERVICE_CLIENT_PREFIX+store.owner,'worker'));store.db.commit()
    assert not native_active(home)

def lock_attempt(home):
    package_root=str(Path(sys.modules['codex_deepseek.service'].__file__).parent.parent)
    code='import sys;sys.path.insert(0,'+repr(package_root)+');from pathlib import Path;from codex_deepseek.platforms import file_lock\ntry:\n with open('+repr(str(home/'provider-state/gateway.lock'))+',"a") as f:\n  with file_lock(f,blocking=False):print("acquired")\nexcept BlockingIOError:print("blocked")'
    result=subprocess.run([sys.executable,'-I','-c',code],capture_output=True,text=True,check=True,timeout=10)
    return result.stdout.strip()

def test_retirement_lock_blocks_concurrent_startup_until_release(state):
    home,store=state
    gate=idle_retirement(home);assert gate is not None
    try:assert lock_attempt(home)=='blocked'
    finally:gate.close()
    assert lock_attempt(home)=='acquired'

def test_late_client_prevents_retirement_after_startup_lock_released(state):
    home,store=state
    from codex_deepseek.platforms import file_lock
    with (home/'provider-state/gateway.lock').open('a') as f:
        with file_lock(f,blocking=False):
            assert idle_retirement(home) is None
            Store.retain_service(store.path,store.owner)
    assert idle_retirement(home) is None

def test_existing_healthy_service_reuse_retains_unbound_client(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','NONFUNCTIONAL-SERVICE-LEASE-TEST')
    monkeypatch.setenv('CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS','1')
    home=tmp_path/'profile';store=Store(home/'provider-state/adapter.sqlite');value=None;closed=False
    catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps({'data':[]}))
    try:
        value=ensure(home,catalog)
        retained=ensure(home,catalog,owner=store.owner)
        assert (retained['pid'],retained['birth'],retained['port'])==(value['pid'],value['birth'],value['port'])
        time.sleep(2.5)
        assert healthy(retained) and native_active(home)
        assert store.db.execute('select native_pid from runtime_owners where owner_id=?',(store.owner,)).fetchone()==(None,)
        assert store.db.execute('select count(*) from receipts').fetchone()[0]==0
        store.close();closed=True
        deadline=time.monotonic()+8
        while matches(value['pid'],value['birth']) and time.monotonic()<deadline:time.sleep(.05)
        assert not matches(value['pid'],value['birth'])
        current=read_descriptor(home)
        assert not matches(current.get('worker_pid'),current.get('worker_birth'))
    finally:
        if not closed:store.close()
        if value:terminate(value['pid'],value['birth'],timeout=2)
