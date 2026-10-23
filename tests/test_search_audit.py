"""Adversarial live-search verifier checks; no provider credential or network."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("status,error,passes", [
    ("completed", None, True),
    ("failed", {"message": "upstream failed after cited search"}, False),
    ("incomplete", None, False),
    ("in_progress", None, False),
    ("completed", {"message": "contradictory error"}, False),
])
async def test_search_verifier_requires_terminal_success_and_owned_output(
    tmp_path, monkeypatch, status, error, passes,
):
    spec = importlib.util.spec_from_file_location("search_verifier_case", ROOT / "verify_search_audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    query = "Stack Overflow Python asyncio TaskGroup cancellation"
    url = "https://docs.python.org/3/library/asyncio-task.html"
    reply = {
        "id": "fixture-response", "status": status,
        "output": [{"type": "message", "content": [{
            "type": "output_text", "text": "fixture",
            "annotations": [{"type": "url_citation", "url": url}],
        }]}],
    }
    if error is not None:
        reply["error"] = error

    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self): return reply

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, *args, **kwargs):
            assert kwargs["json"]["max_tool_calls"] == 1
            return Response()

    class Store:
        def __init__(self, path): self.db = self
        def execute(self, query):
            assert query == "SELECT item FROM searches"
            return self
        def fetchall(self):
            return [(json.dumps({"action": {"query": query, "sources": [{"url": url}]}}),)]
        def close(self): pass

    class Gateway:
        def __init__(self, credential, token, store, catalog, **kwargs):
            self.port = 1
            self.token = "NONFUNCTIONAL_LOCAL_FIXTURE"
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

    selected = tmp_path / "selected-output"
    unused = tmp_path / "unselected-output"
    monkeypatch.setattr(sys, "argv", ["verify_search_audit.py", "--output", str(selected)])
    monkeypatch.setenv("CODEX_DEEPSEEK_TEST_EVIDENCE_ROOT", str(unused))
    monkeypatch.setenv("CODEX_DEEPSEEK_TRACE_DIR", "test-initial-value")
    monkeypatch.delenv("CODEX_DEEPSEEK_TEST_CATALOG", raising=False)
    monkeypatch.delenv("CODEX_DEEPSEEK_TEST_HTTP_BINARY", raising=False)
    monkeypatch.setattr(module, "catalog", lambda: {})
    monkeypatch.setattr(module, "credential", lambda: pytest.fail("must not read credentials"))
    monkeypatch.setattr(module, "Gateway", Gateway)
    monkeypatch.setattr(module, "Store", Store)
    monkeypatch.setattr(module.aiohttp, "ClientSession", Client)
    if passes:
        await module.main()
    else:
        with pytest.raises(AssertionError):
            await module.main()
    assert not unused.exists()
    runs = list(selected.iterdir())
    assert len(runs) == 1
    result = json.loads((runs[0] / "results.json").read_text(encoding="utf-8"))
    assert result["passed"] is passes
    assert result["status"] == status
    assert json.loads((runs[0] / "response.json").read_text()) == reply
