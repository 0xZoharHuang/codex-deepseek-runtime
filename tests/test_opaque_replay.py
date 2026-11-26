"""Synthetic end-to-end opaque replay checks, all traffic stays on loopback."""
import copy
import json
from contextlib import asynccontextmanager

import aiohttp
from aiohttp import web
import pytest

import codex_deepseek.gateway as module
from codex_deepseek.gateway import Gateway
from codex_deepseek.protocol import MODEL
from codex_deepseek.state import Store


@pytest.fixture
async def upstream(monkeypatch):
    requests=[]
    state={'mode':'produce'}
    items=[{'id':'rs_synthetic_'+str(i),'type':'reasoning','summary':[],
            'encrypted_content':'SYNTHETIC_OPAQUE_'+str(i),
            'status':'completed','format':'unknown','synthetic_extension':{'index':i}}
           for i in range(4)]
    items[0]['content']=None
    items[1]['content']=[]
    items[2]['content']=[{'type':'text','text':'SYNTHETIC_TEXT_ONLY'}]
    items[3]['content']=[{'type':'reasoning_text','text':'SYNTHETIC_REASONING_TEXT'}]
    async def endpoint(request):
        payload=await request.json();requests.append(payload)
        identity = {}
        if observed := state.get('observed_provider'):
            identity = {'model': MODEL, 'provider': observed}
        if state['mode']=='produce':
            ev=[{'type':'response.created','response':{
                'id':'resp_first','status':'in_progress','output':[],**identity}}]
            output = [*state.get('items', items), *state.get('additional_output', [])]
            if state.get('tool_first'):
                ev.append({'type':'response.output_item.done','output_index':0,'item':{
                    'id':'fc_prior','type':'function_call','call_id':'call_prior','name':'synthetic_tool','arguments':'{}'}})
            if not state.get('terminal_only'):
                for index,item in enumerate(output):
                    ev.append({'type':'response.output_item.done','output_index':index,'item':item})
            terminal={'id':'resp_first','status':'failed' if state.get('fail') else 'completed',
                      'output':output,**identity}
            if terminal_provider := state.get('terminal_provider'):
                terminal['provider'] = terminal_provider
            if state.get('fail'):terminal['error']={'code':'fixture_failure','message':'synthetic failure'}
            if not payload.get('stream'):
                return web.json_response(terminal)
            ev.append({'type':'response.failed' if state.get('fail') else 'response.completed','response':terminal})
            if state.get('late'):
                ev.append({'type':'response.output_text.delta','item_id':'late','delta':'contradiction'})
            text=''.join('data: '+json.dumps(x)+'\n\n' for x in ev)
            if state.get('truncated'):
                body=text.encode();response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Content-Length':str(len(body)+10)})
                await response.prepare(request);await response.write(body);request.transport.close()
                return response
            return web.Response(text=text,content_type='text/event-stream')
        terminal={'id':'resp_second','status':'completed','output':[],**identity}
        if not payload.get('stream'):
            return web.json_response(terminal)
        return web.Response(text='data: '+json.dumps({'type':'response.completed','response':terminal})+'\n\n',content_type='text/event-stream')
    app=web.Application();app.router.add_post('/responses',endpoint)
    runner=web.AppRunner(app,handler_cancellation=True);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
    monkeypatch.setattr(module,'UPSTREAM','http://127.0.0.1:'+str(runner.addresses[0][1])+'/responses')
    yield state,items,requests
    await runner.cleanup()


def original():
    return {'model':MODEL,'stream':True,'input':[{'role':'user','content':'synthetic fixture'}]}


def continuation(items):
    # Native serde can omit provider-only output fields while retaining opaque bytes.
    history = []
    for source in items:
        projected = {field: copy.deepcopy(source[field])
                     for field in ('type', 'id', 'summary', 'encrypted_content')}
        if source.get('content') is None:
            projected['content'] = None
        elif any(part['type'] == 'reasoning_text' for part in source['content']):
            projected['content'] = copy.deepcopy(source['content'])
        history.append(projected)
    return {'model':MODEL,'stream':True,'input':[{'role':'user','content':'first'},*history,{'role':'user','content':'continue'}]}


