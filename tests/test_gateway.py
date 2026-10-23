"""Transport invariants: local authorization, no retries, terminal errors and cancellation."""

import asyncio

import aiohttp
from aiohttp import web
import pytest

import codex_deepseek.gateway as module
from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import MODEL


@pytest.fixture
async def fake_upstream(monkeypatch):
    handlers = []
    async def dispatch(request):
        return await handlers[0](request)
    app = web.Application()
    app.router.add_post("/responses", dispatch)
    runner = web.AppRunner(app, handler_cancellation=True)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    monkeypatch.setattr(module, "UPSTREAM", f"http://127.0.0.1:{runner.addresses[0][1]}/responses")
    yield handlers
    await runner.cleanup()


async def test_no_retry_or_key_leak_on_upstream_error(fake_upstream, tmp_path):
    calls = 0
    async def failure(request):
        nonlocal calls
        calls += 1
        assert request.headers["Authorization"] == "Bearer SECRET"
        payload = await request.json()
        assert payload["provider"]["only"] == ["deepseek"]
        return web.json_response({"error": {"message": "rate limit"}}, status=429,headers={"Retry-After":"7","X-Request-Id":"native-retry-proof"})
    fake_upstream.append(failure)
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SECRET", "LOCAL", store, {}) as gateway, aiohttp.ClientSession() as client:
            url = f"http://127.0.0.1:{gateway.port}/v1/responses"
            async with client.post(url, json={"model": MODEL}, headers={"Authorization": "Bearer LOCAL"}) as response:
                assert response.status == 429
                assert response.headers["Retry-After"]=="7" and response.headers["X-Request-Id"]=="native-retry-proof"
                assert "SECRET" not in await response.text()
            async with client.post(url, json={"model": MODEL}) as response:
                assert response.status == 401
        assert calls == 1
    finally:
        store.close()


async def test_truncated_stream_reports_failure_and_does_not_complete(fake_upstream, tmp_path):
    async def truncated(request):
        return web.Response(body=b'event: response.created\ndata: {"type":"response.created","response":{"id":"r1"}}\n\n', content_type="text/event-stream")
    fake_upstream.append(truncated)
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SECRET", "LOCAL", store, {}) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", json={"model": MODEL, "stream": True}, headers={"Authorization": "Bearer LOCAL"}) as response:
                text = await response.text()
                assert "response.failed" in text and "response.completed" not in text
        assert store.db.execute("SELECT status FROM receipts").fetchone()[0] == "adapter_error"
    finally:
        store.close()


async def test_native_disconnect_closes_inflight_upstream(fake_upstream, tmp_path):
    disconnected = asyncio.Event()
    started = asyncio.Event()
    async def slow(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'data: {"type":"response.created","response":{"id":"r1"}}\n\n')
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            disconnected.set()
    fake_upstream.append(slow)
    store = Store(tmp_path / "state.sqlite")
    try:
        async with Gateway("SECRET", "LOCAL", store, {}) as gateway, aiohttp.ClientSession() as client:
            response = await client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", json={"model": MODEL, "stream": True}, headers={"Authorization": "Bearer LOCAL"})
            await asyncio.wait_for(started.wait(), 2)
            await response.content.read(10)
            response.close()
            await asyncio.wait_for(disconnected.wait(), 2)
        assert store.db.execute("SELECT status FROM receipts").fetchone()[0] == "cancelled"
    finally:
        store.close()


async def test_failed_trace_storage_records_terminal_failure_without_upstream(fake_upstream,tmp_path,monkeypatch):
    trace=tmp_path/'not-a-directory';trace.write_text('preserve')
    monkeypatch.setenv('CODEX_DEEPSEEK_TRACE_DIR',str(trace))
    store=Store(tmp_path/'state.sqlite')
    try:
        async with Gateway('SECRET','LOCAL',store,{}) as gateway,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json={'model':MODEL},headers={'Authorization':'Bearer LOCAL'}) as response:
                assert response.status==502
        assert store.db.execute('SELECT status FROM receipts').fetchone()[0]=='adapter_error'
        assert trace.read_text()=='preserve'
    finally:
        store.close()


async def test_damaged_trace_configuration_fails_before_upstream_with_terminal_receipt(fake_upstream,tmp_path):
    import json
    def broken():raise json.JSONDecodeError('damaged private trace configuration','[',1)
    store=Store(tmp_path/'state.sqlite')
    try:
        async with Gateway('SECRET','LOCAL',store,{},trace_source=broken) as gateway,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json={'model':MODEL},headers={'Authorization':'Bearer LOCAL'}) as response:
                assert response.status==502 and 'SECRET' not in await response.text()
        assert store.db.execute('SELECT status FROM receipts').fetchall()==[('adapter_error',)]
    finally:store.close()


