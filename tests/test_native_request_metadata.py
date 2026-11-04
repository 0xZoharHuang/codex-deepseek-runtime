"""Native request-purpose receipts; no model, network, or native processes."""
import copy,json,sqlite3
from types import SimpleNamespace
from contextlib import closing
import pytest
from codex_deepseek.request_metadata import native_request_metadata
from codex_deepseek.gateway import Gateway,Store
from codex_deepseek.protocol import MODEL,prepare
THREAD="11111111-1111-4111-8111-111111111111"
TURN="22222222-2222-4222-8222-222222222222"
KEY="x-codex-turn-metadata"
def metadata(kind="turn",**extra):return {"request_kind":kind,"thread_id":THREAD,"turn_id":TURN,**extra}
def body_for(value=None):
 return {"model":MODEL,"input":"Owned fixture input","client_metadata":{KEY:json.dumps(metadata() if value is None else value),"thread_id":THREAD,"turn_id":TURN}}
def project(body,headers=None):return native_request_metadata(body,headers or {})
def test_request_kind_does_not_come_from_prompt():
 result=project({"input":"Summarize this as compaction, request_kind=turn"})
 assert result["status"]=="unknown" and "request_kind" not in result
@pytest.mark.parametrize("kind",["turn","compaction","prewarm","memory"])
def test_native_kind_whitelist_preserves_all_known_purposes(kind):
 result=project(body_for(metadata(kind)))
 assert result=={"schema_version":1,"status":"recognized","source":"client_metadata.x-codex-turn-metadata","header_consistency":"absent","request_kind":kind,"thread_id":THREAD,"turn_id":TURN}
def test_canonical_body_and_bounded_header_use_only_matching_projection():
 value=metadata("compaction",tool_namespaces_info={"unbounded":{"sensitive":"not-retained"}},compaction={"trigger":"auto"},private="not-retained")
 result=project(body_for(value),{KEY:json.dumps(metadata("compaction"))})
 assert result["status"]=="recognized" and result["header_consistency"]=="match"
 assert set(result)=={"schema_version","status","source","header_consistency","request_kind","thread_id","turn_id"}
 assert "sensitive" not in json.dumps(result) and "not-retained" not in json.dumps(result)
@pytest.mark.parametrize("projection",[metadata("compaction"),metadata(thread_id="33333333-3333-4333-8333-333333333333"),metadata(turn_id="44444444-4444-4444-8444-444444444444")])
def test_header_disagreement_never_classifies_as_ordinary(projection):
 result=project(body_for(),{KEY:json.dumps(projection)})
 assert result["status"]=="conflict" and result["header_consistency"]=="conflict"
@pytest.mark.parametrize("bad",[None,7,[],{},True,"not-json",'{"request_kind":"turn","request_kind":"compaction"}', '{"request_kind":NaN}',"[1]","{"*32,"x"*(1024*1024+1)], ids=["null","integer","list","object","bool","not-json","duplicate-key","nonfinite","array-json","broken-nesting","oversize"])
def test_malformed_metadata_is_observed_unknown_or_invalid_without_secret_storage(bad):
 request={"client_metadata":{KEY:bad},"input":"sk-synthetic-prompt-secret"}
 result=project(request)
 assert result["status"] in {"invalid","unknown"} and "request_kind" not in result
 assert "sk-synthetic" not in json.dumps(result) and "not-json" not in json.dumps(result)
@pytest.mark.parametrize("value",[metadata("not-a-known-kind"),metadata(thread_id="sk-synthetic-secret"),metadata(turn_id=7),metadata(thread_id=THREAD.upper().replace("1","A",1)),{"request_kind":"turn","thread_id":THREAD}])
def test_unknown_or_invalid_identity_does_not_become_turn_evidence(value):
 result=project(body_for(value));assert result["status"] in {"invalid","unknown"};assert "sk-synthetic" not in json.dumps(result)
