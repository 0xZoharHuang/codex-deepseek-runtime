"""Zero-paid native supplement: a real tool effect precedes a fault in the same upstream stream.

Run separately from the standard runtime gates. No tool deduplication or retry policy
is added. All upstream traffic terminates at the owned TLS fixture with a synthetic key.
"""
import argparse
import asyncio
import ctypes
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import traceback
import uuid

from aiohttp import web
import codex_deepseek

import verify_fault_boundaries as base
from codex_deepseek.gateway import Gateway
from codex_deepseek.state import Store

REPO = base.REPO
KINDS = ("undeclared-tool", "malformed-json", "eof")
VERIFIERS = (*base.VERIFIERS, "verify_mixed_stream_faults.py")


def retry_history(body, call_id):
    calls = [item for item in body.get("input", [])
             if isinstance(item, dict) and item.get("type") == "function_call"
             and item.get("call_id") == call_id]
    outputs = base.find_output(body, call_id)
    assert len(calls) == 1, "Retry lost or duplicated the completed original call"
    assert json.loads(calls[0]["arguments"]) == {"marker": call_id}
    assert len(outputs) == 1 and isinstance(outputs[0].get("output"), list) and any(
        part.get("type") == "input_text" and part.get("text") == "APPENDED:" + call_id
        for part in outputs[0]["output"] if isinstance(part, dict)), (
        "Retry lost the actual tool result or fabricated an unrelated result")
    return {"calls": calls, "outputs": outputs}


class MixedUpstream(base.Upstream):
    def __init__(self, root, kind):
        if kind not in KINDS:
            raise ValueError("Unknown mixed-stream fault")
        super().__init__(root, "effect-mixed")
        self.mixed_kind = kind

    async def handle(self, request):
        body = await request.json()
        if self.phase == "cold":
            return await super().handle(request)
        if base.find_output(body, self.call_id):
            proof = retry_history(body, self.call_id)
            result = await super().handle(request)
            self.requests[-1]["retry_history"] = proof
            base.save(self.root / "upstream-requests.json", self.requests)
            return result
        found = base.find_tool(body.get("tools", []))
        if not found:
            for item in body.get("input", []):
                if isinstance(item, dict) and item.get("type") in {"tool_search_output", "additional_tools"}:
                    found = base.find_tool(item.get("tools", []))
                    if found:
                        break
        if not found:
            return await super().handle(request)
        assert not self.fault_sent, "Native retry omitted the completed tool output"
        assert request.headers.get("Authorization") == "Bearer " + base.KEY1
        name, namespace = found
        call = {"type": "function_call", "id": "mixed_call", "call_id": self.call_id,
                "name": name, "arguments": json.dumps({"marker": self.call_id}),
                "status": "completed"}
        if namespace:
            call["namespace"] = namespace
        row = {"at": time.time(), "body": body, "credential": "synthetic",
               "fault_kind": self.mixed_kind, "wire": [], "fault_emitted": False,
               "premise_established": False}
        self.requests.append(row)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)

        async def emit(event):
            row["wire"].append({"at": time.time(), "event": event})
            base.save(self.root / "upstream-requests.json", self.requests)
            await response.write(("data: " + json.dumps(event) + "\n\n").encode())

        await emit({"type": "response.created", "response": {
            "id": "resp_mixed", "status": "in_progress", "output": []}})
        await emit({"type": "response.output_item.done", "output_index": 0, "item": call})
        row["tool_done_sent_at"] = time.time()
        # This barrier is written only AFTER the non-idempotent append is fsynced.
        # Do not await persisted output: native drains results after the stream fails.
        await base.until(lambda: (self.root / "append-entered").exists(), seconds=30)
        ledger = base.effects(self.root)
        assert len(ledger) == 1 and ledger[0]["marker"] == self.call_id, (
            "The real side-effect premise was not established")
        row["effect_before_fault"] = ledger
        row["premise_established"] = True
        row["effect_observed_at"] = time.time()
        self.fault_sent = True
        row["fault_at"] = time.time()
        if self.mixed_kind == "undeclared-tool":
            await emit({"type": "response.output_item.done", "output_index": 1, "item": {
                "type": "function_call", "id": "mixed_bad", "call_id": "must_never_execute",
                "name": "nonexistent_mixed_stream_fixture", "arguments": "{}", "status": "completed"}})
        elif self.mixed_kind == "malformed-json":
            row["malformed_wire_utf8"] = 'data: {"type":\n\n'
            await response.write(row["malformed_wire_utf8"].encode())
        # EOF is deliberate, without a terminal Responses event. The other two
        # failures also finish the connection after their invalid upstream item.
        row["fault_emitted"] = True
        base.save(self.root / "upstream-requests.json", self.requests)
        await response.write_eof()
        return response


