"""Bundled browser upgrades preserve unrelated config bytes and newline style."""
import sys

import pytest

from codex_deepseek import web_config


@pytest.mark.parametrize('eol', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_existing_browser_update_preserves_unrelated_newlines_and_is_idempotent(tmp_path, monkeypatch, eol):
    prefix = '# retain this comment'+eol+'[unrelated]'+eol+'value = "untouched"'+eol+eol
    config = (prefix+'[mcp_servers.codex_web]'+eol+'enabled = false'+eol+
              'command = "old-python"'+eol+'args = ["codex_deepseek.web_mcp"]'+eol)
    path = tmp_path/'config.toml'
    path.write_bytes(config.encode())
    monkeypatch.setattr(sys, 'executable', 'new-python')
    web_config.ensure_browser(tmp_path)
    actual = path.read_bytes()
    assert actual.startswith(prefix.encode())
    assert b'enabled = false' in actual and b'command = "new-python"' in actual
    if eol == '\r\n':
        assert actual.count(b'\r\n') == actual.count(b'\n')
    else:
        assert b'\r\n' not in actual
    def unexpected_replace(*args):
        raise AssertionError('unchanged config must not be rewritten')
    monkeypatch.setattr(web_config, 'replace_file', unexpected_replace)
    web_config.ensure_browser(tmp_path)
    assert path.read_bytes() == actual


def test_creating_browser_stanza_respects_existing_crlf(tmp_path, monkeypatch):
    original = b'# retained\r\n[unrelated]\r\nvalue = "untouched"\r\n'
    path = tmp_path/'config.toml'
    path.write_bytes(original)
    monkeypatch.setattr(sys, 'executable', 'new-python')
    web_config.ensure_browser(tmp_path)
    actual = path.read_bytes()
    assert actual.startswith(original)
    assert actual.count(b'\r\n') == actual.count(b'\n')
    assert b'codex_deepseek.web_mcp' in actual
    def unexpected_replace(*args):
        raise AssertionError('unchanged config must not be rewritten')
    monkeypatch.setattr(web_config, 'replace_file', unexpected_replace)
    web_config.ensure_browser(tmp_path)
    assert path.read_bytes() == actual


def test_mixed_existing_line_endings_are_not_globally_normalized(tmp_path, monkeypatch):
    prefix = b'# first\r\n[unrelated]\nvalue = "untouched"\r\n\n'
    path = tmp_path/'config.toml'
    path.write_bytes(prefix+b'[mcp_servers.codex_web]\ncommand = "old-python"\nargs = ["codex_deepseek.web_mcp"]\n')
    monkeypatch.setattr(sys, 'executable', 'new-python')
    web_config.ensure_browser(tmp_path)
    assert path.read_bytes().startswith(prefix)
