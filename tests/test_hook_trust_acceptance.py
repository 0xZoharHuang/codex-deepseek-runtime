"""Counterexamples for the raw native hook-trust evidence contract; no model/terminal."""
import copy
import base64
import json
import pytest
from verify_hook_trust import CASE_NAMES, REVIEW_LABELS, case_contract, one_fixture_hook


def fixture(name):
    positive = name == CASE_NAMES[1]
    modified = name == CASE_NAMES[2]
    current = "sha256:changed" if modified else "sha256:original"
    prior = "sha256:original" if modified else None
    def config(trusted):
        return (b'sandbox_mode="workspace-write"\n' if trusted is None else
                ('sandbox_mode="workspace-write"\n[hooks.state.fixture]\ntrusted_hash='
                 + json.dumps(trusted) + '\n').encode())
    before = {"key": "fixture", "currentHash": current,
              "trustStatus": "modified" if modified else "untrusted", "enabled": True}
    after = {**before, "trustStatus": "trusted" if positive else before["trustStatus"]}
    expected = {"nonce": "unique-nonce", "reply": "FINAL"}
    prior_calls = [{"hook_nonce": "old", "tool_input": "old-command"}] if modified else []
    after_calls = [*prior_calls, {"hook_nonce": "HOOK", "tool_input": "shell-trust-fixture.py unique-nonce"}] if positive else prior_calls
    actions = ["select_trust", "confirm_trust", "exit"] if positive else ["continue_without_trust", "exit"]
    return dict(
        name=name, before_hook=before, after_hook=after,
        before_config=config(prior), after_config=config(current if positive else prior),
        calls_before=prior_calls, calls_after=after_calls, hook_nonce="HOOK",
        shell_effect=expected.copy(), expected_effect=expected,
        transcript="\n".join(REVIEW_LABELS).encode(),
        inputs=[{"action": x, "bytes_base64": base64.b64encode({
            "select_trust": b"2", "confirm_trust": b"\r",
            "continue_without_trust": b"3", "exit": b"\x04"}[x]).decode(),
            "after_output_bytes": len("\n".join(REVIEW_LABELS).encode()), "at_monotonic": index}
            for index, x in enumerate(actions)],
        completed={"thread_id": "thread", "turn_id": "turn", "reply": "FINAL"},
        native_thread={"thread": {"id": "thread", "turns": [{
            "id": "turn", "status": "completed", "items": [{
                "type": "commandExecution", "command": "python shell-trust-fixture.py unique-nonce",
                "status": "completed", "exitCode": 0,
                "aggregatedOutput": "ACTUAL_SHELL_unique-nonce",
            }],
        }]}},
    )


@pytest.mark.parametrize("name", CASE_NAMES)
def test_three_distinct_native_trust_outcomes(name):
    assert case_contract(**fixture(name))


@pytest.mark.parametrize("problem", [
    "no_confirmation", "wrong_saved_hash", "skipped_hook_ran", "modified_already_trusted",
    "bypass", "missing_screen", "simulated_file_without_command", "failed_command",
    "foreign_thread", "foreign_turn", "interrupted_turn", "duplicate_hook",
    "completed_with_error", "hook_from_other_turn", "forged_input_bytes", "negative_offset", "input_before_review",
])
def test_forged_or_incomplete_native_evidence_cannot_pass(problem):
    name = CASE_NAMES[0] if problem == "skipped_hook_ran" else (
        CASE_NAMES[2] if problem == "modified_already_trusted" else CASE_NAMES[1])
    data = fixture(name)
    command = data["native_thread"]["thread"]["turns"][0]["items"][0]
    if problem == "no_confirmation": data["inputs"].pop(1)
    elif problem == "wrong_saved_hash": data["after_config"] = data["before_config"]
    elif problem == "skipped_hook_ran": data["calls_after"] = [{"hook_nonce": "HOOK"}]
    elif problem == "modified_already_trusted": data["before_hook"]["currentHash"] = "sha256:original"
    elif problem == "bypass": data["before_config"] = b"bypass_hook_trust=true\n" + data["before_config"]
    elif problem == "missing_screen": data["transcript"] = b"FINAL"
    elif problem == "simulated_file_without_command": data["native_thread"]["thread"]["turns"][0]["items"] = []
    elif problem == "failed_command": command["exitCode"] = 1
    elif problem == "foreign_thread": data["native_thread"]["thread"]["id"] = "another"
    elif problem == "foreign_turn": data["native_thread"]["thread"]["turns"][0]["id"] = "another"
    elif problem == "interrupted_turn": data["native_thread"]["thread"]["turns"][0]["status"] = "interrupted"
    elif problem == "duplicate_hook": data["calls_after"].append(copy.deepcopy(data["calls_after"][0]))
    elif problem == "completed_with_error": data["native_thread"]["thread"]["turns"][0]["error"] = {"message": "failure"}
    elif problem == "hook_from_other_turn": data["calls_after"][0]["tool_input"] = "shell-trust-fixture.py another-nonce"
    elif problem == "forged_input_bytes": data["inputs"][0]["bytes_base64"] = base64.b64encode(b"3").decode()
    elif problem == "negative_offset": data["inputs"][0]["after_output_bytes"] = -1
    elif problem == "input_before_review": data["inputs"][0]["after_output_bytes"] = 0
    with pytest.raises(AssertionError):
        case_contract(**data)


def test_hook_discovery_rejects_missing_duplicate_and_incomplete_metadata():
    hook = {"key": "fixture", "currentHash": "sha256:test", "enabled": True,
            "command": "python hook-trust-fixture.py", "trustStatus": "untrusted"}
    assert one_fixture_hook({"data": [{"hooks": [hook]}]}) == hook
    for hooks in [[], [hook, hook], [{**hook, "currentHash": ""}], [{**hook, "enabled": False}]]:
        with pytest.raises(AssertionError):
            one_fixture_hook({"data": [{"hooks": hooks}]})