async def case(root, binary, http_binary, catalog, python, kind):
    base.private_directory(root)
    base.private_directory(root / "home")
    (root / "work").mkdir()
    upstream = MixedUpstream(root, kind)
    peer = None
    store = None
    observed = {}
    controller = (os.getpid(), base.identity(os.getpid()))
    assert controller[1], "Fixture controller identity is missing"
    family = {controller: {"pid": controller[0], "birth": controller[1],
                           "ownership": "current_fixture_controller"}}
    uncertain, rejected, observation_errors = {}, {}, {}
    stop = asyncio.Event()
    report = {"kind": kind, "passed": False, "model_calls": 0, "real_credentials": 0,
              "started": time.time(), "binary_sha256": base.sha(binary)}

    async def observe():
        while not stop.is_set():
            base.collect_fault_processes(controller, family, observed, uncertain,
                                         rejected, observation_errors)
            await asyncio.sleep(.05)

    watcher = asyncio.create_task(observe())
    try:
        ca = await upstream.start()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                    "http_proxy", "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR"):
            os.environ.pop(key, None)
        os.environ.update(HTTPS_PROXY=upstream.proxy_url, CODEX_CA_CERTIFICATE=str(ca),
                          OPENROUTER_API_KEY=base.KEY1)
        store = Store(root / "state.sqlite")
        async with Gateway(base.KEY1, uuid.uuid4().hex, store, json.loads(catalog.read_text(encoding="utf-8")),
                           native_http_binary=http_binary,
                           trace_source=lambda: str(root / "adapter-traces")) as gateway:
            descriptor = {"port": gateway.port, "token": gateway.token}
            peer = await asyncio.to_thread(base.new_peer, root, binary, catalog, descriptor, python, True)
            thread = await asyncio.to_thread(peer.new)
            turn, start = await asyncio.to_thread(peer.start, thread, "Run the owned append tool once.")
            report.update(thread=thread, turn=turn)
            items = await asyncio.to_thread(peer.complete, thread, start, turn_id=turn, timeout=90)
            assert "EFFECT_OK" in base.text(items)
            report["warm_events"] = list(peer.events)
            report["native_retry_events"] = [event for event in peer.events
                if event.get("method") == "error"
                and event.get("params", {}).get("threadId") == thread
                and event.get("params", {}).get("turnId") == turn
                and event.get("params", {}).get("willRetry") is True]
            assert report["native_retry_events"], "Native did not expose its real retry decision"
            starts = [event for event in peer.events if event.get("method") == "item/started"
                      and event.get("params", {}).get("item", {}).get("type") == "mcpToolCall"]
            ends = [event for event in peer.events if event.get("method") == "item/completed"
                    and event.get("params", {}).get("item", {}).get("type") == "mcpToolCall"]
            assert len(starts) == len(ends) == 1, "Native repeated or omitted a real MCP invocation"
            assert not peer.callbacks, "An undeclared/client tool callback was dispatched"
            fault_rows = [row for row in upstream.requests if row.get("fault_kind")]
            retries = [row for row in upstream.requests if row.get("retry_history")]
            assert len(fault_rows) == len(retries) == 1
            assert fault_rows[0]["premise_established"] is True and fault_rows[0]["fault_emitted"] is True
            assert len(base.effects(root)) == 1
            report["fault_sequence"] = fault_rows[0]
            report["retry_history"] = retries[0]["retry_history"]
            report["persisted_result"] = base.persisted_output(root / "home", upstream.call_id)
            assert report["persisted_result"], "Native did not persist the actual tool output"
            await asyncio.to_thread(base.close_peer, peer)
            peer = None
            upstream.phase = "cold"
            peer = await asyncio.to_thread(base.new_peer, root, binary, catalog, descriptor, python, True)
            await asyncio.to_thread(peer.call, "thread/resume", {"threadId": thread})
            items = await asyncio.to_thread(peer.turn, thread, "Observe only; do not repeat the completed external append.")
            assert "COLD_OK" in base.text(items)
            report["cold_evidence"] = base.cold_evidence(upstream, root, peer, returned=True)
            report["cold_history"] = retry_history(upstream.requests[-1]["body"], upstream.call_id)
            await asyncio.to_thread(base.close_peer, peer)
            peer = None
        report["receipts"] = store.db.execute(
            "SELECT status,upstream_status,response_id,detail FROM receipts ORDER BY started").fetchall()
        statuses = [row[0] for row in report["receipts"]]
        assert statuses.count("adapter_error") == 1 and statuses[-1] == "completed"
        assert statuses.count("completed") == len(statuses) - 1, "Unexpected extra failure/retry"
        report["passed"] = True
    except BaseException as exc:
        report.update(error=repr(exc), traceback=traceback.format_exc())
        if peer:
            report["last_native_events"] = list(peer.events)
    finally:
        cleanup = []
        if peer:
            try:
                await asyncio.to_thread(base.close_peer, peer)
            except BaseException as exc:
                cleanup.append("peer:" + repr(exc))
        if hasattr(upstream, "proxy"):
            try:
                await upstream.close()
            except BaseException as exc:
                cleanup.append("upstream:" + repr(exc))
        if store:
            store.close()
        end = time.monotonic() + 5
        while time.monotonic() < end and any(base.matches(pid, birth) for pid, birth in observed):
            await asyncio.sleep(.05)
        stop.set()
        await watcher
        remaining = [row for (pid, birth), row in observed.items() if base.matches(pid, birth)]
        observation = base.fault_observation_issues(uncertain, rejected, observation_errors)
        report.update(observation)
        if observation["unknown_live"] or observation["observation_errors"]:
            cleanup.append("Process ownership observation remained unresolved")
        if remaining:
            cleanup.append("Owned subprocesses survived normal cleanup")
            for row in remaining:
                await asyncio.to_thread(base.terminate, row["pid"], row["birth"], timeout=2)
        report.update(effects=base.effects(root), owned_processes=list(observed.values()),
                      remaining_before_forced_cleanup=remaining, cleanup_errors=cleanup, finished=time.time())
        report["passed"] = report["passed"] and not cleanup
        base.save(root / "upstream-requests.json", upstream.requests)
        base.save(root / "verdict.json", report)
    return report


