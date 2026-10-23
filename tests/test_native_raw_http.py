"""Real native helper against owned loopback TLS/proxy fixtures, never OpenRouter."""
import asyncio,base64,datetime,hashlib,ipaddress,json,os,ssl
from contextlib import asynccontextmanager
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path
import aiohttp
from aiohttp import web
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
from codex_deepseek.native_http import NativeTransport
from codex_deepseek.image_provider import NativeFilesSession
from codex_deepseek.image_transport import ImageTransport,TransportPolicy,OpenRouterFilesBackend,encode_body,image_plan
from test_network import clear_proxies
from test_image_transport import png,part

KEY='synthetic-native-provider-only'
UPSTREAM='https://openrouter.ai/api/v1/responses'


def certificate(root):
    now=datetime.datetime.now(datetime.timezone.utc);ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    issuer=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'CDS native raw fixture CA')])
    def base(subject,key):
        return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1)))
    ca=base(issuer,ca_key).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(ca_key,hashes.SHA256())
    leaf=(base(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'openrouter.ai')]),key)
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('openrouter.ai'),x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False).sign(ca_key,hashes.SHA256()))
    ca_path=root/'ca.pem';cert=root/'server.pem';key_path=root/'server.key'
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM));cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM));key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key_path);return ca_path,context


class Origin:
    def __init__(self):
        self.files={};self.calls=[];self.models=[];self.tunnels=[];self.handlers=set();self.started=asyncio.Event();self.disconnected=asyncio.Event();self.hold=None;self.error_status=None
    async def handle(self,request):
        assert request.headers.get('Authorization')=='Bearer '+KEY
        raw=await request.read();self.calls.append((request.method,request.path,len(raw)))
        if request.path=='/api/v1/responses':
            self.models.append(raw)
            if self.error_status:return web.Response(status=self.error_status,body=b'<html>synthetic failure</html>',headers={'Retry-After':'7','X-Request-Id':'synthetic-transport-proof'})
            if self.hold=='model':
                self.started.set()
                try:await asyncio.Future()
                finally:self.disconnected.set()
            return web.json_response({'id':'gen-native-fixture','status':'completed','model':'deepseek/deepseek-v4.1-flash-1017','output':[]})
        if request.method=='POST':
            message=BytesParser(policy=email_policy.default).parsebytes(('Content-Type: '+request.headers['Content-Type']+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
            parts=list(message.iter_parts());assert len(parts)==1
            data=parts[0].get_payload(decode=True);fid='or_file_'+str(len(self.files)+1).zfill(12)
            metadata={'id':fid,'filename':parts[0].get_filename(),'mime_type':parts[0].get_content_type(),'size_bytes':len(data)}
            self.files[fid]=(metadata,data)
            if self.hold=='upload':
                self.started.set()
                try:await asyncio.Future()
                finally:self.disconnected.set()
            return web.json_response(metadata)
        if request.path=='/api/v1/files':
            # Only this owned fake account exists. Actual account listing is never used.
            return web.json_response({'data':[value[0] for value in self.files.values()],'has_more':False,'next_cursor':None})
        fid=request.path.rsplit('/',1)[-1]
        if fid not in self.files:return web.json_response({'error':{'code':'not_found'}},status=404)
        if request.method=='DELETE':self.files.pop(fid);return web.json_response({'id':fid,'type':'file_deleted'})
        return web.json_response(self.files[fid][0])


@pytest.fixture
async def origin(tmp_path,monkeypatch):
    assert os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY'),'Real helper tests require an explicit isolated binary'
    clear_proxies(monkeypatch);monkeypatch.delenv('SSL_CERT_FILE',raising=False);monkeypatch.delenv('CODEX_CA_CERTIFICATE',raising=False)
    state=Origin();ca,context=certificate(tmp_path);state.ca=ca
    app=web.Application(client_max_size=101*1024*1024);app.router.add_route('*','/{path:.*}',state.handle)
    runner=web.AppRunner(app,handler_cancellation=True,shutdown_timeout=.2);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0,ssl_context=context).start()
    port=runner.addresses[0][1];state.local_url=f'https://127.0.0.1:{port}/api/v1/responses'
    async def tunnel(reader,writer):
        task=asyncio.current_task();state.handlers.add(task);upstream=None
        try:
            header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),3)
            text=header.decode('ascii');state.tunnels.append(text)
            assert text.startswith('CONNECT ') and 'proxy-authorization: basic dtpw' in text.lower() and KEY not in text
            source,upstream=await asyncio.open_connection('127.0.0.1',port)
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n');await writer.drain()
            async def copy(source,target):
                while data:=await source.read(65536):target.write(data);await target.drain()
            jobs=[asyncio.create_task(copy(reader,upstream)),asyncio.create_task(copy(source,writer))]
            try:await asyncio.wait(jobs,return_when=asyncio.FIRST_COMPLETED)
            finally:
                for job in jobs:job.cancel()
                await asyncio.gather(*jobs,return_exceptions=True)
        except (ConnectionError,asyncio.IncompleteReadError,asyncio.CancelledError):pass
        finally:
            if upstream:upstream.close()
            writer.close();state.handlers.discard(task)
    proxy=await asyncio.start_server(tunnel,'127.0.0.1',0)
    monkeypatch.setenv('HTTPS_PROXY',f'http://u:p@127.0.0.1:{proxy.sockets[0].getsockname()[1]}');monkeypatch.setenv('CODEX_CA_CERTIFICATE',str(ca))
    try:yield state
    finally:
        proxy.close();await proxy.wait_closed()
        for task in list(state.handlers):task.cancel()
        await asyncio.gather(*list(state.handlers),return_exceptions=True);await runner.cleanup()


