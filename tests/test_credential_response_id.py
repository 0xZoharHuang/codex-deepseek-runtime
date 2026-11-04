"""Synthetic credential-bearing response IDs never become durable or wire identities."""
import json
import os
import aiohttp
from aiohttp import web
import pytest
from codex_deepseek.gateway import Gateway,Store
from codex_deepseek.protocol import MODEL,encode_sse
from test_gateway import fake_upstream
KEY="SYNTHETIC-RESPONSE-ID-CREDENTIAL"
HEADERS={"Authorization":"Bearer LOCAL"}
MODES=["created","completed","failed","error","nonstream_completed","nonstream_failed"]
def response(identifier,status):
    return {"id":identifier,"model":MODEL,"status":status,"output":[],
            "error":{"code":"server_error","message":"original failure"} if status=="failed" else None}
@pytest.mark.parametrize("mode",MODES)
@pytest.mark.parametrize("unsafe",[True,False])
async def test_response_id_checked_before_recording_redaction_and_trace(fake_upstream,tmp_path,mode,unsafe):
    identifier="gen-prefix-"+KEY+"-suffix" if unsafe else "gen-legitimate-identity"
    status="failed" if "failed" in mode or mode=="error" else "completed"
    value=response(identifier,status)
    event={"type":"response."+mode,"response":value}
    if mode=="created":
        event={"type":"response.created","response":response(identifier,"in_progress")}
    elif mode=="error":
        event={"type":"error","response":value,"code":"server_error","message":"original failure","param":None}
    elif mode.startswith("nonstream"):event=None
    fake_upstream.append(lambda request: None)
    async def upstream(request):
        await request.json()
        if event is None:return web.json_response(value)
        body=encode_sse(event)
        if mode=="created":body+=encode_sse({"type":"response.completed","response":response(identifier,"completed")})
        return web.Response(body=body,content_type="text/event-stream")
    fake_upstream[0]=upstream
    store=Store(tmp_path/"adapter.sqlite");trace=tmp_path/"trace"
    try:
        async with Gateway(KEY,"LOCAL",store,{},trace_source=lambda:trace,
            native_http_binary=os.environ.get("CODEX_DEEPSEEK_TEST_HTTP_BINARY")) as gateway,aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses",
                headers=HEADERS,json={"model":MODEL,"input":"synthetic","stream":event is not None}) as reply:
                text=await reply.text()
        row=store.db.execute("SELECT status,response_id FROM receipts").fetchone()
        parsed=json.loads(text) if event is None else [json.loads(x[6:]) for x in text.splitlines() if x.startswith("data: ")]
        if unsafe:
            assert row==("adapter_error",None)
            assert KEY not in text and "[REDACTED]" not in json.dumps(parsed)
            if event is not None:
                assert "id" not in parsed[-1]["response"]
            assert not list(trace.glob("*-upstream-events.jsonl"))
        else:
            assert row==("failed" if status=="failed" else "completed",identifier)
            final=parsed if event is None else parsed[-1]["response"]
            assert final["id"]==identifier
    finally:store.close()
