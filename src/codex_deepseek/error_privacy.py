"""Pure bounded upstream-error privacy/diagnostics; no transport or runtime ownership."""
import base64
import copy
import html
import json
import re
from urllib.parse import unquote

# Diagnostic labels are a finite policy, not arbitrary provider text. Native
# failure labels and common protocol error classes are admitted verbatim; adding
# labels requires review. Never search messages, metadata.raw or nested requests.
_HTTP_ERROR_CODES = frozenset({400, 401, 402, 403, 404, 408, 409, 413, 415, 422,
                              429, 500, 502, 503, 504})
_HTTP_ERROR_LABELS = frozenset({
    "context_length_exceeded", "insufficient_quota", "credit_balance_exhausted",
    "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
    "usage_not_included", "cyber_policy", "bio_policy", "misalignment_policy_violation",
    "invalid_prompt", "server_is_overloaded", "rate_limit_exceeded", "slow_down",
    "image_file_too_large", "invalid_request_error", "authentication_error",
    "permission_error", "not_found_error", "timeout_error", "server_error",
    "api_error", "provider_error", "rate_limit_error",
})
_HTTP_DIAGNOSTIC_BODY_BYTES = 32 * 1024

_IDENTITY_EVENT_PHASES = frozenset({
    "response.created", "response.in_progress", "response.completed",
    "response.failed", "response.incomplete", "error", "nonstream",
})
_IDENTITY_TERMINAL_STATUSES = frozenset({"completed", "failed", "incomplete"})


def upstream_identity_context(event_phase, response_status=None):
    """Safe context for an identity rejection, never an identity attestation.

    Only finite protocol labels survive. Do not pass or inspect the response
    body, model/provider values, request input, IDs or credentials here.
    """
    phase = (event_phase if type(event_phase) is str
             and event_phase in _IDENTITY_EVENT_PHASES else "other")
    result = {"schema": 1, "event_phase": phase}
    if type(response_status) is str and response_status in _IDENTITY_TERMINAL_STATUSES:
        result["terminal_status"] = response_status
    return result


def _http_error_diagnostic(raw, content_type, credential):
    """Bounded observation only; no body, message, request or header IDs survive."""
    result = {"schema": 1, "event_type": "upstream.http_error"}
    if not raw:
        return {**result, "body_status": "empty"}
    if len(raw) > _HTTP_DIAGNOSTIC_BODY_BYTES:
        return {**result, "body_status": "oversize"}
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime != "application/json" and not (mime.startswith("application/") and mime.endswith("+json")):
        return {**result, "body_status": "non_json"}
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate diagnostic JSON key")
            value[key] = item
        return value
    def invalid_constant(value):
        raise ValueError("Non-JSON diagnostic number")
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        return {**result, "body_status": "invalid_json"}
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return {**result, "body_status": "no_error_object"}
    result["body_status"] = "json"
    for field in ("code", "type"):
        candidate = error.get(field)
        if (field == "code" and type(candidate) is int and candidate in _HTTP_ERROR_CODES
                and (not isinstance(credential, str) or str(candidate) != credential)):
            result["error_code"] = candidate
        elif (isinstance(candidate, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", candidate)
                and candidate in _HTTP_ERROR_LABELS
                and (not isinstance(credential, str) or not credential or credential not in candidate)):
            result["error_" + field] = candidate
    return result


# This is a credential privacy bound, separate from the smaller diagnostic
# decoder budget. It does not bound the pre-existing upstream.read() buffer.
_ERROR_PRIVACY_BYTES = 96 * 1024
_ERROR_PRIVACY_DEPTH = 64
_ERROR_ENCODED_JSON_DEPTH = 8
_ERROR_ESCAPE = chr(92)
_UNICODE_ESCAPE = re.compile(re.escape(_ERROR_ESCAPE) + r"+u([0-9a-fA-F]{4})")


def _strict_error_json(text):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate error JSON key")
            value[key] = item
        return value
    def invalid(value):
        raise ValueError("Non-JSON error number")
    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid)


