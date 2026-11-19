import aiohttp
import pytest
from contextlib import asynccontextmanager

from codex_deepseek.gateway import Gateway
from codex_deepseek.protocol import MODEL
from codex_deepseek.state import Store
from test_image_provider import FakeNative, Response, config
from test_image_transport import payload


@pytest.mark.asyncio
@pytest.mark.parametrize('file_status', [413, 429])
async def test_file_rejection_preserves_capacity_domain(tmp_path, monkeypatch, file_status):
    class FailingFiles(FakeNative):
        @asynccontextmanager
        async def request_raw(self, method, url, **kwargs):
            if '/files' in url and method == 'POST':
                self.calls.append({'method': method, 'url': url})
                yield Response(file_status, {'message': 'SYNTHETIC-PRIVATE-ERROR'}, {'Retry-After': '17'})
            else:
                async with super().request_raw(method, url, **kwargs) as response:
                    yield response

    native = FailingFiles()
    monkeypatch.setattr('codex_deepseek.gateway.NativeTransport', lambda *a, **kw: native)
    config(tmp_path)
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        async with Gateway(lambda: 'SYNTHETIC-PRIVATE-KEY', 'LOCAL', store, {}) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',
                    json={'model': MODEL, 'input': payload()['input'], 'stream': False},
                    headers={'Authorization': 'Bearer LOCAL'}) as response:
                body = await response.json()
                if file_status == 413:
                    assert response.status == 400
                    assert body['error'] == {'type': 'invalid_request_error', 'code': 'file_operation_rejected',
                        'message': 'The Files API rejected a file operation (HTTP 413).', 'file_http_status': 413}
                    assert 'Retry-After' not in response.headers
                else:
                    assert response.status == 429
                    assert response.headers['Retry-After'] == '17'
                assert 'SYNTHETIC-PRIVATE-ERROR' not in str(body)
                assert 'SYNTHETIC-PRIVATE-KEY' not in str(body)
            assert len([c for c in native.calls if c.get('url', '').endswith('/files')]) == 1
            assert not any(c.get('url', '').endswith('/responses') for c in native.calls)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_real_responses_413_remains_native_capacity_signal(tmp_path, monkeypatch):
    native = FakeNative()
    native.model_status = 413
    native.model_body = {'error': {'message': 'synthetic context limit'}}
    # Match the content_type attribute exposed by a real aiohttp response.
    monkeypatch.setattr(Response, 'content_type', 'application/json', raising=False)
    monkeypatch.setattr('codex_deepseek.gateway.NativeTransport', lambda *a, **kw: native)
    config(tmp_path)
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        async with Gateway(lambda: 'SYNTHETIC-PRIVATE-KEY', 'LOCAL', store, {}) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',
                    json={'model': MODEL, 'input': payload()['input'], 'stream': False},
                    headers={'Authorization': 'Bearer LOCAL'}) as response:
                assert response.status == 413
                assert 'synthetic context limit' in await response.text()
            assert sum(c.get('url', '').endswith('/responses') for c in native.calls) == 1
    finally:
        store.close()