@pytest.mark.parametrize('stream',[False,True])
@pytest.mark.parametrize('fault',['earlier-final','final-with-tool','nonfinite','tool-before-terminal'])
async def test_structured_faults_never_publish_native_message_completion(fake_upstream,tmp_path,stream,fault):
    import json
    def message(text,identity,**kw):return dict(type='message',id=identity,role='assistant',content=[{'type':'output_text','text':text}],**kw)
    bad=message('{"value":1e999}' if fault=='nonfinite' else 'INVALID','bad',phase='final_answer')
    output=[bad]
    if fault=='earlier-final':output.append(message('{"value":1}','good'))
    if fault in {'final-with-tool','tool-before-terminal'}:output.append({'type':'function_call','id':'fc','call_id':'c','name':'fixture','arguments':'{}'})
    terminal={'id':'r','status':'completed','output':output}
    async def provider(request):
        if not stream:return web.json_response(terminal)
        events=[{'type':'response.created','response':{'id':'r','status':'in_progress','output':[]}},
                {'type':'response.output_item.added','output_index':0,'item':{**bad,'content':[]}},
                {'type':'response.output_text.delta','item_id':'bad','delta':bad['content'][0]['text']},
                {'type':'response.output_item.done','output_index':0,'item':bad},
                {'type':'response.completed','response':terminal}]
        if fault=='tool-before-terminal':events.insert(-1,{'type':'response.output_item.done','output_index':1,'item':output[-1]})
        return web.Response(text=''.join('data: '+json.dumps(event)+'\n\n' for event in events),content_type='text/event-stream')
    fake_upstream.append(provider)
    store=Store(tmp_path/'state.sqlite')
    body={'model':MODEL,'input':'Fixture','stream':stream,'tools':[{'type':'function','name':'fixture','parameters':{'type':'object'}}],
          'text':{'format':{'type':'json_schema','name':'fixture','strict':True,'schema':{'type':'object','properties':{'value':{'type':'number'}},'required':['value'],'additionalProperties':False}}}}
    try:
        async with Gateway('SECRET','LOCAL',store,{}) as gateway,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json=body,headers={'Authorization':'Bearer LOCAL'}) as response:
                if stream:
                    events=[json.loads(line[6:]) for line in (await response.text()).splitlines() if line.startswith('data: ')]
                    assert events[-1]['type']=='response.failed'
                    assert not any(e['type'] in {'response.completed','response.output_item.done'} for e in events)
                else:assert response.status==502
        assert store.db.execute('SELECT status FROM receipts').fetchall()==[('adapter_error',)]
    finally:store.close()


class _OpaqueInvalidCredential:
    def __bool__(self):
        raise AssertionError("Credential truthiness must not be inspected")
    def __str__(self):
        raise AssertionError("Credential string conversion must not be used")
    def __repr__(self):
        raise AssertionError("Credential repr must not be used")


@pytest.mark.parametrize("invalid", [
    None, "", b"synthetic-credential-material", True, 7,
    {"private": "synthetic-credential-material"}, ["synthetic-credential-material"],
    _OpaqueInvalidCredential(),
], ids=["none", "empty", "bytes", "bool", "integer", "mapping", "list", "opaque"])
@pytest.mark.parametrize("stream", [False, True])
async def test_invalid_credential_types_finish_receipt_without_transport(tmp_path, invalid, stream):
    calls = []
    class NoTransport:
        def post(self, *args, **kwargs):
            calls.append(True)
            raise AssertionError("Invalid credentials must never reach transport")
    class Request:
        headers = {"Authorization": "Bearer LOCAL"}
        async def json(self):
            return {"model": MODEL, "input": "Synthetic input never sent", "stream": stream}
    store = Store(tmp_path / "state.sqlite")
    try:
        # Direct public handler + real fresh Store: no native helper or HTTP server
        # needs to start in order to verify this pre-network failure path.
        gateway = Gateway(lambda: invalid, "LOCAL", store, {}, trace_source=lambda: None)
        gateway.client = NoTransport()
        response = await gateway.responses(Request())
        assert response.status == 502
        import json
        assert json.loads(response.text)["error"]["message"] == "No usable provider credential"
        assert "synthetic-credential-material" not in response.text
        rows = store.db.execute("SELECT started,finished,status,detail FROM receipts").fetchall()
        assert len(rows) == 1
        started, finished, status, detail = rows[0]
        assert finished is not None and finished >= started
        assert status == "adapter_error" and detail == "No usable provider credential"
        assert calls == []
    finally:
        store.close()


@pytest.mark.parametrize("callback", [False, True])
async def test_valid_captured_string_still_redacts_failure_without_native(tmp_path, callback):
    key = "synthetic-valid-credential-material"
    class FailingTransport:
        def post(self, *args, **kwargs):
            assert kwargs["headers"]["Authorization"] == "Bearer " + key
            raise OSError("Synthetic transport failure contains " + key)
    class Request:
        headers = {"Authorization": "Bearer LOCAL"}
        async def json(self):
            return {"model": MODEL, "input": "Synthetic input never sent"}
    store = Store(tmp_path / "state.sqlite")
    try:
        gateway = Gateway((lambda: key) if callback else key, "LOCAL", store, {}, trace_source=lambda: None)
        gateway.client = FailingTransport()
        response = await gateway.responses(Request())
        assert response.status == 502 and key not in response.text
        assert "Synthetic transport failure" in response.text and "[REDACTED]" in response.text
        finished, status, detail = store.db.execute("SELECT finished,status,detail FROM receipts").fetchone()
        assert finished is not None and status == "adapter_error"
        assert key not in detail and "[REDACTED]" in detail
    finally:
        store.close()