def _normalized_error_text(text):
    """Bounded known text decodings; no arbitrary codec guessing."""
    text.encode('utf-8')
    current = text
    for _ in range(_ERROR_ENCODED_JSON_DEPTH):
        decoded = _UNICODE_ESCAPE.sub(lambda match: chr(int(match[1], 16)), current)
        decoded = decoded.replace(_ERROR_ESCAPE + '/', '/').replace(_ERROR_ESCAPE + '"', '"').replace(_ERROR_ESCAPE + _ERROR_ESCAPE, _ERROR_ESCAPE)
        decoded = html.unescape(unquote(decoded, errors='replace'))
        if any(0xD800 <= ord(char) <= 0xDFFF for char in decoded):
            decoded = decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
        if decoded == current:
            break
        current = decoded
    else:
        raise ValueError("Error encoding inspection limit")
    return current


def _credential_text(text, credential, *, fold=False):
    current = _normalized_error_text(text)
    if fold and credential.casefold() in current.casefold():
        return '[REDACTED]', True
    changed = credential in current
    safe = current.replace(credential, '[REDACTED]')
    for encoded in {base64.b64encode(credential.encode()).decode(),
                    base64.urlsafe_b64encode(credential.encode()).decode()}:
        for variant in {encoded, encoded.rstrip('=')}:
            if variant and variant in safe:
                safe = safe.replace(variant, '[REDACTED]'); changed = True
    return (safe, True) if changed else (text, False)


def _redact_error_json(value, credential, depth=0, encoded_depth=0):
    if depth > _ERROR_PRIVACY_DEPTH:
        raise ValueError("Error JSON inspection depth")
    if isinstance(value, str):
        normalized = _normalized_error_text(value)
        safe, changed = _credential_text(value, credential)
        # A string may be ordinary text beginning with braces. Only successfully
        # parsed nested JSON gets structural handling; otherwise inspect bounded
        # known text/base64 tokens without discarding its useful error reason.
        for candidate in dict.fromkeys([value, normalized]):
            if not candidate.lstrip().startswith(('{', '[', '"')):
                continue
            try:
                nested = _strict_error_json(candidate)
            except (ValueError, RecursionError):
                continue
            if encoded_depth >= _ERROR_ENCODED_JSON_DEPTH:
                raise ValueError("Nested error JSON inspection limit")
            nested, hit = _redact_error_json(nested, credential, 0, encoded_depth + 1)
            return (json.dumps(nested, ensure_ascii=True, allow_nan=False), True) if hit else (value, False)
        inspected = safe if changed else normalized
        tokens = list(re.finditer(r'[A-Za-z0-9+/_-]{16,}={0,2}', inspected))
        for match in reversed(tokens):
            encoded = match[0]
            if len(encoded) > 4096 or encoded_depth >= _ERROR_ENCODED_JSON_DEPTH:
                raise ValueError("Encoded error inspection limit")
            try:
                raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
                if credential.encode('utf-8') in raw:
                    encode = base64.urlsafe_b64encode if '-' in encoded or '_' in encoded else base64.b64encode
                    cleaned = encode(raw.replace(credential.encode('utf-8'), b'[REDACTED]')).decode('ascii')
                    if not encoded.endswith('='):
                        cleaned = cleaned.rstrip('=')
                    inspected = inspected[:match.start()] + cleaned + inspected[match.end():]
                    changed = True
                    continue
                decoded = raw.decode('utf-8')
            except (ValueError, UnicodeError):
                continue
            nested, hit = _redact_error_json(decoded, credential, 0, encoded_depth + 1)
            if hit:
                encode = base64.urlsafe_b64encode if '-' in encoded or '_' in encoded else base64.b64encode
                cleaned = encode(nested.encode('utf-8')).decode('ascii')
                if not encoded.endswith('='):
                    cleaned = cleaned.rstrip('=')
                inspected = inspected[:match.start()] + cleaned + inspected[match.end():]
                changed = True
        return (inspected, True) if changed else (value, False)
    if isinstance(value, list):
        result, changed = [], False
        for item in value:
            item, hit = _redact_error_json(item, credential, depth + 1, encoded_depth)
            result.append(item); changed |= hit
        return result, changed
    if isinstance(value, dict):
        result, changed, reserved, sequence = {}, False, set(value), 0
        for key, item in value.items():
            safe_key, key_changed = _redact_error_json(key, credential, depth + 1, encoded_depth)
            item, hit = _redact_error_json(item, credential, depth + 1, encoded_depth)
            if safe_key in result or (key_changed and safe_key in reserved and safe_key != key):
                while True:
                    sequence += 1
                    safe_key = '[REDACTED_KEY_' + str(sequence) + ']'
                    if safe_key not in result and safe_key not in reserved:
                        break
            result[safe_key] = item; changed |= key_changed or hit
        return result, changed
    return value, False