async def test_native_raw_large_multipart_files_delete_exact_wire_and_warm_reuse(origin,tmp_path):
    from synthetic_image_capacity import make_fixture
    payload,manifest=make_fixture();payload['provider']['only']=['deepseek'];payload['input'][-2]['content'][0]['text']+='x'
    original=encode_body(payload);assert len(original)==34009154
    native=NativeTransport(UPSTREAM)
    transport=ImageTransport(tmp_path/'image-cache',lambda:KEY,OpenRouterFilesBackend(NativeFilesSession(native,'default')),TransportPolicy(1,wire_budget_bytes=30*1024*1024,verify_after_seconds=60,idle_ttl_seconds=0))
    try:
        async with transport.prepare(payload,credential=KEY) as prepared:
            assert native.raw_protocol_version==1 and prepared.uploads==16
            wire=prepared.body;assert len(wire)<30*1024*1024
            positions,blobs=image_plan(payload);converted=0
            for (item,field,index),digest in positions:
                value=prepared.payload['input'][item][field][index]
                if 'file_id' in value:
                    converted+=1;assert hashlib.sha256(origin.files[value['file_id']][1]).hexdigest()==digest
                    assert origin.files[value['file_id']][1]==blobs[digest].data
                else:assert value==payload['input'][item][field][index]
            assert converted==16 and len(positions)==101
            await prepared.mark_upstream_started('native-wire-attempt-one')
            async with native.request_raw('POST',UPSTREAM,api_key=KEY,body=wire,content_type='application/json') as response:
                assert response.status==200;result=await response.json()
            await prepared.mark_upstream_settled(result['id'],result['status'])
            assert origin.models==[wire] and encode_body(payload)==original
        prior=len(origin.calls)
        async with transport.prepare(payload,credential=KEY) as second:
            assert second.uploads==0 and second.reused==16 and second.body==wire
        assert len(origin.calls)==prior,'Warm cache must not add per-image native GETs'
        result=await transport.cleanup(all_unpinned=True)
        assert result['deleted']==16 and result['all_owned_files_deleted'] and not origin.files
        assert len(origin.tunnels)==1,'One origin/policy must reuse its native HTTPS pool'
        assert sum(method=='DELETE' for method,path,size in origin.calls)==16
        assert len(origin.models)==1
    finally:await native.close()


@pytest.mark.parametrize('status',[413,429,500])
async def test_native_raw_errors_preserve_status_headers_body_without_replay(origin,status):
    origin.error_status=status;native=NativeTransport(UPSTREAM)
    try:
        raw=b' { "preserve" : 1.00 } '
        async with native.request_raw('POST',UPSTREAM,api_key=KEY,body=raw,content_type='application/json') as response:
            assert response.status==status and response.headers['Retry-After']=='7'
            assert response.headers['X-Request-Id']=='synthetic-transport-proof'
            assert await response.read()==b'<html>synthetic failure</html>'
        assert origin.models==[raw]
    finally:await native.close()


