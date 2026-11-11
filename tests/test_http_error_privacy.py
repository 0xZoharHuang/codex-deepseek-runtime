"""Synthetic HTTP-error privacy contracts; localhost only, no real credential/model."""
import asyncio
import base64
import html
import json
import os
from types import SimpleNamespace
from urllib.parse import unquote

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek.gateway import Gateway
from codex_deepseek.protocol import MODEL
from codex_deepseek.state import Store
from test_gateway import fake_upstream

KEY = 'sk-or-v1-SYNTHETIC-NOT-A-REAL-KEY'
AUTH = {'Authorization': 'Bearer LOCAL'}
ESCAPED_KEY = 'sk-or-v1-\\u0053YNTHETIC-NOT-A-REAL-KEY'
PERCENT_KEY = ''.join('%%%02X' % byte for byte in KEY.encode())
BASE64_KEY = base64.b64encode(KEY.encode()).decode()


def no_duplicate_object(pairs):
    out = {}
    for name, value in pairs:
        assert name not in out, 'Redaction must not produce duplicate JSON object keys'
        out[name] = value
    return out


def contains_key(value, depth=0):
    if depth > 6:
        raise AssertionError('Unexpected unbounded nested response in test')
    if isinstance(value, dict):
        return any(contains_key(name, depth + 1) or contains_key(part, depth + 1)
                   for name, part in value.items())
    if isinstance(value, list):
        return any(contains_key(part, depth + 1) for part in value)
    if not isinstance(value, str):
        return False
    if KEY in value or KEY in unquote(value) or KEY in html.unescape(value):
        return True
    try:
        if KEY in value.encode('ascii').decode('unicode_escape'):
            return True
    except (UnicodeError, ValueError):
        pass
    stripped = value.strip()
    if stripped.startswith(('{', '[')):
        try:
            nested = json.loads(stripped, object_pairs_hook=no_duplicate_object)
        except (ValueError, TypeError):
            nested = None
        if nested is not None and contains_key(nested, depth + 1):
            return True
    if 16 <= len(stripped) <= 4096:
        try:
            decoded = base64.b64decode(stripped, validate=True).decode('utf-8')
        except (ValueError, UnicodeError):
            decoded = ''
        if KEY in decoded:
            return True
    return False


def assert_safe_error(raw, status):
    assert raw and len(raw) < 1024 * 1024, 'Fallback must be bounded and nonempty'
    body = json.loads(raw, object_pairs_hook=no_duplicate_object)
    error = body.get('error')
    assert isinstance(error, dict), 'Native must still receive a structured error'
    assert isinstance(error.get('message'), str) and error['message'].strip()
    assert isinstance(error.get('code'), (str, int)) and not isinstance(error.get('code'), bool)
    assert str(error['code']).strip(), 'Failure must remain machine-identifiable'
    assert not contains_key(body), 'Decoded output retains synthetic credential'
    return body


