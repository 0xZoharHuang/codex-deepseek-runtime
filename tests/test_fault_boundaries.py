"""Fault qualification must not confuse seven repetitions with full coverage."""
from types import SimpleNamespace
import pytest
import verify_fault_boundaries as verifier

@pytest.mark.asyncio
async def test_seven_duplicate_kinds_are_rejected_before_any_runtime_start(monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail("Duplicate scenarios reached runtime or filesystem setup")
    monkeypatch.setattr(verifier,"private_directory",forbidden)
    monkeypatch.setattr(verifier.subprocess,"run",forbidden)
    with pytest.raises(ValueError,match="unique"):
        await verifier.main(SimpleNamespace(kind=["truncated"]*7))

@pytest.mark.parametrize("kinds",[
    ["truncated"]*7,
    ["truncated"],
    [],
    [*verifier.SCENARIOS[:-1],"unknown"],
])
def test_count_alone_never_claims_complete_fault_coverage(kinds):
    assert not verifier.complete_scenario_set(kinds)

def test_exact_complete_set_is_accepted_in_any_order():
    assert verifier.complete_scenario_set(list(verifier.SCENARIOS))
    assert verifier.complete_scenario_set(list(reversed(verifier.SCENARIOS)))
    assert verifier.selected_scenarios(None)==list(verifier.SCENARIOS)

def test_diagnostic_subset_is_preserved():
    assert verifier.selected_scenarios(["auth401","effect-after"])==["auth401","effect-after"]
    with pytest.raises(ValueError):verifier.selected_scenarios(["unknown"])


@pytest.mark.parametrize("change", ["none","thread","turn","method","delta","missing"])
def test_tcp_reset_requires_exact_native_stream_observation(change):
    event={"method":"item/agentMessage/delta","params":{"threadId":"thread","turnId":"turn","delta":"PARTIAL"}}
    if change in {"thread","turn"}:event["params"][change+"Id"]="other"
    elif change=="method":event["method"]="turn/completed"
    elif change=="delta":event["params"]["delta"]="unrelated"
    elif change=="missing":event={}
    assert verifier.native_partial_delta(event,"thread","turn") is (change=="none")

@pytest.mark.asyncio
@pytest.mark.parametrize("acknowledge", [True,False])
async def test_tcp_reset_never_aborts_before_native_receipt(tmp_path,monkeypatch,acknowledge):
    import asyncio
    writes=[];written=asyncio.Event();aborted=asyncio.Event()
    class Response:
        def __init__(self,**kwargs):pass
        async def prepare(self,request):pass
        async def write(self,data):
            writes.append(data)
            if b'"delta":"PARTIAL"' in data:written.set()
    async def body():return {"messages":[]}
    request=SimpleNamespace(headers={"Authorization":"Bearer "+verifier.KEY1},json=body,
                            transport=SimpleNamespace(abort=aborted.set))
    upstream=verifier.Upstream(tmp_path,"tcp-reset")
    monkeypatch.setattr(verifier.web,"StreamResponse",Response)
    task=asyncio.create_task(upstream.handle(request))
    try:
        await asyncio.wait_for(written.wait(),2)
        assert not aborted.is_set() and not task.done()
        if acknowledge:
            upstream.native_partial_seen.set()
            await asyncio.wait_for(task,2)
            assert aborted.is_set() and upstream.requests[0]["reset_after_native_partial"] is True
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert not aborted.is_set() and "reset_after_native_partial" not in upstream.requests[0]
    finally:
        if not task.done():
            task.cancel();await asyncio.gather(task,return_exceptions=True)
