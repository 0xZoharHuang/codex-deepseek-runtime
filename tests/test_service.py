"""Real private provider worker crash/restart with a retained listening socket."""
import json
import os
from pathlib import Path
import signal
import time

from codex_deepseek.processes import matches,terminate
from codex_deepseek.service import ensure,healthy,native_active,read_descriptor


def test_profile_service_reuses_identity_and_recovers_worker_without_port_change(tmp_path,monkeypatch,assert_private):
    monkeypatch.setenv('CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS','15')
    home=tmp_path/'private-home';home.mkdir(mode=0o700)
    catalog=Path(__file__).resolve().parents[1]/'catalog.json'
    monkeypatch.setenv('CODEX_DEEPSEEK_TRACE_DIR',str(tmp_path/'traces'))
    value=ensure(home,catalog)
    monkeypatch.delenv('CODEX_DEEPSEEK_TRACE_DIR')
    try:
        assert healthy(value) and not native_active(home)
        assert_private(home/'provider-state/gateway.json')
        again=ensure(home,catalog)
        assert again['pid']==value['pid'] and again['port']==value['port'] and again['token']==value['token']
        assert json.loads((home/'provider-state/trace.json').read_text())['directory']==str(tmp_path/'traces')
        monkeypatch.setenv('CODEX_DEEPSEEK_TRACE_DIR','')
        ensure(home,catalog)
        assert json.loads((home/'provider-state/trace.json').read_text())['directory'] is None
        actual=read_descriptor(home)
        assert matches(actual['worker_pid'],actual['worker_birth'])
        os.kill(actual['worker_pid'],getattr(signal,'SIGKILL',signal.SIGTERM))
        end=time.monotonic()+6
        while time.monotonic()<end:
            current=read_descriptor(home)
            if current.get('worker_pid')!=actual['worker_pid'] and healthy(current):break
            time.sleep(.05)
        assert current['worker_pid']!=actual['worker_pid'] and current['restarts']==1
        assert current['port']==value['port'] and current['token']==value['token'] and healthy(current)
    finally:
        terminate(value['pid'],value['birth'],timeout=8)
    assert not healthy(value)


def test_idle_service_exits_without_leaving_a_worker(tmp_path,monkeypatch):
    monkeypatch.setenv('CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS','1')
    home=tmp_path/'private-home';home.mkdir(mode=0o700)
    value=ensure(home,Path(__file__).resolve().parents[1]/'catalog.json')
    end=time.monotonic()+5
    while matches(value['pid'],value['birth']) and time.monotonic()<end:time.sleep(.05)
    current=read_descriptor(home)
    try:
        assert not matches(value['pid'],value['birth'])
        assert not matches(current['worker_pid'],current['worker_birth'])
    finally:terminate(value['pid'],value['birth'],timeout=2)


def test_corrupt_descriptor_recovers_backup_without_second_watchdog(tmp_path,monkeypatch):
    monkeypatch.setenv('CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS','15')
    home=tmp_path/'private-home';home.mkdir(mode=0o700)
    catalog=Path(__file__).resolve().parents[1]/'catalog.json'
    value=ensure(home,catalog)
    try:
        (home/'provider-state/gateway.json').write_text('{"pid":1,"port":"invalid"}')
        repaired=ensure(home,catalog)
        assert repaired['pid']==value['pid'] and repaired['token']==value['token'] and repaired['port']==value['port']
        assert json.loads((home/'provider-state/gateway.json').read_text())['pid']==value['pid']
        (home/'provider-state/gateway.json').write_text('[')
        (home/'provider-state/gateway.last-good.json').write_text('[')
        import pytest
        with pytest.raises(RuntimeError,match='lease is held'):ensure(home,catalog)
        assert matches(value['pid'],value['birth'])
    finally:terminate(value['pid'],value['birth'],timeout=8)


def test_schema_initialization_window_retains_service_until_ownership_is_known(tmp_path):
    import sqlite3
    from codex_deepseek.state import Store
    home=tmp_path/'profile';state=home/'provider-state/adapter.sqlite';state.parent.mkdir(parents=True)
    with sqlite3.connect(state) as db:db.execute('PRAGMA journal_mode=WAL')
    assert native_active(home) is True
    with sqlite3.connect(state) as db:
        db.execute('CREATE TABLE searches(id TEXT PRIMARY KEY,item TEXT NOT NULL)')
    assert native_active(home) is True
    store=Store(state)
    try:assert native_active(home) is False
    finally:store.close()


def test_unreadable_ownership_database_does_not_authorize_runtime_replacement(tmp_path):
    home=tmp_path/'profile';state=home/'provider-state/adapter.sqlite';state.parent.mkdir(parents=True)
    state.write_bytes(b'NOT_A_SQLITE_DATABASE')
    assert native_active(home) is True
    assert state.read_bytes()==b'NOT_A_SQLITE_DATABASE'


def test_real_supervisor_survives_worker_schema_publication_delay(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    monkeypatch.setenv('CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS','15')
    home=tmp_path/'profile';state=home/'provider-state/adapter.sqlite';state.parent.mkdir(parents=True)
    blocker=sqlite3.connect(state);blocker.execute('PRAGMA journal_mode=WAL');blocker.execute('BEGIN IMMEDIATE')
    value=None
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(ensure,home,Path(__file__).resolve().parents[1]/'catalog.json')
            deadline=time.monotonic()+5
            while not read_descriptor(home).get('worker_pid') and time.monotonic()<deadline:time.sleep(.02)
            descriptor=read_descriptor(home)
            assert matches(descriptor.get('pid'),descriptor.get('birth'))
            time.sleep(.35)
            assert not future.done(),'The service must wait for the worker schema rather than die or fake readiness'
            blocker.rollback()
            value=future.result(15)
            assert healthy(value) and not native_active(home)
    finally:
        blocker.close()
        if value:terminate(value['pid'],value['birth'],timeout=8)


def test_concurrent_large_catalog_reads_survive_three_worker_crashes(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import hashlib
    import urllib.request
    monkeypatch.setenv('CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS','30')
    home=tmp_path/'profile';catalog=tmp_path/'large-catalog.json'
    expected={'data':[{'id':'transport-fixture','description':'真实 UTF-8 transport '+('x'*2*1024*1024)}]}
    catalog.write_text(json.dumps(expected,ensure_ascii=False),encoding='utf-8')
    value=ensure(home,catalog);current=value
    def fetch(_):
        request=urllib.request.Request(f'http://127.0.0.1:{value["port"]}/v1/models',headers={'Authorization':'Bearer '+value['token']})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=10) as response:
            assert response.status==200
            result=json.load(response)
            assert result==expected
            return hashlib.sha256(result['data'][0]['description'].encode()).hexdigest()
    try:
        for crash_count in range(4):
            with ThreadPoolExecutor(4) as pool:assert len(set(pool.map(fetch,range(8))))==1
            if crash_count==3:break
            previous=read_descriptor(home)
            os.kill(previous['worker_pid'],getattr(signal,'SIGKILL',signal.SIGTERM))
            end=time.monotonic()+10
            while time.monotonic()<end:
                current=read_descriptor(home)
                if current.get('worker_pid')!=previous['worker_pid'] and healthy(current):break
                time.sleep(.05)
            assert current['restarts']==crash_count+1 and current['worker_pid']!=previous['worker_pid']
            assert current['port']==value['port'] and current['token']==value['token'] and healthy(current)
    finally:terminate(value['pid'],value['birth'],timeout=8)