async def exchange(fake_upstream, tmp_path, raw, *, status=400, content_type='application/json',
                   response_headers=None, explicit_trace=False, image=False, image_receipts=None):
    calls = []
    async def provider(request):
        calls.append(await request.json())
        return web.Response(body=raw, status=status, content_type=content_type,
                            headers=response_headers or {})
    fake_upstream.append(provider)
    store = Store(tmp_path / 'receipts.sqlite')
    trace = tmp_path / 'private-trace'
    transport = None
    if image:
        from codex_deepseek.image_transport import ImageTransport, TransportPolicy
        from test_image_transport import FakeFiles, part
        backend = FakeFiles()
        transport = ImageTransport(tmp_path / 'images', lambda: KEY, backend, TransportPolicy(1))
        from codex_deepseek.image_provider import ImageProvider
        image_provider = ImageProvider(tmp_path / 'image-provider', None, lambda: KEY, {})
        image_stub = SimpleNamespace(transport=lambda _: transport, record=image_provider.record)
        payload = {'model': MODEL, 'input': [{'type': 'message', 'role': 'user', 'content': [part()]}]}
    else:
        payload = {'model': MODEL}
    try:
        async with Gateway(KEY, 'LOCAL', store, {}, trace_source=(lambda: trace) if explicit_trace else (lambda: None),
                           native_http_binary=os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY')) as gateway, aiohttp.ClientSession(trust_env=False) as client:
            if image:
                gateway.images = image_stub
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json=payload, headers=AUTH) as response:
                answer = await response.read()
                observed_status = response.status
                headers = {name.lower(): value for name, value in response.headers.items()}
        assert observed_status == status, 'Do not turn the original HTTP error into success or a different status'
        assert len(calls) == 1, 'Diagnostic/redaction must not cause another upstream inference'
        row = store.db.execute('SELECT status, upstream_status, detail FROM receipts').fetchone()
        assert row[:2] == ('upstream_error', status)
        assert KEY not in (row[2] or ''), 'Receipt must never retain the upstream credential'
        if explicit_trace:
            paths = list(trace.glob('*-error.json'))
            assert len(paths) == 1
            traced = paths[0].read_bytes()
        else:
            traced = None
        if transport is not None:
            diagnostics = await transport.diagnostics()
            assert diagnostics['active_transport_pins'] == 0
            assert not diagnostics['unknown_upstream_calls']
            assert (await transport.cleanup(all_unpinned=True))['all_owned_files_deleted']
            if image_receipts is not None:
                import sqlite3
                with sqlite3.connect(image_provider.receipts) as db:
                    image_receipts.extend(json.loads(row[0]) for row in db.execute('SELECT record FROM calls'))
        return answer, headers, traced
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 429, 503])
async def test_no_secret_valid_json_preserves_error_bytes_status_and_retry(fake_upstream, tmp_path, status):
    raw = b'{ "error": {"code":400, "type":"invalid_request_error", "message":"Useful diagnostic"}, "extra": 7 }'
    body, headers, _ = await exchange(fake_upstream, tmp_path, raw, status=status,
        response_headers={'Retry-After': '7', 'X-Request-Id': 'safe-request-id'})
    assert body == raw
    assert headers['retry-after'] == '7'
    assert headers['x-request-id'] == 'safe-request-id'


@pytest.mark.asyncio
async def test_no_secret_valid_text_preserves_original_error(fake_upstream, tmp_path):
    raw = b'provider is temporarily unavailable'
    body, headers, _ = await exchange(fake_upstream, tmp_path, raw, status=503, content_type='text/plain',
                                     response_headers={'Retry-After': '5'})
    assert body == raw and headers['retry-after'] == '5'


@pytest.mark.asyncio
@pytest.mark.parametrize('encoding', ['literal', 'unicode_escape'])
async def test_json_message_redacts_key_without_losing_safe_error_context(fake_upstream, tmp_path, encoding):
    key_wire = KEY if encoding == 'literal' else ESCAPED_KEY
    raw = ('{"error":{"code":400,"type":"invalid_request_error","message":"Useful prefix '
           + key_wire + ' useful suffix"},"safe_context":"keep-this"}').encode()
    body, _, trace = await exchange(fake_upstream, tmp_path, raw, explicit_trace=True)
    result = assert_safe_error(body, 400)
    assert 'Useful prefix' in result['error']['message'] and 'useful suffix' in result['error']['message']
    assert result.get('safe_context') == 'keep-this'
    assert trace == body, 'Explicit error trace must use the same safe bytes as native delivery'


@pytest.mark.asyncio
@pytest.mark.parametrize('form', ['nested_json', 'encoded_base64'])
async def test_nested_metadata_cannot_hide_synthetic_key(fake_upstream, tmp_path, form):
    nested = ('{"message":"' + ESCAPED_KEY + '","hint":"inner"}' if form == 'nested_json' else BASE64_KEY)
    raw = json.dumps({'error': {'code': 400, 'type': 'invalid_request_error', 'message': 'Useful diagnostic',
                                'metadata': {'raw': nested}}}).encode()
    assert KEY not in raw.decode(), 'Fixture must avoid an easy literal match'
    body, _, trace = await exchange(fake_upstream, tmp_path, raw, explicit_trace=True)
    result = assert_safe_error(body, 400)
    assert 'Useful diagnostic' in result['error']['message']
    assert trace == body


@pytest.mark.asyncio
async def test_secret_in_json_key_cannot_create_duplicate_or_drop_safe_error(fake_upstream, tmp_path):
    raw = ('{"error":{"code":400,"type":"invalid_request_error","message":"Keep this diagnostic",'
           '"metadata":{"' + ESCAPED_KEY + '":"first", "[REDACTED]":"second"}}}').encode()
    body, _, trace = await exchange(fake_upstream, tmp_path, raw, explicit_trace=True)
    result = assert_safe_error(body, 400)
    assert result['error']['code'] == 400
    assert 'Keep this diagnostic' in result['error']['message']
    assert trace == body


