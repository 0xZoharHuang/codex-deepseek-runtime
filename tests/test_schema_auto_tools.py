"""Automatic native tools and strict final schemas use explicit wire mappings."""
import copy
from contextlib import closing
import hashlib
import json
import sqlite3

import aiohttp
from aiohttp import web
import pytest

from codex_deepseek.gateway import Gateway, Store
from codex_deepseek.protocol import MODEL, ProtocolError, prepare
from test_gateway import fake_upstream


SCHEMA = {"type": "object", "properties": {"value": {"type": "integer"}},
          "required": ["value"], "additionalProperties": False}
TOOL = {"type": "function", "name": "read", "parameters": {"type": "object"}}


def request(schema=None, **overrides):
    value = {"model": MODEL, "instructions": "Original native policy.",
             "tools": [copy.deepcopy(TOOL)], "input": [], "tool_choice": "auto",
             "text": {"format": {"type": "json_schema", "name": "native_result",
                                "strict": True, "schema": copy.deepcopy(SCHEMA if schema is None else schema)}}}
    value.update(overrides)
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("schema,mode", [
    (SCHEMA, "json_object"),
    ({"type": "array", "items": {"type": "integer"}}, "json_object"),
    ({"type": "string", "const": "真实值"}, "text"),
    ({"type": ["object", "null"]}, "text"),
    ({"$ref": "#/$defs/item", "$defs": {"item": SCHEMA}}, "text"),
    (True, "text"), (False, "text"), ({}, "text"),
])
def test_complete_original_schema_and_native_semantics_are_preserved(schema, mode):
    body = request(schema); frozen = copy.deepcopy(body)
    plain = copy.deepcopy(body); plain.pop("text")
    expected, _ = prepare(plain)
    outgoing, registry = prepare(body)
    assert body == frozen
    assert outgoing["tools"] == expected["tools"] and outgoing["input"] == expected["input"]
    assert outgoing["tool_choice"] == "auto" and outgoing["model"] == MODEL
    assert outgoing["text"]["format"] == {"type": mode}
    assert json.loads(outgoing["instructions"].split("JSON Schema:\n", 1)[1]) == schema
    assert outgoing["instructions"].startswith("Original native policy.\n\n")
    assert outgoing["provider"] == expected["provider"] == {
        "only": ["deepseek"], "allow_fallbacks": False, "require_parameters": True}
    mapping = registry.structured_output_mapping
    assert mapping["requested_format_sha256"] == digest(frozen["text"]["format"])
    assert mapping["requested_schema_sha256"] == digest(schema)
    assert mapping["upstream_format"] == mode and mapping["requested_strict"] is True
    assert mapping["upstream_decoder_constraint"] == ("json_syntax_only" if mode == "json_object" else False)
    assert mapping["tool_choice"] == "auto_preserved"
    assert registry.route_reason == "auto_tools_final_schema_validation/" + mode + "/official_default"
    again, _ = prepare({**body, "instructions": outgoing["instructions"]})
    assert again["instructions"] == outgoing["instructions"]
    # Mutating the wire request cannot alter the native request or its validator input.
    outgoing["tools"][0]["parameters"]["new"] = "wire-only"
    assert body == frozen


@pytest.mark.parametrize("kind", ["strict_false", "strict_missing", "no_tools", "empty_namespace",
                                  "required", "none", "named", "null", "no_schema"])
def test_unqualified_controls_keep_the_existing_wire_contract(kind):
    body = request()
    if kind == "strict_false": body["text"]["format"]["strict"] = False
    elif kind == "strict_missing": body["text"]["format"].pop("strict")
    elif kind == "no_tools": body["tools"] = []
    elif kind == "empty_namespace": body["tools"] = [{"type": "namespace", "name": "empty", "tools": []}]
    elif kind in ("required", "none"): body["tool_choice"] = kind
    elif kind == "named": body["tool_choice"] = {"type": "function", "name": "read"}
    elif kind == "null": body["tool_choice"] = None
    elif kind == "no_schema": body.pop("text")
    frozen = copy.deepcopy(body)
    outgoing, registry = prepare(body)
    assert body == frozen and registry.structured_output_mapping is None
    if kind == "no_schema":
        assert "text" not in outgoing and outgoing["provider"]["only"] == ["deepseek"]
    else:
        assert outgoing["text"] == body["text"]
        assert outgoing["provider"]["only"] == ["morph/fp8"]
    if "tool_choice" in body: assert outgoing["tool_choice"] == body["tool_choice"]


