"""Profile-owned native HTTP clients; Python handles only authenticated local IPC."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
import json
import os
import re
import base64
import html
from urllib.parse import unquote
from pathlib import Path
import secrets
import time

import aiohttp
from .platforms import executable


def native_helper():
    from .cli import native
    path=executable(native().parent,'codex-http-transport')
    if not path.is_file():raise RuntimeError('The verified native HTTP transport is missing; rerun the installer')
    return path


def network_policy(headers):
    policy=headers.get('x-codex-deepseek-network-policy','default')
    if policy not in {'default','default-fallback','system'}:raise ValueError('Unknown native outbound network policy')
    return policy


class NativeRawUnavailable(RuntimeError):
    """No raw request was sent; never fall back to a different encoding."""


class NativeRawUnsupported(NativeRawUnavailable):
    """The selected immutable helper lacks the required protocol version."""


class NativeTransport:
    def __init__(self,upstream,*,binary=None,metadata_path=None):
        from .windows_asyncio import install_proactor_cleanup_backport
        install_proactor_cleanup_backport()
        self.upstream=upstream;self.binary=binary;self.metadata_path=metadata_path;self.failures=[];self.restarts=0;self.process=None;self.client=None
        self.lock=asyncio.Lock();self.token=secrets.token_urlsafe(48);self.port=None;self.closed=False;self.raw_protocol_version=None

    async def start(self):
        async with self.lock:
            if self.closed:raise RuntimeError('Native transport is closed')
            if self.process is not None:
                if self.process.returncode is None:return
                self.failures=[stamp for stamp in self.failures if time.monotonic()-stamp<60]
                self.failures.append(time.monotonic())
                if len(self.failures)>5:raise RuntimeError('Native HTTP transport repeatedly crashed; refusing a restart loop')
                await self._stop();self.process=None;self.port=None;self.restarts+=1
            environment={key:value for key,value in os.environ.items()
                         if (not key.startswith('CODEX_') or key=='CODEX_CA_CERTIFICATE')
                         and key not in {'OPENROUTER_API_KEY','OPENAI_API_KEY'}}
            binary=Path(self.binary) if self.binary else native_helper()
            self.process=await asyncio.create_subprocess_exec(str(binary),stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=environment)
            try:
                self.process.stdin.write(json.dumps({'token':self.token,'upstream':self.upstream,'parent_pid':os.getpid()}).encode())
                await self.process.stdin.drain();self.process.stdin.close()
                ready=json.loads(await asyncio.wait_for(self.process.stdout.readline(),10))
                if ready.get('pid')!=self.process.pid or type(ready.get('port')) is not int or not 0<ready['port']<65536:
                    raise RuntimeError('Native HTTP transport returned invalid ownership metadata')
                self.port=ready['port'];self.raw_protocol_version=ready.get('raw_protocol_version')
                if self.metadata_path:
                    from .processes import identity
                    from .service import atomic_json
                    atomic_json(Path(self.metadata_path),{'pid':self.process.pid,'birth':identity(self.process.pid),'parent_pid':os.getpid(),'port':self.port,'restarts':self.restarts})
                self.client=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None,sock_connect=30,sock_read=None),
                    cookie_jar=aiohttp.DummyCookieJar(),trust_env=False,read_bufsize=1024*1024)
            except BaseException:
                await self._stop();raise

    async def _stop(self):
        if self.client is not None:await self.client.close();self.client=None
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:await asyncio.wait_for(self.process.wait(),5)
            except TimeoutError:self.process.kill();await self.process.wait()

    async def close(self):
        self.closed=True
        await self._stop()

    @asynccontextmanager
    async def request(self,method,url,*,api_key,payload=None,policy='default'):
        await self.start()
        envelope={'api_key':api_key,'url':url,'method':method,'payload':payload,'policy':policy}
        # Match aiohttp's default JsonPayload encoding without a large BytesPayload.
        body=await asyncio.to_thread(lambda:json.dumps(envelope).encode('utf-8'))
        with BytesIO(body) as stream:
            async with self.client.post(f'http://127.0.0.1:{self.port}/forward',data=stream,
                    headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},
                    allow_redirects=False) as response:
                yield response

    async def require_raw(self):
        try:await self.start()
        except Exception:
            raise NativeRawUnavailable('Native raw HTTP transport is unavailable before sending a request') from None
        if type(self.raw_protocol_version) is not int or self.raw_protocol_version!=1:
            raise NativeRawUnsupported('Verified native helper does not support raw HTTP protocol v1; a qualified matching build is required')

    @asynccontextmanager
    async def request_raw(self,method,url,*,api_key,body=b'',content_type=None,policy='default'):
        # Only metadata is JSON. The potentially large body is never JSON/base64
        # wrapped or reserialized between Python and the native HTTP factory.
        if not isinstance(body,bytes) or len(body)>100*1024*1024+16384:
            raise ValueError('Raw native HTTP body exceeds its bounded IPC budget')
        if policy not in {'default','default-fallback','system'} or method not in {'POST','GET','DELETE'}:
            raise ValueError('Unsupported raw native HTTP request')
        metadata=json.dumps({'api_key':api_key,'url':url,'method':method,'policy':policy,'content_type':content_type},ensure_ascii=True,separators=(',',':'))
        if len(metadata.encode('ascii'))>8192:
            raise ValueError('Raw native HTTP metadata exceeds its IPC budget')
        await self.require_raw()
        # Keep ownership through response exit, including cancellation and errors.
        with BytesIO(body) as stream:
            async with self.client.post(f'http://127.0.0.1:{self.port}/forward-raw',data=stream,
                    headers={'Authorization':'Bearer '+self.token,'X-CDS-Transport-Metadata':metadata,
                             'Content-Type':'application/octet-stream'},allow_redirects=False) as response:
                yield response

    def post(self,url,*,json,headers,policy='default',**unused):
        authorization=headers.get('Authorization','')
        if not authorization.startswith('Bearer '):raise ValueError('Native provider credentials are missing')
        return self.request('POST',url,api_key=authorization[7:],payload=json,policy=policy)


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


def _credential_text(text, credential, *, fold=False):
    """Known literal/JSON/URL/HTML and token-Base64 equivalences, not all codecs."""
    text.encode('utf-8')
    current = text
    for _ in range(_ERROR_ENCODED_JSON_DEPTH):
        decoded = _UNICODE_ESCAPE.sub(lambda match: chr(int(match[1], 16)), current)
        decoded = decoded.replace(_ERROR_ESCAPE + '/', '/').replace(_ERROR_ESCAPE + '"', '"').replace(_ERROR_ESCAPE + _ERROR_ESCAPE, _ERROR_ESCAPE)
        decoded = html.unescape(unquote(decoded, errors='strict'))
        if any(0xD800 <= ord(char) <= 0xDFFF for char in decoded):
            decoded = decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
        if decoded == current:
            break
        current = decoded
    else:
        raise ValueError("Error encoding inspection limit")
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
        safe, changed = _credential_text(value, credential)
        if not changed:
            encoded = value.strip()
            # Probe only bounded canonical base64 text; do not guess arbitrary
            # codecs or decode large opaque payloads. Plain strings stay exact.
            if len(encoded) >= 16 and re.fullmatch(r"[A-Za-z0-9+/_-]+={0,2}", encoded):
                if len(encoded) > 4096 or encoded_depth >= _ERROR_ENCODED_JSON_DEPTH:
                    raise ValueError("Encoded error inspection limit")
                try:
                    raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
                    decoded = raw.decode('utf-8')
                except (ValueError, UnicodeError):
                    return value, False
                nested, hit = _redact_error_json(decoded, credential, 0, encoded_depth + 1)
                if hit:
                    encode = base64.urlsafe_b64encode if '-' in encoded or '_' in encoded else base64.b64encode
                    cleaned = encode(nested.encode('utf-8')).decode('ascii')
                    return cleaned if encoded.endswith('=') else cleaned.rstrip('='), True
            return value, False
        if value.lstrip().startswith(('{', '[')):
            if encoded_depth >= _ERROR_ENCODED_JSON_DEPTH:
                raise ValueError("Nested error JSON inspection limit")
            try:
                nested = _strict_error_json(value)
            except (ValueError, RecursionError):
                return safe, True
            nested, nested_changed = _redact_error_json(nested, credential, 0, encoded_depth + 1)
            if nested_changed:
                return json.dumps(nested, ensure_ascii=True, allow_nan=False), True
        return safe, True
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
        return _redact_error_json(value, credential)[1]
    except Exception:
        return True


def response_headers(headers, credential=None):
    """Preserve checked transport advice/correlation; drop unsafe names/values."""
    try:
        items = [(name, value) for name, value in headers.items() if name.lower() in {
            'retry-after', 'retry-after-ms', 'x-ms-retry-after-ms', 'x-request-id', 'x-openrouter-request-id'}
            or name.lower().startswith('x-ratelimit-')]
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