@pytest.mark.asyncio
@pytest.mark.parametrize(('header_name', 'header_value'), [
    ('X-Request-Id', KEY), ('X-OpenRouter-Request-Id', PERCENT_KEY),
])
async def test_credential_in_literal_and_percent_encoded_headers_does_not_escape(
        fake_upstream, tmp_path, header_name, header_value):
    raw = b'{"error":{"code":429,"type":"rate_limit_error","message":"retry later"}}'
    body, headers, _ = await exchange(fake_upstream, tmp_path, raw, status=429,
        response_headers={'Retry-After': '3', header_name: header_value})
    assert headers['retry-after'] == '3'
    assert body == raw
    value = headers.get(header_name.lower(), '')
    assert KEY not in value and KEY not in unquote(value)


@pytest.mark.asyncio
@pytest.mark.parametrize('encoding', ['unicode_escape', 'html_entity'])
async def test_nonjson_obfuscated_credential_is_not_forwarded(fake_upstream, tmp_path, encoding):
    encoded = ESCAPED_KEY if encoding == 'unicode_escape' else KEY.replace('S', '&#83;', 1)
    raw = ('upstream refused: ' + encoded).encode()
    assert KEY.encode() not in raw
    body, headers, trace = await exchange(fake_upstream, tmp_path, raw, status=400, content_type='text/plain',
        response_headers={'Retry-After': '3'}, explicit_trace=True)
    assert headers['retry-after'] == '3'
    text = body.decode('utf-8')
    assert not contains_key(text)
    assert trace == body
    assert body, 'Do not turn the upstream error into an empty body'


@pytest.mark.asyncio
@pytest.mark.parametrize('form', ['malformed_json', 'oversize_json'])
async def test_uninspectable_error_uses_clear_bounded_failure(fake_upstream, tmp_path, form):
    raw = (b'{"error":{"message":"truncated \\u0073' if form == 'malformed_json' else
           b'{"error":{"code":429,"message":"' + b'x' * (2 * 1024 * 1024) + b'"}}')
    body, headers, trace = await exchange(fake_upstream, tmp_path, raw, status=429,
        response_headers={'Retry-After': '3'}, explicit_trace=True)
    assert headers['retry-after'] == '3'
    assert body != raw
    assert_safe_error(body, 429)
    assert trace == body


@pytest.mark.asyncio
async def test_known_http_400_image_rejection_still_settles_lease(fake_upstream, tmp_path):
    raw = ('{"error":{"code":400,"type":"invalid_request_error","message":"Useful '
           + ESCAPED_KEY + '"}}').encode()
    body, _, _ = await exchange(fake_upstream, tmp_path, raw, image=True)
    result = assert_safe_error(body, 400)
    assert result['error']['code'] == 400


def test_real_http_diagnostic_without_source_bound_retry_id_stays_unrecovered():
    from codex_deepseek.gateway import _http_error_diagnostic
    from test_complex_runtime import recovery_rows
    from verify_complex_runtime import provider_transport_evidence

    model, requests, receipts, evidence = recovery_rows()
    receipts[0] = ('failure', 'upstream_error', 429, 'deepseek', 'shared-cache', 'official_default')
    raw = b'{"error":{"code":429,"type":"rate_limit_error","message":"synthetic rate limit"}}'
    detail = json.dumps(_http_error_diagnostic(raw, 'application/json', 'synthetic-key'),
                        separators=(',', ':'))
    evidence['receipt_observations'][0]['detail'] = detail
    error = evidence['native_events'][0]['params']['error']
    error['additionalDetails'] = 'unexpected status 429: synthetic rate limit'
    error['codexErrorInfo'] = {'httpConnectionFailed': {'httpStatusCode': 429}}
    result = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
    assert result['passed'] is False
    assert result['original_error_receipt_ids'] == ['failure']
    assert result['recovered_error_receipt_ids'] == []
    assert result['unresolved_or_failed_receipt_ids'] == ['failure']


