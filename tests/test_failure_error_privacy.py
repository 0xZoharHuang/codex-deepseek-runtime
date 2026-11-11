"""Failure-only shared privacy; real localhost, never a paid model."""
import base64
import json
from types import SimpleNamespace

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import MODEL, encode_sse
from test_gateway import fake_upstream
from test_http_error_privacy import KEY, AUTH, contains_key


@pytest.mark.parametrize('mode', ['sse_failed', 'nonstream_failed', 'flat_error'])
@pytest.mark.parametrize('refused', [False, True])
async def test_failed_event_retains_schema_code_and_never_encoded_credential(fake_upstream, tmp_path, mode, refused):
    encoded = base64.b64encode(('prefix:' + KEY + ':suffix').encode()).decode()
    error = {'code': 'context_length_exceeded', 'message': 'Useful original diagnostic',
             'metadata': {'raw': json.dumps({'encoded': encoded})}}
    if refused:
        error['metadata']['uninspectable'] = 'private-detail-' * 9000
    failed = {'id': 'r-failure', 'status': 'failed', 'output': [], 'error': error,
              'usage': {'input_tokens': 7, 'output_tokens': 3, 'total_tokens': 10}}
    calls = []
    async def provider(request):
        calls.append(await request.json())
        if mode == 'nonstream_failed':
            return web.json_response(failed)
        event = ({'type': 'error', 'code': error['code'], 'message': encoded, 'param': None}
                 if mode == 'flat_error' else {'type': 'response.failed', 'response': failed})
        return web.Response(body=encode_sse(event), content_type='text/event-stream')
    fake_upstream.append(provider)
    store = Store(tmp_path/'state.sqlite')
    try:
        async with Gateway(KEY, 'LOCAL', store, {}, trace_source=lambda: None) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL, 'stream': mode != 'nonstream_failed'}, headers=AUTH) as response:
                assert response.status == 200
                if mode == 'nonstream_failed':
                    result = await response.json()
                else:
                    frames = [json.loads(line[6:]) for line in (await response.text()).splitlines() if line.startswith('data: ')]
                    assert frames[-1]['type'] == 'response.failed'
                    result = frames[-1]['response']
                assert result['status'] == 'failed' and isinstance(result['output'], list)
                assert result['error']['code'] == 'context_length_exceeded'
                assert not contains_key(result)
                if mode != 'flat_error':
                    assert result['id'] == 'r-failure'
                    if refused:
                        assert 'withheld' in result['error']['message']
                    else:
                        assert result['error']['message'] == 'Useful original diagnostic'
        row = store.db.execute('SELECT status,detail FROM receipts').fetchone()
        assert row[0] == 'failed' and not contains_key(json.loads(row[1]))
        assert len(calls) == 1
    finally:
        store.close()


async def test_exception_after_credential_capture_is_safe_in_502_and_receipt(fake_upstream, tmp_path):
    encoded = base64.b64encode(('prefix:' + KEY + ':suffix').encode()).decode()
    async def forbidden(request):
        raise AssertionError('Image factory failed before an upstream request')
    fake_upstream.append(forbidden)
    def broken(policy):
        raise RuntimeError(json.dumps({'factory_error': encoded, 'safe_context': 'keep'}))
    store = Store(tmp_path/'state.sqlite')
    try:
        async with Gateway(lambda: KEY, 'LOCAL', store, {}, trace_source=lambda: None) as gateway, aiohttp.ClientSession() as client:
            gateway.images = SimpleNamespace(transport=broken, record=lambda *args, **kwargs: None)
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL}, headers=AUTH) as response:
                assert response.status == 502
                returned = await response.json()
                assert not contains_key(returned)
        status, detail = store.db.execute('SELECT status,detail FROM receipts').fetchone()
        assert status == 'adapter_error' and not contains_key(detail)
        assert 'keep' in detail
    finally:
        store.close()


async def test_success_model_content_is_not_semantically_filtered(fake_upstream, tmp_path):
    text = KEY + ' ' + base64.b64encode(KEY.encode()).decode()
    message = {'id': 'm', 'type': 'message', 'role': 'assistant', 'status': 'completed',
               'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
    async def provider(request):
        return web.json_response({'id': 'success', 'status': 'completed', 'output': [message]})
    fake_upstream.append(provider)
    store = Store(tmp_path/'state.sqlite')
    try:
        async with Gateway(KEY, 'LOCAL', store, {}, trace_source=lambda: None) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses', json={'model': MODEL}, headers=AUTH) as response:
                assert response.status == 200
                assert (await response.json())['output'][0]['content'][0]['text'] == text
    finally:
        store.close()
