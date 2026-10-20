"""Native transport ownership, authenticated IPC, pooling and crash recovery."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek.native_http import NativeTransport,native_helper
from codex_deepseek.processes import identity,matches,terminate


async def test_authenticated_native_transport_reuses_connections_and_recovers_next_request():
    calls=[]
    async def endpoint(request):
        calls.append(request.transport.get_extra_info('peername'))
        assert request.headers['Authorization']=='Bearer FIXTURE_ONLY'
        return web.json_response({'ok':True})
    app=web.Application();app.router.add_post('/responses',endpoint)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
    upstream=f'http://127.0.0.1:{runner.addresses[0][1]}/responses';transport=NativeTransport(upstream)
    try:
        for _ in range(2):
            async with transport.request('POST',upstream,api_key='FIXTURE_ONLY',payload={}) as response:assert response.status==200 and (await response.json())['ok']
        assert len(calls)==2 and calls[0]==calls[1],'The native upstream connection must be pooled'
        async with aiohttp.ClientSession() as client:
            for headers in [{},{'Authorization':'Bearer '+transport.token,'Origin':'https://untrusted.invalid'}]:
                async with client.post(f'http://127.0.0.1:{transport.port}/forward',data='not-json',headers=headers) as response:assert response.status==401
        old=transport.process.pid;os.kill(old,signal.SIGKILL);await transport.process.wait()
        async with transport.request('POST',upstream,api_key='FIXTURE_ONLY',payload={}) as response:assert response.status==200
        assert transport.process.pid!=old and len(calls)==3 and transport.restarts==1
    finally:await transport.close();await runner.cleanup()


def test_native_helper_does_not_outlive_killed_parent(tmp_path):
    """The parent cannot run finally; the native helper must observe kernel PPID."""
    import codex_deepseek.native_http as native_module
    binary=str(native_module.native_helper());record=tmp_path/'owned-child.json'
    program='''import subprocess,json,os,sys,time
from pathlib import Path
from codex_deepseek.processes import identity
child=subprocess.Popen([sys.argv[1]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
child.stdin.write(json.dumps({'token':'x'*64,'upstream':'https://openrouter.ai/api/v1/responses','parent_pid':os.getpid()}).encode());child.stdin.close()
ready=json.loads(child.stdout.readline());Path(sys.argv[2]).write_text(json.dumps({'pid':child.pid,'birth':identity(child.pid),'ready':ready}))
time.sleep(60)
'''
    from codex_deepseek.bootstrap import module_command
    package=str(Path(__import__('codex_deepseek').__file__).resolve().parent.parent)
    bootstrap='import sys;sys.path.insert(0,'+repr(package)+');exec('+repr(program)+')'
    parent=subprocess.Popen([sys.executable,'-I','-c',bootstrap,binary,str(record)])
    parent_birth=identity(parent.pid);owned=None
    try:
        end=time.monotonic()+5
        while not record.exists() and time.monotonic()<end:time.sleep(.02)
        assert record.exists(),'Native helper did not start'
        owned=json.loads(record.read_text());assert matches(owned['pid'],owned['birth'])
        os.kill(parent.pid,signal.SIGKILL);parent.wait(timeout=5)
        end=time.monotonic()+3
        while matches(owned['pid'],owned['birth']) and time.monotonic()<end:time.sleep(.025)
        assert not matches(owned['pid'],owned['birth']),'A helper was orphaned after its parent was killed'
    finally:
        terminate(parent.pid,parent_birth,timeout=1)
        if owned:terminate(owned['pid'],owned['birth'],timeout=1)