@pytest.mark.asyncio
@pytest.mark.parametrize('secret', [False, True])
async def test_image_generation_header_does_not_persist_encoded_credential(fake_upstream, tmp_path, secret):
    encoded = base64.urlsafe_b64encode(KEY.encode()).rstrip(b'=').decode()
    candidate = ('gen-' + encoded) if secret else 'gen-safe-123'
    receipts = []
    raw = b'{"error":{"code":400,"type":"invalid_request_error","message":"rejected"}}'
    body, _, _ = await exchange(fake_upstream, tmp_path, raw, status=400, image=True,
                                response_headers={'X-Generation-Id': candidate}, image_receipts=receipts)
    assert body == raw
    assert len(receipts) == 1
    image = receipts[0]['adaptations']['image_transport']
    assert image['consumption_status'] == 'http_rejected'
    saved = image.get('generation_header_candidate')
    if secret:
        assert saved is None, 'Encoded credential must not be persisted as a generation ID'
        assert encoded not in json.dumps(receipts), 'Encoded credential leaked into image receipts'
    else:
        assert saved == {'id': candidate, 'provenance': 'http_response_header:X-Generation-Id'}


@pytest.mark.asyncio
@pytest.mark.parametrize('with_secret', [False, True])
async def test_native_context_window_code_and_safe_reason_are_not_masked(fake_upstream, tmp_path, with_secret):
    message = 'Maximum context window exceeded' + (' ' + ESCAPED_KEY if with_secret else '')
    raw = ('{ "error": {"code":"context_length_exceeded","message":"' + message + '"} }').encode()
    body, headers, traced = await exchange(fake_upstream, tmp_path, raw, explicit_trace=True,
                                         response_headers={'Retry-After': '7'})
    result = json.loads(body)
    assert result['error']['code'] == 'context_length_exceeded'
    assert 'Maximum context window exceeded' in result['error']['message']
    assert {name.lower(): value for name, value in headers.items()}['retry-after'] == '7' and traced == body
    assert not contains_key(result)
    if not with_secret:
        assert body == raw


@pytest.mark.asyncio
async def test_success_headers_keep_normal_advice_but_never_echo_key(fake_upstream, tmp_path):
    async def provider(request):
        return web.json_response({'id': 'r-success', 'status': 'completed', 'output': []},
            headers={'Retry-After': '7', 'X-Request-Id': 'safe-id', 'X-OpenRouter-Request-Id': PERCENT_KEY,
                     'X-RateLimit-Limit': '100'})
    fake_upstream.append(provider)
    store = Store(tmp_path/'receipts.sqlite')
    try:
        async with Gateway(KEY, 'LOCAL', store, {}, trace_source=lambda: None,
                           native_http_binary=os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY')) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL}, headers=AUTH) as response:
                assert response.status == 200 and (await response.json())['status'] == 'completed'
                assert response.headers['Retry-After'] == '7' and response.headers['X-Request-Id'] == 'safe-id'
                assert response.headers['X-RateLimit-Limit'] == '100'
                assert 'X-OpenRouter-Request-Id' not in response.headers
    finally:
        store.close()


@pytest.mark.asyncio
async def test_cancel_during_http_error_body_does_not_forward_or_trace_partial_secret(fake_upstream, tmp_path, monkeypatch):
    from codex_deepseek import gateway as module
    started, stopped = asyncio.Event(), asyncio.Event()
    async def provider(request):
        response = web.StreamResponse(status=400, headers={'Content-Type': 'application/json'})
        await response.prepare(request)
        await response.write(b'{"error":{"message":"partial ' + ESCAPED_KEY.encode())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    fake_upstream.append(provider)
    inspected = []
    original = module._http_error_diagnostic
    def observed(*args):
        inspected.append(True); return original(*args)
    monkeypatch.setattr(module, '_http_error_diagnostic', observed)
    store = Store(tmp_path/'receipts.sqlite'); trace = tmp_path/'trace'
    try:
        async with Gateway(KEY, 'LOCAL', store, {}, trace_source=lambda: trace,
                           native_http_binary=os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY')) as gateway, aiohttp.ClientSession() as client:
            task = asyncio.create_task(client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL}, headers=AUTH))
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(stopped.wait(), 5)
            assert store.db.execute('SELECT status,detail FROM receipts').fetchone() == ('cancelled', None)
            assert not inspected and not list(trace.glob('*-error.json'))
    finally:
        store.close()