async def main(args):
    if not __debug__:
        raise SystemExit("Assertions must remain enabled")
    if os.name != "nt":
        raise SystemExit("This supplement currently qualifies Windows only")
    session = ctypes.c_ulong()
    assert ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    assert session.value > 0 and not ctypes.windll.shell32.IsUserAnAdmin(), (
        "Run in a Limited interactive Windows desktop")
    for key in ("CODEX_DEEPSEEK_TEST_WORKSPACE_LAYOUT", "CODEX_DEEPSEEK_TEST_SANDBOX_MODE"):
        os.environ.pop(key, None)
    entry = args.entry.resolve()
    release = entry.parent.parent.parent
    installation = json.loads((release / "installation.json").read_text(encoding="utf-8"))
    manifest = json.loads((release / "native-runtime.json").read_text(encoding="utf-8"))
    assert entry == base.console_entry(release / "venv")
    assert base.valid_release(release, installation["adapter_source_sha256"],
                              installation["model_catalog_sha256"], manifest)
    assert base.source_identity() == (installation["source_id"], installation["source_sha256"])
    loaded_adapter = Path(codex_deepseek.__file__).resolve().parent
    loaded_sources = {"src/codex_deepseek/" + path.relative_to(loaded_adapter).as_posix(): base.sha(path)
                      for path in loaded_adapter.rglob("*.py")}
    expected_sources = {name: digest for name, digest in installation["source_sha256"].items()
                        if name.startswith("src/codex_deepseek/") and name.endswith(".py")}
    assert loaded_sources == expected_sources, "Loaded QA adapter bytes differ from the immutable installation"
    package = Path(installation["native_package"])
    binary = package / "bin/codex.exe"
    http_binary = package / "bin/codex-http-transport.exe"
    python = base.python_entry(release / "venv")
    version = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=15)
    assert version.returncode == 0 and version.stdout.strip() == "codex-cli " + base.NATIVE_VERSION
    root = args.output.resolve() / str(uuid.uuid4())
    base.private_directory(root)
    dependencies = {name: base.sha(REPO / name) for name in VERIFIERS if name != "verify_mixed_stream_faults.py"}
    artifact = {"entry": str(entry), "entry_sha256": base.sha(entry),
                "installation_sha256": base.sha(release / "installation.json"),
                "native_manifest_sha256": base.sha(release / "native-runtime.json"),
                "native_cli_sha256": base.sha(binary), "http_binary_sha256": base.sha(http_binary),
                "source_id": installation["source_id"], "source_sha256": installation["source_sha256"],
                "loaded_adapter_source_sha256": loaded_sources,
                "verifier_sha256": base.sha(__file__), "dependency_sha256": dependencies,
                "controller": {"pid": os.getpid(), "birth": base.identity(os.getpid()),
                               "session": session.value, "high": False},
                "model_calls": 0, "real_credentials": 0, "standard_gate_set_changed": False}
    base.save(root / "artifact.json", artifact)
    print("EVIDENCE", root, flush=True)
    results = []
    for kind in KINDS:
        report = await case(root / kind, binary, http_binary, release / "catalog.json", python, kind)
        results.append({"kind": kind, "passed": report["passed"],
                        "verdict": str(root / kind / "verdict.json"),
                        "verdict_sha256": base.sha(root / kind / "verdict.json")})
        base.save(root / "results.json", results)
        print(json.dumps(results[-1]), flush=True)
    unchanged = (
        base.sha(entry) == artifact["entry_sha256"]
        and base.sha(release / "installation.json") == artifact["installation_sha256"]
        and base.sha(release / "native-runtime.json") == artifact["native_manifest_sha256"]
        and base.sha(binary) == artifact["native_cli_sha256"]
        and base.sha(http_binary) == artifact["http_binary_sha256"]
        and base.sha(__file__) == artifact["verifier_sha256"]
        and all(base.sha(REPO / name) == digest for name, digest in dependencies.items())
        and base.source_identity() == (installation["source_id"], installation["source_sha256"])
        and all(base.sha(loaded_adapter / name.removeprefix("src/codex_deepseek/")) == digest
                for name, digest in loaded_sources.items())
        and base.valid_release(release, installation["adapter_source_sha256"],
                               installation["model_catalog_sha256"], manifest))
    passed = unchanged and len(results) == len(KINDS) and all(row["passed"] is True for row in results)
    base.save(root / "complete.json", {"passed": passed, "artifact_unchanged": unchanged,
              "artifact_sha256": base.sha(root / "artifact.json"), "results": results,
              "model_calls": 0, "real_credentials": 0, "supplement": "mixed-stream-side-effects"})
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", type=Path, required=True, help="Exact new immutable installed entry")
    parser.add_argument("--output", type=Path, required=True, help="Fresh owned evidence parent")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
