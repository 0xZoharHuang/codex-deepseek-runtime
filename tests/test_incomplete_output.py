"""Incomplete snapshots are raw diagnostics, not executable completed items."""
import copy
import json

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import Events, MODEL, ProtocolError, encode_sse
from test_gateway import fake_upstream
from test_tool_commit_status import fixture


USAGE={"input_tokens":7,"output_tokens":3,"total_tokens":10}

def incomplete(item, reason):
    return {"type":"response.incomplete","response":{"id":"r","status":"incomplete",
        "incomplete_details":{"reason":reason},"usage":USAGE,"output":[item]}}


@pytest.mark.parametrize("kind",["function","custom","search"])
@pytest.mark.parametrize("status",["in_progress","incomplete"])
@pytest.mark.parametrize("reason",["max_output_tokens","content_filter","interrupted"])
def test_incomplete_snapshot_preserves_raw_partial_without_dispatch(kind,status,reason):
    _,registry,item=fixture(kind)
    item.update(status=status,arguments='{"input":' if kind=="custom" else '{"query":')
    event=incomplete(item,reason);frozen=copy.deepcopy(event);events=Events(registry)
    result=events.transform(event)
    assert len(result)==1 and result[0]["type"]=="response.incomplete"
    assert result[0]["response"]==event["response"]
    assert events.terminal and events.status=="incomplete" and events.response_id=="r" and events.usage==USAGE
    assert not events.done and event==frozen


@pytest.mark.parametrize("include_done",[False,True])
def test_incomplete_does_not_repeat_or_erase_previously_committed_item(include_done):
    _,registry,done=fixture("custom");events=Events(registry)
    events.transform({"type":"response.output_item.done","item":done})
    partial={"type":"function_call","id":"partial","call_id":"partial_call","name":"unmapped_raw_wire",
        "status":"incomplete","arguments":'{"query":'}
    event=incomplete(partial,"max_output_tokens")
    if include_done:event["response"]["output"].insert(0,done)
    result=events.transform(event)
    assert len(result)==1 and result[0]["type"]=="response.incomplete"
    assert events.done=={done["id"]:done}
    assert result[0]["response"]["output"]==event["response"]["output"]


def test_incomplete_cannot_change_a_previously_committed_item():
    _,registry,item=fixture("function");events=Events(registry)
    events.transform({"type":"response.output_item.done","item":item})
    with pytest.raises(ProtocolError):events.transform(incomplete({**item,"arguments":'{"changed":true}'},"max_output_tokens"))
    assert not events.terminal and events.done[item["id"]]==item


def test_incomplete_unknown_diagnostics_do_not_create_search_replay_evidence(tmp_path):
    store=Store(tmp_path/'state.sqlite')
    try:
        from codex_deepseek.protocol import Registry
        events=Events(Registry(store))
        item={"type":"openrouter:web_search","id":"w","status":"incomplete","action":{"type":"search","query":17}}
        event=incomplete(item,"max_output_tokens")
        assert events.transform(event)[0]["response"]==event["response"]
        assert store.get('ws_w') is None and not events.done
    finally:store.close()


@pytest.mark.parametrize("stream",[False,True])
@pytest.mark.parametrize("reason",["max_output_tokens","content_filter","interrupted"])
@pytest.mark.parametrize("prior_done",[False,True])
async def test_incomplete_real_loopback_preserves_native_error_contract(fake_upstream,tmp_path,stream,reason,prior_done):
    body,_,partial=fixture('search')
    partial.update(status='incomplete',arguments='{"query":')
    complete={**partial,'id':'done','call_id':'done_call','status':'completed','arguments':'{"query":"fixture"}'}
    terminal=incomplete(partial,reason)
    if prior_done:terminal['response']['output'].insert(0,complete)
    async def provider(request):
        if not stream:return web.json_response(terminal['response'])
        prefix=[{'type':'response.output_item.done','item':complete}] if prior_done else []
        return web.Response(body=b''.join(encode_sse(frame) for frame in [*prefix,terminal]),content_type='text/event-stream')
    fake_upstream.append(provider);store=Store(tmp_path/'state.sqlite')
    try:
        async with Gateway('SYNTHETIC','LOCAL',store,{}) as gateway,aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{gateway.port}/v1/responses',json={**body,'stream':stream},headers={'Authorization':'Bearer LOCAL'}) as response:
                assert response.status==200
                if stream:
                    frames=[json.loads(line[6:]) for line in (await response.text()).splitlines() if line.startswith('data: ')]
                    assert frames[-1]['type']=='response.incomplete'
                    assert frames[-1]['response']==terminal['response']
                    assert sum(frame['type']=='response.output_item.done' for frame in frames)==int(prior_done)
                    assert not any(frame['type'] in {'response.failed','response.completed'} for frame in frames)
                else:assert await response.json()==terminal['response']
        assert store.db.execute('SELECT status FROM receipts').fetchall()==[('incomplete',)]
    finally:store.close()
