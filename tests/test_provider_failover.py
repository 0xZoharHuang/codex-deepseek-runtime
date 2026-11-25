"""Opt-in failover: confirmed outages move only new threads; existing threads never change by themselves."""
from contextlib import asynccontextmanager
import json
import socket

import aiohttp
import pytest
from aiohttp import web

from codex_deepseek import cli, gateway as module, provider_config, thread_routes
from codex_deepseek.gateway import Gateway, ProviderBreaker, Store
from codex_deepseek.protocol import MODEL

NAMES = {'deepseek': 'DeepSeek', 'morph/fp8': 'Morph'}
USER = {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'hi'}]}
ASSISTANT = {'type': 'message', 'role': 'assistant', 'id': 'm0', 'content': [{'type': 'output_text', 'text': 'earlier'}]}
OPAQUE = {'type': 'reasoning', 'id': 'rs0', 'summary': [], 'encrypted_content': 'official-only'}


def completed(provider):
    return {'id': 'r-' + provider.replace('/', '-'), 'object': 'response', 'status': 'completed', 'model': MODEL,
            'provider': NAMES[provider],
            'output': [{'type': 'message', 'id': 'm1', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': 'CONNECTED'}]}]}


class Upstream:
    """Fake OpenRouter: a per-provider HTTP failure, else a completed response."""

    def __init__(self, failing):
        self.failing = dict(failing)
        self.calls = []
        self.bodies = []

    async def handle(self, request):
        body = await request.json()
        provider = body['provider']['only'][0]
        self.calls.append(provider)
        self.bodies.append(body)
        if provider in self.failing:
            status, kind = self.failing[provider] if isinstance(self.failing[provider], tuple) else (self.failing[provider], 'json')
            if kind == 'html':
                return web.Response(text='<html>bad gateway</html>', status=status, content_type='text/html')
            return web.json_response({'error': {'message': 'Provider returned error', 'code': status}}, status=status)
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


class Confirm:
    def __init__(self, answer=True):
        self.answer = answer
        self.calls = 0

    async def __call__(self, provider, policy='default'):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def gateway(store, confirm=None, **options):
    g = Gateway('FAKE', 'LOCAL', store, {}, **options)
    if confirm is not None:
        g.breaker.confirm = confirm
    return g


async def post(g, thread='thread-new', inputs=None, **body):
    payload = {'model': MODEL, **body}
    if thread:
        payload['prompt_cache_key'] = thread
    if inputs is not None:
        payload['input'] = inputs
    async with aiohttp.ClientSession() as client:
        async with client.post(f'http://127.0.0.1:{g.port}/v1/responses', json=payload,
                               headers={'Authorization': 'Bearer LOCAL'}) as r:
            return r.status, await r.text()


def completed_thread(store, thread, provider='deepseek'):
    receipt = store.start(provider, context_key=thread)
    store.finish(receipt, 'completed', 200)


def receipts(store, thread=None):
    query = 'select provider,status,upstream_status,route_reason from receipts'
    if thread:
        return store.db.execute(query + ' where context_key=? order by started', (thread,)).fetchall()
    return store.db.execute(query + ' order by started').fetchall()


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'adapter.sqlite')
    yield value
    value.close()


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


# --- breaker --------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def test_breaker_needs_repeated_failures_and_confirmation():
    clock, confirm = Clock(), Confirm(True)
    breaker = ProviderBreaker('deepseek', confirm, clock=clock)
    assert not await breaker.failure() and not await breaker.failure()
    assert confirm.calls == 0 and not breaker.tripped()
    assert await breaker.failure() and breaker.tripped() and confirm.calls == 1
    clock.now += 301
    assert not breaker.tripped()


async def test_breaker_window_success_and_unconfirmed_outages():
    clock = Clock()
    breaker = ProviderBreaker('deepseek', Confirm(True), clock=clock)
    await breaker.failure(); await breaker.failure()
    clock.now += 121  # older failures leave the window
    assert not await breaker.failure() and not breaker.tripped()
    await breaker.failure(); breaker.success()
    assert breaker.snapshot()['recent_failures'] == 0
    for answer in (False, RuntimeError('status unavailable')):
        other = ProviderBreaker('deepseek', Confirm(answer), clock=clock)
        for _ in range(3):
            await other.failure()
        assert not other.tripped()


async def test_breaker_rechecks_status_at_most_every_30_seconds():
    clock, confirm = Clock(), Confirm(False)
    breaker = ProviderBreaker('deepseek', confirm, clock=clock)
    for _ in range(6):
        await breaker.failure()
    assert confirm.calls == 1
    clock.now += 31
    await breaker.failure()
    assert confirm.calls == 2


@pytest.mark.parametrize('status,body,expected', [
    (200, {'data': {'endpoints': [{'tag': 'deepseek', 'status': -5, 'uptime_last_5m': 0}]}}, True),
    (200, {'data': {'endpoints': [{'tag': 'deepseek', 'status': 0, 'uptime_last_5m': 20}]}}, True),
    (200, {'data': {'endpoints': [{'tag': 'deepseek', 'status': 0, 'uptime_last_5m': 100}]}}, False),
    (200, {'data': {'endpoints': [{'tag': 'morph/fp8', 'status': -5, 'uptime_last_5m': 0}]}}, False),
    (200, {'data': {}}, False),
    (503, {}, False),
])
async def test_outage_confirmation_reads_public_endpoint_status(store, status, body, expected):
    g = Gateway('FAKE', 'LOCAL', store, {}, ordinary_fallback_provider='morph/fp8')
    seen = []

    class Response:
        def __init__(self):
            self.status = status

        async def json(self):
            return body

    class Client:
        @asynccontextmanager
        async def request(self, method, url, **options):
            seen.append((method, url))
            yield Response()

    g.client = Client()
    assert await g._confirm_outage('deepseek') is expected
    assert seen == [('GET', 'https://openrouter.ai/api/v1/models/' + MODEL + '/endpoints')]


# --- routing --------------------------------------------------------------

async def test_without_fallback_policy_nothing_changes(store, upstream):
    fake, confirm = await upstream({'deepseek': 502}), Confirm()
    async with gateway(store, confirm) as g:
        for _ in range(4):
            assert (await post(g))[0] == 502
        async with aiohttp.ClientSession() as client:
            async with client.get(f'http://127.0.0.1:{g.port}/health', headers={'Authorization': 'Bearer LOCAL'}) as r:
                health = await r.json()
    assert fake.calls == ['deepseek'] * 4 and confirm.calls == 0
    assert health['fallbacks'] is False and health['breaker'] is None


@pytest.mark.parametrize('stream', [False, True])
async def test_confirmed_outage_moves_only_new_threads_and_pins_them(store, upstream, stream):
    fake, confirm = await upstream({'deepseek': 502}), Confirm(True)
    completed_thread(store, 'thread-old')
    async with gateway(store, confirm, ordinary_fallback_provider='morph/fp8') as g:
        # Native retries of one turn: each request fails as is, never retried elsewhere.
        for _ in range(3):
            assert (await post(g, 'thread-new', stream=stream))[0] == 502
        assert fake.calls == ['deepseek'] * 3 and g.breaker.tripped()
        # The same still-new thread now starts on the fallback, and is pinned there.
        code, text = await post(g, 'thread-new', stream=stream)
        assert code == 200 and 'CONNECTED' in text
        # An existing thread waits on its own provider during the outage.
        assert (await post(g, 'thread-old', [USER, ASSISTANT, USER], stream=stream))[0] == 502
        # Recovery does not move the pinned thread back.
        fake.failing = {}
        g.breaker.open_until = 0
        assert (await post(g, 'thread-new', [USER, ASSISTANT, USER], stream=stream))[0] == 200
    assert fake.calls[3:] == ['morph/fp8', 'deepseek', 'morph/fp8']
    assert [row[3] for row in receipts(store, 'thread-new')][3:] == [
        'failover_new_thread/deepseek/operator_default/morph/fp8', 'thread_pin/failover/operator_default/morph/fp8']
    assert thread_routes.get(store.db, 'thread-new') == {'provider': 'morph/fp8', 'origin': 'failover'}
    assert thread_routes.get(store.db, 'thread-old') is None


async def test_new_thread_with_existing_model_history_is_not_new(store, upstream):
    fake = await upstream({'deepseek': 502})
    async with gateway(store, Confirm(True), ordinary_fallback_provider='morph/fp8') as g:
        g.breaker.open_until = float('inf')
        assert (await post(g, 'resumed', [USER, ASSISTANT, USER]))[0] == 502
        assert (await post(g, None))[0] == 502  # no thread identity: never moved
    assert fake.calls == ['deepseek', 'deepseek']


@pytest.mark.parametrize('failure', [400, 401, 402, 408, 413, 429, (502, 'html')])
async def test_request_errors_and_non_provider_bodies_never_count(store, upstream, failure):
    fake, confirm = await upstream({'deepseek': failure}), Confirm(True)
    async with gateway(store, confirm, ordinary_fallback_provider='morph/fp8') as g:
        for _ in range(4):
            await post(g)
        assert confirm.calls == 0 and not g.breaker.tripped()


async def test_network_errors_never_count(store, monkeypatch):
    with socket.socket() as closed:
        closed.bind(('127.0.0.1', 0))
        port = closed.getsockname()[1]
    monkeypatch.setattr(module, 'UPSTREAM', f'http://127.0.0.1:{port}/responses')
    confirm = Confirm(True)
    async with gateway(store, confirm, ordinary_fallback_provider='morph/fp8') as g:
        for _ in range(4):
            assert (await post(g))[0] >= 400
        assert confirm.calls == 0 and not g.breaker.tripped()


async def test_strict_schema_route_keeps_its_own_pin(store, upstream):
    fake = await upstream({})
    schema = {'type': 'json_schema', 'name': 'x', 'strict': True,
              'schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
    async with gateway(store, Confirm(True), ordinary_default_provider='morph/fp8',
                       ordinary_fallback_provider='deepseek') as g:
        g.breaker.open_until = float('inf')
        await post(g, 'schema-thread', text={'format': schema})
    assert fake.calls == ['morph/fp8']
    assert receipts(store, 'schema-thread')[0][3] == 'strict_json_schema'
    assert thread_routes.get(store.db, 'schema-thread') is None


# --- manual move ----------------------------------------------------------

async def test_manual_move_drops_only_official_reasoning(store, upstream):
    fake = await upstream({})
    completed_thread(store, 'thread-old')
    thread_routes.move(store.db, 'thread-old', 'morph/fp8')
    history = [USER, OPAQUE, ASSISTANT, USER]
    async with gateway(store) as g:  # works without any fallback policy
        code, text = await post(g, 'thread-old', history)
    assert code == 200
    sent = fake.bodies[0]['input']
    assert fake.calls == ['morph/fp8'] and not any(item.get('encrypted_content') for item in sent)
    assert [item.get('role') for item in sent if item.get('type', 'message') == 'message'][:3] == ['user', 'assistant', 'user']
    row = store.db.execute("select route_reason,native_request from receipts where context_key='thread-old' and provider='morph/fp8'").fetchone()
    assert row[0] == 'thread_pin/manual/operator_default/morph/fp8'
    assert json.loads(row[1])['dropped_official_reasoning_items'] == 1


async def test_manual_move_back_to_official_keeps_reasoning_path(store, upstream):
    fake = await upstream({})
    completed_thread(store, 'thread-x')
    thread_routes.move(store.db, 'thread-x', 'morph/fp8')
    thread_routes.move(store.db, 'thread-x', 'default')
    async with gateway(store) as g:
        assert (await post(g, 'thread-x', [USER, ASSISTANT, USER]))[0] == 200
    assert fake.calls == ['deepseek'] and thread_routes.get(store.db, 'thread-x') is None


def test_move_rules(store):
    completed_thread(store, 'busy')
    store.start('deepseek', context_key='busy')
    with pytest.raises(RuntimeError, match='in progress'):
        thread_routes.move(store.db, 'busy', 'morph/fp8')
    with pytest.raises(ValueError):
        thread_routes.move(store.db, 'busy', 'primeintellect')


def test_thread_command_list_show_move(tmp_path, capsys):
    home = tmp_path / 'home'
    store = Store(home / 'provider-state' / 'adapter.sqlite')
    try:
        completed_thread(store, 'older')
        completed_thread(store, 'latest')
    finally:
        store.close()
    (home / 'session_index.jsonl').write_text(json.dumps({'id': 'latest', 'thread_name': 'Fix 502', 'updated_at': 'x'}) + '\n')
    assert thread_routes.command(home, ['list']) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [row['thread'] for row in listed] == ['latest', 'older'] and listed[0]['name'] == 'Fix 502'
    assert thread_routes.command(home, ['move', 'last', 'morph/fp8']) == 0
    moved = json.loads(capsys.readouterr().out)
    assert moved['thread'] == 'latest' and moved['pin'] == {'provider': 'morph/fp8', 'origin': 'manual'}
    assert 'Hidden reasoning' in moved['note'] and 'kept' in moved['note']
    assert thread_routes.command(home, ['show', 'latest']) == 0
    assert json.loads(capsys.readouterr().out)['pin']['provider'] == 'morph/fp8'
    assert thread_routes.command(home, ['move', 'latest', 'default']) == 0
    assert json.loads(capsys.readouterr().out)['pin'] is None
    for bad in (['move', 'missing', 'morph/fp8'], ['move'], ['bogus']):
        with pytest.raises(ValueError):
            thread_routes.command(home, bad)


def test_doctor_accepts_only_the_configured_fallback():
    generation = {'id': 'response1', 'model': 'deepseek/deepseek-v4.1-flash-20261017', 'provider_name': 'Morph'}
    assert cli._verified_generation(generation, 'response1', 'morph/fp8', 'deepseek') is False
    assert cli._verified_generation(generation, 'response1', 'morph/fp8', 'deepseek', 'morph/fp8') is True
    official = {**generation, 'provider_name': 'DeepSeek'}
    assert cli._verified_generation(official, 'response1', 'morph/fp8', 'deepseek', 'morph/fp8') is False
    assert cli._verified_generation(official, 'response1', 'deepseek', 'deepseek', 'morph/fp8') is True
