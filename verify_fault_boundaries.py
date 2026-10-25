"""Windows native fault boundaries against a same-version reference, with zero model calls."""
import argparse,asyncio,hashlib,json,os,signal,sys,time,traceback,uuid,subprocess,ctypes
from pathlib import Path
from contextlib import closing
import sqlite3
import aiohttp
from aiohttp import web
import psutil
REPO=Path(__file__).resolve().parent
MCP_FIXTURE=REPO/"fixtures/fault-boundaries/mcp_append.py"
AUTH_NATIVE_ATTEMPTS={}
SCENARIOS=("truncated","tcp-reset","auth401","auth403","effect-before","effect-after","effect-worker")
def selected_scenarios(kinds):
    selected=list(SCENARIOS if kinds is None else kinds)
    if len(selected)!=len(set(selected)) or any(kind not in SCENARIOS for kind in selected):
        raise ValueError("Fault scenarios must be unique known names")
    if not selected:raise ValueError("At least one fault scenario is required")
    return selected
def complete_scenario_set(kinds):
    return len(kinds)==len(SCENARIOS) and len(set(kinds))==len(SCENARIOS) and set(kinds)==set(SCENARIOS)

VERIFIERS=("verify_fault_boundaries.py","verify_live.py","windows_acceptance_setup.py","qa_workspace.py",
           "install.py","tests/test_native_raw_http.py","tests/test_native_payload_retry.py",
           "tests/test_network.py","tests/test_image_transport.py","fixtures/fault-boundaries/mcp_append.py","ci/qualify_native.py")
sys.path[:0]=[str(REPO),str(REPO/"tests")]
from verify_live import Peer,text
from test_native_raw_http import certificate
from test_native_payload_retry import events as final_events
from codex_deepseek.cli import native_environment,NATIVE_VERSION
from codex_deepseek.service import ensure,read_descriptor,healthy
from codex_deepseek.processes import identity,matches,terminate
from codex_deepseek.platforms import private_directory
from codex_deepseek.state import session_lines
from install import console_entry,python_entry,valid_release,source_identity
from ci.qualify_native import _observe_process_family

KEY1="synthetic-fault-only-invalid"
KEY2="synthetic-fault-only-restored"
MODEL="deepseek/deepseek-v4.1-flash-1017"
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
def effects(root):
    p=root/"effects.jsonl";return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
async def until(predicate,seconds=30):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=predicate()
        if value:return value
        await asyncio.sleep(.02)
    raise TimeoutError("Owned fixture state barrier was not reached")
def find_tool(specs):
    for spec in specs:
        if spec.get("type")=="namespace":
            found=find_tool(spec.get("tools",[]))
            if found:return found[0],spec["name"]
        elif spec.get("type")=="function" and not spec.get("defer_loading") and "append_once" in spec.get("name",""):
            return spec["name"],None
    return None
def find_output(body,call):
    return [x for x in body.get("input",[]) if isinstance(x,dict) and x.get("type")=="function_call_output" and x.get("call_id")==call]
def persisted_items(home,call):
    found=[]
    paths=list((home/"sessions").glob("**/*.jsonl"))+list((home/"sessions").glob("**/*.jsonl.zst"))
    for p in paths:
        for line in session_lines(p):
            row=json.loads(line);payload=row.get("payload",{})
            if row.get("type")=="response_item" and payload.get("call_id")==call:
                found.append({"path":str(p),"record":row})
    return found
def persisted_output(home,call):
    return next((r for r in persisted_items(home,call) if r["record"]["payload"].get("type")=="function_call_output"),None)
def cold_evidence(upstream,root,peer,*,returned):
    body=upstream.requests[-1]["body"];call=upstream.call_id
    calls=[r for r in body.get("input",[]) if isinstance(r,dict) and r.get("type") in {"function_call","custom_tool_call"} and r.get("call_id")==call]
    outputs=find_output(body,call)
    assert calls,"Cold resumed native request lost the original tool call"
    if returned:assert outputs and "APPENDED:"+call in json.dumps(outputs),"Cold resumed native request lost the actual tool result"
    else:assert "APPENDED:"+call not in json.dumps(outputs),"Unreturned MCP result was fabricated as successful"
    tool_events=[e for e in peer.events if e.get("method") in {"item/started","item/completed"} and e.get("params",{}).get("item",{}).get("type")=="mcpToolCall"]
    assert not tool_events,"Cold resumed native process dispatched MCP again"
    assert len(effects(root))==1 and effects(root)[0]["marker"]==call
    return {"call":calls,"outputs":outputs,"mcp_events":tool_events,"ledger":effects(root),"persisted":persisted_items(root/"home",call)}

