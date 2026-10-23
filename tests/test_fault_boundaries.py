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