def hosted_continuation():
    body = original()
    body['reasoning'] = {'effort': 'max'}
    body['tools'] = [
        {'type': 'web_search'},
        {'type': 'function', 'name': 'synthetic_tool', 'parameters': {}},
    ]
    pending = {'type': 'reasoning', 'id': 'rs_pending', 'summary': [],
               'content': [{'type': 'reasoning_text', 'text': 'SYNTHETIC_UNATTESTED_THINKING'}]}
    call = {'type': 'function_call', 'name': 'synthetic_tool', 'arguments': '{}',
            'call_id': 'call_synthetic'}
    result = {'type': 'function_call_output', 'call_id': 'call_synthetic',
              'output': 'SYNTHETIC_TOOL_RESULT'}
    body['input'].extend([pending, call, result, {'role': 'user', 'content': 'continue with search'}])
    return body


def morph_items(items):
    result = copy.deepcopy(items)
    for index, item in enumerate(result):
        item['id'] = f'rs_morph_{index}'
        item['encrypted_content'] = f'SYNTHETIC_MORPH_OPAQUE_{index}'
        item['signature'] = f'SYNTHETIC_MORPH_SIGNATURE_{index}'
    return result


def opaque_ledger_count(store, version):
    return store.db.execute('select count(*) from adapter_metadata where key like ?',
                            (f'opaque-reasoning-v{version}:%',)).fetchone()[0]


@asynccontextmanager
async def running(path,key='SYNTHETIC_KEY',**kw):
    store=Store(path)
    try:
        async with Gateway(key,'LOCAL',store,{},**kw) as gateway,aiohttp.ClientSession() as client:
            async def post(body):
                async with client.post('http://127.0.0.1:'+str(gateway.port)+'/v1/responses',json=body,
                                       headers={'Authorization':'Bearer LOCAL'}) as r:
                    return r.status,await r.text()
            yield post,store
    finally:store.close()


async def test_four_opaque_items_survive_restart_with_exact_upstream_wire(upstream,tmp_path):
    state,items,requests=upstream;path=tmp_path/'adapter.sqlite'
    async with running(path) as (post,store):
        status,text=await post(original())
        assert status==200 and 'response.completed' in text and 'response.failed' not in text
        for item in items:assert item['encrypted_content'] in text
        assert store.db.execute("select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'").fetchone()[0]==4
    state['mode']='replay'
    async with running(path) as (post,store):
        status,text=await post(continuation(items))
        assert status==200 and 'response.completed' in text and 'response.failed' not in text
        assert len(requests)==2
        replay=[x for x in requests[-1]['input'] if x.get('type')=='reasoning']
        assert replay==items
        assert requests[-1]['provider']['only']==['deepseek']
        assert store.db.execute('select status from receipts order by started').fetchall()==[('completed',),('completed',)]


async def test_verified_opaque_history_with_hosted_search_keeps_official_route(upstream,tmp_path):
    state,items,requests=upstream;path=tmp_path/'adapter.sqlite'
    async with running(path) as (post,_):
        status,text=await post(original());assert status==200 and 'response.completed' in text
    state['mode']='replay';body=continuation(items);body['tools']=[{'type':'web_search'}]
    async with running(path) as (post,_):
        status,text=await post(body)
        assert status==200 and 'response.completed' in text
    assert requests[-1]['provider']['only']==['deepseek']
    assert [x for x in requests[-1]['input'] if x.get('type')=='reasoning']==items


async def test_interrupted_reasoning_does_not_reroute_verified_opaque_history(upstream, tmp_path):
    state, items, requests = upstream
    path = tmp_path / 'adapter.sqlite'
    async with running(path) as (post, _):
        status, text = await post(original())
        assert status == 200 and 'response.completed' in text

    state['mode'] = 'replay'
    body = continuation(items)
    body['reasoning'] = {'effort': 'max'}
    body['tools'] = [
        {'type': 'web_search'},
        {'type': 'function', 'name': 'synthetic_tool', 'parameters': {}},
    ]
    call = {'type': 'function_call', 'name': 'synthetic_tool', 'arguments': '{}',
            'call_id': 'call_completed'}
    output = {'type': 'function_call_output', 'call_id': 'call_completed',
              'output': 'SYNTHETIC_COMPLETED_TOOL_RESULT'}
    partial = {'type': 'reasoning', 'id': 'rs_interrupted', 'summary': [],
               'content': [{'type': 'reasoning_text', 'text': 'SYNTHETIC_INTERRUPTED_REASONING'}]}
    body['input'][-1:-1] = [call, output, partial]
    async with running(path) as (post, store):
        status, text = await post(body)
        assert status == 200 and 'response.completed' in text and 'response.failed' not in text, text
        assert store.db.execute('select provider, route_reason, upstream_status from receipts '
                                'order by started desc limit 1').fetchone() == (
            'deepseek', 'official_opaque_history_with_hosted_tools', 200)

    assert len(requests) == 2
    replay = requests[-1]
    assert replay['provider']['only'] == ['deepseek']
    assert replay['provider']['allow_fallbacks'] is False
    assert replay['reasoning'] == {'effort': 'max'}
    assert any(tool['type'] == 'openrouter:web_search' for tool in replay['tools'])
    assert [row for row in replay['input'] if row.get('encrypted_content')] == items
    assert [row for row in replay['input'] if row.get('call_id') == call['call_id']] == [call, output]
    projected = replay['input'][0]['content'][0]['text']
    assert json.loads(projected[projected.index('['):])[-1]['item'] == partial


