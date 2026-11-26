"""Lossless replay of completed reasoning items on their original OpenRouter route."""
import hmac
import json

from .protocol import MODEL, PROVIDER, STRUCTURED_PROVIDER, ProtocolError

MAX_ITEM_BYTES = 1024 * 1024
PREFIX = 'opaque-reasoning-v1:'
ROUTE_PREFIXES = {PROVIDER: PREFIX, STRUCTURED_PROVIDER: 'opaque-reasoning-v2:'}
NATIVE_FIELDS = frozenset({'type', 'id', 'summary', 'content', 'encrypted_content'})
NATIVE_METADATA = 'internal_chat_message_metadata_passthrough'


def opaque_items(value):
    """Extract only actual output-item envelopes from a response or event."""
    if not isinstance(value, dict):
        return []
    if isinstance(value.get('item'), dict):
        items = [value['item']]
    else:
        response = value.get('response', value)
        items = response.get('output', []) if isinstance(response, dict) else []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get('encrypted_content')]


def _encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(',', ':'))


def _parts(value, kinds, *, wire):
    if not isinstance(value, list):
        raise ProtocolError('Opaque reasoning parts do not match the pinned native shape')
    result = []
    for part in value:
        if (not isinstance(part, dict) or part.get('type') not in kinds
                or not isinstance(part.get('text'), str) or (not wire and set(part) != {'type', 'text'})):
            raise ProtocolError('Opaque reasoning parts do not match the pinned native shape')
        result.append({'type': part['type'], 'text': part['text']})
    return result


def _binding(item, credential, provider, *, output=False):
    ciphertext = item.get('encrypted_content')
    item_id = item.get('id')
    if (item.get('type') != 'reasoning' or not isinstance(item_id, str)
            or not item_id.startswith('rs_') or not isinstance(ciphertext, str) or not ciphertext
            or len(ciphertext) > MAX_ITEM_BYTES):
        raise ProtocolError('Opaque output is not a replayable DeepSeek reasoning item')
    summary = _parts(item.get('summary'), {'summary_text'}, wire=output)
    content = item.get('content')
    if content is not None:
        content = _parts(content, {'reasoning_text', 'text'}, wire=output)
        # Native omits typed content if none of its parts are ReasoningText.
        if output and not any(part['type'] == 'reasoning_text' for part in content):
            content = None
    prefix = ROUTE_PREFIXES[provider]
    binding = _encode([prefix, MODEL, provider, item_id, summary, content, ciphertext])
    if len(binding) > 2 * MAX_ITEM_BYTES:
        raise ProtocolError('Opaque reasoning exceeds its replay bounds')
    return prefix + hmac.digest(credential.encode(), binding.encode(), 'sha256').hex()


def _completed_provider(db, receipt_id):
    row = db.execute('SELECT status, upstream_status, provider, response_id FROM receipts WHERE id=?',
                     (receipt_id,)).fetchone()
    if row and tuple(row[:2]) == ('completed', 200) and row[2] in ROUTE_PREFIXES and row[3]:
        return row[2]
    return None


def remember_completed(db, credential, receipt_id, output):
    """Persist exact wire fields, excluding the ciphertext the native item carries."""
    items = [item for item in output if isinstance(item, dict) and item.get('encrypted_content')]
    if not items:
        return 0
    provider = _completed_provider(db, receipt_id)
    if provider is None:
        raise ProtocolError('Opaque reasoning requires a completed receipt on a supported DeepSeek route')
    rows = []
    for item in items:
        key = _binding(item, credential, provider, output=True)
        wire = {name: value for name, value in item.items() if name != 'encrypted_content'}
        entry = _encode({'receipt': receipt_id, 'wire': wire})
        if len(entry) > MAX_ITEM_BYTES or credential in entry or credential in item['encrypted_content']:
            raise ProtocolError('Opaque reasoning evidence exceeds its bounds or contains a credential')
        rows.append((key, entry, wire))
    for key, entry, wire in rows:
        previous = db.execute('SELECT value FROM adapter_metadata WHERE key=?', (key,)).fetchone()
        if previous is not None and _encode(json.loads(previous[0])['wire']) != _encode(wire):
            raise ProtocolError('Conflicting producer evidence for an opaque reasoning item')
        db.execute('INSERT OR IGNORE INTO adapter_metadata VALUES (?,?)', (key, entry))
    return len(rows)


def _restore_item(item, db, credential):
    matches = []
    for provider in ROUTE_PREFIXES:
        key = _binding(item, credential, provider)
        row = db.execute('SELECT value FROM adapter_metadata WHERE key=?', (key,)).fetchone()
        if row is not None:
            matches.append((provider, key, row[0]))
    if not matches:
        raise ProtocolError('Opaque reasoning has no verified same-route replay evidence')
    if len(matches) != 1:
        raise ProtocolError('Opaque reasoning has ambiguous producing route evidence')
    provider, key, evidence = matches[0]
    try:
        proof = json.loads(evidence)
        original = {**proof['wire'], 'encrypted_content': item['encrypted_content']}
        if (_completed_provider(db, proof['receipt']) != provider
                or _binding(original, credential, provider, output=True) != key):
            raise ProtocolError('Opaque reasoning replay evidence failed verification')
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ProtocolError('Opaque reasoning replay evidence failed verification') from None
    extra = item.keys() - NATIVE_FIELDS - {NATIVE_METADATA}
    if any(field not in original or _encode(original[field]) != _encode(item[field]) for field in extra):
        raise ProtocolError('Opaque reasoning contains fields outside its verified replay evidence')
    if NATIVE_METADATA in item:
        if NATIVE_METADATA in original and _encode(original[NATIVE_METADATA]) != _encode(item[NATIVE_METADATA]):
            raise ProtocolError('Opaque reasoning contains conflicting native metadata')
        original[NATIVE_METADATA] = item[NATIVE_METADATA]
    return provider, original


def verified_history(inputs, db, credential, *, omit_official=False):
    """Reconstruct the exact items and require one verified issuer for the whole request."""
    origin = None
    restored = []
    omitted = 0
    for item in inputs:
        if item.get('encrypted_content'):
            provider, item = _restore_item(item, db, credential)
            if omit_official and provider == PROVIDER:
                omitted += 1
                continue
            if origin is not None and origin != provider:
                raise ProtocolError('Opaque reasoning history contains different DeepSeek routes')
            origin = provider
        restored.append(item)
    return origin, restored, omitted


def require_route(payload, provider):
    if payload.get('model') == MODEL and payload['provider']['only'] == [provider]:
        return
    if provider == PROVIDER:
        raise ProtocolError('Opaque reasoning requires its original official DeepSeek model route')
    raise ProtocolError('Opaque reasoning requires its original OpenRouter DeepSeek provider route')


def restore(payload, db, credential):
    """Restore the wire shape after native history projection and route selection."""
    inputs = payload.get('input')
    if isinstance(inputs, list) and opaque_items({'output': inputs}):
        provider, restored, _ = verified_history(inputs, db, credential)
        require_route(payload, provider)
        payload['input'][:] = restored
