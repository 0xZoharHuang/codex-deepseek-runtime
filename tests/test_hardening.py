"""Real transport/process failure regressions, beyond model response examples."""

import asyncio
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import time
import sqlite3
import pytest

import aiohttp
from aiohttp import web

from codex_deepseek.gateway import Gateway, sse_events
from codex_deepseek.protocol import MODEL
from codex_deepseek.state import Store, migrate_searches


async def test_large_sse_record_through_actual_http_transport(monkeypatch,tmp_path):
    import codex_deepseek.gateway as module
    text='中🦉' * 500000
    event={'type':'response.completed','response':{'id':'large','status':'completed','output':[{'type':'message','id':'msg_large','role':'assistant','content':[{'type':'output_text','text':text}]}]}}
    async def upstream(request):
        return web.Response(body=('data: '+json.dumps(event,ensure_ascii=False)+'\n\n').encode(),content_type='text/event-stream')
    app=web.Application();app.router.add_post('/responses',upstream)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
    monkeypatch.setattr(module,'UPSTREAM',f'http://127.0.0.1:{runner.addresses[0][1]}/responses')
    store=Store(tmp_path/'state.sqlite')
    try:
        async with Gateway('SECRET','LOCAL',store,{}) as gateway,aiohttp.ClientSession(read_bufsize=4*1024*1024) as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',headers={'Authorization':'Bearer LOCAL'},json={'model':MODEL,'stream':True}) as response:
                body=await response.text()
        decoded=next(event for line in body.splitlines() if line.startswith('data: ') and (event:=json.loads(line[6:])).get('type')=='response.completed')
        assert decoded['response']['output'][0]['content'][0]['text']==text
        assert store.db.execute('SELECT status FROM receipts').fetchone()[0]=='completed'
    finally:
        store.close();await runner.cleanup()


async def test_sse_fragmented_bom_unicode_comments_and_crlf():
    event={'type':'response.created','response':{'id':'字🦉'}}
    wire=b'\xef\xbb\xbf: comment\r\ndata: '+json.dumps(event,ensure_ascii=False).encode()+b'\r\n\r\ndata: [DONE]\r\n\r\n'
    class Chunks:
        async def iter_any(self):
            for byte in wire:yield bytes([byte])
    assert [item async for item in sse_events(Chunks())]==[event]


def test_profile_migration_copies_only_own_searches_and_owner_recovery(tmp_path):
    legacy=Store(tmp_path/'legacy.sqlite')
    legacy.put('ws_own',{'id':'own','status':'completed'})
    legacy.put('ws_other',{'id':'other','status':'completed'})
    home=tmp_path/'home';(home/'sessions').mkdir(parents=True)
    (home/'sessions/log.jsonl').write_text(json.dumps({'type':'response_item','payload':{'type':'web_search_call','id':'ws_own'}})+'\n')
    target=Store(home/'provider-state/adapter.sqlite')
    other=Store(tmp_path/'other/provider-state/adapter.sqlite')
    try:
        migrate_searches(legacy.path,home,target)
        assert target.get('ws_own') is not None and target.get('ws_other') is None
        assert other.get('ws_own') is None
        receipt=target.start();other_receipt=other.start()
        Store.owner_lost(target.path,target.owner)
        assert target.db.execute('SELECT status FROM receipts WHERE id=?',(receipt,)).fetchone()[0]=='owner_lost'
        assert other.db.execute('SELECT status FROM receipts WHERE id=?',(other_receipt,)).fetchone()[0]=='running'
    finally:
        legacy.close();target.close();other.close()