async def test_nonstream_completion_can_seed_streamed_opaque_continuation(upstream,tmp_path):
    state, items, requests = upstream
    path = tmp_path / 'adapter.sqlite'
    async with running(path) as (post, store):
        status, text = await post({**original(), 'stream': False})
        assert status == 200 and json.loads(text)['output'] == items
        assert store.db.execute(
            "select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'"
        ).fetchone()[0] == len(items)
    state['mode'] = 'replay'
    async with running(path) as (post, _):
        status, text = await post(continuation(items))
        assert status == 200 and 'response.completed' in text and 'response.failed' not in text
    assert [item for item in requests[-1]['input'] if item.get('type') == 'reasoning'] == items


async def test_terminal_only_stream_is_not_authoritative(upstream,tmp_path):
    state,items,_=upstream;state['terminal_only']=True
    async with running(tmp_path/'adapter.sqlite') as (post,store):
        status,text=await post(original())
        assert status==200 and 'response.failed' in text and 'response.completed' not in text
        assert all(x['encrypted_content'] not in text for x in items)
        assert store.db.execute("select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'").fetchone()[0]==0


@pytest.mark.parametrize('fault',['late','truncated'])
async def test_opaque_completion_waits_for_clean_transport_eof(upstream,tmp_path,fault):
    state,items,_=upstream;state[fault]=True
    async with running(tmp_path/'adapter.sqlite') as (post,store):
        status,text=await post(original())
        assert status==200 and 'response.failed' in text and 'response.completed' not in text
        assert all(x['encrypted_content'] not in text for x in items)
        assert store.db.execute("select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'").fetchone()[0]==0
        assert store.db.execute('select status from receipts').fetchone()[0]=='adapter_error'


@pytest.mark.parametrize('error_kind',['protocol','sqlite'])
async def test_evidence_failure_rolls_back_receipt_and_evidence(upstream,tmp_path,monkeypatch,error_kind):
    import codex_deepseek.opaque_replay as replay
    from codex_deepseek.protocol import ProtocolError
    import sqlite3
    def fail_after_partial_insert(db,credential,receipt_id,output):
        db.execute("insert into adapter_metadata values ('opaque-reasoning-v1:partial','synthetic')")
        raise (ProtocolError('synthetic evidence failure') if error_kind=='protocol' else sqlite3.OperationalError('synthetic database failure'))
    monkeypatch.setattr(replay,'remember_completed',fail_after_partial_insert)
    _,items,_=upstream
    async with running(tmp_path/'adapter.sqlite') as (post,store):
        status,text=await post(original())
        assert status==200 and 'response.failed' in text and 'response.completed' not in text
        assert all(x['encrypted_content'] not in text for x in items)
        assert store.db.execute("select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'").fetchone()[0]==0
        assert store.db.execute('select status from receipts').fetchone()[0]=='adapter_error'


