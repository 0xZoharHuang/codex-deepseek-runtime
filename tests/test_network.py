"""Exercise production native egress with authenticated proxy and real TLS sockets."""
import asyncio
import os
import ssl

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
    # Use the already locked cryptography dependency to mint a fresh test CA;
    # no platform-specific OpenSSL executable or machine trust-store changes.
    import datetime,ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
    ca=tmp_path/'ca.pem';server_key=tmp_path/'server.key';cert=tmp_path/'server.pem'
    now=datetime.datetime.now(datetime.timezone.utc)
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    leaf_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    issuer=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Isolated runtime test CA')])
    def builder(subject,key):
        return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1)))
    ca_cert=builder(issuer,ca_key).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(ca_key,hashes.SHA256())
    leaf=(builder(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]),leaf_key)
          .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
          .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
          .sign(ca_key,hashes.SHA256()))
    ca.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM));cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    server_key.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
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
