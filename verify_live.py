"""Native app-server acceptance in an isolated, disposable workspace (uses paid API calls)."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tomllib
import uuid


QA_SANDBOX_ENV='CODEX_DEEPSEEK_TEST_SANDBOX_MODE'
QA_SANDBOX_MODES=('workspace-write','danger-full-access')

_WINDOWS_SETUP_SPEC=importlib.util.spec_from_file_location('cds_qa_windows_setup',Path(__file__).with_name('windows_acceptance_setup.py'))
_WINDOWS_SETUP=importlib.util.module_from_spec(_WINDOWS_SETUP_SPEC)
_WINDOWS_SETUP_SPEC.loader.exec_module(_WINDOWS_SETUP)

_WORKSPACE_SPEC=importlib.util.spec_from_file_location('cds_qa_workspace',Path(__file__).with_name('qa_workspace.py'))
_QA_WORKSPACE=importlib.util.module_from_spec(_WORKSPACE_SPEC)
_WORKSPACE_SPEC.loader.exec_module(_QA_WORKSPACE)
prepare_workspace=_QA_WORKSPACE.prepare_workspace
workspace_path=_QA_WORKSPACE.workspace_path


def qa_sandbox_mode(value=None):
    """Controller-only selection; the installed adapter strips TEST variables."""
    selected=os.environ.get(QA_SANDBOX_ENV,'workspace-write') if value is None else value
    if selected not in QA_SANDBOX_MODES:
        raise ValueError(f'Invalid QA sandbox mode: {selected!r}')
    return selected


def fixture_sandbox_mode(root, *, runtime_home=None):
    """Supply the QA default only when the fixture has no explicit override."""
    default=qa_sandbox_mode()
    path=(Path(runtime_home) if runtime_home is not None else Path(root)/'home')/'config.toml'
    content=path.read_text(encoding='utf-8') if path.exists() else ''
    configured=tomllib.loads(content).get('sandbox_mode')
    if configured is None:
        if runtime_home is not None:raise ValueError("An explicit runtime home must already declare its sandbox_mode; acceptance will not rewrite it")
        configured=default
        path.write_text('sandbox_mode='+json.dumps(default)+'\n'+content,encoding='utf-8')
    if configured not in (*QA_SANDBOX_MODES,'read-only'):
        raise ValueError(f'Invalid fixture sandbox mode: {configured!r}')
    return configured


def qa_command_sandbox(workspace):
    workspace=workspace_path(Path(workspace).parent,str(workspace))
    mode=qa_sandbox_mode()
    return ({'type':'dangerFullAccess'} if mode=='danger-full-access' else
            {'type':'workspaceWrite','writableRoots':[str(workspace)],'networkAccess':False})


def verify_fixture_sandbox(config,expected):
    value=config.get('config',{})
    actual=value.get('sandbox_mode')
    proof={'fixture_sandbox_mode':expected,'native_config_sandbox_mode':actual,
           'windows_backend_config':value.get('windows'),
           'scope':'Native configuration readback only; OS backend activation and filesystem enforcement require separate evidence.',
           'passed':actual==expected}
    return proof


class Peer:
    def __init__(self, root: Path, command=None, environment=None, server_args=None, *, runtime_home=None):
        self.root = root
        self.workspace = (prepare_workspace(root) if os.path.lexists(root/"work") or os.path.lexists(root/_QA_WORKSPACE.RECORD) else root/"work")
        self.runtime_home = Path(runtime_home).resolve() if runtime_home is not None else root / "home"
        selected=command or [os.environ.get('CODEX_DEEPSEEK_TEST_ENTRY') or str(Path.home()/".local/bin/codex-deepseek")]
        self.windows_prepare_enabled = _WINDOWS_SETUP.configure_fixture_home(
            self.runtime_home, entry=selected[0], explicit=runtime_home is not None)
        self.sandbox_mode = fixture_sandbox_mode(root,runtime_home=runtime_home)
        self.events = []
        self.queue = queue.Queue()
        self.counter = 0
        self.lock = threading.Lock()
        self.condition = threading.Condition()
        self.exited = False
        self.protocol_error = None
        self.protocol_evidence = root / f"protocol-error-{time.time_ns()}"
        self.callbacks = []
        self.dynamic_answers = {}
        self.stderr = (root / f"stderr-{time.time_ns()}.txt").open("w")
        env = dict(os.environ)
        env["CODEX_DEEPSEEK_HOME"] = str(self.runtime_home)
        env["CODEX_DEEPSEEK_TRACE_DIR"] = str(root / "adapter-traces")
        env.update(environment or {})
        if runtime_home is not None:env["CODEX_DEEPSEEK_HOME"] = str(self.runtime_home)
        env.pop(QA_SANDBOX_ENV,None)
        executable=Path(selected[0]).resolve()
        launch={'command':selected,'server_args':server_args or ['app-server','--strict-config'],'resolved_entry':str(executable),
                'entry_sha256':hashlib.sha256(executable.read_bytes()).hexdigest() if executable.is_file() else None,'runtime_home':env['CODEX_DEEPSEEK_HOME'],
                'fixture_sandbox_mode':self.sandbox_mode,'qa_default_sandbox_mode':qa_sandbox_mode(),
                'physical_workspace':str(self.workspace),'process_cwd':str(self.workspace if self.workspace.is_dir() else root)}
        (root/f'launch-{time.time_ns()}.json').write_text(json.dumps(launch,indent=2), encoding='utf-8')
        self.process = subprocess.Popen([*selected, *(server_args or ["app-server", "--strict-config"])],
                                        env=env, cwd=self.workspace if self.workspace.is_dir() else root,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                                        text=True, encoding="utf-8", bufsize=1)
        self.closed=False
        self.reader=threading.Thread(target=self.read, daemon=True);self.reader.start()
        try:
            initialized=self.call("initialize", {"clientInfo": {"name": "isolated_deepseek_acceptance", "version": "1.0"},
                                     "capabilities": {"experimentalApi": True}})
            if runtime_home is not None:
                actual=initialized.get("codexHome")
                proof={"requested_home":str(self.runtime_home),"native_home":actual,"passed":isinstance(actual,str) and Path(actual).resolve()==self.runtime_home}
                (root/f"qa-runtime-home-{time.time_ns()}.json").write_text(json.dumps(proof,indent=2),encoding="utf-8")
                if not proof["passed"]:raise AssertionError("Native runtime home differs from explicit pinned profile")
            self.send({"method": "initialized"})
            self.windows_prepare_evidence = _WINDOWS_SETUP.prepare_initialized_home(
                self.runtime_home, initialized, entry=selected[0])
            if self.windows_prepare_evidence is not None:
                (root/f"qa-windows-provision-{time.time_ns()}.json").write_text(
                    json.dumps(self.windows_prepare_evidence, indent=2), encoding="utf-8")
            self.sandbox_evidence=verify_fixture_sandbox(self.call('config/read',{'includeLayers':False}),self.sandbox_mode)
            (root/f'qa-sandbox-{time.time_ns()}.json').write_text(json.dumps(self.sandbox_evidence,indent=2),encoding='utf-8')
            if self.windows_prepare_enabled:
                backend = self.sandbox_evidence.get("windows_backend_config") or {}
                if backend.get("sandbox") != "elevated":
                    raise AssertionError("Native fixture did not read the declared elevated Windows backend")
            if not self.sandbox_evidence['passed']:
                raise AssertionError('Native fixture sandbox configuration differs from the selected QA mode')
        except BaseException as primary:
            before=self.process.poll();cleanup=None
            try:self.close()
            except BaseException as error:cleanup=error
            receipt={'initialized':False,'primary_error_type':type(primary).__name__,
                     'primary_error':str(primary),'returncode_before_cleanup':before,
                     'returncode_after_cleanup':self.process.returncode,
                     'stderr_file':Path(self.stderr.name).name,
                     'stderr_sha256':hashlib.sha256(Path(self.stderr.name).read_bytes()).hexdigest(),
                     'cleanup_error':None if cleanup is None else str(cleanup)}
            try:
                (root/f'startup-failure-{time.time_ns()}.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
            except BaseException as error:
                raise BaseExceptionGroup('Native startup and evidence persistence failed',
                                         [primary,*([cleanup] if cleanup is not None else []),error])
            if cleanup is not None:raise BaseExceptionGroup('Native startup and cleanup failed',[primary,cleanup])
            raise

    def send(self, message):
        message = _QA_WORKSPACE.workspace_request(self.root, message)
        with self.lock:
            encoded=json.dumps({"jsonrpc": "2.0", **message})
            with (self.root/"client-requests.jsonl").open("a") as trace:trace.write(encoded+"\n")
            self.process.stdin.write(encoded + "\n")
            self.process.stdin.flush()

    def read(self):
        def object_pairs(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError("Duplicate JSON member: "+key)
                result[key]=value
            return result
        def constant(value):
            raise ValueError("Invalid JSON constant: "+value)
        raw = b""
        try:
            stream = getattr(self.process.stdout, "buffer", self.process.stdout)
            for raw in stream:
                if isinstance(raw, str):raw = raw.encode("utf-8")
                if not raw.strip():continue
                item = json.loads(raw.decode("utf-8"),object_pairs_hook=object_pairs,parse_constant=constant)
                if not isinstance(item, dict) or not ("method" in item or "id" in item):
                    raise ValueError("Native stdout must contain an object with method or id")
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
        except Exception as error:
            detail = {"type":type(error).__name__, "message":str(error),
                      "raw_path":str(self.protocol_evidence / "stdout.raw"),
                      "metadata_path":str(self.protocol_evidence / "error.json")}
            try:
                from codex_deepseek.platforms import private_directory
                private_directory(self.protocol_evidence)
                with (self.protocol_evidence / "stdout.raw").open("xb") as evidence:evidence.write(raw)
                detail["raw_sha256"] = hashlib.sha256(raw).hexdigest()
                with (self.protocol_evidence / "error.json").open("x",encoding="utf-8") as evidence:
                    json.dump(detail,evidence,ensure_ascii=False,indent=2)
            except Exception as evidence_error:
                detail["evidence_error"] = str(evidence_error)
            with self.condition:
                self.protocol_error = detail
                self.condition.notify_all()
        finally:
            self.queue.put(None)
            with self.condition:
                self.exited = True
                self.condition.notify_all()

    def wait(self, predicate, start=0, timeout=180):
        end = time.monotonic() + timeout
        with self.condition:
            while time.monotonic() < end:
                if getattr(self, "protocol_error", None) is not None:
                    raise RuntimeError(f"Native app-server protocol error: {self.protocol_error}")
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
        params={"cwd": str(getattr(self,"workspace",self.root / "work")), "approvalPolicy": "on-request", "sandbox": self.sandbox_mode, **options}
        result=self.call("thread/start",params)
        with (self.root/'qa-thread-sandbox.jsonl').open('a',encoding='utf-8') as evidence:
            evidence.write(json.dumps({'thread':result['thread']['id'],'requested_sandbox_mode':params['sandbox'],
                                       'fixture_sandbox_mode':self.sandbox_mode,'native_response_sandbox':result.get('sandbox'),
                                       'scope':'Native policy selection, not OS enforcement proof.'})+'\n')
        return result['thread']['id']

    def start(self, thread, text, **options):
        start = len(self.events)
        response = self.call("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}], **options})
        return response["turn"]["id"], start

    def complete(self, thread, start, timeout=180, *, expected_status="completed", turn_id=None):
        """Accept only the requested native terminal, never a merely stopped turn."""
        if expected_status not in {"completed", "interrupted"}:
            raise ValueError("Acceptance terminal must be completed or explicitly interrupted")
        if expected_status == "interrupted" and (not isinstance(turn_id, str) or not turn_id):
            raise ValueError("Interrupt acceptance requires the exact requested turn ID")
        if turn_id is not None and (not isinstance(turn_id, str) or not turn_id):
            raise ValueError("Invalid expected native turn ID")

        def terminal(value):
            params = value.get("params", {})
            turn = params.get("turn", {}) if isinstance(params, dict) else {}
            return (value.get("method") == "turn/completed"
                    and isinstance(params, dict) and params.get("threadId") == thread
                    and (turn_id is None or isinstance(turn, dict) and turn.get("id") == turn_id))

        event = self.wait(terminal, start, timeout)
        turn = event["params"].get("turn")
        if (not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not turn["id"]
                or turn.get("status") != expected_status or turn.get("error") is not None):
            raise RuntimeError(f"Native turn did not reach error-free {expected_status}: {turn!r}")
        # A subsequent autonomous/queued turn can already be arriving. Return
        # only items owned by the terminal we just verified, before that event.
        observed = self.events[start:]
        end = next((index for index, value in enumerate(observed) if value is event), None)
        if end is None:
            raise RuntimeError("Native terminal is missing from the recorded event history")
        return [value["params"]["item"] for value in observed[:end]
                if value.get("method") == "item/completed"
                and isinstance(value.get("params"), dict)
                and value["params"].get("threadId") == thread
                and value["params"].get("turnId") == turn["id"]]

    def turn(self, thread, text, **options):
        turn_id, start = self.start(thread, text, **options)
        return self.complete(thread, start, turn_id=turn_id)

    def close(self):
        if not self.closed:
            self.closed=True
            errors=[]
            try:
                if self.process.poll() is None:self.process.terminate()
            except Exception as error:errors.append(("terminate",str(error)))
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:self.process.kill()
                except Exception as error:errors.append(("kill",str(error)))
                try:self.process.wait(timeout=10)
                except Exception as error:errors.append(("wait_after_kill",str(error)))
            except Exception as error:errors.append(("wait",str(error)))
            try:
                self.reader.join(timeout=3)
                if self.reader.is_alive():errors.append(("reader","Native stdout reader did not exit"))
            except Exception as error:errors.append(("reader_join",str(error)))
            for name,stream in [("stdin",self.process.stdin),("stdout",self.process.stdout),("stderr",self.stderr)]:
                # Closing a buffered stream under an active reader can itself block.
                if name=="stdout" and self.reader.is_alive():continue
                try:stream.close()
                except Exception as error:errors.append((name+"_close",str(error)))
            try:
                (self.root / f"events-{time.time_ns()}.json").write_text(json.dumps(self.events, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception as error:errors.append(("events_save",str(error)))
            self.close_errors=errors
        if getattr(self,"protocol_error",None) is not None:
            raise RuntimeError(f"Native app-server protocol error: {self.protocol_error}; cleanup: {getattr(self,'close_errors',[])}")
        if getattr(self,"close_errors",None):
            raise RuntimeError(f"Native acceptance cleanup failed: {self.close_errors}")


def text(items):
    return "\n".join(item.get("text", "") for item in items if item.get("type") == "agentMessage")


def prepare(root,sandbox_mode=None):
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
''', encoding='utf-8')
    config = f'''sandbox_mode = {json.dumps(qa_sandbox_mode(sandbox_mode))}
model_reasoning_effort = "low"
web_search = "live"
[features]
default_mode_request_user_input = true
multi_agent_v2 = true
[mcp_servers.acceptance]
command = {json.dumps(sys.executable)}
args = [{json.dumps(str(mcp))}, {json.dumps(str(root/'mcp-called.txt'))}]
default_tools_approval_mode = "approve"
'''
    (root / "home/config.toml").write_text(config, encoding='utf-8')


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
        results.append({"name": name, "passed": bool(condition), "sandbox_mode":peer.sandbox_mode, "detail": detail})
        (root / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
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
        callback_start=len(peer.callbacks)
        items = peer.turn(thread, "Do not delegate. Use request_user_input to ask which fixture color to choose, with Blue as the first option and Green second. Wait for the answer, then repeat the selected option.")
        actual_questions=[e for e in peer.callbacks[callback_start:] if e.get('method')=='item/tool/requestUserInput' and e.get('params',{}).get('threadId')==thread and e['params'].get('questions')]
        passed("native_user_input", bool(actual_questions) and "Blue" in text(items), {'callbacks':actual_questions,'answer':text(items)})
        items = peer.turn(thread, "Do not delegate. Search the live web for the official DeepSeek API documentation website using the built-in web search tool, then cite its URL.")
        passed("native_web_search_event", any(i.get('type')=='webSearch' for i in items) and "deepseek" in text(items).lower(), text(items))
        long_thread = peer.new()
        turn, start = peer.start(long_thread, "Do not delegate. Start a shell command that sleeps for 120 seconds before printing SHOULD_NOT_FINISH. Wait for it to finish.")
        peer.wait(lambda x: x.get('method')=='item/started' and x.get('params',{}).get('threadId')==long_thread and x.get('params',{}).get('item',{}).get('type')=='commandExecution', start)
        peer.call("turn/interrupt", {"threadId": long_thread, "turnId": turn})
        peer.complete(long_thread, start, expected_status='interrupted', turn_id=turn)
        items = peer.turn(long_thread, "Do not use tools or delegate. Reply RECOVERED.")
        passed("interrupt_and_continue", "RECOVERED" in text(items), text(items))
        if args.agents:
            agents_thread = peer.new()
            items = peer.turn(agents_thread, "This is an explicitly authorized isolated native subagent test. Spawn exactly one subagent with the task: compute 19*23, do not spawn descendants or use tools, and reply CHILD-437. Wait for its completion using the native lifecycle tool. Then report its answer. Do not use shell as a substitute for delegation.")
            children = []
            for rollout in (root/'home/sessions').rglob('*.jsonl'):
                records = [json.loads(line) for line in rollout.read_text(encoding='utf-8').splitlines()]
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