def test_omitted_tool_choice_keeps_its_omission_and_default_auto_mapping():
    body = request(); body.pop("tool_choice")
    outgoing, registry = prepare(body)
    assert "tool_choice" not in outgoing and registry.structured_output_mapping
    assert outgoing["text"]["format"] == {"type": "json_object"}


def test_serial_contract_keeps_prime_and_false_with_preselected_text_mapping():
    body = request(parallel_tool_calls=False)
    outgoing, registry = prepare(body)
    assert outgoing["parallel_tool_calls"] is False and outgoing["tool_choice"] == "auto"
    assert outgoing["provider"]["only"] == ["primeintellect"]
    assert outgoing["text"]["format"] == {"type": "text"}
    assert registry.structured_output_mapping["route_format_strategy"] == "nonofficial_route_text"
    assert registry.route_reason == "auto_tools_final_schema_validation/text/explicit_serial_tools"


def test_unattested_hosted_history_keeps_compatible_route_and_original_call_pair():
    from test_replay_provenance import request as replay_request
    body = replay_request(); body["text"] = request()["text"]
    frozen = copy.deepcopy(body)
    plain = copy.deepcopy(body); plain.pop("text")
    expected, _ = prepare(plain)
    outgoing, registry = prepare(body)
    assert body == frozen and outgoing["input"] == expected["input"]
    assert outgoing["tools"] == expected["tools"]
    assert outgoing["provider"] == expected["provider"] and outgoing["provider"]["only"] == ["morph/fp8"]
    assert outgoing["text"]["format"] == {"type": "text"}
    assert registry.route_reason.endswith("/unattested_native_history_with_hosted_tools")


def test_retained_discovery_exposes_real_tools_without_replacing_schemas_or_history():
    spec = {"type": "tool_search", "execution": "client"}
    initial, registry = prepare({"model": MODEL, "tools": [spec]})
    native = registry.output({"type": "function_call", "name": initial["tools"][0]["name"],
                              "call_id": "native-search", "arguments": '{"query":"read"}'})
    definition = {"type": "namespace", "name": "audit",
                  "tools": [{"type": "function", "name": "read_nonce", "parameters": {"type": "object"}}]}
    body = request(tools=[spec], input=[native, {"type": "tool_search_output",
                   "call_id": "native-search", "tools": [definition]}])
    plain = copy.deepcopy(body); plain.pop("text")
    expected, _ = prepare(plain)
    outgoing, registry = prepare(body)
    assert len(outgoing["tools"]) == 2 and outgoing["tools"] == expected["tools"]
    assert outgoing["input"] == expected["input"]
    assert registry.structured_output_mapping and outgoing["text"]["format"] == {"type": "json_object"}


@pytest.mark.parametrize("instructions", [False, 0, [], {}])
def test_mapping_does_not_coerce_falsy_invalid_native_instructions(instructions):
    with pytest.raises(ProtocolError, match="instructions must be text"):
        prepare(request(instructions=instructions))


def test_receipt_column_is_additive_and_original_rows_and_format_data_are_preserved(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE receipts(id TEXT PRIMARY KEY,started REAL,finished REAL,status TEXT,upstream_status INTEGER,response_id TEXT,usage TEXT,detail TEXT)")
        db.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?)",
                   ("legacy", 1, 2, "completed", 200, "old-response", "{}", None))
        db.execute("PRAGMA user_version=3")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")  # Transaction completion must also end this fixture handle.
    body = request(); _, registry = prepare(body)
    store = Store(path)
    try:
        assert store.db.execute("SELECT id,started,finished,status,upstream_status,response_id,usage,detail FROM receipts WHERE id='legacy'").fetchone() == (
            "legacy", 1, 2, "completed", 200, "old-response", "{}", None)
        assert store.db.execute("SELECT structured_output_mapping FROM receipts WHERE id='legacy'").fetchone() == (None,)
        original = copy.deepcopy(registry.structured_output_mapping)
        key = store.start(route_reason=registry.route_reason, structured_output_mapping=original)
        original["upstream_format"] = "must-not-mutate-persisted-data"
        saved = store.db.execute("SELECT structured_output_mapping FROM receipts WHERE id=?", (key,)).fetchone()[0]
        assert json.loads(saved) == registry.structured_output_mapping
        assert store.db.execute("PRAGMA user_version").fetchone() == (3,)
        assert store.db.execute("SELECT structured_output_mapping FROM receipts WHERE id=?", (store.start(),)).fetchone() == (None,)
    finally:
        store.close()


