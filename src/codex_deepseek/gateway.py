"""Authenticated loopback provider; disconnects cancel upstream work and no tools execute here."""

from __future__ import annotations

import asyncio
import codecs
import hmac
import json
import os
import tempfile
from pathlib import Path

import aiohttp
from aiohttp import web

from .protocol import Events, MODEL, PROVIDER, SERIAL_PROVIDER, STRUCTURED_PROVIDER, PEER_HOSTED_PROVIDER, ProtocolError, encode_sse, prepare, validate_response

from .state import Store
from .native_http import NativeTransport, network_policy, response_headers
from .structured import StructuredOutput

UPSTREAM = "https://openrouter.ai/api/v1/responses"


def private_write(path, data):
    """Replace one trace atomically, with private permissions and no symlink traversal."""
    with tempfile.NamedTemporaryFile('wb',dir=path.parent,prefix='.trace-',delete=False) as output:
        temporary=Path(output.name)
        try:
            os.fchmod(output.fileno(),0o600)
            output.write(data.encode('utf-8') if isinstance(data,str) else data)
            output.flush()
            os.replace(temporary,path)
        finally:
            if temporary.exists():temporary.unlink()


async def sse_events(content, max_event_bytes=96 * 1024 * 1024):
    """Parse byte chunks, including large records, split UTF-8 and all SSE line endings."""
    data = []
    decoder = codecs.getincrementaldecoder('utf-8-sig')()
    buffer = ''
    event_bytes = 0

    def consume(line):
        nonlocal event_bytes
        event_bytes += len(line.encode('utf-8')) + 1
        if event_bytes > max_event_bytes:
            raise ProtocolError('SSE event exceeds the supported Responses body budget')
        if line.startswith('data:'):
            value = line[5:]
            data.append(value[1:] if value.startswith(' ') else value)
        elif not line:
            event_bytes = 0
            if data:
                value = '\n'.join(data)
                data.clear()
                return None if value == '[DONE]' else json.loads(value)
        return None

    async for raw in content.iter_any():
        buffer += decoder.decode(raw)
        while True:
            positions = [position for char in ('\r', '\n') if (position := buffer.find(char)) >= 0]
            if not positions:
                break
            position = min(positions)
            if buffer[position] == '\r' and position == len(buffer) - 1:
                break  # The next chunk may complete CRLF.
            count = 2 if buffer[position:position+2] == '\r\n' else 1
            event = consume(buffer[:position])
            buffer = buffer[position+count:]
            if event is not None:
                yield event
        if event_bytes + len(buffer.encode('utf-8')) > max_event_bytes:
            raise ProtocolError('SSE event exceeds the supported Responses body budget')
    buffer += decoder.decode(b'', final=True)
    if buffer:
        event = consume(buffer.rstrip('\r'))
        if event is not None:
            yield event
    if data:
        text = "\n".join(data)
        if text != "[DONE]":
            yield json.loads(text)


def error_response(message, status=400):
    return web.json_response({"error": {"type": "invalid_request_error", "code": "provider_compatibility", "message": message}}, status=status)


