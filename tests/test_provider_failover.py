"""Opt-in ordinary-route failover: only before output, only for eligible routes."""
import json

import aiohttp
import pytest
from aiohttp import web

from codex_deepseek import cli, gateway as module, provider_config
from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import MODEL

NAMES = {'deepseek': 'DeepSeek', 'morph/fp8': 'Morph'}


def completed(provider):
    return {'id': 'r-' + provider.replace('/', '-'), 'object': 'response', 'status': 'completed', 'model': MODEL,
            'provider': NAMES[provider],
            'output': [{'type': 'message', 'id': 'm1', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': 'CONNECTED'}]}]}


class Upstream:
    """Fake OpenRouter: a per-provider HTTP status, else a completed response."""

    def __init__(self, failing):
        self.failing = dict(failing)
        self.calls = []

    async def handle(self, request):
        body = await request.json()
        provider = body['provider']['only'][0]
        self.calls.append(provider)
        if provider in self.failing:
            return web.json_response({'error': {'message': 'Provider returned error'}}, status=self.failing[provider])
        result = completed(provider)
        if not body.get('stream'):
            return web.json_response(result)
        created = {**result, 'status': 'in_progress', 'output': []}
        text = ''.join('event: %s\ndata: %s\n\n' % (event['type'], json.dumps(event)) for event in (
            {'type': 'response.created', 'response': created},
            {'type': 'response.completed', 'response': result}))
        return web.Response(text=text, content_type='text/event-stream')


@pytest.fixture
async def upstream(monkeypatch):
    servers = []

    async def start(failing):
        fake = Upstream(failing)
        app = web.Application()
        app.router.add_post('/responses', fake.handle)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 0).start()
        servers.append(runner)
        monkeypatch.setattr(module, 'UPSTREAM', f'http://127.0.0.1:{runner.addresses[0][1]}/responses')
        return fake

    yield start
    for runner in servers:
        await runner.cleanup()


async def post(g, body):
    async with aiohttp.ClientSession() as client:
        async with client.post(f'http://127.0.0.1:{g.port}/v1/responses', json={'model': MODEL, **body},
                               headers={'Authorization': 'Bearer LOCAL'}) as r:
            return r.status, await r.text()


def receipts(store):
    return store.db.execute('select provider,status,upstream_status,route_reason from receipts order by started').fetchall()


# --- policy ---------------------------------------------------------------

def test_fallback_is_optional_and_absent_policy_digest_is_unchanged():
    base = {'schema': 1, 'ordinary_default_provider': 'deepseek'}
    assert provider_config.digest(base) == provider_config.digest(provider_config.default())
    with_fallback = {**base, 'ordinary_fallback_provider': 'morph/fp8'}
    assert provider_config.validate(with_fallback) == with_fallback
    assert provider_config.digest(with_fallback) != provider_config.digest(base)


@pytest.mark.parametrize('value', ['deepseek', 'primeintellect', '', None, 1])
def test_fallback_must_be_a_different_ordinary_provider(value):
    with pytest.raises(ValueError):
        provider_config.validate({'schema': 1, 'ordinary_default_provider': 'deepseek', 'ordinary_fallback_provider': value})


def test_set_fallback_cas_and_default_removes_field(tmp_path):
    home = tmp_path / 'home'
    first = provider_config.set_fallback_provider(home, 'morph/fp8', expected_revision='absent')
    assert first['configured_provider'] == 'deepseek' and first['configured_fallback_provider'] == 'morph/fp8'
    with pytest.raises(ValueError, match='CAS'):
        provider_config.set_fallback_provider(home, 'default', expected_revision='absent')
    # The ordinary default cannot become its own fallback.
    with pytest.raises(ValueError, match='differ'):
        provider_config.set_provider(home, 'morph/fp8', expected_revision=first['configured_revision'])
    cleared = provider_config.set_fallback_provider(home, 'default', expected_revision=first['configured_revision'])
    assert cleared['configured_fallback_provider'] is None
    assert provider_config.load(home)['policy'] == provider_config.default()
    with pytest.raises(ValueError):
        provider_config.set_fallback_provider(home, 'primeintellect')


def test_command_accepts_set_fallback(tmp_path, capsys):
    assert provider_config.command(tmp_path / 'home', ['set-fallback', 'morph/fp8', '--if-match', 'absent']) == 0
    assert json.loads(capsys.readouterr().out)['configured_fallback_provider'] == 'morph/fp8'


# --- gateway --------------------------------------------------------------

@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('status', [502, 503, 429])
async def test_retryable_default_failure_fails_over_before_output(tmp_path, upstream, stream, status):
    fake = await upstream({'deepseek': status})
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        async with Gateway('FAKE', 'LOCAL', store, {}, ordinary_fallback_provider='morph/fp8') as g:
            code, text = await post(g, {'stream': stream})
        assert code == 200 and 'CONNECTED' in text
        assert fake.calls == ['deepseek', 'morph/fp8']
        assert receipts(store) == [
            ('deepseek', 'upstream_error', status, 'official_default'),
            ('morph/fp8', 'completed', 200, 'failover/deepseek/operator_default/morph/fp8')]
    finally:
        store.close()


async def test_cooldown_prefers_fallback_then_still_tries_default(tmp_path, upstream):
    fake = await upstream({'deepseek': 502})
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        async with Gateway('FAKE', 'LOCAL', store, {}, ordinary_fallback_provider='morph/fp8') as g:
            assert (await post(g, {}))[0] == 200
            assert (await post(g, {}))[0] == 200
            assert fake.calls == ['deepseek', 'morph/fp8', 'morph/fp8']
            assert receipts(store)[-1][3] == 'failover_cooldown/deepseek/operator_default/morph/fp8'
            # During cooldown a failing fallback still falls back to the default.
            fake.failing = {'morph/fp8': 503}
            code, _ = await post(g, {})
            assert code == 200 and fake.calls[-2:] == ['morph/fp8', 'deepseek']
    finally:
        store.close()


async def test_cooldown_expires(tmp_path, upstream, monkeypatch):
    monkeypatch.setenv('CODEX_DEEPSEEK_FAILOVER_COOLDOWN_SECONDS', '0')
    fake = await upstream({'deepseek': 502})
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        async with Gateway('FAKE', 'LOCAL', store, {}, ordinary_fallback_provider='morph/fp8') as g:
            await post(g, {})
            await post(g, {})
        assert fake.calls == ['deepseek', 'morph/fp8', 'deepseek', 'morph/fp8']
    finally:
        store.close()


async def test_both_failing_returns_last_failure(tmp_path, upstream):
    fake = await upstream({'deepseek': 502, 'morph/fp8': 503})
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        async with Gateway('FAKE', 'LOCAL', store, {}, ordinary_fallback_provider='morph/fp8') as g:
            code, _ = await post(g, {})
        assert code == 503 and fake.calls == ['deepseek', 'morph/fp8']
        assert [row[:3] for row in receipts(store)] == [('deepseek', 'upstream_error', 502), ('morph/fp8', 'upstream_error', 503)]
    finally:
        store.close()


@pytest.mark.parametrize('status', [400, 401, 402, 413])
async def test_request_errors_never_fail_over(tmp_path, upstream, status):
    fake = await upstream({'deepseek': status})
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        async with Gateway('FAKE', 'LOCAL', store, {}, ordinary_fallback_provider='morph/fp8') as g:
            assert (await post(g, {}))[0] == status
        assert fake.calls == ['deepseek']
    finally:
        store.close()


async def test_without_fallback_policy_nothing_changes(tmp_path, upstream):
    fake = await upstream({'deepseek': 502})
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        async with Gateway('FAKE', 'LOCAL', store, {}) as g:
            assert (await post(g, {}))[0] == 502
            async with aiohttp.ClientSession() as client:
                async with client.get(f'http://127.0.0.1:{g.port}/health', headers={'Authorization': 'Bearer LOCAL'}) as r:
                    health = await r.json()
        assert fake.calls == ['deepseek'] and health['fallbacks'] is False and health['ordinary_fallback_provider'] is None
    finally:
        store.close()


async def test_morph_default_can_fail_over_to_official(tmp_path, upstream):
    fake = await upstream({'morph/fp8': 502})
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        async with Gateway('FAKE', 'LOCAL', store, {}, ordinary_default_provider='morph/fp8',
                           ordinary_fallback_provider='deepseek') as g:
            assert (await post(g, {}))[0] == 200
        assert fake.calls == ['morph/fp8', 'deepseek']
        assert receipts(store)[-1][3] == 'failover/morph/fp8/official_default'
    finally:
        store.close()


async def test_strict_schema_route_keeps_its_pin(tmp_path, upstream):
    # Strict JSON schema without tools routes to the structured provider, never the ordinary one.
    fake = await upstream({'morph/fp8': 502})
    store = Store(tmp_path / 'adapter.sqlite')
    schema = {'type': 'json_schema', 'name': 'x', 'strict': True,
              'schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
    try:
        async with Gateway('FAKE', 'LOCAL', store, {}, ordinary_default_provider='morph/fp8',
                           ordinary_fallback_provider='deepseek') as g:
            assert (await post(g, {'text': {'format': schema}}))[0] == 502
        assert fake.calls == ['morph/fp8']
        assert receipts(store)[0][3] == 'strict_json_schema'
    finally:
        store.close()


async def test_opaque_history_is_ineligible(tmp_path):
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        g = Gateway('FAKE', 'LOCAL', store, {}, ordinary_fallback_provider='morph/fp8')
        body = {'model': MODEL, 'input': [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'hi'}]}]}
        payload, registry = g._prepare_route(body, {}, 'deepseek')
        assert g._failover_route(body, {}, payload, registry) is not None
        payload['input'].append({'type': 'reasoning', 'id': 'rs1', 'summary': [], 'encrypted_content': 'opaque'})
        assert g._failover_route(body, {}, payload, registry) is None
    finally:
        store.close()


def test_doctor_accepts_only_the_configured_fallback():
    generation = {'id': 'response1', 'model': 'deepseek/deepseek-v4.1-flash-20261017', 'provider_name': 'Morph'}
    assert cli._verified_generation(generation, 'response1', 'morph/fp8', 'deepseek') is False
    assert cli._verified_generation(generation, 'response1', 'morph/fp8', 'deepseek', 'morph/fp8') is True
    official = {**generation, 'provider_name': 'DeepSeek'}
    # A claimed fallback selection must still match the billed provider.
    assert cli._verified_generation(official, 'response1', 'morph/fp8', 'deepseek', 'morph/fp8') is False
    assert cli._verified_generation(official, 'response1', 'deepseek', 'deepseek', 'morph/fp8') is True
