"""Exercise production native egress with authenticated proxy and real TLS sockets."""
import asyncio
import os
from pathlib import Path
import ssl
import subprocess

import aiohttp
from aiohttp import web
import pytest
from codex_deepseek.native_http import NativeTransport


def clear_proxies(monkeypatch):
    for name in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy']:
        monkeypatch.delenv(name,raising=False)


async def test_real_native_http_proxy_auth_and_no_proxy(monkeypatch):
    clear_proxies(monkeypatch);observed=[]
    async def proxy(request):
        observed.append((request.raw_path,request.headers.get('Proxy-Authorization'),request.headers.get('Authorization')))
        return web.json_response({'proxy':'ok'},headers={'Retry-After':'9'})
    app=web.Application();app.router.add_route('*','/{path:.*}',proxy)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
    proxy_url=f'http://u:p@127.0.0.1:{runner.addresses[0][1]}'
    monkeypatch.setenv('HTTP_PROXY',proxy_url);monkeypatch.setenv('ALL_PROXY',proxy_url)
    url='http://provider.invalid/responses';transport=NativeTransport(url)
    try:
        async with transport.request('POST',url,api_key='origin-secret',payload={}) as response:
            assert response.status==200 and response.headers['Retry-After']=='9' and await response.json()=={'proxy':'ok'}
        assert observed==[(url,'Basic dTpw','Bearer origin-secret')]
    finally:await transport.close();await runner.cleanup()


async def test_real_native_tls_ca_precedence_connect_tunnel_and_rejection(tmp_path,monkeypatch):
    clear_proxies(monkeypatch)
    ca=tmp_path/'ca.pem';key=tmp_path/'ca.key';server_key=tmp_path/'server.key';csr=tmp_path/'server.csr';cert=tmp_path/'server.pem'
    extensions=tmp_path/'extensions.cnf';extensions.write_text('subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n')
    commands=[['req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(ca),'-days','1','-subj','/CN=Isolated runtime test CA'],
              ['req','-newkey','rsa:2048','-nodes','-keyout',str(server_key),'-out',str(csr),'-subj','/CN=localhost'],
              ['x509','-req','-in',str(csr),'-CA',str(ca),'-CAkey',str(key),'-CAcreateserial','-out',str(cert),'-days','1','-extfile',str(extensions)]]
    for command in commands:subprocess.run(['openssl',*command],check=True,capture_output=True)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,server_key)
    async def endpoint(request):
        assert request.headers['Authorization']=='Bearer origin-only'
        return web.json_response({'tls':'ok'})
    app=web.Application();app.router.add_post('/',endpoint)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0,ssl_context=context).start()
    url=f'https://127.0.0.1:{runner.addresses[0][1]}/'
    transports=[];proxy=None;connections=set();observations=[]
    async def call():
        transport=NativeTransport(url);transports.append(transport)
        async with transport.request('POST',url,api_key='origin-only',payload={}) as response:return response.status,await response.json()
    try:
        monkeypatch.delenv('CODEX_CA_CERTIFICATE',raising=False);monkeypatch.delenv('SSL_CERT_FILE',raising=False)
        status,body=await call();assert status==502 and 'origin-only' not in str(body)
        monkeypatch.setenv('CODEX_CA_CERTIFICATE',str(ca));monkeypatch.setenv('SSL_CERT_FILE',str(tmp_path/'ignored-missing.pem'))
        assert await call()==(200,{'tls':'ok'})
        async def tunnel(reader,writer):
            task=asyncio.current_task();connections.add(task);upstream=None
            try:
                header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),3);observations.append(header.decode())
                upstream_reader,upstream=await asyncio.open_connection('127.0.0.1',runner.addresses[0][1])
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n');await writer.drain()
                async def copy(source,target):
                    while data:=await source.read(65536):target.write(data);await target.drain()
                tasks=[asyncio.create_task(copy(reader,upstream)),asyncio.create_task(copy(upstream_reader,writer))]
                try:await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for pending in tasks:pending.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
            finally:
                if upstream:upstream.close();await upstream.wait_closed()
                writer.close();await writer.wait_closed();connections.discard(task)
        proxy=await asyncio.start_server(tunnel,'127.0.0.1',0)
        monkeypatch.setenv('HTTPS_PROXY',f'http://u:p@127.0.0.1:{proxy.sockets[0].getsockname()[1]}')
        assert await call()==(200,{'tls':'ok'})
        assert len(observations)==1 and observations[0].startswith('CONNECT ')
        assert 'proxy-authorization: basic dtpw' in observations[0].lower() and 'origin-only' not in observations[0]
        monkeypatch.setenv('NO_PROXY','127.0.0.1');assert await call()==(200,{'tls':'ok'})
        assert len(observations)==1,'NO_PROXY must bypass the CONNECT proxy'
        monkeypatch.setenv('CODEX_CA_CERTIFICATE',str(tmp_path/'missing.pem'))
        status,body=await call();assert status==502,'Invalid CA must not silently fall back'
        monkeypatch.setenv('CODEX_CA_CERTIFICATE','');monkeypatch.setenv('SSL_CERT_FILE',str(ca))
        assert await call()==(200,{'tls':'ok'})
    finally:
        for transport in transports:await transport.close()
        if proxy:proxy.close();await proxy.wait_closed()
        for task in list(connections):task.cancel()
        await asyncio.gather(*list(connections),return_exceptions=True)
        await runner.cleanup()
