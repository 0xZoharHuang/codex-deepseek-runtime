"""Native networking tests use an explicit built/installed transport executable."""
import os
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def native_http_test_binary(monkeypatch):
    value=os.environ.get('CODEX_DEEPSEEK_TEST_HTTP_BINARY')
    if value:
        path=Path(value).resolve()
        assert path.is_file(),'The explicitly selected native HTTP test binary is missing'
        monkeypatch.setattr('codex_deepseek.native_http.native_helper',lambda:path)