@pytest.mark.parametrize("stream", [False, True], ids=["JSON", "SSE"])
@pytest.mark.parametrize("fault", [None, "malformed", "type", "extra", "fences", "nonfinite", "unphased_type"])
async def test_mapped_schema_finals_are_guarded_before_native_completion(fake_upstream, tmp_path, stream, fault):
    text = {"malformed": "INVALID", "type": '{"value":"wrong"}',
            "extra": '{"value":1,"extra":2}', "fences": chr(96)*3+'json\n{"value":1}\n'+chr(96)*3,
            "nonfinite": '{"value":1e999}', "unphased_type": '{"value":"wrong"}'}.get(fault, '{"value":1}')
    message = {"type": "message", "id": "m1", "role": "assistant",
               "content": [{"type": "output_text", "text": text}]}
    if fault != "unphased_type": message["phase"] = "final_answer"
    terminal = {"id": "resp_schema", "status": "completed", "output": [message]}
    seen = []
    async def provider(incoming):
        payload = await incoming.json(); seen.append(payload)
        assert payload["text"]["format"] == {"type": "json_object"}
        assert json.loads(payload["instructions"].split("JSON Schema:\n", 1)[1]) == SCHEMA
        assert payload["tool_choice"] == "auto" and payload["provider"]["only"] == ["deepseek"]
        if not stream: return web.json_response(terminal)
        frames = [
            {"type": "response.created", "response": {"id": "resp_schema", "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0, "item": {**message, "content": []}},
            {"type": "response.output_text.delta", "item_id": "m1", "output_index": 0, "delta": text},
            {"type": "response.output_item.done", "output_index": 0, "item": message},
            {"type": "response.completed", "response": terminal},
        ]
        return web.Response(text="".join("data: "+json.dumps(x)+"\n\n" for x in frames), content_type="text/event-stream")
    fake_upstream.append(provider)
    store = Store(tmp_path / "gateway.sqlite")
    try:
        body = request(stream=stream)
        async with Gateway("SECRET", "LOCAL", store, {}) as gateway, aiohttp.ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/responses", json=body,
                                   headers={"Authorization": "Bearer LOCAL"}) as response:
                if stream:
                    frames = [json.loads(x[6:]) for x in (await response.text()).splitlines() if x.startswith("data: ")]
                    if fault:
                        assert frames[-1]["type"] == "response.failed"
                        assert not any(x["type"] in ("response.completed", "response.output_item.done") for x in frames)
                    else:
                        assert frames[-1]["type"] == "response.completed"
                        assert any(x["type"] == "response.output_item.done" and x["item"] == message for x in frames)
                elif fault: assert response.status == 502
                else: assert response.status == 200 and (await response.json())["output"] == [message]
        assert len(seen) == 1  # The mapping never retries or changes providers on a bad final.
        row = store.db.execute("SELECT status,route_reason,structured_output_mapping FROM receipts").fetchone()
        assert row[0] == ("adapter_error" if fault else "completed")
        assert row[1] == "auto_tools_final_schema_validation/json_object/official_default"
        mapping = json.loads(row[2])
        assert mapping["requested_format_sha256"] == digest(body["text"]["format"])
        assert mapping["requested_schema_sha256"] == digest(SCHEMA)
        assert mapping["upstream_decoder_constraint"] == "json_syntax_only"
    finally:
        store.close()
