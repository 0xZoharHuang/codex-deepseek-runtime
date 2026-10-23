"""Terminal acceptance must distinguish completed work from deliberate interruption."""
from threading import Condition
import pytest
from verify_live import Peer


def item(thread="thread", turn="turn", text="owned"):
    return {"method": "item/completed", "params": {"threadId": thread, "turnId": turn,
            "item": {"type": "agentMessage", "text": text}}}


def terminal(status="completed", error=None, thread="thread", turn="turn"):
    return {"method": "turn/completed", "params": {"threadId": thread,
            "turn": {"id": turn, "status": status, "error": error}}}


def peer(events):
    value = Peer.__new__(Peer)
    value.events = events
    value.condition = Condition()
    value.exited = True  # Missing requested evidence must fail without waiting.
    return value


@pytest.mark.parametrize("status", ["interrupted", "cancelled", "failed", "inProgress", "unknown", None])
def test_normal_completion_rejects_every_noncompleted_terminal(status):
    value = peer([item(), terminal(status)])
    with pytest.raises(RuntimeError, match="error-free completed"):
        value.complete("thread", 0)


@pytest.mark.parametrize("error", [{"message": "provider failed"}, {}, "", False, 0])
def test_completed_status_cannot_hide_any_nonnull_error(error):
    value = peer([item(), terminal(error=error)])
    with pytest.raises(RuntimeError, match="error-free completed"):
        value.complete("thread", 0)


def test_success_returns_only_matching_turn_items_before_terminal():
    events = [item(text="before-start"), item(thread="other"), item(turn="other"),
              item(text="verified"), terminal(), item(text="after-terminal"),
              item(turn="next", text="next-turn"), terminal(turn="next")]
    assert peer(events).complete("thread", 1) == [events[3]["params"]["item"]]


def test_explicit_interrupt_requires_matching_turn_and_no_error():
    events = [terminal(turn="other"), item(), terminal("interrupted")]
    assert peer(events).complete("thread", 0, expected_status="interrupted", turn_id="turn") == [events[1]["params"]["item"]]
    with pytest.raises(RuntimeError, match="error-free interrupted"):
        peer([terminal("interrupted", {"message": "failure"})]).complete(
            "thread", 0, expected_status="interrupted", turn_id="turn")


@pytest.mark.parametrize("status", ["completed", "cancelled", "failed"])
def test_explicit_interrupt_does_not_accept_a_different_terminal(status):
    with pytest.raises(RuntimeError, match="error-free interrupted"):
        peer([terminal(status)]).complete("thread", 0, expected_status="interrupted", turn_id="turn")


def test_interrupt_without_turn_identity_is_not_an_escape_hatch():
    with pytest.raises(ValueError, match="exact requested turn ID"):
        peer([]).complete("thread", 0, expected_status="interrupted")
    with pytest.raises(ValueError, match="explicitly interrupted"):
        peer([]).complete("thread", 0, expected_status="cancelled", turn_id="turn")


def test_wrong_thread_or_wrong_requested_turn_cannot_satisfy_completion():
    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        peer([terminal(thread="other"), terminal(turn="other")]).complete("thread", 0, turn_id="turn")


@pytest.mark.parametrize("bad_turn", [None, {}, {"status": "completed", "error": None}, {"id": "", "status": "completed"}])
def test_malformed_terminal_is_not_success(bad_turn):
    event = terminal();event["params"]["turn"] = bad_turn
    with pytest.raises(RuntimeError, match="error-free completed"):
        peer([event]).complete("thread", 0)

def test_turn_binds_completion_to_its_start_response():
    value = peer([terminal(turn="unrelated"), item(), terminal()])
    value.start = lambda thread, text, **options: ("turn", 0)
    assert value.turn("thread", "request") == [value.events[1]["params"]["item"]]
