from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
from threading import Event

import pytest

from codex_deepseek import thread_routes
from codex_deepseek.state import Store


def test_move_waits_for_admission_and_refuses_running_request(tmp_path):
    store = Store(tmp_path / 'adapter.sqlite')
    write_attempt = Event()

    def move_from_cli():
        with closing(sqlite3.connect(store.path, timeout=5)) as db:
            def observe(statement):
                if statement.startswith(('BEGIN IMMEDIATE', 'INSERT', 'DELETE')):
                    write_attempt.set()

            db.set_trace_callback(observe)
            return thread_routes.move(db, 'thread', 'morph/fp8')

    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            with store.route_admission():
                receipt = store.start(context_key='thread', commit=False)
                future = worker.submit(move_from_cli)
                assert write_attempt.wait(3)
            with pytest.raises(RuntimeError, match='in progress'):
                future.result(timeout=5)
        assert thread_routes.get(store.db, 'thread') is None
        store.finish(receipt, 'completed', 200)
        with closing(sqlite3.connect(store.path)) as db:
            assert thread_routes.move(db, 'thread', 'morph/fp8') == {
                'provider': 'morph/fp8', 'origin': 'manual',
            }
    finally:
        store.close()


def test_failed_admission_leaves_no_pin_or_receipt(tmp_path):
    store = Store(tmp_path / 'adapter.sqlite')
    try:
        with pytest.raises(ValueError, match='invalid admission'):
            with store.route_admission():
                thread_routes.pin(store.db, 'thread', 'morph/fp8', 'failover', commit=False)
                store.start('morph/fp8', context_key='thread', commit=False)
                raise ValueError('invalid admission')
        assert thread_routes.get(store.db, 'thread') is None
        assert store.db.execute('SELECT COUNT(*) FROM receipts').fetchone() == (0,)
    finally:
        store.close()


def test_native_request_update_is_scoped_to_the_running_owner(tmp_path):
    with closing(Store(tmp_path / 'adapter.sqlite')) as store, closing(Store(store.path)) as other:
        receipt = store.start(native_request={'status': 'recognized'})
        with pytest.raises(RuntimeError, match='owned running receipt'):
            other.record_native_request(receipt, {'status': 'foreign'})
        store.record_native_request(receipt, {'status': 'recognized', 'dropped_official_reasoning_items': 2})
        expected = ('{"dropped_official_reasoning_items":2,"status":"recognized"}',)
        assert store.db.execute('SELECT native_request FROM receipts WHERE id=?', (receipt,)).fetchone() == expected
        store.finish(receipt, 'completed', 200)
        with pytest.raises(RuntimeError, match='owned running receipt'):
            store.record_native_request(receipt, {'status': 'too late'})
        assert store.db.execute('SELECT native_request FROM receipts WHERE id=?', (receipt,)).fetchone() == expected


@pytest.mark.parametrize('status,http_status,eligible', [
    ('running', None, False),
    ('completed', 200, False),
    ('incomplete', 200, False),
    ('cancelled', 200, False),
    ('owner_lost', None, False),
    ('upstream_error', 400, False),
    ('upstream_error', None, False),
    ('upstream_error', 502, True),
])
def test_only_provider_http_failures_leave_unstarted_thread_eligible(tmp_path, status, http_status, eligible):
    with closing(Store(tmp_path / 'adapter.sqlite')) as store:
        receipt = store.start(context_key='thread')
        if status != 'running':
            store.finish(receipt, status, http_status)
        assert thread_routes.is_new_thread(store.db, 'thread', 'question') is eligible


@pytest.mark.parametrize('kind', [
    'function_call_output', 'custom_tool_call_output', 'tool_search_call',
    'tool_search_output', 'web_search_call', 'reasoning', 'agent_message',
])
def test_retained_tool_or_agent_history_cannot_be_treated_as_new(tmp_path, kind):
    with closing(Store(tmp_path / 'adapter.sqlite')) as store:
        assert thread_routes.is_new_thread(store.db, 'thread', [{'type': 'message', 'role': 'user'}])
        assert not thread_routes.is_new_thread(store.db, 'thread', [{'type': kind, 'role': 'user'}])


def test_thread_commands_skip_non_native_or_invalid_receipts(tmp_path, capsys):
    home = tmp_path / 'home'
    trusted = '01234567-89ab-4cde-8fab-0123456789ab'
    invalid = '01234567-89ab-4cde-8fab-0123456789af'
    evidence = {'status': 'recognized', 'source': 'client_metadata.x-codex-turn-metadata',
                'request_kind': 'compaction', 'thread_id': trusted}
    with closing(Store(home / 'provider-state' / 'adapter.sqlite')) as store:
        complete = store.start(context_key=trusted, native_request=evidence)
        store.finish(complete, 'completed', 200)
        # Newer direct/synthetic alias activity cannot replace last provider or thread selection.
        store.start('morph/fp8', context_key=trusted)
        store.start(context_key='bare-cache-key', native_request={**evidence, 'thread_id': 'bare-cache-key'})
        store.start(context_key=invalid, native_request={**evidence, 'request_kind': 'memory', 'thread_id': invalid})
        store.start(context_key=invalid, native_request=evidence)
        malformed = store.start(context_key=invalid, native_request={**evidence, 'thread_id': invalid})
        with store.db:
            store.db.execute('UPDATE receipts SET native_request=? WHERE id=?', ('{invalid', malformed))
        assert thread_routes.command(home, ['list']) == 0
        listing = json.loads(capsys.readouterr().out)
        assert [entry['thread'] for entry in listing] == [trusted]
        assert listing[0]['last_provider'] == 'deepseek'
        for target in ('bare-cache-key', invalid):
            with pytest.raises(ValueError, match='recognized native'):
                thread_routes.command(home, ['show', target])
        store.db.execute("UPDATE receipts SET status='cancelled' WHERE status='running'")
        store.db.commit()
        assert thread_routes.command(home, ['move', 'last', 'morph/fp8']) == 0
        moved = json.loads(capsys.readouterr().out)
        assert moved['thread'] == trusted
        assert moved['pin'] == {'provider': 'morph/fp8', 'origin': 'manual'}