class Gateway:
    def __init__(self, key: str, token: str, store: Store, catalog: dict, *, listen_socket=None, trace_source=None, native_http_binary=None):
        self.key = key if isinstance(key,str) else None
        self.credential_source = key
        self.token = token
        self.store = store
        self.catalog = catalog
        self.runner = None
        self.port = None
        self.client = None
        self.listen_socket = listen_socket
        self.trace_source = trace_source
        self.native_http_binary = native_http_binary

    async def __aenter__(self):
        self.client = NativeTransport(UPSTREAM,binary=self.native_http_binary,metadata_path=self.store.path.parent/'native-http.json' if self.store else None)
        app = web.Application(client_max_size=96 * 1024 * 1024)
        app.router.add_get("/health", self.health)
        app.router.add_get("/v1/models", self.models)
        app.router.add_post("/v1/responses", self.responses)
        self.runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=5)
        await self.runner.setup()
        site = web.SockSite(self.runner,self.listen_socket) if self.listen_socket else web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = self.runner.addresses[0][1]
        return self

    async def __aexit__(self, *exc):
        await self.runner.cleanup()
        await self.client.close()

    def authorized(self, request):
        return not request.headers.get("Origin") and hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + self.token)

    async def health(self, request):
        if not self.authorized(request):
            return error_response("Unauthorized", 401)
        return web.json_response({"status": "ready", "model": MODEL, "provider": PROVIDER,
                                  "structured_provider": STRUCTURED_PROVIDER, "serial_provider": SERIAL_PROVIDER,
                                  "peer_hosted_provider": PEER_HOSTED_PROVIDER,"fallbacks": False})

    async def models(self, request):
        if not self.authorized(request):
            return error_response("Unauthorized", 401)
        return web.json_response(self.catalog)

    async def responses(self, request):
        if not self.authorized(request):
            return error_response("Unauthorized", 401)
        try:
            body = await request.json()
            payload, registry = prepare(body, self.store)
            structured = StructuredOutput(body)
            transport_policy=network_policy(request.headers)
        except (ValueError, KeyError, TypeError) as exc:
            return error_response(str(exc))
        receipt = self.store.start(payload['provider']['only'][0],context_key=payload.get('prompt_cache_key'),network_policy=transport_policy)
        trace = None
        response = None
        events = Events(registry)
        status = None
        try:
            from .policy import isolated_path
            trace_root = self.trace_source() if self.trace_source else os.environ.get("CODEX_DEEPSEEK_TRACE_DIR")
            trace = isolated_path(Path(trace_root)) if trace_root else None
            if not isinstance(self.credential_source,str):
                self.key = self.credential_source()
            if trace:
                trace.mkdir(parents=True, exist_ok=True, mode=0o700)
                trace.chmod(0o700)
                private_write(trace / (receipt + "-request.json"),json.dumps(payload, ensure_ascii=False, indent=2))
            async with self.client.post(UPSTREAM, json=payload, policy=transport_policy, allow_redirects=False, headers={
                "Authorization": "Bearer " + self.key,
                "Content-Type": "application/json", "X-Title": "Codex DeepSeek Runtime",
            }) as upstream:
                status = upstream.status
                transport_headers=response_headers(upstream.headers)
                if trace:private_write(trace/(receipt+'-network.json'),json.dumps({'policy':transport_policy,'native_http':True}))
                if status >= 300:
                    raw = await upstream.read()
                    if trace:
                        private_write(trace / (receipt + "-error.json"),raw.replace(self.key.encode(), b"[REDACTED]"))
                    self.store.finish(receipt, "upstream_error", status)
                    return web.Response(body=raw.replace(self.key.encode(), b"[REDACTED]"), status=status,
                                        content_type="application/json", headers={**transport_headers,"X-Adapter-Request-Id": receipt})
                if not body.get("stream"):
                    result = await upstream.json()
                    validate_response(result)
                    structured.validate(result)
                    result["output"] = [registry.output(item) for item in result.get("output", [])]
                    events.response_id = result.get("id")
                    events.usage = result.get("usage") or {}
                    self.store.finish(receipt, result.get("status", "completed"), status, events)
                    return web.json_response(result, headers={**transport_headers,"X-Adapter-Request-Id": receipt})
                response = web.StreamResponse(headers={**transport_headers,"Content-Type": "text/event-stream", "Cache-Control": "no-store",
                                                        "X-Adapter-Request-Id": receipt})
                await response.prepare(request)
                async for original in sse_events(upstream.content):
                    for event in structured.events(original):
                        normalized_events = events.transform(event)
                        if event.get('type') == 'response.created':
                            self.store.progress(receipt, events)
                        if events.terminal:
                            # The receipt describes the upstream model result;
                            # native logs remain authoritative for tool effects.
                            self.store.finish(receipt, events.status, status, events)
                        for normalized in normalized_events:
                            await response.write(encode_sse(normalized))
                        if events.terminal:
                            await response.write_eof()
                            return response
                if not events.terminal:
                    raise ProtocolError("Upstream disconnected without a terminal Responses event")
                await response.write_eof()
                self.store.finish(receipt, events.status, status, events)
                return response
        except asyncio.CancelledError:
            self.store.finish(receipt, "cancelled", status, events)
            raise
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            detail = str(exc).replace(self.key, "[REDACTED]") if self.key else str(exc)
            self.store.finish(receipt, "adapter_error", status, events, detail)
            if response is not None and response.prepared:
                try:
                    await response.write(encode_sse({"type": "response.failed", "response": {
                        "id": events.response_id or "resp_adapter_failure", "status": "failed",
                        "error": {"code": "provider_compatibility", "message": detail}, "output": []}}))
                    await response.write_eof()
                except (ConnectionError, RuntimeError):
                    pass  # The native client already closed the stream; the receipt records failure.
                return response
            return error_response(detail, 502)