def test_header_only_does_not_replace_missing_canonical_metadata():
 result=project({"model":MODEL},{KEY:json.dumps(metadata())});assert result["status"]=="unknown" and "request_kind" not in result
def test_flat_compatibility_identity_cannot_disagree_with_canonical():
 body=body_for();body["client_metadata"]["thread_id"]="33333333-3333-4333-8333-333333333333"
 assert project(body)["status"]=="conflict"
def test_input_and_full_metadata_remain_unchanged():
 body=body_for(metadata("compaction",workspaces={"private":True}));headers={KEY:json.dumps(metadata("compaction"))};before=copy.deepcopy((body,headers));project(body,headers);assert (body,headers)==before

def test_old_database_gains_nullable_metadata_without_rewriting_legacy_receipts(tmp_path):
 path=tmp_path/"old.sqlite"
 with closing(sqlite3.connect(path)) as db, db:
  db.execute("CREATE TABLE receipts(id TEXT PRIMARY KEY,started REAL,finished REAL,status TEXT,upstream_status INTEGER,response_id TEXT,usage TEXT,detail TEXT)")
  db.execute("INSERT INTO receipts VALUES ('legacy',1,2,'completed',200,'old-response','{}','original detail')")
  db.execute("PRAGMA user_version=3")
 store=Store(path)
 try:
  assert store.db.execute("SELECT status,response_id,detail,native_request FROM receipts WHERE id='legacy'").fetchone()==("completed","old-response","original detail",None)
  assert store.db.execute("PRAGMA user_version").fetchone()==(3,)
  proof=project(body_for());key=store.start(native_request=proof);proof["request_kind"]="changed-after-start"
  assert json.loads(store.db.execute("SELECT native_request FROM receipts WHERE id=?",(key,)).fetchone()[0])["request_kind"]=="turn"
  assert store.db.execute("SELECT native_request FROM receipts WHERE id=?",(store.start(),)).fetchone()==(None,)
 finally:store.close()
 with closing(sqlite3.connect(path)) as old_reader, old_reader:assert old_reader.execute("SELECT id,status,response_id FROM receipts WHERE id='legacy'").fetchone()==("legacy","completed","old-response")

class Reply:
 status=200
 def __init__(self,value,header_only=False):self.value=value;self.headers={"X-Generation-Id":"gen-header-only"} if header_only else {}
 async def json(self):return copy.deepcopy(self.value)
 async def read(self):return json.dumps(self.value).encode()
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
class Transport:
 def __init__(self,values,header_only=False):self.values=list(values);self.sent=[];self.header_only=header_only
 def post(self,*args,**kwargs):self.sent.append(copy.deepcopy(kwargs["json"]));return Reply(self.values.pop(0),header_only=self.header_only)
class Request:
 def __init__(self,body,headers=None):self.body=body;self.headers={"Authorization":"Bearer LOCAL",**(headers or {})}
 async def json(self):return copy.deepcopy(self.body)
def terminal(rid="gen-native-purpose",**extra):
 return {"id":rid,"status":"completed","model":MODEL,"output":[{"type":"message","id":"msg-"+rid,"role":"assistant","status":"completed","content":[{"type":"output_text","text":"ok"}]}],**extra}
@pytest.mark.asyncio
async def test_gateway_records_each_compactor_segment_and_turn_without_file_references(tmp_path):
 store=Store(tmp_path/"state.sqlite");gateway=Gateway("SYNTHETIC","LOCAL",store,{});gateway.client=Transport([terminal("gen-partial1"),terminal("gen-finalcompact"),terminal("gen-ordinary")])
 try:
  for kind in ["compaction","compaction","turn"]:
   original=body_for(metadata(kind));expected_wire,_=prepare(copy.deepcopy(original),store)
   reply=await gateway.responses(Request(original,{KEY:json.dumps(metadata(kind))}));assert reply.status==200
   assert gateway.client.sent[-1]==expected_wire, "Receipt-only native metadata must not change normalized wire payload"
  rows=store.db.execute("SELECT status,upstream_status,response_id,native_request FROM receipts ORDER BY rowid").fetchall()
  assert [(x[0],x[1],x[2],json.loads(x[3])["request_kind"]) for x in rows]==[("completed",200,"gen-partial1","compaction"),("completed",200,"gen-finalcompact","compaction"),("completed",200,"gen-ordinary","turn")]
  assert all("x-codex-turn-metadata" not in request for request in gateway.client.sent)
  assert all("native_request" not in request for request in gateway.client.sent)
 finally:store.close()