async def test_native_raw_gate_denies_auth_origin_metadata_and_non_files_delete(origin):
    native=NativeTransport(UPSTREAM)
    try:
        await native.require_raw()
        async with aiohttp.ClientSession(trust_env=False) as local:
            url=f'http://127.0.0.1:{native.port}/forward-raw'
            for headers in [{},{'Authorization':'Bearer '+native.token,'Origin':'https://untrusted.invalid'}]:
                async with local.post(url,data=b'bad',headers=headers) as response:assert response.status==401
            base={'Authorization':'Bearer '+native.token}
            for metadata in ['x'*8193,'{"api_key":"a","api_key":"b","url":"x","method":"GET","policy":"default"}']:
                async with local.post(url,data=b'',headers={**base,'X-CDS-Transport-Metadata':metadata}) as response:assert response.status==400
        for method,url in [('POST','https://other.invalid/api/v1/responses'),('DELETE','https://openrouter.ai/api/v1/keys/or_file_12345678'),('DELETE',UPSTREAM)]:
            async with native.request_raw(method,url,api_key=KEY,body=b'{}' if method=='POST' else b'',content_type='application/json' if method=='POST' else None) as response:assert response.status==400
        assert not origin.calls
    finally:await native.close()


async def test_native_raw_tls_rejection_and_no_proxy(origin,monkeypatch,tmp_path):
    native=NativeTransport(UPSTREAM)
    monkeypatch.setenv('CODEX_CA_CERTIFICATE',str(tmp_path/'missing-ca.pem'))
    try:
        async with native.request_raw('POST',UPSTREAM,api_key=KEY,body=b'{}',content_type='application/json') as response:
            assert response.status==502 and KEY not in (await response.text())
        assert not origin.calls
    finally:await native.close()
    monkeypatch.setenv('CODEX_CA_CERTIFICATE',str(origin.ca));monkeypatch.setenv('NO_PROXY','127.0.0.1')
    native=NativeTransport(origin.local_url);before=len(origin.tunnels)
    try:
        async with native.request_raw('POST',origin.local_url,api_key=KEY,body=b'{}',content_type='application/json') as response:assert response.status==200
        assert len(origin.tunnels)==before
    finally:await native.close()


async def test_native_raw_cancel_model_before_upstream_headers(origin):
    origin.hold='model';native=NativeTransport(UPSTREAM)
    async def call():
        async with native.request_raw('POST',UPSTREAM,api_key=KEY,body=b'{}',content_type='application/json'):pass
    try:
        task=asyncio.create_task(call());await asyncio.wait_for(origin.started.wait(),5);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        await asyncio.wait_for(origin.disconnected.wait(),5)
        assert len(origin.models)==1
    finally:await native.close()


async def test_native_upload_cancel_before_headers_preserves_unknown_without_replay(origin,tmp_path):
    origin.hold='upload';native=NativeTransport(UPSTREAM)
    transport=ImageTransport(tmp_path/'image-cache',lambda:KEY,OpenRouterFilesBackend(NativeFilesSession(native,'default')),TransportPolicy(1))
    async def prepare():
        async with transport.prepare({'input':[{'role':'user','content':[part(png())]}]}):pass
    try:
        task=asyncio.create_task(prepare());await asyncio.wait_for(origin.started.wait(),5);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        await asyncio.wait_for(origin.disconnected.wait(),5)
        diagnostics=await transport.diagnostics(private=True)
        assert len(diagnostics['assets'])==1 and diagnostics['assets'][0]['state']=='upload_unknown'
        assert len(origin.files)==1 and not origin.models
        # A second prepare must fail locally, not repeat the upload.
        from codex_deepseek.image_transport import UploadOutcomeUnknown
        with pytest.raises(UploadOutcomeUnknown):await prepare()
        assert sum(method=='POST' for method,path,size in origin.calls)==1
    finally:await native.close()
