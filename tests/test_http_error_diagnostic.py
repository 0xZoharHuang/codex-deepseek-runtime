"""Safe HTTP diagnostics are observations only; real localhost, no model calls."""
import json

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek import gateway as module
from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import MODEL
from test_gateway import fake_upstream

KEY = 'SYNTHETIC-PRIVATE-CREDENTIAL'
PRIVATE = 'PRIVATE_PROMPT_IMAGE_PATH_VALUE'


def encoded(error):
    return json.dumps({'error': error, 'request': PRIVATE, 'image': 'data:image/png;base64,' + PRIVATE}).encode()


CASES = [
    (encoded({'code': 400, 'type': 'invalid_request_error', 'message': PRIVATE}), 'application/json', 'json', 400, 'invalid_request_error'),
    (encoded({'code': 'context_length_exceeded', 'type': 'invalid_request_error', 'message': PRIVATE}), 'application/problem+json', 'json', 'context_length_exceeded', 'invalid_request_error'),
    (encoded({'code': PRIVATE.lower(), 'type': KEY, 'message': PRIVATE}), 'application/json', 'json', None, None),
    (encoded({'code': True, 'type': [PRIVATE]}), 'application/json', 'json', None, None),
    (encoded({'code': {'value': PRIVATE}, 'type': {'value': PRIVATE}}), 'application/json', 'json', None, None),
    (encoded({'code': 999999, 'type': 'future_type'}), 'application/json', 'json', None, None),
    (b'{"error":{"code":400,"code":401,"message":"' + PRIVATE.encode() + b'"}}', 'application/json', 'invalid_json', None, None),
    (b'{"error":{"code":NaN}}', 'application/json', 'invalid_json', None, None),
    (b'\xff\xfe' + PRIVATE.encode(), 'application/json', 'invalid_json', None, None),
    (b'[' * 1200 + b']' * 1200, 'application/json', ('invalid_json', 'no_error_object'), None, None),
    (b'[' * 10000 + b']' * 10000, 'application/json', ('invalid_json', 'no_error_object'), None, None),
    (b'{"error":{"code":400}}' + b' ' * (32768 - len(b'{"error":{"code":400}}')), 'application/json', 'json', 400, None),
    (b'{"error":{"code":400}}' + b' ' * 32768, 'application/json', 'oversize', None, None),
    (b'<html>' + PRIVATE.encode() + b'</html>', 'text/html', 'non_json', None, None),
    (encoded({'code': 400}), 'text/plain', 'non_json', None, None),
    (b'', 'application/json', 'empty', None, None),
    (json.dumps({'message': PRIVATE, 'code': 400}).encode(), 'application/json', 'no_error_object', None, None),
]


