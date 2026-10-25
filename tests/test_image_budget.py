"""Offline-only diagnostic and exact existing selection/receipt regression tests."""
from __future__ import annotations
import asyncio
import copy
import io
import json
import socket
import sqlite3
import subprocess
from pathlib import Path

import pytest

from codex_deepseek import cli, image_budget
from codex_deepseek.gateway import Gateway
from codex_deepseek.image_provider import ImageProvider, parse_image_config
from codex_deepseek.image_transport import (
    ImageTransport, TransportPolicy, PayloadBudgetExceeded, FileReferenceLimitExceeded,
    encode_body, image_plan, count_file_references, select_image_references, plan_image_budget)
from codex_deepseek.protocol import MODEL, prepare
from codex_deepseek.state import Store
from test_image_transport import FakeFiles, png, part
from test_image_provider import FakeNative, KEY


def normalized(parts=None):
    return prepare({'model': MODEL, 'input': [{'type': 'message', 'role': 'user',
        'content': [{'type': 'input_text', 'text': 'PRIVATE-TEXT-SENTINEL'}] + (parts or [part()])}]})[0]


def policy_value(**updates):
    value = {'schema_version': 1, 'enabled': True, 'native_outbound_policy': 'system',
             'activate_above_bytes': 1, 'wire_budget_bytes': 1_000_000}
    value.update(updates)
    return value


def write_inputs(tmp_path, value=None, policy=None):
    request = tmp_path / 'request-PRIVATE-PATH-SENTINEL.json'
    config = tmp_path / 'policy.json'
    request.write_bytes(json.dumps(value if value is not None else normalized(), indent=2).encode())
    config.write_text(json.dumps(policy or policy_value()), encoding='utf-8')
    return request, config


def args(request, config):
    return ['runtime', 'image-budget', '--input-kind', 'normalized-responses-json',
            '--request', str(request), '--policy-json', str(config)]


@pytest.mark.parametrize('threshold_offset', [-1, 0, 1])
@pytest.mark.parametrize('slots', [0, 1, 16])
def test_pure_plan_matches_original_selection_and_activation(threshold_offset, slots):
    payload = normalized([part(detail='low'), part(png((4, 5, 6)), 'high'), part()])
    payload['input'].append({'type': 'function_call_output', 'call_id': 'actual-call',
                            'output': [part(detail='original')]})
    original = encode_body(payload)
    threshold = len(original) + threshold_offset
    policy = TransportPolicy(threshold, max_file_references=slots)
    before = copy.deepcopy(payload)
    plan = plan_image_budget(payload, policy)
    assert payload == before
    assert plan.original == original
    if len(original) <= threshold:
        assert plan.selected == [] and plan.positions == [] and plan.blobs == {}
        assert plan.diagnostics['status'] == 'activation_bypass'
        assert plan.diagnostics['image_scan_performed'] is False
        assert plan.diagnostics['inline_image_occurrences'] is None
        assert plan.diagnostics['existing_file_reference_occurrences'] is None
    else:
        positions, blobs = image_plan(before)
        picked = select_image_references(before, positions, slots)
        estimate = copy.deepcopy(before)
        for (i, key, j), _ in picked:
            item = estimate['input'][i][key][j]
            del item['image_url']
            item['file_id'] = 'or_file_' + 'x' * 24
        assert plan.selected == picked
        assert plan.diagnostics['estimated_min_bytes'] == len(encode_body(estimate))
        assert plan.diagnostics['planned_unique_images'] == len({digest for _, digest in picked})
        assert plan.diagnostics['low_detail_inline_occurrences'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['custom', 'function'])
