"""Identity diagnostics retain protocol position, not arbitrary upstream data."""
import json

import pytest

from codex_deepseek.error_privacy import upstream_identity_context
from test_gateway import fake_upstream


@pytest.mark.parametrize("phase", [
    "response.created", "response.in_progress", "response.completed",
    "response.failed", "response.incomplete", "error", "nonstream",
])
def test_known_protocol_phase_is_preserved(phase):
    assert upstream_identity_context(phase) == {"schema": 1, "event_phase": phase}


@pytest.mark.parametrize("status", ["completed", "failed", "incomplete"])
def test_only_known_terminal_status_is_added(status):
    assert upstream_identity_context("response.completed", status) == {
        "schema": 1, "event_phase": "response.completed", "terminal_status": status,
    }


@pytest.mark.parametrize("phase", [None, False, 3, {}, [], "RESPONSE.CREATED",
                                         "response.created.private", "response.output_text.delta"])
def test_unknown_event_is_other_without_echo(phase):
    assert upstream_identity_context(phase, "in_progress") == {
        "schema": 1, "event_phase": "other",
    }


class MustNotInspect:
    def __str__(self):
        raise AssertionError("Diagnostic attempted to stringify upstream content")

    def __repr__(self):
        raise AssertionError("Diagnostic attempted to represent upstream content")

    def __hash__(self):
        raise AssertionError("Diagnostic attempted to hash upstream content")


class MustNotCompare(str):
    def __hash__(self):
        raise AssertionError("Diagnostic admitted an arbitrary string subclass")


def test_unknown_objects_are_not_inspected_or_serialized():
    assert upstream_identity_context(MustNotInspect(), MustNotInspect()) == {
        "schema": 1, "event_phase": "other",
    }
    assert upstream_identity_context(MustNotCompare("response.created")) == {
        "schema": 1, "event_phase": "other",
    }


def test_sensitive_or_oversize_labels_never_survive():
    private = "sk-or-v1-SYNTHETIC-NOT-A-REAL-KEY-private-model-and-input"
    result = upstream_identity_context("response.created-" + private * 2000,
                                       "completed-" + private)
    assert result == {"schema": 1, "event_phase": "other"}
    assert private not in json.dumps(result)
    assert len(json.dumps(result)) < 150


def test_each_context_is_independent_and_does_not_mutate_inputs():
    first = upstream_identity_context("response.completed", "completed")
    first["event_phase"] = "private"
    assert upstream_identity_context("response.completed", "completed") == {
        "schema": 1, "event_phase": "response.completed", "terminal_status": "completed",
    }


@pytest.mark.parametrize("mode,phase,status", [
    ("nonstream", "nonstream", "completed"),
    ("early", "response.created", "in_progress"),
    ("late", "response.completed", "completed"),
])
async def test_identity_rejection_receipt_records_only_safe_phase(
        fake_upstream, tmp_path, mode, phase, status):
    from test_gateway_observed_identity import exercise, events_from

    http_status, text, calls, receipt, no_attested = await exercise(
        fake_upstream, tmp_path, {"model": "SYNTHETIC-not-a-real-key"}, mode)
    assert len(calls) == 1 and receipt[0] == "adapter_error" and no_attested
    detail = json.loads(receipt[3])
    expected = {"schema": 1, "event_phase": phase}
    if status == "completed":
        expected["terminal_status"] = status
    assert detail["upstream_identity_context"] == expected
    assert "SYNTHETIC" not in receipt[3]
    assert "SYNTHETIC" not in text
    assert "incompatible model" in detail["message"]
    if mode == "nonstream":
        assert http_status == 502
        assert json.loads(text)["error"]["message"] == detail["message"]
    else:
        assert events_from(text)[-1]["type"] == "response.failed"
        assert events_from(text)[-1]["response"]["error"]["message"] == detail["message"]


async def test_invalid_image_terminal_identity_records_phase_without_settling(
        fake_upstream, tmp_path):
    from test_image_terminal_identity import envelope, run_case

    result = await run_case(fake_upstream, tmp_path, envelope(), stream=True, damage="model_shape")
    assert result["receipt_status"] == "adapter_error"
    detail = json.loads(result["receipt_detail"])
    assert detail["upstream_identity_context"] == {
        "schema": 1, "event_phase": "response.completed", "terminal_status": "completed",
    }
    assert (await result["transport"].diagnostics())["active_transport_pins"] == 1