@pytest.mark.parametrize('raw,mime,classification,code,error_type', CASES)
async def test_http_failure_detail_is_safe_bounded_and_response_is_unchanged(fake_upstream, tmp_path, raw, mime, classification, code, error_type):
    calls = []
    async def provider(request):
        calls.append(await request.json())
        return web.Response(body=raw, status=400, content_type=mime,
            headers={'Retry-After': '7', 'X-Request-Id': PRIVATE, 'X-OpenRouter-Request-Id': PRIVATE + '-2'})
    fake_upstream.append(provider)
    store = Store(tmp_path/'state.sqlite')
    try:
        async with Gateway(KEY, 'LOCAL', store, {}, trace_source=lambda: None) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL}, headers={'Authorization': 'Bearer LOCAL'}) as response:
                assert response.status == 400
                returned = await response.read()
                if classification == 'invalid_json' or isinstance(classification, tuple):
                    fallback = json.loads(returned)
                    assert fallback['error']['code'] == 'upstream_error_body_unverified'
                    assert 'could not be safely inspected' in fallback['error']['message']
                else:
                    assert returned == raw.replace(KEY.encode(), b'[REDACTED]')
                assert response.headers['Retry-After'] == '7'
                assert response.headers['X-Request-Id'] == PRIVATE
                assert response.headers['X-OpenRouter-Request-Id'] == PRIVATE + '-2'
        row = store.db.execute('SELECT status,upstream_status,response_id,usage,detail FROM receipts').fetchone()
        assert row[:4] == ('upstream_error', 400, None, '{}')
        assert row[4] is not None
        detail = json.loads(row[4])
        # Decoder nesting limits vary by Python/pytest. A valid nested array
        # may be refused or parsed without an error object; neither leaks data.
        allowed = classification if isinstance(classification, tuple) else (classification,)
        assert detail['body_status'] in allowed
        assert detail == {'schema': 1, 'event_type': 'upstream.http_error', 'body_status': detail['body_status'],
                          **({'error_code': code} if code is not None else {}),
                          **({'error_type': error_type} if error_type is not None else {})}
        assert len(row[4].encode()) <= 1024
        assert PRIVATE not in row[4] and KEY not in row[4] and 'data:image' not in row[4]
        database = '\n'.join(store.db.iterdump())
        assert PRIVATE not in database and KEY not in database and 'data:image' not in database
        assert len(calls) == 1
        assert store.db.execute('SELECT count(*) FROM completed_native_evidence').fetchone() == (0,)
        assert store.db.execute("SELECT count(*) FROM adapter_metadata WHERE key LIKE 'native-complete-v1:%'").fetchone() == (0,)
        assert not list(tmp_path.rglob('*-error.json'))
    finally:
        store.close()


async def test_observation_exception_cannot_replace_original_http_error_or_duplicate_finish(fake_upstream, tmp_path, monkeypatch):
    raw = encoded({'code': 400, 'message': PRIVATE + KEY})
    async def provider(request):
        return web.Response(body=raw, status=429, content_type='application/json', headers={'Retry-After': '3'})
    fake_upstream.append(provider)
    def broken(*args):
        raise RuntimeError(PRIVATE + KEY)
    monkeypatch.setattr(module, '_http_error_diagnostic', broken, raising=False)
    store = Store(tmp_path/'state.sqlite'); finishes = []
    original = store.finish
    def observed(*args, **kwargs):
        finishes.append((args[1], args[2])); return original(*args, **kwargs)
    monkeypatch.setattr(store, 'finish', observed)
    try:
        async with Gateway(KEY, 'LOCAL', store, {}, trace_source=lambda: None) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL}, headers={'Authorization': 'Bearer LOCAL'}) as response:
                assert response.status == 429 and response.headers['Retry-After'] == '3'
                assert await response.read() == raw.replace(KEY.encode(), b'[REDACTED]')
        assert finishes == [('upstream_error', 429)]
        assert json.loads(store.db.execute('SELECT detail FROM receipts').fetchone()[0]) == {
            'schema': 1, 'event_type': 'upstream.http_error', 'body_status': 'unavailable'}
    finally:
        store.close()


async def test_http_success_never_uses_error_observation(fake_upstream, tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError('Error observation must not inspect a successful response')
    monkeypatch.setattr(module, '_http_error_diagnostic', forbidden, raising=False)
    async def provider(request):
        return web.json_response({'id': 'r', 'status': 'completed', 'output': []})
    fake_upstream.append(provider)
    store = Store(tmp_path/'state.sqlite')
    try:
        async with Gateway(KEY, 'LOCAL', store, {}, trace_source=lambda: None) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL}, headers={'Authorization': 'Bearer LOCAL'}) as response:
                assert response.status == 200 and (await response.json())['status'] == 'completed'
        assert store.db.execute('SELECT status,detail FROM receipts').fetchone() == ('completed', None)
    finally:
        store.close()