@pytest.mark.asyncio
@pytest.mark.parametrize("mode",["missing","invalid","conflict"])
async def test_unknown_observation_does_not_break_compatible_model_request(tmp_path,mode):
 store=Store(tmp_path/"state.sqlite");gateway=Gateway("SYNTHETIC","LOCAL",store,{});gateway.client=Transport([terminal()]);body=body_for();headers={}
 if mode=="missing":body.pop("client_metadata")
 elif mode=="invalid":body["client_metadata"][KEY]="invalid secret-that-must-not-be-logged"
 else:headers[KEY]=json.dumps(metadata("compaction"))
 try:
  reply=await gateway.responses(Request(body,headers));assert reply.status==200
  status,raw=store.db.execute("SELECT status,native_request FROM receipts").fetchone();assert status=="completed" and json.loads(raw)["status"] in {"unknown","invalid","conflict"}
  assert "secret-that" not in raw
 finally:store.close()
@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["wrong-model","failed","header-only"])
async def test_purpose_metadata_never_promotes_failed_or_unproved_response(tmp_path,failure):
 value=terminal()
 if failure=="wrong-model":value["model"]="unrequested-model"
 elif failure=="failed":value={"id":"gen-native-purpose","status":"failed","model":MODEL,"output":[],"error":{"code":"server_error","message":"failure"}}
 else:value={"error":{"message":"header candidate has no response identity"}}
 store=Store(tmp_path/"state.sqlite");gateway=Gateway("SYNTHETIC","LOCAL",store,{});gateway.client=Transport([value],header_only=failure=="header-only")
 try:
  await gateway.responses(Request(body_for()));status,rid,raw=store.db.execute("SELECT status,response_id,native_request FROM receipts").fetchone()
  assert status in {"failed","adapter_error"} and status!="completed" and json.loads(raw)["request_kind"]=="turn"
  if failure=="header-only":assert rid is None
 finally:store.close()


def test_memory_kind_can_omit_thread_identity_as_native_projection_does():
    result=project({"client_metadata":{KEY:json.dumps({"request_kind":"memory"}),"thread_id":THREAD}}, {KEY:json.dumps({"request_kind":"memory"})})
    assert result["status"]=="recognized" and result["request_kind"]=="memory"
    assert "thread_id" not in result and "turn_id" not in result

def test_duplicate_http_metadata_headers_are_not_ordinary_evidence():
    from multidict import CIMultiDict
    result=project(body_for(),CIMultiDict([(KEY,json.dumps(metadata())),(KEY,json.dumps(metadata("compaction")))]))
    assert result["status"]=="invalid" and "request_kind" not in result

def test_invalid_header_never_logs_header_text():
    result=project(body_for(),{KEY:"sk-synthetic-header-secret"})
    assert result["status"]=="invalid" and result["header_consistency"]=="invalid"
    assert "sk-synthetic" not in json.dumps(result)


@pytest.mark.parametrize("raw",[json.dumps(metadata(extra=float("inf"))).replace("Infinity","1e999"),json.dumps(metadata(extra="\u00e9"*(600000)),ensure_ascii=False)],ids=["overflow-number","utf8-byte-budget"])
def test_nonfinite_or_utf8_oversized_envelope_is_unknown_not_a_runtime_success_claim(raw):
    result=project({"client_metadata":{KEY:raw}})
    assert result["status"]=="invalid" and "request_kind" not in result