def native_partial_delta(event,thread,turn):
    params=event.get("params",{})
    return (event.get("method")=="item/agentMessage/delta" and params.get("threadId")==thread
            and params.get("turnId")==turn and params.get("delta")=="PARTIAL")


def terminal(peer,thread,turn,start):
    return peer.wait(lambda e:e.get("method")=="turn/completed" and e.get("params",{}).get("threadId")==thread and e.get("params",{}).get("turn",{}).get("id")==turn,start,timeout=90)
def close_peer(peer,*,crashed=False):
    if not crashed and peer.process.poll() is None:
        peer.process.stdin.close();peer.process.wait(timeout=20);peer.reader.join(5)
        if peer.process.returncode!=0 or peer.reader.is_alive():raise AssertionError("Native EOF failed")
    peer.close()

class Upstream:
    def __init__(self,root,kind):
        self.root=root;self.kind=kind;self.requests=[];self.tunnels=[];self.handlers=set()
        self.call_id="call_effect_"+uuid.uuid4().hex;self.phase="initial";self.fault_sent=False
        self.tool_result_seen=asyncio.Event();self.hold_release=asyncio.Event();self.disconnected=asyncio.Event();self.native_partial_seen=asyncio.Event()
    async def handle(self,request):
        body=await request.json()
        authorization=request.headers.get("Authorization")
        if authorization not in {"Bearer "+KEY1,"Bearer "+KEY2}:raise AssertionError("Unexpected credential entered owned fixture")
        row={"at":time.time(),"body":body,"credential":("restored" if authorization=="Bearer "+KEY2 else "initial")}
        self.requests.append(row);save(self.root/"upstream-requests.json",self.requests)
        if self.kind in {"auth401","auth403"} and row["credential"]=="initial":
            row["fault"]=self.kind;return web.json_response({"error":{"message":"synthetic credential rejected"}},status=int(self.kind[-3:]))
        if self.kind in {"truncated","tcp-reset"} and not self.fault_sent:
            self.fault_sent=True
            response=web.StreamResponse(headers={"Content-Type":"text/event-stream"});await response.prepare(request)
            await response.write(b'data: {"type":"response.created","response":{"id":"resp_fault","status":"in_progress","output":[]}}\n\n')
            await response.write(b'data: {"type":"response.output_item.added","output_index":0,"item":{"id":"message_fault","type":"message","role":"assistant","phase":"final_answer","status":"in_progress","content":[]}}\n\n')
            await response.write(b'data: {"type":"response.content_part.added","item_id":"message_fault","output_index":0,"content_index":0,"part":{"type":"output_text","text":"","annotations":[]}}\n\n')
            await response.write(b'data: {"type":"response.output_text.delta","item_id":"message_fault","output_index":0,"content_index":0,"delta":"PARTIAL"}\n\n')
            row["fault"]=self.kind
            if self.kind=="tcp-reset":
                await asyncio.wait_for(self.native_partial_seen.wait(),30)
                row["reset_after_native_partial"]=True
                save(self.root/"upstream-requests.json",self.requests)
                request.transport.abort()
                return response
            await response.write_eof();return response
        if self.kind.startswith("effect"):
            if self.phase=="cold":return web.Response(text=final_events("COLD_OK"),content_type="text/event-stream")
            if find_output(body,self.call_id):
                row["tool_result"]=find_output(body,self.call_id);self.tool_result_seen.set()
                if self.kind=="effect-worker" and not self.fault_sent:
                    self.fault_sent=True
                    response=web.StreamResponse(headers={"Content-Type":"text/event-stream"});await response.prepare(request)
                    await response.write(b'data: {"type":"response.created","response":{"id":"resp_worker_fault","status":"in_progress","output":[]}}\n\n')
                    await response.write(b'data: {"type":"response.output_item.added","output_index":0,"item":{"id":"worker_message","type":"message","role":"assistant","phase":"final_answer","status":"in_progress","content":[]}}\n\n')
                    await response.write(b'data: {"type":"response.content_part.added","item_id":"worker_message","output_index":0,"content_index":0,"part":{"type":"output_text","text":"","annotations":[]}}\n\n')
                    await response.write(b'data: {"type":"response.output_text.delta","item_id":"worker_message","output_index":0,"content_index":0,"delta":"WORKER_PENDING"}\n\n')
                    try:await self.hold_release.wait()
                    except asyncio.CancelledError:self.disconnected.set();raise
                    return response
                return web.Response(text=final_events("EFFECT_OK"),content_type="text/event-stream")
            if self.kind=="effect-before" and self.fault_sent:
                return web.Response(text=final_events("RECOVERED_WITHOUT_EFFECT"),content_type="text/event-stream")
            found=find_tool(body.get("tools",[]))
            if not found:
                for item in body.get("input",[]):
                    if isinstance(item,dict) and item.get("type") in {"tool_search_output","additional_tools"}:
                        found=find_tool(item.get("tools",[]))
                        if found:break
            if not found:
                discovery=[spec for spec in body.get("tools",[]) if spec.get("type")=="function" and spec.get("name")=="tool_search"]
                assert len(discovery)==1,"Native tool search must be actually declared"
                call={"type":"function_call","id":"search_"+uuid.uuid4().hex,"call_id":"discover_"+uuid.uuid4().hex,"name":discovery[0]["name"],"arguments":json.dumps({"query":"nonidempotent append_once","limit":3}),"status":"completed"}
            else:
                name,namespace=found
                call={"type":"function_call","id":"fc_effect","call_id":self.call_id,"name":name,"arguments":json.dumps({"marker":self.call_id}),"status":"completed"}
                if namespace:call["namespace"]=namespace
            rid="resp_"+uuid.uuid4().hex
            eventlist=[{"type":"response.created","response":{"id":rid,"status":"in_progress","output":[]}}]
            if found and self.kind=="effect-before":
                self.fault_sent=True
                eventlist+=[{"type":"response.output_item.added","output_index":0,"item":{**call,"arguments":"","status":"in_progress"}},
                            {"type":"response.function_call_arguments.delta","item_id":"fc_effect","output_index":0,"delta":'{"marker":'}]
            else:eventlist += [{"type":"response.output_item.done","output_index":0,"item":call},{"type":"response.completed","response":{"id":rid,"status":"completed","output":[call],"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}]
            return web.Response(text="".join("data: "+json.dumps(e)+"\n\n" for e in eventlist),content_type="text/event-stream")
        return web.Response(text=final_events("NETWORK_RECOVERED"),content_type="text/event-stream")
    async def start(self):
        ca,sslctx=certificate(self.root)
        app=web.Application();app.router.add_route("*","/{path:.*}",self.handle)
        self.runner=web.AppRunner(app,handler_cancellation=True,shutdown_timeout=.2)
        await self.runner.setup();await web.TCPSite(self.runner,"127.0.0.1",0,ssl_context=sslctx).start()
        origin_port=self.runner.addresses[0][1]
        async def tunnel(reader,writer):
            task=asyncio.current_task();self.handlers.add(task);upstream=None
            try:
                header=await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"),5)
                if not header.startswith(b"CONNECT openrouter.ai:443 "):raise AssertionError("Fixture attempted an unowned host")
                self.tunnels.append({"at":time.time(),"host":"openrouter.ai:443"})
                source,upstream=await asyncio.open_connection("127.0.0.1",origin_port)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n");await writer.drain()
                async def copy(src,dst):
                    while data:=await src.read(65536):dst.write(data);await dst.drain()
                jobs=[asyncio.create_task(copy(reader,upstream)),asyncio.create_task(copy(source,writer))]
                try:await asyncio.wait(jobs,return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for job in jobs:job.cancel()
                    await asyncio.gather(*jobs,return_exceptions=True)
            except (ConnectionError,asyncio.IncompleteReadError,asyncio.CancelledError):pass
            finally:
                if upstream:upstream.close()
                writer.close();self.handlers.discard(task)
        self.proxy=await asyncio.start_server(tunnel,"127.0.0.1",0)
        self.proxy_url="http://127.0.0.1:"+str(self.proxy.sockets[0].getsockname()[1])
        return ca
    async def close(self):
        self.hold_release.set();self.proxy.close();await self.proxy.wait_closed()
        for task in list(self.handlers):task.cancel()
        await asyncio.gather(*list(self.handlers),return_exceptions=True);await self.runner.cleanup()

def config(root,catalog,port,token,python,mcp):
    home=root/"home"
    value='model='+json.dumps(MODEL)+'\nmodel_provider="fault_fixture"\nmodel_catalog_json='+json.dumps(str(catalog))+'\nmodel_reasoning_effort="low"\nsandbox_mode="read-only"\nweb_search="disabled"\n'
    value+='[features]\ncode_mode=false\ncode_mode_only=false\nplugins=false\napps=false\nmemories=false\nmulti_agent=false\n'
    value+='[model_providers.fault_fixture]\nname="Owned fault fixture"\nbase_url='+json.dumps(f"http://127.0.0.1:{port}/v1")+'\nenv_key="FAULT_LOCAL_TOKEN"\nwire_api="responses"\nsupports_websockets=false\n'
    if mcp:
        value+='[mcp_servers.nonidempotent]\ncommand='+json.dumps(str(python))+'\nargs='+json.dumps(["-I",str(MCP_FIXTURE),str(root)])+'\ndefault_tools_approval_mode="approve"\n'
    (home/"config.toml").write_text(value,encoding="utf-8")
def new_peer(root,binary,catalog,descriptor,python,mcp):
    config(root,catalog,descriptor["port"],descriptor["token"],python,mcp)
    env=native_environment(root/"home","",binary,install_shims=False)
    for key in ["CODEX_DEEPSEEK_POLICY_HOME","CODEX_DEEPSEEK_BRIDGE_TOKEN","CODEX_DEEPSEEK_BRIDGE_PORT",
                "CODEX_DEEPSEEK_TEST_WINDOWS_PREPARE_MANIFEST","CODEX_DEEPSEEK_TEST_WINDOWS_PREPARE_SHA256"]:
        env.pop(key,None)
    env["FAULT_LOCAL_TOKEN"]=descriptor["token"]
    return Peer(root,command=[str(binary)],environment=env)


def collect_fault_processes(controller,family,observed,uncertain,rejected,errors):
    """Use the existing proven-edge observer; this controller is not a child."""
    _observe_process_family(family,uncertain,rejected,errors)
    observed.clear()
    observed.update({key:row for key,row in family.items() if key!=controller})


def fault_observation_issues(uncertain,rejected,errors):
    live=[]
    for row in uncertain.values():
        stamp=row.get("birth_after") or row.get("birth")
        if ((stamp and matches(row["pid"],stamp)) or (not stamp and psutil.pid_exists(row["pid"]))):
            live.append(row["pid"])
    return {"uncertain_observations":list(uncertain.values()),
            "rejected_processes":list(rejected.values()),
            "observation_errors":list(errors.values()),"unknown_live":sorted(set(live))}


async def scenario(root,binary,candidate,catalog,python,kind):
    private_directory(root);private_directory(root/"home");(root/"work").mkdir()
    if kind=="effect-after":(root/"hold-tool-result").write_text("hold")
    upstream=Upstream(root,kind);ca=await upstream.start()
    for key in ["HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","http_proxy","https_proxy","all_proxy","no_proxy","SSL_CERT_FILE","SSL_CERT_DIR"]:
        os.environ.pop(key,None)
    os.environ.update(HTTPS_PROXY=upstream.proxy_url,CODEX_CA_CERTIFICATE=str(ca),OPENROUTER_API_KEY=KEY1,
                      CODEX_DEEPSEEK_TEST_HTTP_BINARY=str(candidate.with_name("codex-http-transport.exe")),
                      CODEX_DEEPSEEK_TRACE_DIR=str(root/"adapter-traces"),CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS="90")
    for key in ["CODEX_DEEPSEEK_TEST_WINDOWS_PREPARE_MANIFEST","CODEX_DEEPSEEK_TEST_WINDOWS_PREPARE_SHA256"]:os.environ.pop(key,None)
    observed={};stop_observer=asyncio.Event()
    controller=(os.getpid(),identity(os.getpid()));assert controller[1],"Fixture controller identity is missing"
    family={controller:{"pid":controller[0],"birth":controller[1],"ownership":"current_fixture_controller"}}
    uncertain={};rejected={};observation_errors={}
    async def observe():
        while not stop_observer.is_set():
            collect_fault_processes(controller,family,observed,uncertain,rejected,observation_errors)
            await asyncio.sleep(.05)
    watcher=asyncio.create_task(observe())
    peer=None;service=None;report={"kind":kind,"passed":False,"binary":str(binary),"binary_sha256":sha(binary),"started":time.time()}
    try:
        service=await asyncio.to_thread(ensure,root/"home",catalog,candidate)
        report["gateway_owner"]={k:service.get(k) for k in ["pid","birth","worker_pid","worker_birth","port","restarts"]}
        peer=await asyncio.to_thread(new_peer,root,binary,catalog,service,python,kind.startswith("effect"))
        tid=await asyncio.to_thread(peer.new);turn,start=await asyncio.to_thread(peer.start,tid,"Run the owned append tool once." if kind.startswith("effect") else "Return the fixture marker. Do not call tools.")
        report.update(thread=tid,turn=turn,call_id=upstream.call_id)
        if kind=="tcp-reset":
            report["native_partial_before_reset"]=await asyncio.to_thread(peer.wait,
                lambda event:native_partial_delta(event,tid,turn),start,20)
            upstream.native_partial_seen.set()
        if kind in {"auth401","auth403"}:
            event=await asyncio.to_thread(terminal,peer,tid,turn,start)
            report["first_terminal"]=event
            assert event["params"]["turn"]["status"]=="failed",event
            # Match the independently observed direct-reference policy; never
            # alter native retry limits or add a Gateway retry loop.
            assert len(upstream.requests)==AUTH_NATIVE_ATTEMPTS[kind],{"count":len(upstream.requests),"kind":kind,"reference":AUTH_NATIVE_ATTEMPTS[kind]}
            assert all(r["credential"]=="initial" and r["body"]["provider"]["only"]==["deepseek"] and r["body"]["provider"]["allow_fallbacks"] is False for r in upstream.requests)
            report["failed_auth_attempts"]=len(upstream.requests)
            await asyncio.to_thread(close_peer,peer);peer=None
            os.environ["OPENROUTER_API_KEY"]=KEY2
            service=await asyncio.to_thread(ensure,root/"home",catalog,candidate)
            peer=await asyncio.to_thread(new_peer,root,binary,catalog,service,python,False)
            await asyncio.to_thread(peer.call,"thread/resume",{"threadId":tid})
            items=await asyncio.to_thread(peer.turn,tid,"Credential explicitly repaired. Return the fixture marker; no tools.")
            assert "NETWORK_RECOVERED" in text(items) and len(upstream.requests)==AUTH_NATIVE_ATTEMPTS[kind]+1
            report["explicit_credential_repair"]=True
        elif kind=="effect-after":
            await until(lambda:(root/"append-entered").exists())
            assert len(effects(root))==1
            report["effects_before_exit"]=effects(root)
            report["persisted_call_before_exit"]=await until(lambda:next((r for r in persisted_items(root/"home",upstream.call_id) if r["record"]["payload"].get("type") in {"function_call","custom_tool_call"}),None))
            assert not persisted_output(root/"home",upstream.call_id),"The before-result boundary was missed"
            report["unreturned_tool_result"]=not upstream.tool_result_seen.is_set()
            assert report["unreturned_tool_result"]
            report["native_owner_before_kill"]={"pid":peer.process.pid,"birth":identity(peer.process.pid)}
            peer.process.kill();await asyncio.to_thread(peer.process.wait,10)
            await asyncio.to_thread(close_peer,peer,crashed=True);peer=None
            upstream.phase="cold";(root/"release-tool-result").write_text("release")
            peer=await asyncio.to_thread(new_peer,root,binary,catalog,service,python,True)
            await asyncio.to_thread(peer.call,"thread/resume",{"threadId":tid})
            items=await asyncio.to_thread(peer.turn,tid,"Resume observation only; do not repeat the completed external append.")
            assert "COLD_OK" in text(items) and len(effects(root))==1
            report["cold_evidence"]=cold_evidence(upstream,root,peer,returned=False)
        else:
            if kind=="effect-worker":
                await asyncio.wait_for(upstream.tool_result_seen.wait(),40)
                record=await until(lambda:persisted_output(root/"home",upstream.call_id))
                report["persisted_result"]=record
                assert len(effects(root))==1
                report["native_stream_before_worker_exit"]=await asyncio.to_thread(peer.wait,
                    lambda e:e.get("method")=="item/agentMessage/delta" and e.get("params",{}).get("threadId")==tid and e.get("params",{}).get("turnId")==turn and e.get("params",{}).get("delta")=="WORKER_PENDING",start,30)
                previous=read_descriptor(root/"home")
                assert matches(previous["worker_pid"],previous["worker_birth"])
                report["worker_before_kill"]={k:previous[k] for k in ["worker_pid","worker_birth","port"]}
                os.kill(previous["worker_pid"],getattr(signal,"SIGKILL",signal.SIGTERM))
                current=await until(lambda:(v if (v:=read_descriptor(root/"home")).get("worker_pid")!=previous["worker_pid"] and healthy(v) else None),20)
                assert current["port"]==previous["port"] and current["token"]==previous["token"]
                report["worker_after_restart"]={k:current.get(k) for k in ["worker_pid","worker_birth","port","restarts"]}
            items=await asyncio.to_thread(peer.complete,tid,start,turn_id=turn,timeout=90)
            report["answer"]=text(items)
            if kind=="effect-before":assert "RECOVERED_WITHOUT_EFFECT" in text(items) and not effects(root)
            elif kind=="effect-worker":
                assert "EFFECT_OK" in text(items) and len(effects(root))==1
                await asyncio.to_thread(close_peer,peer);peer=None;upstream.phase="cold"
                peer=await asyncio.to_thread(new_peer,root,binary,catalog,service,python,True)
                await asyncio.to_thread(peer.call,"thread/resume",{"threadId":tid})
                items=await asyncio.to_thread(peer.turn,tid,"Cold resumed observation only. Do not repeat append.")
                assert "COLD_OK" in text(items) and len(effects(root))==1
                report["cold_evidence"]=cold_evidence(upstream,root,peer,returned=True)
            else:assert "NETWORK_RECOVERED" in text(items) and len(upstream.requests)==2
        report["events"]=list(peer.events);report["effects"]=effects(root)
        if kind in {"truncated","tcp-reset","effect-before","effect-worker"}:
            retry_events=[e for e in peer.events if e.get("method")=="error" and e.get("params",{}).get("willRetry")]
            if kind=="effect-worker":
                # First owner events were saved by Peer.close before cold restart.
                for path in root.glob("events-*.json"):
                    retry_events.extend(e for e in json.loads(path.read_text()) if e.get("method")=="error" and e.get("params",{}).get("willRetry"))
            assert retry_events,"Native did not expose its actual retry decision"
            report["native_retry_events"]=retry_events
        report["passed"]=True
    except BaseException as exc:
        report["error"]=repr(exc);report["traceback"]=traceback.format_exc()
        if peer:report["events"]=list(peer.events)
    finally:
        cleanup=[]
        if peer:
            try:await asyncio.to_thread(close_peer,peer)
            except BaseException as exc:cleanup.append("peer:"+repr(exc))
        if service:
            try:await asyncio.to_thread(terminate,service["pid"],service["birth"],timeout=8)
            except BaseException as exc:cleanup.append("service:"+repr(exc))
        await upstream.close()
        state=root/"home/provider-state/adapter.sqlite"
        if state.exists():
            with closing(sqlite3.connect(state.as_uri()+"?mode=ro",uri=True)) as db:
                report["receipts"]=[dict(zip(["id","status","upstream_status","response_id","detail"],r)) for r in db.execute("SELECT id,status,upstream_status,response_id,detail FROM receipts ORDER BY started")]
        if report["passed"]:
            rows=report.get("receipts",[])
            terminal_ok=bool(rows) and rows[-1]["status"]=="completed"
            if kind in {"truncated","tcp-reset","effect-before"}:terminal_ok=terminal_ok and any(r["status"]=="adapter_error" for r in rows)
            if kind in {"auth401","auth403"}:terminal_ok=terminal_ok and len(rows)==AUTH_NATIVE_ATTEMPTS[kind]+1 and all(r["status"]=="upstream_error" and r["upstream_status"]==int(kind[-3:]) for r in rows[:-1])
            if not terminal_ok:cleanup.append("Actual receipt terminal sequence did not match the injected fault")

        end=time.monotonic()+5
        while time.monotonic()<end and any(matches(pid,birth) for pid,birth in observed):await asyncio.sleep(.05)
        stop_observer.set();await watcher
        remaining=[row for (pid,birth),row in observed.items() if matches(pid,birth)]
        report["owned_processes"]=list(observed.values());report["remaining_before_forced_cleanup"]=remaining
        observation=fault_observation_issues(uncertain,rejected,observation_errors);report.update(observation)
        if observation["unknown_live"] or observation["observation_errors"]:cleanup.append("Process ownership observation remained unresolved")
        if remaining:
            cleanup.append("Owned subprocesses survived normal cleanup")
            for row in remaining:await asyncio.to_thread(terminate,row["pid"],row["birth"],timeout=2)
        report["effects"]=effects(root);report["requests"]=len(upstream.requests);report["cleanup_errors"]=cleanup
        report["passed"]=report["passed"] and not cleanup;report["finished"]=time.time()
        save(root/"upstream-requests.json",upstream.requests);save(root/"verdict.json",report)
    return report

async def direct_auth_baseline(root,binary,catalog,python):
    private_directory(root)
    for kind in ["auth401","auth403"]:
        case=root/kind;private_directory(case);private_directory(case/"home");(case/"work").mkdir()
        origin=Upstream(case,kind);app=web.Application();app.router.add_post("/v1/responses",origin.handle)
        runner=web.AppRunner(app,handler_cancellation=True);await runner.setup();await web.TCPSite(runner,"127.0.0.1",0).start()
        port=runner.addresses[0][1];peer=None
        report={"kind":kind,"layer":"official raw native -> direct owned Responses HTTP; no Gateway/native HTTP helper","passed":False,"binary_sha256":sha(binary)}
        try:
            config(case,catalog,port,KEY1,python,False)
            env=native_environment(case/"home","",binary,install_shims=False)
            for key in list(env):
                if key.lower() in {"http_proxy","https_proxy","all_proxy","no_proxy"} or key in {"CODEX_DEEPSEEK_POLICY_HOME","CODEX_DEEPSEEK_BRIDGE_TOKEN","CODEX_DEEPSEEK_BRIDGE_PORT","CODEX_CA_CERTIFICATE","SSL_CERT_FILE"}:env.pop(key,None)
            env["FAULT_LOCAL_TOKEN"]=KEY1
            peer=await asyncio.to_thread(Peer,case,command=[str(binary)],environment=env)
            thread=await asyncio.to_thread(peer.new);turn,start=await asyncio.to_thread(peer.start,thread,"Return fixture marker; no tools.")
            event=await asyncio.to_thread(terminal,peer,thread,turn,start)
            assert event["params"]["turn"]["status"]=="failed",event
            assert event["params"]["turn"]["error"]["codexErrorInfo"]["httpConnectionFailed"]["httpStatusCode"]==int(kind[-3:])
            report.update(passed=True,thread=thread,turn=turn,request_count=len(origin.requests),terminal=event,events=peer.events)
        except BaseException as exc:report["error"]=repr(exc)
        finally:
            if peer:
                try:await asyncio.to_thread(close_peer,peer)
                except BaseException as exc:report["passed"]=False;report["cleanup_error"]=repr(exc)
            await runner.cleanup()
            save(case/"upstream-requests.json",origin.requests);save(case/"verdict.json",report)
        assert report["passed"],report.get("error") or report.get("cleanup_error")

async def main(args):
    selected=selected_scenarios(args.kind)
    if os.name!="nt":raise SystemExit("This verifier currently qualifies Windows only")
    session=ctypes.c_ulong()
    assert ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(),ctypes.byref(session))
    assert session.value>0 and not ctypes.windll.shell32.IsUserAnAdmin(),"Actual native fault fixtures require a Limited interactive Windows desktop"
    os.environ.pop("CODEX_DEEPSEEK_TEST_WORKSPACE_LAYOUT",None)
    os.environ.pop("CODEX_DEEPSEEK_TEST_SANDBOX_MODE",None)
    entry=args.entry.resolve();release=entry.parent.parent.parent
    install=json.loads((release/"installation.json").read_text());manifest=json.loads((release/"native-runtime.json").read_text())
    assert entry==console_entry(release/"venv") and valid_release(release,install["adapter_source_sha256"],install["model_catalog_sha256"],manifest)
    actual_source=source_identity()
    differences={k:{"installed":install["source_sha256"].get(k),"controller":actual_source[1].get(k)} for k in actual_source[1].keys()|install["source_sha256"].keys() if actual_source[1].get(k)!=install["source_sha256"].get(k)}
    assert actual_source==(install["source_id"],install["source_sha256"]),"Controller source differs from the installed artifact"
    candidate=Path(install["native_package"])/"bin/codex.exe";catalog=release/"catalog.json";args.python=python_entry(release/"venv")
    root=args.output.resolve()/str(uuid.uuid4());private_directory(root)
    artifact={"entry":str(entry),"entry_sha256":sha(entry),"installation_sha256":sha(release/"installation.json"),"official_sha256":sha(args.official),"candidate_sha256":sha(candidate),"source":{"verify_fault_boundaries.py":sha(__file__),"fixtures/fault-boundaries/mcp_append.py":sha(MCP_FIXTURE)},"model_calls":0,"real_credentials":0}
    for name,binary in [("official",args.official),("candidate",candidate)]:
        version=subprocess.run([str(binary),"--version"],capture_output=True,text=True,timeout=15)
        assert version.returncode==0 and version.stdout.strip()=="codex-cli "+NATIVE_VERSION,(name,version.stdout,version.stderr)
    print("EVIDENCE",root,flush=True)
    direct_root=args.reference_auth_evidence.resolve() if args.reference_auth_evidence else root/"direct-reference"
    if args.reference_auth_evidence is None:await direct_auth_baseline(direct_root,args.official,catalog,args.python)
    direct_proofs={}
    for auth_kind in ["auth401","auth403"]:
        direct_path=direct_root/auth_kind/"verdict.json";direct=json.loads(direct_path.read_text())
        assert direct["passed"] and direct["request_count"]>0 and direct["binary_sha256"]==artifact["official_sha256"]
        assert direct["terminal"]["params"]["turn"]["status"]=="failed"
        assert direct["terminal"]["params"]["turn"]["error"]["codexErrorInfo"]["httpConnectionFailed"]["httpStatusCode"]==int(auth_kind[-3:])
        AUTH_NATIVE_ATTEMPTS[auth_kind]=direct["request_count"]
        direct_proofs[auth_kind]={"path":str(direct_path),"sha256":sha(direct_path),"requests":direct["request_count"]}
    artifact["auth_native_default_basis"]={"layer":"official direct Responses, no Gateway","raw":direct_proofs}
    artifact["controller_source_differences"]=differences
    artifact["verifier_sha256"]={name:sha(REPO/name) for name in VERIFIERS}
    artifact["controller"]={"pid":os.getpid(),"birth":identity(os.getpid()),"session":session.value,"high":False}
    save(root/"artifact.json",artifact)
    results=[];comparisons=[]
    for kind in selected:
        variants={}
        for name,binary in [("official",args.official),("candidate",candidate)]:
            report=await scenario(root/(kind+"-"+name),binary,candidate,catalog,args.python,kind)
            variants[name]=report
            results.append({"kind":kind,"variant":name,"passed":report["passed"],"evidence":str(root/(kind+"-"+name)/"verdict.json"),"error":str(report.get("error"))[:700] if report.get("error") else None})
            save(root/"results.json",results);print(json.dumps(results[-1]),flush=True)
            if not report["passed"]:return 1
        def semantic(value):
            return {"requests":value["requests"],"effects":len(value["effects"]),
                    "receipts":[[r["status"],r["upstream_status"]] for r in value["receipts"]],
                    "auth_attempts":value.get("failed_auth_attempts"),
                    "cold_mcp_count":len(value.get("cold_evidence",{}).get("mcp_events",[]))}
        comparison={"kind":kind,"official":semantic(variants["official"]),"candidate":semantic(variants["candidate"])}
        comparison["passed"]=comparison["official"]==comparison["candidate"]
        comparisons.append(comparison);save(root/"comparisons.json",comparisons)
        if not comparison["passed"]:return 1
    assert sha(__file__)==artifact["source"]["verify_fault_boundaries.py"] and sha(MCP_FIXTURE)==artifact["source"]["fixtures/fault-boundaries/mcp_append.py"]
    assert source_identity()==actual_source,"Controller source changed during fault acceptance"
    assert all(sha(REPO/name)==digest for name,digest in artifact["verifier_sha256"].items()),"Acceptance dependency changed during execution"
    artifact_check={
        "entry":sha(entry)==artifact["entry_sha256"],
        "installation":sha(release/"installation.json")==artifact["installation_sha256"],
        "candidate":sha(candidate)==artifact["candidate_sha256"],
        "reference":sha(args.official)==artifact["official_sha256"],
        "native_manifest":json.loads((release/"native-runtime.json").read_text())==manifest,
        "valid_release":valid_release(release,install["adapter_source_sha256"],install["model_catalog_sha256"],manifest),
        "direct_reference_verdicts":all(sha(Path(item["path"]))==item["sha256"] for item in artifact["auth_native_default_basis"]["raw"].values())}
    save(root/"final-artifact-check.json",artifact_check)
    assert all(artifact_check.values()),"Installed/reference artifact changed during fault acceptance"
    save(root/"complete.json",{"passed":True,"all_scenarios":complete_scenario_set([row["kind"] for row in comparisons]),"selected_scenarios":selected,"artifact":sha(root/"artifact.json"),"comparisons":comparisons,"model_calls":0,"real_credentials":0})
    return 0

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry",type=Path,required=True,help="Immutable installed CDS console entry")
    parser.add_argument("--reference",dest="official",type=Path,required=True,help="Unmodified official Codex binary at the same pinned version")
    parser.add_argument("--output",type=Path,required=True,help="Owned evidence directory; use a short path on Windows")
    parser.add_argument("--reference-auth-evidence",type=Path,help="Optional prior direct-reference auth evidence, checked against the exact reference binary hash")
    parser.add_argument("--kind",action="append",choices=SCENARIOS,help="Diagnostic subset; omitted runs all seven scenarios on both binaries")
    args=parser.parse_args();args.entry=args.entry.resolve();args.official=args.official.resolve()
    raise SystemExit(asyncio.run(main(args)))