def error_body_fallback(status, diagnostic=None):
    """Nonempty explicit safety refusal, never presented as original text."""
    error = {'code': 'upstream_error_body_unverified', 'message': 'Upstream HTTP ' + str(status) + ' error body could not be safely inspected; original body withheld.'}
    # The gateway supplies only its already-admitted finite diagnostic fields.
    if isinstance(diagnostic, dict):
        for source, target in [('error_code', 'code'), ('error_type', 'type')]:
            if source in diagnostic:
                error[target] = diagnostic[source]
    return json.dumps({'error': error}, ensure_ascii=True, allow_nan=False).encode('utf-8')


def safe_error_body(raw, content_type, credential, status, diagnostic=None):
    """Keep checked nonsecret bytes; redact credential semantics or fail closed."""
    try:
        if not isinstance(credential, str) or not credential or len(raw) > _ERROR_PRIVACY_BYTES:
            return error_body_fallback(status, diagnostic)
        if not raw:
            return raw
        text = raw.decode('utf-8')
        mime = content_type.split(';', 1)[0].strip().lower()
        is_json = mime == 'application/json' or (mime.startswith('application/') and mime.endswith('+json'))
        if is_json or text.lstrip().startswith(('{', '[')):
            value = _strict_error_json(text)
            value, changed = _redact_error_json(value, credential)
            return json.dumps(value, ensure_ascii=True, allow_nan=False).encode('utf-8') if changed else raw
        safe, changed = _redact_error_json(text, credential)
        return safe.encode('utf-8') if changed else raw
    except Exception:
        return error_body_fallback(status, diagnostic)


def sensitive_http_value(value, credential):
    try:
        if _redact_error_json(value, credential)[1]:
            return True
        # This known generation-header framing is not part of the encoded token.
        return (isinstance(value, str) and value.startswith('gen-')
                and _redact_error_json(value[4:], credential)[1])
    except Exception:
        return True


def filter_error_headers(headers, credential=None):
    """Filter only sensitive values/names; caller owns its transport allowlist."""
    try:
        items = list(headers.items() if hasattr(headers, "items") else headers)
        if not isinstance(credential, str) or not credential:
            return dict(items)
        blocked = set()
        for name, value in items:
            try:
                if (len(name) > 4096 or len(value) > 4096
                        or _credential_text(name, credential, fold=True)[1]
                        or sensitive_http_value(value, credential)):
                    blocked.add(name.lower())
            except Exception:
                blocked.add(name.lower())
        return {name: value for name, value in items if name.lower() not in blocked}
    except Exception:
        return {}


def response_headers(headers, credential=None):
    """Compatibility: retain the CDS transport allowlist and shared filtering."""
    items = [(name, value) for name, value in headers.items() if name.lower() in {
        'retry-after', 'retry-after-ms', 'x-ms-retry-after-ms', 'x-request-id', 'x-openrouter-request-id'}
        or name.lower().startswith('x-ratelimit-')]
    return filter_error_headers(items, credential)


