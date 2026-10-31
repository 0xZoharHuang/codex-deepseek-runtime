"""Future format controls cannot disappear into an automatic-tools schema mapping."""
import copy
import json

import pytest

from codex_deepseek.protocol import MODEL, ProtocolError, prepare


def request():
    return {
        "model": MODEL, "tool_choice": "auto",
        "tools": [{"type": "function", "name": "read", "parameters": {"type": "object"}}],
        "text": {"format": {
            "type": "json_schema", "name": "codex_output_schema", "strict": True,
            "schema": {"type": "object", "properties": {"value": {"type": "integer"}},
                       "required": ["value"], "additionalProperties": False},
        }},
    }


@pytest.mark.parametrize("key,value", [
    ("future_native_control", True),
    ("future_native_control", {"semantics": "must-not-be-ignored"}),
    ("description", "Unknown at the pinned native TextFormat boundary"),
])
def test_mapped_format_rejects_unknown_top_level_semantics(key, value):
    body = request(); body["text"]["format"][key] = value
    before = copy.deepcopy(body)
    with pytest.raises(ProtocolError, match="format"):
        prepare(body)
    assert body == before


@pytest.mark.parametrize("name", [None, False, 1, [], {}, "", "two words", "x"*65, "\u975eASCII"])
def test_mapped_format_does_not_silently_consume_invalid_name(name):
    body = request(); body["text"]["format"]["name"] = name
    with pytest.raises(ProtocolError, match="name"):
        prepare(body)


def test_schema_annotations_remain_data_and_are_not_format_control_fields():
    body = request()
    schema = body["text"]["format"]["schema"]
    schema["x-native-annotation"] = {"opaque": ["arbitrary", None, True]}
    schema["properties"]["value"]["description"] = "An ordinary nested JSON Schema annotation."
    before = copy.deepcopy(body)
    outgoing, registry = prepare(body)
    assert body == before and registry.structured_output_mapping is not None
    assert json.loads(outgoing["instructions"].split("JSON Schema:\n", 1)[1]) == schema


@pytest.mark.parametrize("name", [None, "Native_name-01", "x"*64], ids=["omitted", "ordinary", "maximum"])
def test_valid_name_is_consumed_in_complete_format_hash_only(name):
    import hashlib
    body = request()
    if name is None: body["text"]["format"].pop("name")
    else: body["text"]["format"]["name"] = name
    outgoing, registry = prepare(body)
    original_format = body["text"]["format"]
    expected = hashlib.sha256(json.dumps(original_format, ensure_ascii=False, allow_nan=False,
                                        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert outgoing["text"]["format"] == {"type": "json_object"}
    assert registry.structured_output_mapping["requested_format_sha256"] == expected
    if name is not None: assert name not in json.dumps(registry.structured_output_mapping)
