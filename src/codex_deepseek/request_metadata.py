"""Bounded native Core request-purpose evidence; no Store or runtime ownership.

Only an enum kind (at most10 ASCII characters), canonical36-character UUIDs,
and fixed provenance/status/reason literals are retained. Full native metadata,
workspace/tool inventories, arbitrary extra fields and prompt text are not saved.
The native metadata envelope is bounded to1MiB UTF-8; unknown evidence never
changes the forwarded inference request.
"""
from __future__ import annotations
import json
import uuid

MAX_METADATA_BYTES = 1024 * 1024

def decode_native_metadata_envelope(raw, *, max_bytes=MAX_METADATA_BYTES):
    """Strict bounded native envelope decoding shared with wire-only codecs."""
    if not isinstance(raw, str) or len(raw) > max_bytes or len(raw.encode('utf-8')) > max_bytes:
        raise ValueError('invalid metadata envelope')
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result: raise ValueError('duplicate metadata key')
            result[name] = value
        return result
    def nonfinite(_): raise ValueError('nonfinite metadata')
    def finite_float(text):
        value = float(text)
        if abs(value) == float('inf'): raise ValueError('nonfinite metadata')
        return value
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite, parse_float=finite_float)
    if not isinstance(value, dict): raise ValueError('invalid metadata object')
    return value



def native_request_metadata(body, headers):
    """Bounded purpose/identity evidence from native Core metadata, never prompts.

    This is observational only. Missing, malformed or conflicting metadata stays
    explicit and cannot change an otherwise compatible model request's behavior.
    Canonical body metadata is authoritative; HTTP metadata is its projection.
    """
    key = 'x-codex-turn-metadata'
    source = 'client_metadata.x-codex-turn-metadata'
    base = {'schema_version': 1, 'status': 'unknown', 'source': source,
            'header_consistency': 'not_checked'}
    def invalid(reason, status='invalid', consistency='not_checked'):
        return {**base, 'status': status, 'header_consistency': consistency, 'reason': reason}
    def decode(raw):
        value = decode_native_metadata_envelope(raw)
        kind = value.get('request_kind')
        if kind not in {'turn', 'compaction', 'prewarm', 'memory'}:
            raise ValueError('unknown native request kind')
        projection = {'request_kind': kind}
        for name in ('thread_id', 'turn_id'):
            identifier = value.get(name)
            if identifier is None:
                if kind in {'turn', 'compaction'}: raise ValueError('missing native identity')
                continue
            if not isinstance(identifier, str) or len(identifier) != 36 or str(uuid.UUID(identifier)) != identifier:
                raise ValueError('invalid native identity')
            projection[name] = identifier
        return projection
    client = body.get('client_metadata') if isinstance(body, dict) else None
    if not isinstance(client, dict) or key not in client:
        return {**base, 'source': 'absent', 'reason': 'canonical_metadata_absent'}
    try: projection = decode(client[key])
    except (ValueError, TypeError, RecursionError): return invalid('invalid_canonical_metadata')
    for name in ('thread_id', 'turn_id'):
        if name in projection and name in client and client[name] != projection[name]:
            return invalid('flat_identity_conflict', 'conflict', 'conflict')
    try: header_values = [value for name, value in headers.items()
                          if isinstance(name, str) and name.casefold() == key]
    except (AttributeError, TypeError): header_values = []
    if not header_values:
        return {**base, 'status': 'recognized', 'header_consistency': 'absent', **projection}
    if len(header_values) != 1: return invalid('ambiguous_header_metadata')
    try: header = decode(header_values[0])
    except (ValueError, TypeError, RecursionError): return invalid('invalid_header_metadata', consistency='invalid')
    if header != projection: return invalid('header_projection_conflict', 'conflict', 'conflict')
    return {**base, 'status': 'recognized', 'header_consistency': 'match', **projection}