async def test_native_typed_outputs_share_plan_and_actual_lossless_prepare(tmp_path, monkeypatch, kind):
    import codex_deepseek.image_transport as module
    data = png((1, 2, 3))
    definition = {'type': kind, 'name': 'read_image', 'description': 'synthetic'}
    call = {'type': kind + '_tool_call' if kind == 'custom' else 'function_call',
            'call_id': 'EXACT-CALL', 'name': 'read_image'}
    if kind == 'custom':
        call['input'] = 'synthetic'
        definition['format'] = {'type': 'text'}
    else:
        call['arguments'] = '{}'
        definition['parameters'] = {'type': 'object'}
    native = {'model': MODEL, 'tools': [definition], 'input': [
        {'type': 'message', 'role': 'user', 'content': [part(data, 'low')]},
        call,
        {'type': 'custom_tool_call_output' if kind == 'custom' else 'function_call_output',
         'call_id': 'EXACT-CALL', 'output': [part(data, 'high'), part(data, 'low')]}]}
    payload, _ = prepare(native)
    before = copy.deepcopy(payload)
    plan = plan_image_budget(payload, TransportPolicy(1, max_file_references=1))
    assert len(plan.selected) == 1
    counters = {'plan': 0, 'select': 0}
    actual_plan, actual_select = module.plan_image_budget, module.select_image_references
    def tracked_plan(*a, **kw):
        counters['plan'] += 1
        return actual_plan(*a, **kw)
    def tracked_select(*a, **kw):
        counters['select'] += 1
        return actual_select(*a, **kw)
    monkeypatch.setattr(module, 'plan_image_budget', tracked_plan)
    monkeypatch.setattr(module, 'select_image_references', tracked_select)
    files = FakeFiles()
    transport = ImageTransport(tmp_path / 'cache', lambda: 'SYNTHETIC', files,
                               TransportPolicy(1, max_file_references=1))
    async with transport.prepare(payload) as result:
        assert counters == {'plan': 1, 'select': 1}
        assert result.image_occurrences == 3 and result.converted_occurrences == 1
        assert result.payload['input'][2]['call_id'] == 'EXACT-CALL'
        assert result.payload['input'][0] == before['input'][0]
        assert result.payload['input'][2]['output'][1] == before['input'][2]['output'][1]
        assert result.payload['input'][2]['output'][0]['detail'] == 'high'
        assert files.uploaded[0][1] == data
        assert payload == before


@pytest.mark.asyncio
async def test_known_infeasible_plan_fails_before_credentials_or_files_with_aggregates(tmp_path):
    value = normalized([part(detail='low')] * 143)
    value['input'][0]['content'][0]['text'] += 'x' * 50_000
    policy = TransportPolicy(1, wire_budget_bytes=30_000)
    plan = plan_image_budget(value, policy)
    def forbidden():
        raise AssertionError('credential lookup')
    transport = ImageTransport(tmp_path / 'cache', forbidden, FakeFiles(), policy)
    with pytest.raises(PayloadBudgetExceeded) as caught:
        async with transport.prepare(value):
            raise AssertionError('infeasible request yielded')
    budget = caught.value.diagnostics
    assert budget == plan.diagnostics
    assert budget['before_bytes'] == len(encode_body(value))
    assert budget['estimated_min_bytes'] == budget['before_bytes']
    assert budget['estimated_gap_bytes'] == budget['before_bytes'] - 30_000
    assert budget['inline_image_occurrences'] == budget['low_detail_inline_occurrences'] == 143
    assert budget['selected_image_occurrences'] == 0
    assert budget['upstream_inference_sent'] is False
    assert 'compaction is required' not in str(caught.value)
    assert not transport.backend.uploaded


@pytest.mark.parametrize('threshold', [1, 100_000])
def test_existing_references_are_occurrences_and_bypass_keeps_existing_semantics(threshold):
    value = normalized()
    value['input'][0]['content'] += [
        {'type': 'input_image', 'file_id': 'or_file_PRIVATE-ID-SENTINEL'}] * 17
    plan = plan_image_budget(value, TransportPolicy(threshold))
    assert count_file_references(value) == 17
    if threshold == 1:
        assert plan.diagnostics['existing_file_reference_occurrences'] == 17
        assert plan.diagnostics['status'] == 'file_reference_limit_exceeded'
        assert plan.diagnostics['estimated_min_bytes'] is None
        with pytest.raises(FileReferenceLimitExceeded) as caught:
            plan.require_feasible()
        assert caught.value.diagnostics == plan.diagnostics
    else:
        assert plan.diagnostics['status'] == 'activation_bypass'
        assert plan.diagnostics['existing_file_reference_occurrences'] is None
        plan.require_feasible()


