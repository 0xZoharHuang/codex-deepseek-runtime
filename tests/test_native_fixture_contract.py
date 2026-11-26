"""Verify that loopback fixtures persist provider outcomes in the real receipt store."""
from contextlib import closing
import json
from types import SimpleNamespace

from codex_deepseek.state import Store


def test_loopback_receipts_distinguish_provider_error_from_completion(tmp_path):
    with closing(Store(tmp_path / 'provider' / 'adapter.sqlite')) as state:
        failed = state.start()
        diagnostic = json.dumps({'schema': 1, 'event_type': 'upstream.http_error', 'error_code': 400})
        state.finish(failed, 'upstream_error', 400, detail=diagnostic)
        completed = state.start()
        event = SimpleNamespace(response_id='synthetic-response', usage={'input_tokens': 1})
        state.finish(completed, 'completed', 200, event)

        stmt = 'SELECT status,upstream_status,response_id,usage,detail FROM receipts WHERE id=?'
        assert state.db.execute(stmt, (failed,)).fetchone() == ('upstream_error', 400, None, '{}', diagnostic)
        assert state.db.execute(stmt, (completed,)).fetchone() == (
            'completed', 200, 'synthetic-response', '{"input_tokens": 1}', None,
        )
