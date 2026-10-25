"""Offline aggregate diagnostics for an explicitly supplied normalized request.

This module never reconstructs history or accesses a provider/cache/native process.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .image_transport import ImageTransportError, _strict_json, plan_image_budget
from .image_provider import load_image_config, parse_image_config
from .protocol import MODEL, PROVIDER, STRUCTURED_PROVIDER, SERIAL_PROVIDER

MAX_REQUEST_BYTES = 96 * 1024 * 1024
_REQUEST_FIELDS = {
    'model', 'input', 'tools', 'tool_choice', 'parallel_tool_calls', 'reasoning',
    'store', 'stream', 'stream_options', 'include', 'prompt_cache_key', 'text',
    'service_tier', 'client_metadata', 'access_programs', 'instructions',
    'provider', 'temperature', 'top_p', 'max_output_tokens', 'metadata',
    'user', 'safety_identifier', 'truncation', 'prompt_cache_retention',
}


class _InputTooLarge(ValueError):
    def __init__(self, observed: int, limit: int):
        self.observed, self.limit = observed, limit
        super().__init__('Serialized diagnostic input exceeds its read limit')


class _Arguments(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default can echo an unknown argument containing a secret/path.
        raise ValueError('Invalid diagnostic arguments')


def _read(path: str, limit: int) -> bytes:
    if path == '-':
        raw = getattr(sys.stdin, 'buffer', sys.stdin).read(limit + 1)
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
    else:
        with Path(path).open('rb') as stream:
            raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise _InputTooLarge(len(raw), limit)
    return raw


def _parts(content, *, role=None):
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        raise ValueError('Invalid normalized content')
    for part in content:
        if not isinstance(part, dict):
            raise ValueError('Invalid normalized content')
        kind = part.get('type')
        if kind in {'input_text', 'output_text'}:
            if not isinstance(part.get('text'), str):
                raise ValueError('Invalid normalized text')
        elif kind == 'input_image':
            if role is not None and role not in {'user', 'developer'}:
                raise ValueError('Invalid normalized image role')
            url, fid = part.get('image_url'), part.get('file_id')
            if (isinstance(url, str) and bool(url)) == (isinstance(fid, str) and bool(fid)):
                raise ValueError('Normalized image needs exactly one representation')
            if 'image_url' in part and not isinstance(url, str):
                raise ValueError('Invalid normalized image URL')
            if 'file_id' in part and not isinstance(fid, str):
                raise ValueError('Invalid normalized file reference')
            if part.get('detail', 'auto') not in {'auto', 'low', 'high', 'original'}:
                raise ValueError('Invalid normalized image detail')
        else:
            raise ValueError('Unsupported normalized content type')


def validate_normalized_request(value):
    """Validate the declared stage without applying protocol.prepare a second time.

    Unknown top-level controls or native-only item/tool types are refused, not
    silently omitted. All accepted JSON values remain part of the byte estimate.
    This is a diagnostic input contract, not a replacement runtime validator.
    """
    if not isinstance(value, dict) or not set(value) <= _REQUEST_FIELDS:
        raise ValueError('Unsupported normalized request fields')
    if value.get('model') != MODEL or value.get('store') is not False:
        raise ValueError('Request is not normalized for the pinned stateless model')
    provider = value.get('provider')
    if (not isinstance(provider, dict)
        or set(provider) != {'only', 'allow_fallbacks', 'require_parameters'}
        or provider['only'] not in [[PROVIDER], [STRUCTURED_PROVIDER], [SERIAL_PROVIDER]]
        or provider['allow_fallbacks'] is not False or provider['require_parameters'] is not True):
        raise ValueError('Request lacks an exact normalized provider route')
    reasoning = value.get('reasoning')
    if (not isinstance(reasoning, dict) or set(reasoning) != {'effort'}
        or reasoning['effort'] not in {'none', 'low', 'high', 'max'}):
        raise ValueError('Request lacks normalized reasoning controls')
    for name in ('stream', 'parallel_tool_calls'):
        if name in value and type(value[name]) is not bool:
            raise ValueError('Invalid normalized boolean control')
    tools = value.get('tools')
    if not isinstance(tools, list):
        raise ValueError('Normalized tools must be an array')
    for tool in tools:
        if not isinstance(tool, dict) or tool.get('type') not in {'function', 'openrouter:web_search'}:
            raise ValueError('Unsupported normalized tool type')
        if tool['type'] == 'function' and (not isinstance(tool.get('name'), str)
                or not isinstance(tool.get('parameters'), dict)):
            raise ValueError('Invalid normalized function schema')
    items = value.get('input')
    if isinstance(items, str):
        return
    if not isinstance(items, list):
        raise ValueError('Normalized input must be text or an array')
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Invalid normalized input item')
        kind = item.get('type', 'message')
        if kind == 'message':
            if item.get('role') not in {'system', 'developer', 'user', 'assistant'}:
                raise ValueError('Invalid normalized message role')
            _parts(item.get('content'), role=item['role'])
        elif kind == 'function_call_output':
            if not isinstance(item.get('call_id'), str) or not item['call_id']:
                raise ValueError('Invalid normalized tool call identity')
            _parts(item.get('output'))
        elif kind == 'function_call':
            if any(not isinstance(item.get(name), str) for name in ('call_id', 'name', 'arguments')):
                raise ValueError('Invalid normalized function call')
        elif kind == 'reasoning':
            parts = item.get('content') or item.get('summary') or []
            if not isinstance(parts, list) or any(
                not isinstance(part, dict) or not isinstance(part.get('text'), str) for part in parts):
                raise ValueError('Invalid normalized reasoning history')
            if item.get('encrypted_content'):
                raise ValueError('Opaque history is not a normalized request')
        elif kind != 'openrouter:web_search':
            raise ValueError('Unsupported normalized history item')


def image_budget_command(arguments: list[str]) -> int:
    parser = _Arguments(prog='codex-deepseek runtime image-budget',
        description='Offline aggregate estimate; no upload, inference or native startup.')
    parser.add_argument('--input-kind', required=True, choices=['normalized-responses-json'])
    parser.add_argument('--request', required=True, help='Exact normalized JSON file, or - for stdin')
    policy = parser.add_mutually_exclusive_group(required=True)
    policy.add_argument('--policy-json', help='Explicit existing image-transport policy JSON file')
    policy.add_argument('--profile-home', help='Explicit CDS home; read provider-state/image-transport.json only')
    try:
        args = parser.parse_args(arguments)
        if args.policy_json:
            if args.policy_json == '-':
                raise ValueError('Policy requires an explicit JSON file')
            config = parse_image_config(_strict_json(_read(args.policy_json, 16384)))
            policy_source = 'explicit_policy_json'
        else:
            config = load_image_config(Path(args.profile_home) / 'provider-state')
            policy_source = 'explicit_profile_home'
        if config is None:
            raise ValueError('An explicit enabled image transport policy is required')
        raw = _read(args.request, MAX_REQUEST_BYTES)
        payload = _strict_json(raw)
        validate_normalized_request(payload)
        plan = plan_image_budget(payload, config['policy'])
        result = {
            'input_kind': args.input_kind,
            'input_stage': 'after_protocol_prepare_before_image_transport',
            'serialized_input_bytes': len(raw),
            'canonical_encoding_matches_input': raw == plan.original,
            'policy_source': policy_source,
            'budget': plan.diagnostics,
        }
        print(json.dumps(result, ensure_ascii=True, separators=(',', ':'), allow_nan=False))
        return 2 if plan.diagnostics['status'] in {'wire_budget_exceeded', 'file_reference_limit_exceeded'} else 0
    except _InputTooLarge as exc:
        print(json.dumps({'status': 'invalid_input', 'error': 'serialized_input_too_large',
                          'observed_bytes_at_least': exc.observed, 'read_limit_bytes': exc.limit}),
              file=sys.stderr)
        return 2
    except (ImageTransportError, ValueError, TypeError, KeyError, OSError, RecursionError, UnicodeError):
        # Never echo JSON parse snippets, filenames, URLs, file IDs or argument values.
        print(json.dumps({'status': 'invalid_input',
                          'error': 'Invalid image-budget input, normalized schema, or explicit policy'}),
              file=sys.stderr)
        return 2
