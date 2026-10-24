"""Actual native hook trust UI through the existing ConPTY/PTY acceptance driver.

Never writes trusted_hash or bypass_hook_trust. The native TUI is the only actor
allowed to grant hook trust. This verifier is not part of the installed adapter.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import tomllib
import uuid

from install import console_entry, valid_release
from process_acceptance import shell_command
from terminal_acceptance import terminal
from verify_live import Peer,prepare_workspace,workspace_path
from verify_tui_audit import (
    daemon_record, expected_backend, plain_terminal, rendered_reply,
    session_log_complete, stop_daemon,
)
from windows_acceptance_setup import prepare_terminal_fixture
from codex_deepseek.platforms import private_directory
from codex_deepseek.service import native_active

CASE_NAMES = (
    "untrusted_skip_blocks_hook",
    "native_trust_enables_hook",
    "modified_declaration_requires_retrust",
)
VERIFIERS = (
    "verify_hook_trust.py", "verify_live.py", "windows_acceptance_setup.py",
    "terminal_acceptance.py", "verify_tui_audit.py", "credential_acceptance.py",
    "process_acceptance.py", "install.py", "qa_workspace.py",
)
REVIEW_LABELS = (
    "Hooks need review", "Review hooks", "Trust all and continue",
    "Continue without trusting (hooks won't run)",
)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def hook_state(config_bytes, key):
    config = tomllib.loads(config_bytes.decode("utf-8"))
    if config.get("bypass_hook_trust", False):
        raise AssertionError("Hook trust acceptance must never enable bypass")
    return config.get("hooks", {}).get("state", {}).get(key, {}).get("trusted_hash")


def read_hook_calls(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def one_fixture_hook(response):
    hooks = [
        hook for entry in response.get("data", []) for hook in entry.get("hooks", [])
        if "hook-trust-fixture.py" in json.dumps(hook, ensure_ascii=False)
    ]
    if len(hooks) != 1:
        raise AssertionError("Exactly one owned fixture hook must be discovered")
    hook = hooks[0]
    if (not isinstance(hook.get("key"), str) or not hook["key"]
            or not isinstance(hook.get("currentHash"), str) or not hook["currentHash"]
            or hook.get("enabled") is not True):
        raise AssertionError("Native fixture hook metadata is incomplete")
    return hook


def case_contract(name, before_hook, after_hook, before_config, after_config,
                  calls_before, calls_after, hook_nonce, shell_effect,
                  expected_effect, transcript, inputs, completed, native_thread):
    """Check independent effects and native trust, never a saved passed boolean."""
    if name not in CASE_NAMES:
        raise AssertionError("Unknown hook trust case")
    plain = " ".join(plain_terminal(transcript).decode("utf-8", errors="replace").split())
    if not all(label in plain for label in REVIEW_LABELS):
        raise AssertionError("Native hook review screen was not observed")
    choices = [row["action"] for row in inputs if row["action"] != "terminal_query_reply"]
    expected_choices = (
        ["select_trust", "confirm_trust", "exit"]
        if name == CASE_NAMES[1] else ["continue_without_trust", "exit"]
    )
    if choices != expected_choices:
        raise AssertionError("Actual native UI input sequence differs")
    expected_bytes = {"select_trust": b"2", "confirm_trust": b"\r",
                      "continue_without_trust": b"3", "exit": b"\x04",
                      "terminal_query_reply": b"\x1b[1;1R"}
    last_offset = -1
    last_time = -1.0
    for row in inputs:
        try:
            raw = base64.b64decode(row["bytes_base64"], validate=True)
        except (KeyError, ValueError) as error:
            raise AssertionError("Invalid recorded native terminal input") from error
        offset = row.get("after_output_bytes")
        stamp = row.get("at_monotonic")
        if (raw != expected_bytes.get(row["action"]) or type(offset) is not int
                or not 0 <= offset <= len(transcript) or offset < last_offset
                or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < last_time):
            raise AssertionError("Native terminal input bytes/order differ")
        if row["action"] in {"select_trust", "continue_without_trust"}:
            prefix = " ".join(plain_terminal(transcript[:offset]).decode("utf-8", errors="replace").split())
            if not all(label in prefix for label in REVIEW_LABELS):
                raise AssertionError("Hook choice preceded the observed native review screen")
        last_offset, last_time = offset, stamp
    if not completed or completed.get("reply") != expected_effect["reply"]:
        raise AssertionError("A completed native turn is required")
    if shell_effect != expected_effect:
        raise AssertionError("The actual native shell effect differs")
    thread = native_thread.get("thread", {})
    if thread.get("id") != completed.get("thread_id"):
        raise AssertionError("Native shell evidence belongs to another thread")
    turns = [turn for turn in thread.get("turns", [])
             if turn.get("id") == completed.get("turn_id")]
    if (len(turns) != 1 or turns[0].get("status") != "completed"
            or turns[0].get("error") is not None):
        raise AssertionError("The exact completed native shell turn is missing")
    commands = [item for item in turns[0].get("items", [])
                if item.get("type") == "commandExecution"
                and "shell-trust-fixture.py" in str(item.get("command", ""))
                and expected_effect["nonce"] in str(item.get("command", ""))]
    if (len(commands) != 1 or commands[0].get("status") != "completed"
            or commands[0].get("exitCode") != 0
            or "ACTUAL_SHELL_" + expected_effect["nonce"] not in commands[0].get("aggregatedOutput", "")):
        raise AssertionError("A successful matching native shell tool is required")
    key = before_hook["key"]
    if after_hook["key"] != key or after_hook["currentHash"] != before_hook["currentHash"]:
        raise AssertionError("Hook identity changed during the UI case")
    old = hook_state(before_config, key)
    new = hook_state(after_config, key)
    if name == CASE_NAMES[1]:
        if before_hook["trustStatus"] != "untrusted" or old is not None:
            raise AssertionError("Positive trust must start untrusted")
        if after_hook["trustStatus"] != "trusted" or new != before_hook["currentHash"]:
            raise AssertionError("Native UI did not persist the exact current hook hash")
        delta = calls_after[len(calls_before):]
        if calls_after[:len(calls_before)] != calls_before or len(delta) != 1:
            raise AssertionError("Exactly one actual hook execution is required")
        if (delta[0].get("hook_nonce") != hook_nonce
                or "shell-trust-fixture.py" not in json.dumps(delta[0].get("tool_input"))
                or expected_effect["nonce"] not in json.dumps(delta[0].get("tool_input"))):
            raise AssertionError("Hook did not observe this native shell operation")
    else:
        expected_status = "untrusted" if name == CASE_NAMES[0] else "modified"
        if before_hook["trustStatus"] != expected_status or after_hook["trustStatus"] != expected_status:
            raise AssertionError("Skipped hook trust state must remain unchanged")
        if new != old or calls_after != calls_before:
            raise AssertionError("An untrusted/modified hook executed or gained trust")
        if name == CASE_NAMES[0] and old is not None:
            raise AssertionError("First case already had persisted hook trust")
        if name == CASE_NAMES[2] and (not old or old == before_hook["currentHash"]):
            raise AssertionError("Modified declaration did not invalidate the old hash")
    return True


def hook_observation(entry, root, case, phase, thread_id=None):
    observation = case / ("native-" + phase)
    observation.mkdir(parents=True)
    peer = Peer(observation, command=[str(entry)], runtime_home=root / "home")
    try:
        response = peer.call("hooks/list", {"cwds": [str(workspace_path(root))]})
        write_json(case / ("hooks-" + phase + ".json"), response)
        if thread_id is not None:
            thread = peer.call("thread/read", {"threadId": thread_id, "includeTurns": True})
            write_json(case / "native-thread.json", thread)
        return one_fixture_hook(response)
    finally:
        peer.close()


def run_terminal(entry, root, case, name, prompt, effect, expected_effect, backend):
    log_path = case / "native-session.jsonl"
    environment = {
        **os.environ, "TERM": "xterm-256color", "CODEX_DEEPSEEK_HOME": str(root / "home"),
        "CODEX_TUI_RECORD_SESSION": "1", "CODEX_TUI_SESSION_LOG_PATH": str(log_path),
        "CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT": "1",
    }
    environment.pop("CODEX_HOME", None)
    existing = {str(p): p.stat().st_size for p in (root / "home/sessions").rglob("*.jsonl")}
    child = terminal([str(entry), "--no-alt-screen", "-C", str(workspace_path(root)), prompt],
                     env=environment, cwd=workspace_path(root), columns=150, rows=38)
    transcript = bytearray()
    inputs = []
    completed = {}
    query_tail = b""
    normal_exit = False

    def send(action, data):
        inputs.append({"action": action, "at_monotonic": time.monotonic(),
                       "after_output_bytes": len(transcript),
                       "bytes_base64": base64.b64encode(data).decode()})
        child.write(data)

    def receive():
        nonlocal query_tail
        data = child.read(.2)
        transcript.extend(data)
        scan = query_tail + data
        if b"\x1b[6n" in scan:
            send("terminal_query_reply", b"\x1b[1;1R")
        query_tail = scan[-3:]

    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            receive()
            if child.poll() is not None:
                raise AssertionError("Native TUI exited before hook review")
            plain = " ".join(plain_terminal(transcript).decode("utf-8", errors="replace").split())
            if all(label in plain for label in REVIEW_LABELS):
                break
        else:
            raise TimeoutError("Native first-time hook review did not appear")
        (case / "review-screen.pty").write_bytes(transcript)
        # The native screen discards input queued before its initial draw.
        # Read one more event cycle before sending a user selection.
        receive()
        if name == CASE_NAMES[1]:
            send("select_trust", b"2")
            receive()
            (case / "selected-screen.pty").write_bytes(transcript)
            send("confirm_trust", b"\r")
        else:
            send("continue_without_trust", b"3")
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            receive()
            if child.poll() is not None:
                raise AssertionError("Native TUI exited before a completed shell turn")
            found = []
            for rollout in (root / "home/sessions").rglob("*.jsonl"):
                with rollout.open("rb") as stream:
                    stream.seek(existing.get(str(rollout), 0))
                    for line in stream:
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        payload = event.get("payload", {})
                        if (event.get("type") == "event_msg" and payload.get("type") == "task_complete"
                                and payload.get("last_agent_message") == expected_effect["reply"]):
                            found.append((rollout, payload))
            if (effect.exists() and json.loads(effect.read_text(encoding="utf-8")) == expected_effect
                    and len(found) == 1 and rendered_reply(transcript, expected_effect["reply"])):
                rollout, payload = found[0]
                first = json.loads(rollout.open(encoding="utf-8").readline())
                completed = {"thread_id": first["payload"]["id"], "turn_id": payload.get("turn_id"),
                             "reply": payload["last_agent_message"], "rollout": str(rollout),
                             "rollout_sha256": sha(rollout)}
                break
        else:
            raise TimeoutError("Native shell effect/completion was not observed")
        send("exit", b"\x04")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            receive()
            code = child.poll()
            if code is not None:
                if code != 0:
                    raise AssertionError("Native TUI failed clean exit")
                normal_exit = True
                break
        if not normal_exit:
            raise TimeoutError("Native TUI did not close normally")
        if backend == "shared":
            record = daemon_record(root / "home")
            stop_daemon(entry, root, name + "-daemon-stop.txt", record)
        records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        if not session_log_complete(records):
            raise AssertionError("Native TUI session log did not close")
        completed["rollout_sha256"] = sha(completed["rollout"])
        (case / "rollout.jsonl").write_bytes(Path(completed["rollout"]).read_bytes())
        write_json(case / "completion.json", completed)
        return bytes(transcript), inputs, completed
    except BaseException as error:
        write_json(case / "terminal-failure.json",
                   {"type": type(error).__name__, "message": str(error)})
        raise
    finally:
        (case / "terminal.pty").write_bytes(transcript)
        write_json(case / "inputs.json", inputs)
        cleanup_errors = []
        try:
            child.close()
        except Exception as error:
            cleanup_errors.append({"phase": "terminal_close", "type": type(error).__name__,
                                   "message": str(error)})
        try:
            if backend == "shared" and native_active(root / "home"):
                record = daemon_record(root / "home")
                stop_daemon(entry, root, name + "-failure-daemon-stop.txt", record)
            if native_active(root / "home"):
                raise AssertionError("This hook verifier's native runtime remains active")
        except Exception as error:
            cleanup_errors.append({"phase": "native_daemon_close", "type": type(error).__name__,
                                   "message": str(error)})
        write_json(case / "cleanup.json", {"passed": not cleanup_errors, "errors": cleanup_errors})
        if cleanup_errors:
            raise AssertionError("Owned hook terminal cleanup failed; original failures retained")


def main():
    if not __debug__:
        raise SystemExit("Acceptance assertions must be enabled")
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    entry = args.entry.resolve(strict=True)
    release = entry.parent.parent.parent
    installation = json.loads((release / "installation.json").read_text(encoding="utf-8"))
    native = json.loads((release / "native-runtime.json").read_text(encoding="utf-8"))
    if (entry != console_entry(release / "venv")
            or not valid_release(release, installation["adapter_source_sha256"],
                                 installation["model_catalog_sha256"], native)):
        raise SystemExit("An intact immutable generic installed entry is required")
    backend = expected_backend()
    if os.name == "nt" and backend != "shared":
        raise SystemExit("Hook trust effects must run from the Limited interactive controller")
    root = args.output.resolve() / uuid.uuid4().hex[:12]
    if root.exists():
        raise SystemExit("Preserve existing hook acceptance evidence")
    private_directory(root)
    (root / "home").mkdir()
    (root / "work").mkdir()
    work = prepare_workspace(root)
    (root / "home/config.toml").write_text(
        'model_reasoning_effort="low"\napproval_policy="never"\nsandbox_mode="workspace-write"\n'
        'web_search="disabled"\n[tui]\nanimations=false\n[projects.'
        + json.dumps(str(work)) + ']\ntrust_level="trusted"\n', encoding="utf-8")
    artifact = {
        "entry": str(entry), "entry_sha256": sha(entry), "installation": installation,
        "native_manifest": native, "target": native["target"],
        "verifier_sha256": {name: sha(Path(__file__).with_name(name)) for name in VERIFIERS},
        "scope": "Native first hook-trust UI; workspace trust is explicitly fixture-owned",
        "cases": list(CASE_NAMES), "controller_optimize": sys.flags.optimize,
    }
    write_json(root / "artifact.json", artifact)
    cases = []
    failure = None
    try:
        prepare_terminal_fixture(root, entry)
        hook_script = root / "hook-trust-fixture.py"
        hook_log = root / "hook-calls.jsonl"
        hook_script.write_text(
            'import json,pathlib,sys\n'
            'event=json.load(sys.stdin)\n'
            'with pathlib.Path(sys.argv[1]).open("a",encoding="utf-8") as stream:\n'
            ' stream.write(json.dumps({"hook_nonce":sys.argv[2],"tool_name":event.get("tool_name"),'
            '"tool_input":event.get("tool_input")})+"\\n")\nprint("{}")\n', encoding="utf-8")
        shell = work / "shell-trust-fixture.py"
        shell.write_text(
            'import json,pathlib,sys\n'
            'data={"nonce":sys.argv[2],"reply":sys.argv[3]}\n'
            'pathlib.Path(sys.argv[1]).write_text(json.dumps(data),encoding="utf-8")\n'
            'print("ACTUAL_SHELL_"+sys.argv[2])\n', encoding="utf-8")
        hook_nonce = "HOOK_" + uuid.uuid4().hex
        hooks_path = root / "home/hooks.json"

        def declare(nonce):
            argv = [sys.executable, "-I", "-X", "utf8", str(hook_script), str(hook_log), nonce]
            command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
            hooks_path.write_text(json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}
            ]}}, indent=2), encoding="utf-8")

        declare(hook_nonce)
        for name in CASE_NAMES:
            case = root / name
            case.mkdir()
            if name == CASE_NAMES[2]:
                hook_nonce = "MODIFIED_" + uuid.uuid4().hex
                declare(hook_nonce)
            (case / "hooks-declaration.json").write_bytes(hooks_path.read_bytes())
            before_hook = hook_observation(entry, root, case, "before")
            before_config = (root / "home/config.toml").read_bytes()
            (case / "config-before.toml").write_bytes(before_config)
            calls_before = read_hook_calls(hook_log)
            write_json(case / "hook-calls-before.json", calls_before)
            nonce = uuid.uuid4().hex
            expected = {"nonce": nonce, "reply": "HOOK_TRUST_COMPLETE_" + nonce}
            effect = work / (name + ".json")
            command = shell_command([sys.executable, "-I", str(shell), str(effect),
                                     nonce, expected["reply"]])
            prompt = (
                "Do not delegate, search, edit files, or use other commands. Execute this exact "
                "native shell command exactly once using exec_command and wait for its actual "
                "completion. Do not simulate its output. Then your final answer must be exactly "
                + expected["reply"] + " with no other text.\n" + command
            )
            (case / "prompt.txt").write_text(prompt, encoding="utf-8")
            transcript, inputs, completed = run_terminal(
                entry, root, case, name, prompt, effect, expected, backend)
            after_hook = hook_observation(entry, root, case, "after", completed["thread_id"])
            after_config = (root / "home/config.toml").read_bytes()
            (case / "config-after.toml").write_bytes(after_config)
            calls_after = read_hook_calls(hook_log)
            write_json(case / "hook-calls-after.json", calls_after)
            actual_effect = json.loads(effect.read_text(encoding="utf-8"))
            write_json(case / "shell-effect.json", actual_effect)
            write_json(case / "expectations.json", {
                "name": name, "hook_nonce": hook_nonce, "shell_effect": expected,
                "before_hook": before_hook, "after_hook": after_hook,
            })
            case_contract(name, before_hook, after_hook, before_config, after_config,
                          calls_before, calls_after, hook_nonce, actual_effect,
                          expected, transcript, inputs, completed,
                          json.loads((case / "native-thread.json").read_text(encoding="utf-8")))
            evidence = {path.relative_to(case).as_posix(): sha(path)
                        for path in case.rglob("*") if path.is_file()}
            cases.append({"name": name, "passed": True, "root": name,
                          "evidence_sha256": evidence})
            write_json(root / "results.json", cases)
    except BaseException as error:
        failure = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        unchanged = (sha(entry) == artifact["entry_sha256"]
                     and all(sha(Path(__file__).with_name(n)) == h
                             for n, h in artifact["verifier_sha256"].items())
                     and valid_release(release, installation["adapter_source_sha256"],
                                       installation["model_catalog_sha256"], native))
        verdict = {
            "passed": failure is None and [c["name"] for c in cases] == list(CASE_NAMES) and unchanged,
            "installation": installation, "target": native["target"], "cases": cases,
            "verifier_and_artifact_unchanged": unchanged, "failure": failure,
            "native_trust_ui_exercised": len(cases) == len(CASE_NAMES), "bypass_hook_trust_used": False,
        }
        write_json(root / "verdict.json", verdict)
        print("EVIDENCE", root, flush=True)


if __name__ == "__main__":
    main()
