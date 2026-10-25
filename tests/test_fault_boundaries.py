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


def test_fault_observer_excludes_controller_and_uncertain_or_foreign_candidates(monkeypatch):
    controller=(100,'root')
    family={controller:{'pid':100,'birth':'root'}}
    observed={};uncertain={};rejected={};errors={}
    def proven_edges(seen,pending,foreign,failures):
        seen[(200,'owned')]={'pid':200,'birth':'owned','parent_pid':100,'parent_birth':'root'}
        pending[(300,'unknown')]={'pid':300,'birth':'unknown','parent_pid':200,'parent_birth':'owned'}
        foreign[(400,'foreign')]={'pid':400,'birth':'foreign','ppid':999}
    monkeypatch.setattr(verifier,'_observe_process_family',proven_edges)
    verifier.collect_fault_processes(controller,family,observed,uncertain,rejected,errors)
    assert list(observed)==[(200,'owned')]
    assert controller not in observed and (300,'unknown') not in observed and (400,'foreign') not in observed
    assert observed[(200,'owned')]['parent_birth']=='root' and uncertain and rejected
    # Repeated sampling replaces the observed view; it never infers ownership
    # from diagnostic candidates or an earlier unrelated value in that view.
    observed[(999,'stale')]={'pid':999,'birth':'stale'}
    verifier.collect_fault_processes(controller,family,observed,uncertain,rejected,errors)
    assert list(observed)==[(200,'owned')]


@pytest.mark.parametrize('alive',[True,False])
def test_fault_observer_unresolved_live_identity_is_failure_metadata_not_signal_authority(monkeypatch,alive):
    pending={(300,'maybe'):{'pid':300,'birth':'maybe','birth_after':'maybe','parent_pid':100,'parent_birth':'root'}}
    monkeypatch.setattr(verifier,'matches',lambda pid,birth:alive and pid==300 and birth=='maybe')
    monkeypatch.setattr(verifier,'terminate',lambda *a,**k:pytest.fail('An uncertain process was signalled'))
    value=verifier.fault_observation_issues(pending,{}, {})
    assert value['unknown_live']==([300] if alive else [])
    assert value['uncertain_observations']==list(pending.values())


def test_fault_observer_keeps_required_helper_in_exact_provenance_inventory():
    assert 'ci/qualify_native.py' in verifier.VERIFIERS
    import ast,inspect
    tree=ast.parse(inspect.getsource(verifier.scenario))
    assert not any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                   and node.func.attr=='children' for node in ast.walk(tree))
    assert any(isinstance(node,ast.Call) and isinstance(node.func,ast.Name)
               and node.func.id=='collect_fault_processes' for node in ast.walk(tree))