def test_killing_wrapper_does_not_orphan_owned_native_process(tmp_path):
    child=tmp_path/'native.py';pidfile=tmp_path/'native.pid'
    body=f'import os,pathlib,time; pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(120)'
    child.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -c '+shlex.quote(body)+'\n');child.chmod(0o755)
    code=f'''from pathlib import Path
import asyncio
import codex_deepseek.cli as cli
cli.credential=lambda:'FAKE'
cli.data_root=lambda:Path({str(tmp_path)!r})
cli.native=lambda:Path({str(child)!r})
cli.catalog=lambda:{{'models':[]}}
cli.ProviderService=lambda *args,**kwargs:cli.Gateway('FAKE','LOCAL',None,{{}})
asyncio.run(cli.run(['app-server']))
'''
    process=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    native_pid=None
    try:
        end=time.monotonic()+8
        while not pidfile.exists() and time.monotonic()<end:time.sleep(.05)
        assert pidfile.exists(),process.stderr.read().decode() if process.poll() is not None else 'Native did not start'
        native_pid=int(pidfile.read_text());process.kill();process.wait(timeout=3)
        end=time.monotonic()+8
        state=''
        while time.monotonic()<end:
            state=subprocess.run(['ps','-p',str(native_pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
            if not state or state.startswith('Z'):break
            time.sleep(.05)
        assert not state or state.startswith('Z'),'Owned native process outlived its killed wrapper'
    finally:
        if process.poll() is None:process.kill();process.wait()
        if native_pid:
            try:os.kill(native_pid,signal.SIGTERM)
            except ProcessLookupError:pass


@pytest.mark.parametrize('scenario',['supervisor_only','both_then_restart'])
def test_supervisor_crash_and_restart_reconcile_only_owned_processes(tmp_path,scenario):
    from codex_deepseek.processes import identity,matches
    child=tmp_path/'native';pidfile=tmp_path/'native.pid'
    body=f'import os,pathlib,time;pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(120)'
    child.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -c '+shlex.quote(body)+'\n');child.chmod(0o755)
    code=f'''from pathlib import Path
import asyncio
import codex_deepseek.cli as cli
class Pending(cli.Store):
 def __init__(self,path):
  super().__init__(path)
  self.start()
cli.Store=Pending
cli.credential=lambda:'FAKE'
cli.data_root=lambda:Path({str(tmp_path)!r})
cli.native=lambda:Path({str(child)!r})
cli.catalog=lambda:{{'models':[]}}
cli.ProviderService=lambda *args,**kwargs:cli.Gateway('FAKE','LOCAL',None,{{}})
asyncio.run(cli.run(['exec','test']))
'''
    wrapper=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    native_pid=None;birth=None;supervisor_pid=None
    state=tmp_path/'codex-home/provider-state/adapter.sqlite'
    try:
        end=time.monotonic()+8;row=None
        while time.monotonic()<end:
            if pidfile.exists() and state.exists():
                with sqlite3.connect(state) as db:row=db.execute('SELECT supervisor_pid,native_pid,native_birth FROM runtime_owners WHERE native_pid IS NOT NULL').fetchone()
                if row:break
            time.sleep(.025)
        assert row,wrapper.stderr.read().decode() if wrapper.poll() is not None else 'Native did not register'
        supervisor_pid,native_pid,birth=row
        if scenario=='supervisor_only':
            os.kill(supervisor_pid,signal.SIGKILL);wrapper.wait(timeout=8)
        else:
            os.kill(supervisor_pid,signal.SIGSTOP);wrapper.kill();wrapper.wait(timeout=3)
            os.kill(supervisor_pid,signal.SIGKILL)
            assert matches(native_pid,birth),'The experiment did not leave the intended owned orphan'
            time.sleep(.05)
            recovered=Store(state)
            try:assert recovered.db.execute('SELECT status FROM receipts').fetchone()[0]=='owner_lost'
            finally:recovered.close()
        end=time.monotonic()+5
        while matches(native_pid,birth) and time.monotonic()<end:time.sleep(.025)
        assert not matches(native_pid,birth)
        assert identity(os.getpid()) is not None
    finally:
        if wrapper.poll() is None:wrapper.kill();wrapper.wait(timeout=3)
        if native_pid and matches(native_pid,birth):os.kill(native_pid,signal.SIGKILL)
        if supervisor_pid:
            try:os.kill(supervisor_pid,signal.SIGCONT);os.kill(supervisor_pid,signal.SIGTERM)
            except ProcessLookupError:pass


def test_pid_reuse_mismatch_never_signals_a_live_unrelated_process():
    from codex_deepseek.processes import identity,terminate
    before=identity(os.getpid());terminate(os.getpid(),'different-start-time',timeout=0)
    assert identity(os.getpid())==before