@pytest.mark.parametrize('variant',['missing','cipher','id','summary','content','unknown_top','unknown_nested','known_extra_type','credential','route','model'])
async def test_unproven_or_wrong_scope_stops_before_upstream(upstream,tmp_path,variant):
    state,items,requests=upstream;path=tmp_path/'adapter.sqlite'
    if variant!='missing':
        async with running(path) as (post,_):
            status,text=await post(original());assert status==200 and 'response.completed' in text
    state['mode']='replay';body=continuation(items)
    if variant=='cipher':body['input'][1]['encrypted_content']+='X'
    if variant=='id':body['input'][1]['id']+='X'
    if variant=='summary':body['input'][1]['summary']=[{'type':'summary_text','text':'changed'}]
    if variant=='content':body['input'][4]['content'][0]['text']='changed'
    if variant=='unknown_top':body['input'][1]['unknown_field']='not-discardable'
    if variant=='unknown_nested':body['input'][4]['content'][0]['unknown_field']='not-discardable'
    if variant=='known_extra_type':body['input'][1]['synthetic_extension']={'index':False}
    if variant=='model':body['model']='another/model'
    options={'ordinary_default_provider':'morph/fp8'} if variant=='route' else {}
    key='OTHER_SYNTHETIC_KEY' if variant=='credential' else 'SYNTHETIC_KEY'
    before=len(requests)
    async with running(path,key,**options) as (post,_):
        status,text=await post(body)
        assert status in (400,502) and 'response.completed' not in text and 'SYNTHETIC_OPAQUE_' not in text
    assert len(requests)==before


async def test_failed_stream_does_not_publish_opaque_or_make_replayable(upstream,tmp_path):
    state,items,requests=upstream;state['fail']=True;path=tmp_path/'adapter.sqlite'
    async with running(path) as (post,store):
        status,text=await post(original())
        assert status==200 and 'response.failed' in text and 'response.completed' not in text
        assert 'fixture_failure' in text and 'synthetic failure' in text
        assert all(x['encrypted_content'] not in text for x in items)
        assert store.db.execute("select count(*) from adapter_metadata where key like 'opaque-reasoning-v1:%'").fetchone()[0]==0
        assert store.db.execute('select status from receipts').fetchone()[0]!='completed'
    state.update(mode='replay',fail=False);before=len(requests)
    async with running(path) as (post,_):
        status,text=await post(continuation(items));assert status in (400,502) and 'response.completed' not in text
    assert len(requests)==before


async def test_prior_tool_event_is_not_rolled_back_by_later_opaque_failure(upstream,tmp_path):
    state,items,_=upstream;state.update(fail=True,tool_first=True)
    body=original();body['tools']=[{'type':'function','name':'synthetic_tool','parameters':{'type':'object'}}]
    async with running(tmp_path/'adapter.sqlite') as (post,store):
        status,text=await post(body)
        assert status==200 and 'response.output_item.done' in text and 'call_prior' in text
        assert 'response.failed' in text and 'response.completed' not in text
        assert all(x['encrypted_content'] not in text for x in items)
        assert store.db.execute('select status from receipts').fetchone()[0]=='failed'


@pytest.mark.parametrize('retain_hosted_tool', [False, True], ids=['ordinary-next', 'hosted-next'])
@pytest.mark.parametrize('streamed_seed', [False, True], ids=['nonstream', 'stream'])
async def test_morph_hosted_opaque_replays_verified_items_after_gateway_restart(
    upstream, tmp_path, retain_hosted_tool, streamed_seed,
):
    state, items, requests = upstream
    path = tmp_path / 'adapter.sqlite'
    morph = morph_items(items)
    search = {'type': 'openrouter:web_search', 'id': 'synthetic_search', 'status': 'completed',
              'action': {'type': 'search', 'query': 'fixture docs',
                         'sources': [{'url': 'https://example.com/docs'}]}}
    state.update(observed_provider='Morph', items=morph, additional_output=[search])
    first = hosted_continuation()
    first['stream'] = streamed_seed
    async with running(path) as (post, store):
        status, text = await post(first)
        assert status == 200, text
        if streamed_seed:
            assert 'response.completed' in text and 'response.failed' not in text, text
        else:
            assert json.loads(text)['status'] == 'completed', text
        assert all(item['encrypted_content'] in text for item in morph)
        assert 'https://example.com/docs' in text and 'ws_synthetic_search' in text
        assert store.db.execute('select provider, status, route_reason, upstream_status from receipts'
                                ).fetchone() == (
            'morph/fp8', 'completed', 'unattested_native_history_with_hosted_tools', 200)
        assert opaque_ledger_count(store, 1) == 0
        assert opaque_ledger_count(store, 2) == len(morph)
        saved = store.db.execute("select value from adapter_metadata where key like 'opaque-reasoning-v2:%'")
        assert all(item['encrypted_content'] not in row[0] for row in saved for item in morph)

    state['mode'] = 'replay'
    second = continuation(morph)
    second['tools'] = first['tools'] if retain_hosted_tool else first['tools'][1:]
    call, result = first['input'][2:4]
    second['input'][-1:-1] = [
        {**search, 'type': 'web_search_call', 'id': 'ws_synthetic_search'},
        call,
        result,
    ]
    async with running(path) as (post, store):
        status, text = await post(second)
        assert status == 200 and 'response.completed' in text and 'response.failed' not in text, text
        assert store.db.execute('select provider, status, upstream_status from receipts order by started'
                                ).fetchall() == [('morph/fp8', 'completed', 200)] * 2
    assert len(requests) == 2
    for request in requests:
        assert request['provider'] == {
            'only': ['morph/fp8'], 'allow_fallbacks': False, 'require_parameters': True,
        }
    outgoing = requests[-1]
    assert outgoing['input'][1:1 + len(morph)] == morph
    assert next(item for item in outgoing['input'] if item.get('id') == search['id']) == search
    assert [item for item in outgoing['input'] if item.get('call_id') == call['call_id']] == [call, result]
    assert any(tool['type'] == 'openrouter:web_search' for tool in outgoing['tools']) == retain_hosted_tool


