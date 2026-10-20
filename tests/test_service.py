"""Real private provider worker crash/restart with a retained listening socket."""
import json
import os
from pathlib import Path
import signal
import time

from codex_deepseek.processes import matches,terminate
from codex_deepseek.service import ensure,healthy,native_active,read_descriptor


def test_profile_service_reuses_identity_and_recovers_worker_without_port_change(tmp_path,monkeypatch):
    monkeypatch.setenv('CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS','15')
    home=tmp_path/'private-home';home.mkdir(mode=0o700)
    catalog=Path(__file__).resolve().parents[1]/'catalog.json'
    monkeypatch.setenv('CODEX_DEEPSEEK_TRACE_DIR',str(tmp_path/'traces'))
    value=ensure(home,catalog)
    monkeypatch.delenv('CODEX_DEEPSEEK_TRACE_DIR')
    try:
        assert healthy(value) and not native_active(home)
        assert (home/'provider-state/gateway.json').stat().st_mode&0o777==0o600
        again=ensure(home,catalog)
        assert again['pid']==value['pid'] and again['port']==value['port'] and again['token']==value['token']
        assert json.loads((home/'provider-state/trace.json').read_text())['directory']==str(tmp_path/'traces')
        monkeypatch.setenv('CODEX_DEEPSEEK_TRACE_DIR','')
        ensure(home,catalog)
        assert json.loads((home/'provider-state/trace.json').read_text())['directory'] is None
        actual=read_descriptor(home)
        assert matches(actual['worker_pid'],actual['worker_birth'])
        os.kill(actual['worker_pid'],signal.SIGKILL)
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
