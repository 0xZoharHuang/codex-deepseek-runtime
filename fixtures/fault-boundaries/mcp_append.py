"""Owned non-idempotent MCP fixture; no deduplication, no network, no secret access."""
import json,os,sys,threading,time
from pathlib import Path
root=Path(sys.argv[1]);lock=threading.Lock()
(root/"mcp-owner.json").write_text(json.dumps({"pid":os.getpid()}))
def reply(value):
    with lock: print(json.dumps(value),flush=True)
def call(row):
    rid=row["id"];params=row.get("params",{});name=params.get("name")
    if name!="append_once":reply({"jsonrpc":"2.0","id":rid,"error":{"code":-32602,"message":"unknown fixture tool"}});return
    marker=params.get("arguments",{}).get("marker")
    with lock:
        with (root/"effects.jsonl").open("a",encoding="utf-8") as stream:
            stream.write(json.dumps({"rpc_id":rid,"marker":marker,"pid":os.getpid(),"at":time.time()})+"\n")
            stream.flush();os.fsync(stream.fileno())
    (root/"append-entered").write_text("appended")
    if (root/"hold-tool-result").exists():
        end=time.monotonic()+90
        while not (root/"release-tool-result").exists() and time.monotonic()<end:time.sleep(.01)
        if not (root/"release-tool-result").exists():
            reply({"jsonrpc":"2.0","id":rid,"error":{"code":-32603,"message":"owned result barrier expired"}});return
    reply({"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":"APPENDED:"+str(marker)}],"isError":False}})
for line in sys.stdin:
    row=json.loads(line);method=row.get("method")
    if "id" not in row:continue
    if method=="initialize":reply({"jsonrpc":"2.0","id":row["id"],"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"nonidempotent-owned-fixture","version":"1"}}})
    elif method=="tools/list":reply({"jsonrpc":"2.0","id":row["id"],"result":{"tools":[{"name":"append_once","description":"Owned non-idempotent append ledger fixture. Each invocation adds a new record, without deduplication.","inputSchema":{"type":"object","properties":{"marker":{"type":"string"}},"required":["marker"],"additionalProperties":False},"annotations":{"readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False}}]}})
    elif method=="tools/call":threading.Thread(target=call,args=(row,),daemon=True).start()
    else:reply({"jsonrpc":"2.0","id":row["id"],"result":{}})