@pytest.mark.parametrize('conflict', ['credential', 'serial', 'mixed', 'explicit-peer'])
async def test_verified_morph_history_rejects_incompatible_input_before_i_o(upstream, tmp_path, conflict):
    state, items, requests = upstream
    path = tmp_path / 'adapter.sqlite'
    morph = morph_items(items)
    async with running(path) as (post, store):
        if conflict == 'mixed':
            state['observed_provider'] = 'DeepSeek'
            status, text = await post(original())
            assert status == 200 and 'response.completed' in text, text
            assert opaque_ledger_count(store, 1) == len(items)
        state.update(observed_provider='Morph', items=morph)
        status, text = await post(hosted_continuation())
        assert status == 200 and 'response.completed' in text, text
        assert opaque_ledger_count(store, 2) == len(morph)

    state['mode'] = 'replay'
    body = continuation([*items, *morph] if conflict == 'mixed' else morph)
    if conflict == 'serial':
        body['parallel_tool_calls'] = False
        body['tools'] = hosted_continuation()['tools'][1:]
    if conflict == 'explicit-peer':
        hosted = hosted_continuation()
        body['input'][-1:-1] = hosted['input'][1:-1]
        body['tools'] = hosted['tools']
    key = 'OTHER_SYNTHETIC_KEY' if conflict == 'credential' else 'SYNTHETIC_KEY'
    options = {'peer_hosted_provider': 'primeintellect'} if conflict == 'explicit-peer' else {}
    before = len(requests)
    async with running(path, key, **options) as (post, _):
        status, text = await post(body)
        assert status in (400, 502) and 'response.completed' not in text, text
        assert all(item['encrypted_content'] not in text for item in [*items, *morph])
    assert len(requests) == before


@pytest.mark.parametrize('fault', ['fail', 'truncated', 'late', 'terminal_only', 'mismatch', 'unsupported'])
async def test_ineligible_opaque_stream_never_delivers_replayable_output(upstream, tmp_path, fault):
    state, items, requests = upstream
    path = tmp_path / 'adapter.sqlite'
    morph = morph_items(items)
    state.update(observed_provider='Morph', items=morph)
    options = {}
    if fault == 'mismatch':
        state['terminal_provider'] = 'DeepSeek'
    elif fault == 'unsupported':
        state['observed_provider'] = 'PrimeIntellect'
        options['peer_hosted_provider'] = 'primeintellect'
    else:
        state[fault] = True
    async with running(path, **options) as (post, store):
        status, text = await post(hosted_continuation())
        assert status == 200 and 'response.failed' in text and 'response.completed' not in text, text
        if fault == 'fail':
            assert 'fixture_failure' in text
        assert all(item['encrypted_content'] not in text for item in morph)
        assert opaque_ledger_count(store, 1) == opaque_ledger_count(store, 2) == 0
        assert store.db.execute('select status, upstream_status from receipts').fetchone() != ('completed', 200)
    route = 'primeintellect' if fault == 'unsupported' else 'morph/fp8'
    assert len(requests) == 1 and requests[0]['provider']['only'] == [route]
    state['mode'] = 'replay'
    async with running(path) as (post, _):
        status, text = await post(continuation(morph))
        assert status in (400, 502) and 'response.completed' not in text, text
    assert len(requests) == 1