def _error_value_budget(value):
    pending, size, nodes = [(value, 0)], 0, 0
    while pending:
        item, depth = pending.pop(); nodes += 1
        if depth > _ERROR_PRIVACY_DEPTH or nodes > 8192:
            raise ValueError("Failure inspection structure limit")
        if isinstance(item, str):
            if len(item) > _ERROR_PRIVACY_BYTES:
                raise ValueError("Failure inspection byte limit")
            size += len(item.encode('utf-8'))
        elif isinstance(item, dict):
            if nodes + len(pending) + len(item) * 2 > 8192 or size + len(item) * 4 > _ERROR_PRIVACY_BYTES:
                raise ValueError("Failure inspection structure limit")
            pending.extend((part, depth + 1) for pair in item.items() for part in pair)
            size += len(item) * 4
        elif isinstance(item, list):
            if nodes + len(pending) + len(item) > 8192 or size + len(item) * 2 > _ERROR_PRIVACY_BYTES:
                raise ValueError("Failure inspection structure limit")
            pending.extend((part, depth + 1) for part in item); size += len(item) * 2
        elif item is None or type(item) in (int, float, bool):
            if type(item) is float and (item != item or item in (float('inf'), -float('inf'))):
                raise ValueError("Non-JSON failure number")
            size += 16
        else:
            raise ValueError("Non-JSON failure detail")
        if size > _ERROR_PRIVACY_BYTES:
            raise ValueError("Failure inspection byte limit")


def _safe_failure_fields(error, credential):
    fields = {}
    if not isinstance(error, dict):
        return fields
    for name in ('code', 'type'):
        value = error.get(name)
        if name == 'code' and type(value) is int and value in _HTTP_ERROR_CODES and str(value) != credential:
            fields[name] = value
        elif isinstance(value, str) and value in _HTTP_ERROR_LABELS and not sensitive_http_value(value, credential):
            fields[name] = value
    return fields


def _failed_value_fallback(value, credential):
    message = 'Upstream failure detail could not be safely inspected; original detail withheld.'
    if isinstance(value, str):
        return message
    if not isinstance(value, dict):
        return {'code': 'provider_compatibility', 'message': message}
    response = value.get('response') if value.get('type') == 'response.failed' else value
    if not isinstance(response, dict):
        response = {}
    error = response.get('error') if response.get('status') == 'failed' or value.get('type') == 'response.failed' else response
    safe_error = {'code': 'provider_compatibility', 'message': message,
                  **_safe_failure_fields(error, credential)}
    if value.get('type') == 'error':
        return {'type': 'error', **safe_error, 'param': None}
    if response.get('status') == 'failed' or value.get('type') == 'response.failed':
        safe_response = {'status': 'failed', 'output': [], 'error': safe_error}
        identity = response.get('id')
        if isinstance(identity, str) and len(identity) <= 256 and not sensitive_http_value(identity, credential):
            safe_response['id'] = identity
        # Native counters are optional; only existing nonnegative numeric values
        # survive a refused payload. This is not a new completion/consumption fact.
        usage = response.get('usage')
        if isinstance(usage, dict):
            safe_usage = {key: number for key, number in usage.items()
                          if key in {'input_tokens', 'output_tokens', 'total_tokens'}
                          and type(number) is int and number >= 0}
            if safe_usage:
                safe_response['usage'] = safe_usage
        return {'type': 'response.failed', 'response': safe_response} if value.get('type') == 'response.failed' else safe_response
    return safe_error


def redact_error_value(value, credential):
    """Only for caller-classified failure values, never ordinary success data.

    Keep inspected schemas/content; refused failure payloads receive explicit
    failure-shaped fallback with admitted machine codes, not fabricated success.
    """
    if not isinstance(credential, str) or not credential:
        return copy.deepcopy(value)
    try:
        _error_value_budget(value)
        return _redact_error_json(value, credential)[0]
    except Exception:
        return _failed_value_fallback(value, credential)


def safe_error_text(text, credential):
    result = redact_error_value(text, credential)
    return result if isinstance(result, str) else 'Upstream error detail could not be safely inspected; original detail withheld.'