@pytest.mark.asyncio
async def test_long_actual_ids_retain_post_upload_budget_failure_phase(tmp_path):
    value = normalized()
    estimate = plan_image_budget(value, TransportPolicy(1)).diagnostics['estimated_min_bytes']
    class LongIds(FakeFiles):
        async def upload(self, key, data, mime, filename):
            from codex_deepseek.image_transport import FileMetadata
            self.uploaded.append((key, data, mime, filename))
            return FileMetadata('or_file_' + 'L' * 128, filename, mime, len(data))
    files = LongIds()
    transport = ImageTransport(tmp_path / 'cache', lambda: 'SYNTHETIC', files,
                               TransportPolicy(1, wire_budget_bytes=estimate))
    with pytest.raises(PayloadBudgetExceeded) as caught:
        async with transport.prepare(value):
            raise AssertionError('long-ID request yielded')
    budget = caught.value.diagnostics
    assert budget['estimated_min_bytes'] == estimate
    assert budget['actual_wire_bytes'] > estimate
    assert budget['phase'] == 'post_upload' and budget['new_uploads'] == 1
    assert budget['upstream_inference_sent'] is False
    assert 'or_file_' not in json.dumps(budget)


@pytest.mark.asyncio
async def test_offline_cli_early_dispatch_poisoned_startup_and_privacy(tmp_path, monkeypatch, capsys):
    value = normalized([part(), {'type': 'input_image', 'image_url': 'https://PRIVATE-URL-SENTINEL.invalid/image'}])
    value['input'][0]['content'].append({'type': 'input_image', 'file_id': 'or_file_PRIVATE-ID-SENTINEL'})
    request, config = write_inputs(tmp_path, value)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    called = []
    def forbidden(*a, **kw):
        called.append(True)
        raise AssertionError('offline command crossed a startup boundary')
    for name in ('data_root', 'native', 'Store', 'ProviderService', 'credential', 'check_home_ipc'):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr('codex_deepseek.image_transport.ImageTransport.__init__', forbidden)
    monkeypatch.setattr('codex_deepseek.image_transport.OpenRouterFilesBackend.__init__', forbidden)
    monkeypatch.setattr('codex_deepseek.native_http.NativeTransport.start', forbidden)
    assert await cli.run(args(request, config)) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == '' and called == []
    assert result['input_stage'] == 'after_protocol_prepare_before_image_transport'
    assert result['canonical_encoding_matches_input'] is False
    assert result['serialized_input_bytes'] == len(request.read_bytes())
    assert result['budget']['existing_file_reference_occurrences'] == 1
    assert result['budget']['actual_wire_bytes'] is None
    for sentinel in ('PRIVATE-TEXT', 'PRIVATE-URL', 'PRIVATE-ID', 'PRIVATE-PATH', 'data:image', 'or_file_'):
        assert sentinel not in captured.out
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.asyncio
async def test_explicit_profile_policy_and_bounded_stdin(tmp_path, monkeypatch, capsys):
    value = normalized()
    state = tmp_path / 'selected-home/provider-state'
    state.mkdir(parents=True)
    policy = state / 'image-transport.json'
    policy.write_text(json.dumps(policy_value()))
    request = encode_body(value)
    stream = io.TextIOWrapper(io.BytesIO(request), encoding='utf-8')
    monkeypatch.setattr('sys.stdin', stream)
    assert await cli.run(['runtime', 'image-budget', '--input-kind', 'normalized-responses-json',
                          '--request', '-', '--profile-home', str(state.parent)]) == 0
    assert json.loads(capsys.readouterr().out)['policy_source'] == 'explicit_profile_home'
    stream = io.TextIOWrapper(io.BytesIO(b'x' * 17), encoding='utf-8')
    monkeypatch.setattr('sys.stdin', stream)
    monkeypatch.setattr(image_budget, 'MAX_REQUEST_BYTES', 16)
    assert await cli.run(['runtime', 'image-budget', '--input-kind', 'normalized-responses-json',
                          '--request', '-', '--profile-home', str(state.parent)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err) == {'status': 'invalid_input',
        'error': 'serialized_input_too_large', 'observed_bytes_at_least': 17, 'read_limit_bytes': 16}


@pytest.mark.parametrize('bad', [
    b'{"model": "one", "model": "two"}', b'{"x": NaN}', b'{"x": 1e999}',
    b'[]', b'null', b'{"authorization": "PRIVATE-SECRET-SENTINEL"}'])
def test_strict_json_invalid_inputs_are_safely_rejected(tmp_path, capsys, bad):
    request, config = write_inputs(tmp_path)
    request.write_bytes(bad)
    assert image_budget.image_budget_command(args(request, config)[2:]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['status'] == 'invalid_input'
    assert 'PRIVATE' not in captured.err


@pytest.mark.parametrize('change', [
    lambda v: v.update(extra_unsupported='PRIVATE-SECRET'),
    lambda v: v['input'].append({'type': 'custom_tool_call_output', 'call_id': 'x', 'output': []}),
    lambda v: v['input'][0]['content'].append({'type': 'input_audio', 'data': 'PRIVATE-SECRET'}),
    lambda v: v['input'][0]['content'].append({'type': 'input_image'}),
    lambda v: v.update(provider={'only': ['other'], 'allow_fallbacks': False, 'require_parameters': True}),
])
def test_unsupported_or_non_normalized_schema_is_not_estimated(tmp_path, capsys, change):
    value = normalized()
    change(value)
    request, config = write_inputs(tmp_path, value)
    assert image_budget.image_budget_command(args(request, config)[2:]) == 2
    assert 'PRIVATE' not in capsys.readouterr().err


def test_policy_schema_missing_duplicate_and_unexpected_arguments_are_safe(tmp_path, capsys):
    request, config = write_inputs(tmp_path)
    for content in ('{"enabled": true}', '{"schema_version":1,"schema_version":1}',
                    '{"schema_version":1,"enabled":false}'):
        config.write_text(content)
        assert image_budget.image_budget_command(args(request, config)[2:]) == 2
    config.write_text(json.dumps(policy_value()))
    assert image_budget.image_budget_command(args(request, config)[2:] + ['--PRIVATE-SECRET']) == 2
    assert 'PRIVATE' not in capsys.readouterr().err


def test_diagnostic_exception_rejects_arbitrary_fields_and_strings():
    value = normalized()
    aggregate = plan_image_budget(value, TransportPolicy(1)).diagnostics
    for bad in ({**aggregate, 'file_id': 'PRIVATE-ID'},
                {**aggregate, 'before_bytes': 'PRIVATE-TEXT'},
                {**aggregate, 'phase': 'PRIVATE-URL'}):
        with pytest.raises(ValueError, match='Invalid image-budget diagnostic aggregate'):
            PayloadBudgetExceeded('safe', diagnostics=bad)


@pytest.mark.asyncio
async def test_gateway_real_error_receipts_persist_only_aggregate_without_http_startup(tmp_path, monkeypatch):
    monkeypatch.delenv('CODEX_DEEPSEEK_TRACE_DIR', raising=False)
    native = FakeNative()
    reads = []
    def credential():
        reads.append(True)
        return KEY
    store = Store(tmp_path / 'adapter.sqlite')
    gateway = Gateway(credential, 'LOCAL', store, {})
    gateway.client = native
    gateway.images = ImageProvider(tmp_path, native, credential,
        parse_image_config(policy_value(wire_budget_bytes=128)))
    class Request:
        headers = {'Authorization': 'Bearer LOCAL'}
        async def json(self):
            return {'model': MODEL, 'input': 'PRIVATE-TEXT-SENTINEL' * 200}
    try:
        response = await gateway.responses(Request())
        assert response.status == 413 and native.calls == []
        assert reads == [True]  # The live gateway reads credentials; offline CLI does not.
        row = store.db.execute('SELECT id,status,detail FROM receipts').fetchone()
        assert row[1] == 'adapter_error'
        main = json.loads(row[2])
        assert main['image_budget']['before_bytes'] > 128
        proof = gateway.images.read_receipt(gateway.images.receipts, row[0])
        image = proof['adaptations']['image_transport']
        assert image['budget_failure'] == main['image_budget']
        assert image['consumption_status'] == 'not_sent'
        assert proof['status'] == 'adapter_error' and proof['response_id'] is None
        assert 'PRIVATE' not in row[2] + json.dumps(proof)
        assert 'native compaction is required' not in response.text
    finally:
        store.close()


def test_143_high_images_over_30mib_is_a_pure_infeasible_aggregate():
    import random, struct, zlib
    def random_png(seed):
        def chunk(kind, data):
            return (struct.pack('!I', len(data)) + kind + data +
                    struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff))
        data = random.Random(seed).randbytes(256 * 256 * 3)
        rows = b''.join(b'\0' + data[row * 768:(row + 1) * 768] for row in range(256))
        return (b'\x89PNG\r\n\x1a\n' +
                chunk(b'IHDR', struct.pack('!2I5B', 256, 256, 8, 2, 0, 0, 0)) +
                chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    images = [random_png(seed) for seed in range(4)]
    value = normalized([part(images[i % 4], 'high') for i in range(143)])
    original = encode_body(value)
    policy = TransportPolicy(1024 * 1024, wire_budget_bytes=30 * 1024 * 1024)
    plan = plan_image_budget(value, policy)
    assert plan.diagnostics['status'] == 'wire_budget_exceeded'
    assert plan.diagnostics['before_bytes'] > 30 * 1024 * 1024
    assert plan.diagnostics['estimated_min_bytes'] > 30 * 1024 * 1024
    assert plan.diagnostics['selected_image_occurrences'] == 16
    assert plan.diagnostics['planned_unique_images'] == 4
    assert plan.diagnostics['remaining_inline_image_occurrences'] == 127
    assert encode_body(value) == original
    with pytest.raises(PayloadBudgetExceeded) as caught:
        plan.require_feasible()
    assert caught.value.diagnostics == plan.diagnostics


@pytest.mark.asyncio
async def test_offline_cli_returns_infeasible_aggregate_without_echoing_payload(tmp_path, capsys):
    value = normalized()
    value['input'][0]['content'][0]['text'] *= 1000
    request, config = write_inputs(tmp_path, value, policy_value(wire_budget_bytes=128))
    assert await cli.run(args(request, config)) == 2
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ''
    assert result['budget']['status'] == 'wire_budget_exceeded'
    assert result['budget']['estimated_gap_bytes'] > 0
    assert result['budget']['actual_wire_bytes'] is None
    assert 'PRIVATE' not in captured.out


@pytest.mark.parametrize('case', ['noncanonical', 'invalid_base64', 'empty_bytes', 'conflicting_mime'])
def test_image_transport_validation_errors_keep_offline_static_json_contract(tmp_path, capsys, case):
    if case == 'conflicting_mime':
        first = part()
        second = copy.deepcopy(first)
        second['image_url'] = second['image_url'].replace('data:image/png;', 'data:image/jpeg;', 1)
        parts = [first, second]
    else:
        urls = {'noncanonical': 'data:image/png;base64,%%%PRIVATE-SENTINEL',
                'invalid_base64': 'data:image/png;base64,A',
                'empty_bytes': 'data:image/png;base64,'}
        parts = [{'type': 'input_image', 'image_url': urls[case], 'detail': 'high'}]
    request, config = write_inputs(tmp_path, normalized(parts))
    assert image_budget.image_budget_command(args(request, config)[2:]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err) == {'status': 'invalid_input',
        'error': 'Invalid image-budget input, normalized schema, or explicit policy'}
    assert 'PRIVATE' not in captured.err and 'data:image' not in captured.err


def test_profile_config_actual_read_is_bounded_even_when_stat_reports_small(tmp_path, monkeypatch, capsys):
    import os
    state = tmp_path / 'selected-home/provider-state'
    state.mkdir(parents=True)
    policy = state / 'image-transport.json'
    policy.write_bytes(json.dumps(policy_value()).encode() + b' ' * 30_000)
    request = tmp_path / 'request.json'
    request.write_bytes(encode_body(normalized()))
    original_stat, original_open = Path.stat, Path.open
    reads = []
    def small_stat(path, *a, **kw):
        value = original_stat(path, *a, **kw)
        if path == policy:
            fields = list(value)
            fields[6] = 1
            return os.stat_result(fields)
        return value
    class BoundReader:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def read(self, size=-1):
            reads.append(size)
            assert size == 16385, 'configuration read must be truly bounded'
            return self.stream.read(size)
        def __exit__(self, *exc):
            self.stream.close()
    def observed_open(path, *a, **kw):
        stream = original_open(path, *a, **kw)
        return BoundReader(stream) if path == policy else stream
    monkeypatch.setattr(Path, 'stat', small_stat)
    monkeypatch.setattr(Path, 'open', observed_open)
    result = image_budget.image_budget_command(['--input-kind', 'normalized-responses-json',
        '--request', str(request), '--profile-home', str(state.parent)])
    assert result == 2 and reads == [16385]
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['status'] == 'invalid_input'
    assert 'PRIVATE' not in captured.err and str(policy) not in captured.err
