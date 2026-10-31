"""Authenticated loopback provider; disconnects cancel upstream work and no tools execute here."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import codecs
import hmac
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.web_log import AccessLogger

from .protocol import Events, MODEL, PROVIDER, SERIAL_PROVIDER, STRUCTURED_PROVIDER, PEER_HOSTED_PROVIDER, ProtocolError, encode_sse, prepare, validate_response

from .state import Store
from .native_http import NativeTransport, NativeRawUnsupported, network_policy, response_headers
from .structured import StructuredOutput
from .image_provider import ImageProvider,load_image_config
from .image_transport import ImageTransportError,PayloadBudgetExceeded,FileOperationError
from .platforms import private_fd, private_directory, replace_file

UPSTREAM = "https://openrouter.ai/api/v1/responses"
# Exact aliases and endpoint tag/display names verified by the fixed model's
# OpenRouter catalog. Omitted/null observations remain unknown, not route proof.
_OBSERVED_MODELS = frozenset({
    MODEL, MODEL.split("/", 1)[1],
    "deepseek/deepseek-v4.1-flash-20261017", "deepseek-v4.1-flash-20261017",
})
_OBSERVED_PROVIDERS = {
    PROVIDER: frozenset({PROVIDER, "DeepSeek"}),
    STRUCTURED_PROVIDER: frozenset({STRUCTURED_PROVIDER, "Morph"}),
    SERIAL_PROVIDER: frozenset({SERIAL_PROVIDER, "PrimeIntellect"}),
}


def validate_observed_identity(value, requested_provider):
    """Reject explicit response identity contradictions before native dispatch."""
    if not isinstance(value, dict):
        return  # The existing response/event validator owns envelope validation.
    for name in ("model", "provider"):
        item = value.get(name)
        if item is None:
            continue
        if not isinstance(item, str) or not item:
            raise ProtocolError("Observed upstream " + name + " must be a nonempty string")
        accepted = _OBSERVED_MODELS if name == "model" else _OBSERVED_PROVIDERS[requested_provider]
        if item not in accepted:
            raise ProtocolError("Upstream returned an incompatible " + name + ": " + item)


def private_write(path, data):
    """Replace one trace atomically, with private permissions and no symlink traversal."""
    temporary=None
    try:
        with tempfile.NamedTemporaryFile('wb',dir=path.parent,prefix='.trace-',delete=False) as output:
            temporary=Path(output.name)
            private_fd(output.fileno())
            output.write(data.encode('utf-8') if isinstance(data,str) else data)
            output.flush()
        replace_file(temporary,path)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()


def front_access_logger(trace_source):
    """Use aiohttp's own completed-request hook, including router rejections."""
    class FrontAccessLogger(AccessLogger):
        @property
        def enabled(self):
            # aiohttp caches this on connection creation to enable start timing.
            # Keep timing available when trace is toggled on a live keepalive;
            # log() retains the existing logger behavior while trace is off.
            return True

        def log(self, request, response, duration):
            try:
                trace_root = trace_source()
                if not trace_root:
                    if super().enabled:
                        super().log(request, response, duration)
                    return
                from .policy import isolated_path
                trace = isolated_path(Path(trace_root))
                private_directory(trace)
                record = {
                    "version": 1,
                    "completed_at": time.time(),
                    "duration_seconds": duration,
                    "method": request.method,
                    "path": request.path,
                    "upgrade_websocket": any(
                        value.strip().lower() == "websocket"
                        for value in request.headers.get("Upgrade", "").split(",")
                    ),
                    "status": response.status,
                }
                private_write(trace / ("front-http-" + uuid.uuid4().hex + ".json"),
                              json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")
            except Exception as error:
                # Never log exception text, request data or a traceback: any of
                # those can contain credentials. A missing record is not zero.
                logging.getLogger(__name__).warning(
                    "Front HTTP trace incomplete (%s); request outcome unchanged",
                    type(error).__name__,
                )

    return FrontAccessLogger


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


class _DownstreamDisconnected(ConnectionError):
    """A connection failure originating only from local response delivery."""

    def __init__(self, operation, error_type):
        self.operation = operation
        self.error_type = error_type
        super().__init__(f"Downstream response disconnected during {operation}; delivery outcome unknown")


class Gateway:
    def __init__(self, key: str, token: str, store: Store, catalog: dict, *, listen_socket=None, trace_source=None, native_http_binary=None, external_port=None):
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
        self.external_port = external_port
        self.images = None
        self.image_gc = None

    async def __aenter__(self):
        self.client = NativeTransport(UPSTREAM,binary=self.native_http_binary,metadata_path=self.store.path.parent/'native-http.json' if self.store else None)
        image_config=load_image_config(self.store.path.parent) if self.store else None
        if image_config is not None:
            credentials=self.credential_source if callable(self.credential_source) else lambda:self.credential_source
            self.images=ImageProvider(self.store.path.parent,self.client,credentials,image_config)
            from .image_maintenance import ImageMaintenance
            self.image_gc=ImageMaintenance(self.images.root/'maintenance.json',lambda:self.images.transport())
        app = web.Application(client_max_size=96 * 1024 * 1024)
        app.router.add_get("/health", self.health)
        app.router.add_get("/v1/models", self.models)
        app.router.add_post("/v1/responses", self.responses)
        self.runner = web.AppRunner(
            app, handler_cancellation=True, shutdown_timeout=5,
            access_log_class=front_access_logger(
                lambda: self.trace_source() if self.trace_source else os.environ.get("CODEX_DEEPSEEK_TRACE_DIR")
            ),
        )
        await self.runner.setup()
        if self.external_port is not None:
            self.port=self.external_port
            if self.image_gc is not None:self.image_gc.start()
            return self
        site = web.SockSite(self.runner,self.listen_socket) if self.listen_socket else web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = self.runner.addresses[0][1]
        if self.image_gc is not None:self.image_gc.start()
        return self

    async def accept_socket(self,connection):
        try:
            connection.setblocking(False)
            await asyncio.get_running_loop().connect_accepted_socket(self.runner.server,connection)
        except BaseException:
            connection.close();raise

    async def __aexit__(self, *exc):
        await self.runner.cleanup()
        if self.image_gc is not None:await self.image_gc.stop()
        await self.client.close()

    def authorized(self, request):
        return not request.headers.get("Origin") and hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + self.token)

    async def health(self, request):
        if not self.authorized(request):
            return error_response("Unauthorized", 401)
        return web.json_response({"status": "ready", "model": MODEL, "provider": PROVIDER,
                                  "structured_provider": STRUCTURED_PROVIDER, "serial_provider": SERIAL_PROVIDER,
                                  "peer_hosted_provider": PEER_HOSTED_PROVIDER,"fallbacks": False,
                                  "image_maintenance":self.image_gc.snapshot() if self.image_gc else {"enabled":False}})

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
        receipt = self.store.start(payload['provider']['only'][0],context_key=payload.get('prompt_cache_key'),network_policy=transport_policy,route_reason=registry.route_reason,
                                   structured_output_mapping=registry.structured_output_mapping)
        trace = None
        response = None
        events = Events(registry)
        status = None
        captured_key = self.key
        image_stack=AsyncExitStack();prepared_images=None;image_refs=False;image_started=False
        async def deliver(operation, *args):
            try:
                return await getattr(response, operation)(*args)
            except ConnectionError as exc:
                raise _DownstreamDisconnected(operation, type(exc).__name__) from exc
        async def image_terminal(response_id,terminal):
            if image_refs:
                self.images.record(receipt,consumption_status='provider_terminal',provider_terminal=terminal,provider_response_id=response_id,response_id=response_id)
                await prepared_images.mark_upstream_settled(response_id,terminal)
        try:
            from .policy import isolated_path
            trace_root = self.trace_source() if self.trace_source else os.environ.get("CODEX_DEEPSEEK_TRACE_DIR")
            trace = isolated_path(Path(trace_root)) if trace_root else None
            captured_key=self.credential_source() if callable(self.credential_source) else self.credential_source
            if not isinstance(captured_key,str) or not captured_key or any(c.isspace() for c in captured_key):
                raise ValueError('No usable provider credential')
            if trace:
                private_directory(trace)
                private_write(trace / (receipt + "-request.json"),json.dumps(payload, ensure_ascii=False, indent=2))
            if self.images is not None:
                await self.client.require_raw()
                transport=self.images.transport(transport_policy)
                prepared_images=await image_stack.enter_async_context(transport.prepare(payload,credential=captured_key))
                preparation=prepared_images.receipt(private=False)
                image_refs=preparation.get('converted_image_occurrences',0)>0
                self.images.record(receipt,requested_provider=payload['provider']['only'][0],preparation=preparation,
                    consumption_status='prepared' if image_refs else 'not_applicable',native_outbound_policy=transport_policy)
                if image_refs:
                    await prepared_images.mark_upstream_started(receipt);image_started=True
                    self.images.record(receipt,consumption_status='unknown')
                upstream_context=self.client.request_raw('POST',UPSTREAM,body=prepared_images.body,content_type='application/json',api_key=captured_key,policy=transport_policy)
            else:
                upstream_context=self.client.post(UPSTREAM,json=payload,policy=transport_policy,allow_redirects=False,
                    headers={'Authorization':'Bearer '+captured_key,'Content-Type':'application/json','X-Title':'Codex DeepSeek Runtime'})
            async with upstream_context as upstream:
                status = upstream.status
                transport_headers=response_headers(upstream.headers)
                if trace:private_write(trace/(receipt+'-network.json'),json.dumps({'policy':transport_policy,'native_http':True}))
                if status >= 300:
                    if image_refs and status in {400,401,403,404,413,415,422,429}:
                        await prepared_images.mark_upstream_rejected(status)
                        self.images.record(receipt,consumption_status='http_rejected',status='upstream_error')
                    raw = await upstream.read()
                    if trace:
                        private_write(trace / (receipt + "-error.json"),raw.replace(captured_key.encode(), b"[REDACTED]"))
                    self.store.finish(receipt, "upstream_error", status)
                    return web.Response(body=raw.replace(captured_key.encode(), b"[REDACTED]"), status=status,
                                        content_type="application/json", headers={**transport_headers,"X-Adapter-Request-Id": receipt})
                if not body.get("stream"):
                    result = await upstream.json()
                    validate_response(result)
                    validate_observed_identity(result, payload['provider']['only'][0])
                    await image_terminal(result['id'],result['status'])
                    structured.validate(result)
                    normalized=events.transform({'type':'response.'+result['status'],'response':result})
                    result=next(event['response'] for event in reversed(normalized) if event['type']=='response.'+result['status'])
                    self.store.finish(receipt, result.get("status", "completed"), status, events)
                    return web.json_response(result, headers={**transport_headers,"X-Adapter-Request-Id": receipt})
                response = web.StreamResponse(headers={**transport_headers,"Content-Type": "text/event-stream", "Cache-Control": "no-store",
                                                        "X-Adapter-Request-Id": receipt})
                await deliver('prepare', request)
                async for original in sse_events(upstream.content):
                    validate_observed_identity(original.get('response') if isinstance(original, dict) else None,
                                               payload['provider']['only'][0])
                    if image_refs and original.get('type') in {'response.completed','response.failed','response.incomplete'}:
                        terminal=original.get('response');validate_response(terminal)
                        if events.response_id and terminal['id']!=events.response_id:raise ProtocolError('Provider terminal changed response identity')
                        await image_terminal(terminal['id'],terminal['status'])
                    if self.images is not None and original.get('type')=='response.created':
                        identifier=(original.get('response') or {}).get('id')
                        if isinstance(identifier,str):self.images.record(receipt,response_id=identifier)
                    for event in structured.events(original):
                        normalized_events = events.transform(event)
                        if event.get('type') == 'response.created':
                            self.store.progress(receipt, events)
                        if events.terminal:
                            # The receipt describes the upstream model result;
                            # native logs remain authoritative for tool effects.
                            self.store.finish(receipt, events.status, status, events)
                        for normalized in normalized_events:
                            await deliver('write', encode_sse(normalized))
                        if events.terminal:
                            await deliver('write_eof')
                            return response
                if not events.terminal:
                    raise ProtocolError("Upstream disconnected without a terminal Responses event")
                await deliver('write_eof')
                self.store.finish(receipt, events.status, status, events)
                return response
        except _DownstreamDisconnected as exc:
            if self.images is not None and prepared_images is not None:self.images.record(receipt,status='delivery_cancelled')
            # finish only closes a running receipt. An already-known upstream
            # terminal and its history fingerprints remain authoritative; neither
            # that result nor this disconnect proves native delivery/tool effects.
            self.store.finish(receipt, "cancelled", status, events, str(exc))
            if trace:
                private_write(trace / (receipt + "-delivery.json"), json.dumps({
                    "connection_status": "disconnected", "delivery_status": "unknown",
                    "operation": exc.operation, "error_type": exc.error_type,
                    "upstream_terminal": events.status if events.terminal else None,
                }))
            return response
        except asyncio.CancelledError:
            if self.images is not None and prepared_images is not None:self.images.record(receipt,status='delivery_cancelled')
            self.store.finish(receipt, "cancelled", status, events)
            raise
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            if captured_key is not None and not isinstance(captured_key, str):
                # Invalid callback values are not strings and must never be used
                # for truthiness, repr, or replacement while closing the receipt.
                detail = "No usable provider credential"
            else:
                detail = str(exc).replace(captured_key, "[REDACTED]") if captured_key else str(exc)
            receipt_detail = detail
            budget = getattr(exc, 'diagnostics', None) if isinstance(exc, ImageTransportError) else None
            if isinstance(budget, dict):
                # Only the pure planner's aggregate: no URLs, image data or file IDs.
                receipt_detail = json.dumps({'message': detail, 'image_budget': budget}, separators=(',', ':'))
                if self.images is not None:
                    self.images.record(receipt, status='adapter_error', consumption_status='not_sent',
                                       budget_failure=budget)
            self.store.finish(receipt, "adapter_error", status, events, receipt_detail)
            if response is not None and response.prepared:
                try:
                    await response.write(encode_sse({"type": "response.failed", "response": {
                        "id": events.response_id or "resp_adapter_failure", "status": "failed",
                        "error": {"code": "provider_compatibility", "message": detail}, "output": []}}))
                    await response.write_eof()
                except (ConnectionError, RuntimeError):
                    pass  # The native client already closed the stream; the receipt records failure.
                return response
            error_status=413 if isinstance(exc,PayloadBudgetExceeded) else (400 if isinstance(exc,(ImageTransportError,NativeRawUnsupported)) else 502)
            if isinstance(exc,FileOperationError):
                error_status=exc.status if exc.status in {400,401,403,404,409,413,415,422,429} else 502
                result=error_response(detail,error_status)
                for name,value in exc.safe_headers.items():
                    if name=='retry-after':result.headers['Retry-After']=value
                return result
            return error_response(detail,error_status)
        finally:
            try:await image_stack.aclose()
            except Exception as error:
                if self.images is not None:self.images.record(receipt,release_error_type=type(error).__name__)
