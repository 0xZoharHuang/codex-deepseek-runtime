"""Profile-owned native HTTP clients; Python handles only authenticated local IPC."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import time

import aiohttp
from .platforms import executable


def native_helper():
    from .cli import native
    path=executable(native().parent,'codex-http-transport')
    if not path.is_file():raise RuntimeError('The verified native HTTP transport is missing; rerun the installer')
    return path


def network_policy(headers):
    policy=headers.get('x-codex-deepseek-network-policy','default')
    if policy not in {'default','default-fallback','system'}:raise ValueError('Unknown native outbound network policy')
    return policy


class NativeTransport:
    def __init__(self,upstream,*,binary=None,metadata_path=None):
        self.upstream=upstream;self.binary=binary;self.metadata_path=metadata_path;self.failures=[];self.restarts=0;self.process=None;self.client=None
        self.lock=asyncio.Lock();self.token=secrets.token_urlsafe(48);self.port=None;self.closed=False

    async def start(self):
        async with self.lock:
            if self.closed:raise RuntimeError('Native transport is closed')
            if self.process is not None:
                if self.process.returncode is None:return
                self.failures=[stamp for stamp in self.failures if time.monotonic()-stamp<60]
                self.failures.append(time.monotonic())
                if len(self.failures)>5:raise RuntimeError('Native HTTP transport repeatedly crashed; refusing a restart loop')
                await self._stop();self.process=None;self.port=None;self.restarts+=1
            environment={key:value for key,value in os.environ.items()
                         if (not key.startswith('CODEX_') or key=='CODEX_CA_CERTIFICATE')
                         and key not in {'OPENROUTER_API_KEY','OPENAI_API_KEY'}}
            binary=Path(self.binary) if self.binary else native_helper()
            self.process=await asyncio.create_subprocess_exec(str(binary),stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=environment)
            try:
                self.process.stdin.write(json.dumps({'token':self.token,'upstream':self.upstream,'parent_pid':os.getpid()}).encode())
                await self.process.stdin.drain();self.process.stdin.close()
                ready=json.loads(await asyncio.wait_for(self.process.stdout.readline(),10))
                if ready.get('pid')!=self.process.pid or type(ready.get('port')) is not int or not 0<ready['port']<65536:
                    raise RuntimeError('Native HTTP transport returned invalid ownership metadata')
                self.port=ready['port']
                if self.metadata_path:
                    from .processes import identity
                    from .service import atomic_json
                    atomic_json(Path(self.metadata_path),{'pid':self.process.pid,'birth':identity(self.process.pid),'parent_pid':os.getpid(),'port':self.port,'restarts':self.restarts})
                self.client=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None,sock_connect=30,sock_read=None),
                    cookie_jar=aiohttp.DummyCookieJar(),trust_env=False,read_bufsize=1024*1024)
            except BaseException:
                await self._stop();raise

    async def _stop(self):
        if self.client is not None:await self.client.close();self.client=None
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:await asyncio.wait_for(self.process.wait(),5)
            except TimeoutError:self.process.kill();await self.process.wait()

    async def close(self):
        self.closed=True
        await self._stop()

    @asynccontextmanager
    async def request(self,method,url,*,api_key,payload=None,policy='default'):
        await self.start()
        async with self.client.post(f'http://127.0.0.1:{self.port}/forward',json={
            'api_key':api_key,'url':url,'method':method,'payload':payload,'policy':policy},
            headers={'Authorization':'Bearer '+self.token},allow_redirects=False) as response:
            yield response

    def post(self,url,*,json,headers,policy='default',**unused):
        authorization=headers.get('Authorization','')
        if not authorization.startswith('Bearer '):raise ValueError('Native provider credentials are missing')
        return self.request('POST',url,api_key=authorization[7:],payload=json,policy=policy)


def response_headers(headers):
    """Preserve transport advice and correlation across a transformed response."""
    return {name:value for name,value in headers.items() if name.lower() in {
        'retry-after','retry-after-ms','x-ms-retry-after-ms','x-request-id','x-openrouter-request-id'}
        or name.lower().startswith('x-ratelimit-')}