async def test_even_allowlisted_label_cannot_persist_a_known_credential(fake_upstream, tmp_path):
    credential = 'invalid_request_error'
    raw = encoded({'code': credential, 'type': credential, 'message': PRIVATE})
    async def provider(request):
        return web.Response(body=raw, status=400, content_type='application/json')
    fake_upstream.append(provider)
    store = Store(tmp_path/'state.sqlite')
    try:
        async with Gateway(credential, 'LOCAL', store, {}, trace_source=lambda: None) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL}, headers={'Authorization': 'Bearer LOCAL'}) as response:
                assert response.status == 400
                assert await response.read() == raw.replace(credential.encode(), b'[REDACTED]')
        detail = store.db.execute('SELECT detail FROM receipts').fetchone()[0]
        assert credential not in detail
        assert json.loads(detail) == {'schema': 1, 'event_type': 'upstream.http_error', 'body_status': 'json'}
    finally:
        store.close()


async def test_existing_explicit_raw_trace_policy_is_unchanged(fake_upstream, tmp_path):
    raw = encoded({'code': 400, 'message': PRIVATE + KEY})
    async def provider(request):
        return web.Response(body=raw, status=400, content_type='application/json')
    fake_upstream.append(provider)
    trace = tmp_path/'explicit-trace'
    store = Store(tmp_path/'state.sqlite')
    try:
        async with Gateway(KEY, 'LOCAL', store, {}, trace_source=lambda: trace) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL}, headers={'Authorization': 'Bearer LOCAL'}) as response:
                assert response.status == 400 and await response.read() == raw.replace(KEY.encode(), b'[REDACTED]')
        files = list(trace.glob('*-error.json'))
        assert len(files) == 1 and files[0].read_bytes() == raw.replace(KEY.encode(), b'[REDACTED]')
        detail = store.db.execute('SELECT detail FROM receipts').fetchone()[0]
        assert PRIVATE not in detail and KEY not in detail
    finally:
        store.close()


@pytest.mark.parametrize('observation_fails', [False, True])
async def test_http_rejection_keeps_image_lease_consumption_semantics(fake_upstream, tmp_path, monkeypatch, observation_fails):
    import os
    from types import SimpleNamespace
    from codex_deepseek.image_transport import ImageTransport, TransportPolicy
    from test_image_transport import FakeFiles, part
    raw = encoded({'code': 400, 'type': 'invalid_request_error', 'message': PRIVATE})
    calls = []
    async def provider(request):
        calls.append(await request.json())
        return web.Response(body=raw, status=400, content_type='application/json')
    fake_upstream.append(provider)
    if observation_fails:
        def broken(*args): raise RuntimeError(PRIVATE)
        monkeypatch.setattr(module, '_http_error_diagnostic', broken)
    store = Store(tmp_path/'adapter.sqlite'); backend = FakeFiles()
    transport = ImageTransport(tmp_path/'images', lambda: KEY, backend, TransportPolicy(1))
    records = []
    image_stub = SimpleNamespace(transport=lambda _: transport, record=lambda ref, **kw: records.append(kw))
    try:
        async with Gateway(KEY, 'LOCAL', store, {}, trace_source=lambda: None,
                native_http_binary=os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY')) as gateway, aiohttp.ClientSession() as client:
            gateway.images = image_stub
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL,
                    'input': [{'type': 'message', 'role': 'user', 'content': [part()]}]}, headers={'Authorization': 'Bearer LOCAL'}) as response:
                assert response.status == 400 and await response.read() == raw
        assert len(calls) == 1
        assert sum(record.get('consumption_status') == 'http_rejected' for record in records) == 1
        diagnostics = await transport.diagnostics()
        assert diagnostics['active_transport_pins'] == 0 and not diagnostics['unknown_upstream_calls']
        assert (await transport.cleanup(all_unpinned=True))['all_owned_files_deleted']
        row = store.db.execute('SELECT status,upstream_status,detail FROM receipts').fetchone()
        assert row[:2] == ('upstream_error', 400) and PRIVATE not in row[2]
    finally:
        store.close()
