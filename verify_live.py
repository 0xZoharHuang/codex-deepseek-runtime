"""Native app-server acceptance in an isolated, disposable workspace (uses paid API calls)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid


class Peer:
    def __init__(self, root: Path, command=None, environment=None, server_args=None):
        self.root = root
        self.events = []
        self.queue = queue.Queue()
        self.counter = 0
        self.lock = threading.Lock()
        self.condition = threading.Condition()
        self.exited = False
        self.callbacks = []
        self.dynamic_answers = {}
        self.stderr = (root / f"stderr-{time.time_ns()}.txt").open("w")
        env = dict(os.environ)
        env["CODEX_DEEPSEEK_HOME"] = str(root / "home")
        env["CODEX_DEEPSEEK_TRACE_DIR"] = str(root / "adapter-traces")
        env.update(environment or {})
        selected=command or [os.environ.get('CODEX_DEEPSEEK_TEST_ENTRY') or str(Path.home()/".local/bin/codex-deepseek")]
        executable=Path(selected[0]).resolve()
        launch={'command':selected,'server_args':server_args or ['app-server','--strict-config'],'resolved_entry':str(executable),
                'entry_sha256':hashlib.sha256(executable.read_bytes()).hexdigest() if executable.is_file() else None,'runtime_home':env['CODEX_DEEPSEEK_HOME']}
        (root/f'launch-{time.time_ns()}.json').write_text(json.dumps(launch,indent=2))
        self.process = subprocess.Popen([*selected, *(server_args or ["app-server", "--strict-config"])],
                                        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                                        text=True, bufsize=1)
        self.closed=False
        self.reader=threading.Thread(target=self.read, daemon=True);self.reader.start()
        try:
            self.call("initialize", {"clientInfo": {"name": "isolated_deepseek_acceptance", "version": "1.0"},
                                     "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized"})
        except BaseException:self.close();raise

    def send(self, message):
        with self.lock:
            encoded=json.dumps({"jsonrpc": "2.0", **message})
            with (self.root/"client-requests.jsonl").open("a") as trace:trace.write(encoded+"\n")
            self.process.stdin.write(encoded + "\n")
            self.process.stdin.flush()

    def read(self):
        for line in self.process.stdout:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            with self.condition:
                self.events.append(item)
                self.condition.notify_all()
            if "id" in item and "method" in item:
                self.callbacks.append(item)
                method = item["method"]
                if method == "item/tool/call" and (item['params'].get('namespace'), item['params']['tool']) in self.dynamic_answers:
                    answer = self.dynamic_answers[(item['params'].get('namespace'), item['params']['tool'])]
                    self.send({'id': item['id'], 'result': {'contentItems': [{'type':'inputText','text':answer}], 'success':True}})
                elif method == "item/tool/requestUserInput":
                    answers = {}
                    for question in item["params"]["questions"]:
                        choice = question.get("options", [{}])[0].get("label", "Blue")
                        answers[question["id"]] = {"answers": [choice]}
                    self.send({"id": item["id"], "result": {"answers": answers}})
                elif method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                    # Acceptance never approves escaping its sandbox.
                    self.send({"id": item["id"], "result": {"decision": "decline"}})
                else:
                    self.send({"id": item["id"], "error": {"code": -32601, "message": "Fixture declines unsupported interaction"}})
            self.queue.put(item)
        self.queue.put(None)
        with self.condition:
            self.exited = True
            self.condition.notify_all()

    def wait(self, predicate, start=0, timeout=180):
        end = time.monotonic() + timeout
        with self.condition:
            while time.monotonic() < end:
                for event in self.events[start:]:
                    if predicate(event):
                        return event
                start = len(self.events)
                if self.exited:
                    raise RuntimeError("Native app-server exited unexpectedly")
                self.condition.wait(timeout=max(0.1, end-time.monotonic()))
        raise TimeoutError("Native acceptance event timed out")

    def call(self, method, params):
        with self.lock:
            self.counter += 1
            key = self.counter
        self.send({"id": key, "method": method, "params": params})
        event = self.wait(lambda x: x.get("id") == key and "method" not in x)
        if "error" in event:
            raise RuntimeError(f"{method}: {event['error']}")
        return event.get("result", {})

    def new(self, **options):
        return self.call("thread/start", {"cwd": str(self.root / "work"), "approvalPolicy": "on-request", "sandbox": "workspace-write", **options})["thread"]["id"]

    def start(self, thread, text, **options):
        start = len(self.events)
        response = self.call("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}], **options})
        return response["turn"]["id"], start

    def complete(self, thread, start, timeout=180):
        event = self.wait(lambda x: x.get("method") == "turn/completed" and x.get("params", {}).get("threadId") == thread, start, timeout)
        if event["params"]["turn"]["status"] == "failed":
            raise RuntimeError(str(event["params"]["turn"].get("error")))
        return [x["params"]["item"] for x in self.events[start:] if x.get("method") == "item/completed" and x.get("params", {}).get("threadId") == thread]

    def turn(self, thread, text, **options):
        _, start = self.start(thread, text, **options)
        return self.complete(thread, start)

    def close(self):
        if self.closed:return
        self.closed=True
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=3)
        self.stderr.close()
        (self.root / f"events-{time.time_ns()}.json").write_text(json.dumps(self.events, ensure_ascii=False, indent=2))


def text(items):
    return "\n".join(item.get("text", "") for item in items if item.get("type") == "agentMessage")


def prepare(root):
    (root / "home").mkdir(parents=True)
    (root / "work").mkdir()
    mcp = root / "fixture_mcp.py"
    mcp.write_text('''import sys,json,pathlib
for line in sys.stdin:
 x=json.loads(line)
 if 'id' not in x:continue
 method=x.get('method')
 if method=='initialize':r={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'acceptance-fixture','version':'1'}}
 elif method=='tools/list':r={'tools':[{'name':'read_nonce','description':'Read the acceptance nonce from the fixture.','inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}}]}
 elif method=='tools/call':
  pathlib.Path(sys.argv[1]).write_text('MCP_CALLED')
  r={'content':[{'type':'text','text':'MCP-9f2a17'}]}
 else:r={}
 print(json.dumps({'jsonrpc':'2.0','id':x['id'],'result':r}),flush=True)
''')
    config = f'''model_reasoning_effort = "low"
web_search = "live"
[features]
default_mode_request_user_input = true
multi_agent_v2 = true
[mcp_servers.acceptance]
command = {json.dumps(sys.executable)}
args = [{json.dumps(str(mcp))}, {json.dumps(str(root/'mcp-called.txt'))}]
default_tools_approval_mode = "approve"
'''
    (root / "home/config.toml").write_text(config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agents", action="store_true", help="Run the explicitly authorized native subagent fixture")
    args = parser.parse_args()
    root = args.output.resolve() / str(uuid.uuid4())
    prepare(root)
    results = []
    peer = Peer(root)
    def passed(name, condition, detail):
        results.append({"name": name, "passed": bool(condition), "detail": detail})
        (root / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        if not condition:
            raise AssertionError(name)
    try:
        thread = peer.new()
        items = peer.turn(thread, "Do not delegate. Remember marker SESSION-3b71 and project color teal. Reply ACK without tools.")
        passed("native_thread", bool(items), thread)
        items = peer.turn(thread, "Do not delegate. Discover and call the acceptance MCP read_nonce tool. Repeat exactly the nonce it returns.")
        passed("mcp_discovery_and_call", (root/'mcp-called.txt').exists() and "MCP-9f2a17" in text(items), text(items))
        start = len(peer.events)
        peer.call("thread/compact/start", {"threadId": thread})
        compacted = peer.complete(thread, start)
        items = peer.turn(thread, "Without tools or delegation, give the exact session marker and project color from before compaction.")
        passed("local_compaction", any(i.get('type')=='contextCompaction' for i in compacted) and "SESSION-3b71" in text(items) and "teal" in text(items), text(items))
        fork = peer.call("thread/fork", {"threadId": thread})["thread"]["id"]
        items = peer.turn(fork, "Without tools or delegation, repeat the marker and color you inherited.")
        passed("native_fork", "SESSION-3b71" in text(items) and "teal" in text(items), text(items))
        items = peer.turn(thread, "Do not delegate. Use request_user_input to ask which fixture color to choose, with Blue as the first option and Green second. Wait for the answer, then repeat the selected option.")
        passed("native_user_input", bool(peer.callbacks) and "Blue" in text(items), text(items))
        items = peer.turn(thread, "Do not delegate. Search the live web for the official DeepSeek API documentation website using the built-in web search tool, then cite its URL.")
        passed("native_web_search_event", any(i.get('type')=='webSearch' for i in items) and "deepseek" in text(items).lower(), text(items))
        long_thread = peer.new()
        turn, start = peer.start(long_thread, "Do not delegate. Start a shell command that sleeps for 120 seconds before printing SHOULD_NOT_FINISH. Wait for it to finish.")
        peer.wait(lambda x: x.get('method')=='item/started' and x.get('params',{}).get('threadId')==long_thread and x.get('params',{}).get('item',{}).get('type')=='commandExecution', start)
        peer.call("turn/interrupt", {"threadId": long_thread, "turnId": turn})
        peer.complete(long_thread, start)
        items = peer.turn(long_thread, "Do not use tools or delegate. Reply RECOVERED.")
        passed("interrupt_and_continue", "RECOVERED" in text(items), text(items))
        if args.agents:
            agents_thread = peer.new()
            items = peer.turn(agents_thread, "This is an explicitly authorized isolated native subagent test. Spawn exactly one subagent with the task: compute 19*23, do not spawn descendants or use tools, and reply CHILD-437. Wait for its completion using the native lifecycle tool. Then report its answer. Do not use shell as a substitute for delegation.")
            children = []
            for rollout in (root/'home/sessions').rglob('*.jsonl'):
                records = [json.loads(line) for line in rollout.read_text().splitlines()]
                meta = records[0]['payload']
                source = meta.get('source')
                spawn = source.get('subagent',{}).get('thread_spawn',{}) if isinstance(source,dict) else {}
                if spawn.get('parent_thread_id') == agents_thread:
                    payloads = [record.get('payload',{}) for record in records if record.get('type')=='response_item']
                    children.append({'id':meta['id'], 'provider':meta.get('model_provider'),
                                     'called_tools':any(p.get('type') in {'function_call','custom_tool_call'} for p in payloads),
                                     'task_received':any('Compute 19' in json.dumps(p) or 'compute 19' in json.dumps(p) for p in payloads if p.get('role')=='assistant' or p.get('type')=='agent_message')})
            passed("native_subagent", len(children)==1 and children[0]['task_received'] and not children[0]['called_tools'] and "CHILD-437" in text(items), {"children": children, "answer": text(items)})
        peer.close()
        peer = Peer(root)
        peer.call("thread/resume", {"threadId": thread})
        items = peer.turn(thread, "Without tools or delegation, repeat our original session marker and project color.")
        passed("cold_resume", "SESSION-3b71" in text(items) and "teal" in text(items), text(items))
    finally:
        peer.close()
        print("EVIDENCE", str(root), flush=True)


if __name__ == "__main__":
    main()
