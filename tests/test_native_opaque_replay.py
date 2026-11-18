"""Run the pinned Codex CLI through native tool dispatch and cold resume."""

import json

from aiohttp import web
import pytest

from codex_deepseek.gateway import Gateway
from codex_deepseek.protocol import PROVIDER, Registry
from codex_deepseek.state import Store
from test_gateway import fake_upstream
from test_native_payload_retry import (
    binary, capacity_native_plan_effects, capacity_natural_exit_assertions, configure, events, invoke,
)


@pytest.mark.parametrize("typed_content", [False, True], ids=["incident-shape", "serde-projection"])
async def test_native_tool_continuation_and_cold_resume(fake_upstream, tmp_path, typed_content):
    binary()
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    path = tmp_path / "state.sqlite"
    reasoning = [
        {"type": "reasoning", "id": f"rs_server_test_{index}", "summary": [], "content": None,
         "encrypted_content": str(index) * size, "signature": f"synthetic-signature-{index}"}
        for index, size in enumerate((16067, 226, 10975, 226), 1)
    ]
    if typed_content:
        reasoning[1]["content"] = []
        reasoning[2]["content"] = [{"type": "text", "text": "Retained by the bridge"}]
        reasoning[3]["content"] = [{"type": "reasoning_text", "text": "Retained by native"}]
    requests = []
    name_to_kind = {}
    for namespace in (None, "functions"):
        registry = Registry()
        name_to_kind[registry.add("update_plan", namespace, "function").wire] = "function"
        name_to_kind[registry.add("exec", namespace, "custom").wire] = "custom"

    async def upstream(request):
        body = await request.json()
        requests.append(body)
        if len(requests) == 1:
            advertised = [tool["name"] for tool in body["tools"] if tool.get("name") in name_to_kind]
            assert advertised
            plan = {"plan": [{"step": "CAPACITY_NATIVE_PLAN_STEP_1", "status": "in_progress"}]}
            arguments = (plan if name_to_kind[advertised[0]] == "function" else
                         {"input": "text(await tools.update_plan(" + json.dumps(plan) + "));"})
            call = {"type": "function_call", "id": "fc_opaque_test", "status": "completed",
                    "call_id": "call_opaque_test", "name": advertised[0], "arguments": json.dumps(arguments)}
            reply = events(output=[*reasoning, call])
        else:
            reply = events(text="OPAQUE_REPLAY_OK")
        return web.Response(text=reply, content_type="text/event-stream")

    fake_upstream.append(upstream)
    store = Store(path)
    try:
        async with Gateway("SYNTHETIC_NATIVE_OPAQUE_KEY", "synthetic-native-413-fixture-token",
                           store, {}) as gateway:
            configure(home, gateway.port, capacity=True)
            warm = await invoke(home, gateway.port, work, ephemeral=False, prompt="Run the planning fixture.")
        assert warm["exit_code"] == 0 and "OPAQUE_REPLAY_OK" in warm["stdout"], warm["stdout"]
        capacity_natural_exit_assertions(warm)
        assert capacity_native_plan_effects(warm)["counts"] == {"1": 1, "2": 0}
        assert len(requests) == 2
    finally:
        store.close()

    store = Store(path)
    try:
        async with Gateway("SYNTHETIC_NATIVE_OPAQUE_KEY", "synthetic-native-413-fixture-token",
                           store, {}) as gateway:
            configure(home, gateway.port, capacity=True)
            cold = await invoke(home, gateway.port, work, resume=True, ephemeral=False, prompt="Read the prior plan.")
        assert cold["exit_code"] == 0 and "OPAQUE_REPLAY_OK" in cold["stdout"], cold["stdout"]
        capacity_natural_exit_assertions(cold)
        assert capacity_native_plan_effects(cold)["counts"] == {"1": 0, "2": 0}
        assert len(requests) == 3
        for body in requests[1:]:
            assert body["provider"]["only"] == [PROVIDER]
            assert [{key: item[key] for key in reasoning[0]} for item in body["input"]
                    if item.get("id") in {source["id"] for source in reasoning}] == reasoning
            assert [item["type"] for item in body["input"] if item.get("call_id") == "call_opaque_test"] == [
                "function_call", "function_call_output"]
    finally:
        store.close()
