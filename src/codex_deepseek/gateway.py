"""Authenticated loopback provider; disconnects cancel upstream work and no tools execute here."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import codecs
import hmac
import hashlib
import json
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.web_log import AccessLogger

from .protocol import Events, MODEL, PROVIDER, SERIAL_PROVIDER, STRUCTURED_PROVIDER, PEER_HOSTED_PROVIDER, ProtocolError, encode_sse, prepare, validate_response, provider_failure_diagnostic, redact_provider_failure, redact_provider_trace

from .state import Store
from .request_metadata import native_request_metadata
from .opaque_replay import opaque_items, restore
from . import thread_routes
from .native_http import NativeTransport, NativeRawUnsupported, network_policy
from .error_privacy import (response_headers, safe_error_body, error_body_fallback, sensitive_http_value,
                            _http_error_diagnostic, safe_error_text, filter_error_headers,
                            upstream_identity_context)
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




class UpstreamIdentityError(ProtocolError):
    """An upstream identity field failed the pinned-route checks."""


def validate_observed_identity(value, requested_provider, *, failed=False, incomplete=False):
    """Reject explicit contradictions; an error-only unknown is no attestation."""
    error_incomplete = False
    if incomplete:
        validate_response(value, "incomplete")
        details = value.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        # Fixed native treats every other reason (including missing/non-string)
        # as an error. Literal interrupted instead continues as Completed.
        error_incomplete = reason != "interrupted"
    if not isinstance(value, dict):
        return  # The existing response/event validator owns envelope validation.
    for name in ("model", "provider"):
        item = value.get(name)
        if item is None:
            continue
        if not isinstance(item, str) or not item:
            raise UpstreamIdentityError("Observed upstream " + name + " must be a nonempty string")
        accepted = _OBSERVED_MODELS if name == "model" else _OBSERVED_PROVIDERS[requested_provider]
        if not failed and item not in accepted:
            if name == "model" and item == "unknown" and error_incomplete:
                continue  # Preserve the original error, never claim model identity.
            raise UpstreamIdentityError("Upstream returned an incompatible " + name + ": " + item)



def _peer_trace_sha256(value):
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for part in encoder.iterencode(value):
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def native_peer_trace_evidence(receipt, body, payload, registry, native_request):
    """Hash-only observation of native -> prepared peers, before image transport.

    This is neither an upstream consumption receipt nor runtime authorization.
    Unqualified observations never change the forwarded payload.
    """
    record = {
        "schema_version": 1,
        "scope": "native_to_prepared_before_image_transport",
        "receipt_id": receipt,
        "status": "unqualified",
        "native_request": {key: native_request[key] for key in
                           ("status", "source", "header_consistency", "request_kind", "thread_id", "turn_id", "reason")
                           if key in native_request},
        "bindings": [],
    }
    def finish(reason=None):
        if reason is not None:
            record["status"] = "unqualified"
            record["reason"] = reason
            record["bindings"] = []
        record["record_sha256"] = _peer_trace_sha256(record)
        if len(json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")) > 65536:
            record.pop("record_sha256")
            record["status"], record["reason"], record["bindings"] = "unqualified", "peer_evidence_limit_exceeded", []
            record["record_sha256"] = _peer_trace_sha256(record)
        return record
    try:
        record["prepared_request_sha256"] = _peer_trace_sha256(payload)
        record["prepared_input_sha256"] = _peer_trace_sha256(payload.get("input", []))
        original = body.get("input", [])
        record["native_input_count"] = len(original) if isinstance(original, list) else 0
        peers = [(index, item) for index, item in enumerate(original) if isinstance(item, dict)
                 and item.get("type") == "agent_message"] if isinstance(original, list) else []
        if not peers:
            record["status"] = "no_native_peers"
            return finish()
        if len(peers) > 128:
            return finish("peer_evidence_limit_exceeded")
        if (native_request.get("status") != "recognized" or native_request.get("request_kind") != "turn"
                or not native_request.get("thread_id") or not native_request.get("turn_id")):
            return finish("native_request_identity_unqualified")
        ids = [item.get("id") for _, item in peers]
        if any(not isinstance(item_id, str) or not item_id or len(item_id) > 256 for item_id in ids):
            return finish("native_peer_id_missing_or_invalid")
        if len(set(ids)) != len(ids):
            return finish("native_peer_id_ambiguous")
        observed = getattr(registry, "peer_history_bindings", None)
        if not isinstance(observed, list) or len(observed) != len(peers):
            return finish("native_peer_projection_hook_unavailable")
        bindings = {}
        prepared_indices = set()
        prepared = payload.get("input")
        if not isinstance(prepared, list):
            return finish("prepared_input_unqualified")
        for binding in observed:
            if not isinstance(binding, dict):
                return finish("native_peer_projection_hook_invalid")
            item_id, index = binding.get("native_item_id"), binding.get("prepared_input_index")
            if (item_id not in ids or item_id in bindings or type(index) is not int
                    or index < 0 or index >= len(prepared) or index in prepared_indices):
                return finish("native_peer_projection_hook_invalid")
            bindings[item_id] = binding
            prepared_indices.add(index)
        for source_index, item in peers:
            binding = bindings[item["id"]]
            peer = {key: item.get(key) for key in ("author", "recipient", "content")}
            if any(not isinstance(peer[key], str) or not peer[key] or len(peer[key]) > 2048
                   for key in ("author", "recipient")):
                return finish("native_peer_identity_unqualified")
            fingerprint = _peer_trace_sha256(peer)
            if fingerprint != binding.get("native_peer_sha256"):
                return finish("native_peer_projection_hook_invalid")
            index = binding["prepared_input_index"]
            record["bindings"].append({
                "native_input_index": source_index,
                "native_item_id": item["id"],
                "native_peer_sha256": fingerprint,
                "author": peer["author"], "recipient": peer["recipient"],
                "prepared_input_index": index,
                "prepared_item_sha256": _peer_trace_sha256(prepared[index]),
            })
        record["status"] = "qualified"
        return finish()
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        return finish("peer_evidence_unrepresentable")


def write_native_peer_trace_evidence(trace, receipt, body, payload, registry, native_request):
    """Best-effort diagnostics only; a missing sidecar cannot qualify acceptance."""
    try:
        evidence = native_peer_trace_evidence(receipt, body, payload, registry, native_request)
        private_write(trace / (receipt + "-native-peer-evidence.json"),
                      json.dumps(evidence, ensure_ascii=False, allow_nan=False, indent=2))
    except Exception as error:
        logging.getLogger(__name__).warning("Native peer trace incomplete (%s); source evidence is unavailable",
                                            type(error).__name__)


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


# Upstream HTTP statuses where OpenRouter reports the provider, not the request, failing.
# Network errors never reach this table: they raise before any HTTP status exists.
OUTAGE_UPTIME_PERCENT = 50


def _env_number(name, default, low, high):
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return float(default)
    return value if low <= value <= high else float(default)


def _with_outage_hint(raw, provider, fallback, thread):
    """Append a local, actionable note to OpenRouter's error message; never change the status."""
    try:
        value = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError):
        return raw
    error = value.get('error') if isinstance(value, dict) else None
    if not isinstance(error, dict) or not isinstance(error.get('message'), str):
        return raw
    error['message'] += (' [cds: ' + provider + ' outage confirmed by OpenRouter status. This thread stays on '
                         + provider + ' so it keeps its reasoning; new threads start on ' + fallback
                         + '. To continue this thread now: cds runtime thread move ' + thread + ' ' + fallback + ']')
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


class ProviderBreaker:
    """Trip only after repeated provider failures that OpenRouter's public status confirms.

    While tripped, only brand-new threads start on the fallback. Existing
    threads never change provider automatically.
    """

    def __init__(self, provider, confirm, *, clock=time.monotonic):
        self.provider = provider
        self.confirm = confirm
        self.clock = clock
        self.failures = []
        self.open_until = 0.0
        self.last_check = None
        self.checking = False
        self.trips = 0

    @staticmethod
    def settings():
        return {"failures": int(_env_number('CODEX_DEEPSEEK_FAILOVER_FAILURES', 3, 1, 100)),
                "window": _env_number('CODEX_DEEPSEEK_FAILOVER_WINDOW_SECONDS', 120, 1, 3600),
                "cooldown": _env_number('CODEX_DEEPSEEK_FAILOVER_COOLDOWN_SECONDS', 300, 0, 86400),
                "recheck": _env_number('CODEX_DEEPSEEK_FAILOVER_RECHECK_SECONDS', 30, 0, 3600)}

    def tripped(self):
        return self.clock() < self.open_until

    def success(self):
        self.failures.clear()

    async def failure(self, policy='default'):
        settings = self.settings()
        now = self.clock()
        self.failures = [moment for moment in self.failures if now - moment <= settings["window"]] + [now]
        if (self.tripped() or self.checking or len(self.failures) < settings["failures"]
                or (self.last_check is not None and now - self.last_check < settings["recheck"])):
            return False
        self.checking = True
        self.last_check = now
        try:
            confirmed = await self.confirm(self.provider, policy) is True
        except Exception:
            confirmed = False  # An unconfirmed outage never changes routing.
        finally:
            self.checking = False
        if confirmed and len(self.failures) >= settings["failures"]:
            self.open_until = self.clock() + settings["cooldown"]
            self.failures.clear()
            self.trips += 1
            logging.getLogger(__name__).warning(
                "Provider %s outage confirmed; new threads start on the fallback for %.0fs",
                self.provider, settings["cooldown"])
            return True
        return False

    def snapshot(self):
        remaining = max(0.0, self.open_until - self.clock())
        return {"provider": self.provider, "state": "tripped" if remaining else "closed",
                "recent_failures": len(self.failures), "trips": self.trips,
                "reopens_in_seconds": round(remaining)}


def _ordinary_route(payload, registry, provider):
    reason = registry.route_reason or ''
    if reason.startswith('auto_tools_final_schema_validation/'):
        reason = reason.split('/', 2)[2] if reason.count('/') >= 2 else ''
    ordinary = reason == 'official_default' or reason == 'operator_default/' + provider
    return ordinary and payload['provider']['only'] == [provider]


def _explicit_hosted_opaque_move(current, alternate, provider, note):
    if note != 'thread_pin/manual/':
        return False
    official = 'official_opaque_history_with_hosted_tools'
    peers = {'native_peer_or_continuation_with_hosted_tools', 'unattested_native_history_with_hosted_tools'}
    return ((current == official and alternate in peers and provider == STRUCTURED_PROVIDER)
            or (current in peers and alternate == official and provider == PROVIDER))


class Gateway:
    def __init__(self, key: str, token: str, store: Store, catalog: dict, *, listen_socket=None, trace_source=None, native_http_binary=None, external_port=None, ordinary_default_provider=PROVIDER, peer_hosted_provider=None, ordinary_fallback_provider=None, provider_config_sha256=None):
        from .provider_config import validate, digest
        policy={"schema":1,"ordinary_default_provider":ordinary_default_provider}
        if peer_hosted_provider is not None:policy["peer_hosted_provider"]=peer_hosted_provider
        if ordinary_fallback_provider is not None:policy["ordinary_fallback_provider"]=ordinary_fallback_provider
        policy=validate(policy)
        expected=digest(policy)
        if provider_config_sha256 is not None and provider_config_sha256!=expected:raise ValueError("Provider snapshot hash mismatch")
        self.ordinary_default_provider=ordinary_default_provider
        self.peer_hosted_provider=policy.get("peer_hosted_provider",PEER_HOSTED_PROVIDER)
        self.ordinary_fallback_provider=policy.get("ordinary_fallback_provider")
        self.breaker=ProviderBreaker(ordinary_default_provider,self._confirm_outage)
        self.provider_config_sha256=expected
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
        return web.json_response({"status": "ready", "model": MODEL, "provider": self.ordinary_default_provider,
                                  "provider_config_sha256": self.provider_config_sha256,
                                  "structured_provider": STRUCTURED_PROVIDER, "serial_provider": SERIAL_PROVIDER,
                                  "peer_hosted_provider": self.peer_hosted_provider,
                                  "fallbacks": self.ordinary_fallback_provider is not None,
                                  "ordinary_fallback_provider": self.ordinary_fallback_provider,
                                  "breaker": self.breaker.snapshot() if self.ordinary_fallback_provider else None,
                                  "image_maintenance":self.image_gc.snapshot() if self.image_gc else {"enabled":False}})

    async def models(self, request):
        if not self.authorized(request):
            return error_response("Unauthorized", 401)
        return web.json_response(self.catalog)

    def _prepare_route(self, body, headers, provider):
        payload, registry = prepare(body, self.store, ordinary_default_provider=provider, peer_hosted_provider=self.peer_hosted_provider, native_headers=headers, opaque_replay=True)
        return payload, registry

    async def _confirm_outage(self, provider, policy='default'):
        """Ask OpenRouter's public endpoint status whether this provider is actually down."""
        key = self.credential_source() if callable(self.credential_source) else self.credential_source
        if not isinstance(key, str) or not key:
            return False
        url = 'https://openrouter.ai/api/v1/models/' + MODEL + '/endpoints'
        async with asyncio.timeout(8):
            async with self.client.request('GET', url, api_key=key, policy=policy) as response:
                if response.status != 200:
                    return False
                value = await response.json()
        endpoints = value.get('data', {}).get('endpoints') if isinstance(value, dict) else None
        for endpoint in endpoints if isinstance(endpoints, list) else []:
            if isinstance(endpoint, dict) and endpoint.get('tag') == provider:
                status, uptime = endpoint.get('status'), endpoint.get('uptime_last_5m')
                return ((type(status) is int and status < 0)
                        or (type(uptime) in (int, float) and uptime < OUTAGE_UPTIME_PERCENT))
        return False

    def _thread_route(self, body, context, *, new_turn):
        """Pick the ordinary provider for this thread: its pin, or the fallback for a new thread in an outage."""
        pinned = thread_routes.get(self.store.db, context) if context else None
        if pinned is not None:
            return pinned['provider'], 'thread_pin/' + pinned['origin'] + '/', False
        if (self.ordinary_fallback_provider is not None and new_turn and context and self.breaker.tripped()
                and thread_routes.is_new_thread(self.store.db, context, body.get('input'))):
            return (self.ordinary_fallback_provider,
                    'failover_new_thread/' + self.ordinary_default_provider + '/', True)
        return self.ordinary_default_provider, None, False

    def _select_route(self, body, headers, context, *, new_turn):
        default = self.ordinary_default_provider
        payload, registry = self._prepare_route(body, headers, default)
        ordinary = _ordinary_route(payload, registry, default)
        if not context:
            return payload, registry, False, ordinary
        provider, note, new_pin = self._thread_route(body, context, new_turn=new_turn)
        if provider != default:
            alternate, alternate_registry = self._prepare_route(body, headers, provider)
            hosted_move = (alternate['provider']['only'] == [provider]
                           and _explicit_hosted_opaque_move(
                               registry.route_reason, alternate_registry.route_reason, provider, note))
            if not hosted_move and not (ordinary and _ordinary_route(alternate, alternate_registry, provider)):
                return payload, registry, False, False
            payload, registry = alternate, alternate_registry
        elif not ordinary:
            return payload, registry, False, False
        if note:
            registry.route_reason = note + registry.route_reason
        manual_projection = note == 'thread_pin/manual/' and provider != PROVIDER
        if new_pin and thread_routes.pin(self.store.db, context, provider, 'failover', commit=False) != {
                "provider": provider, "origin": "failover"}:
            raise ProtocolError("Thread pin changed during request admission")
        return payload, registry, manual_projection, provider == default

    async def responses(self, request):
        if not self.authorized(request):
            return error_response("Unauthorized", 401)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ProtocolError("Responses request must be an object")
            native_request = native_request_metadata(body, request.headers)
            kind = native_request.get('request_kind')
            context = native_request.get('thread_id')
            if (native_request["status"] != "recognized" or kind not in {'turn', 'compaction'}
                    or context != body.get('prompt_cache_key')):
                context = None
            structured = StructuredOutput(body)
            transport_policy=network_policy(request.headers)
            # Publish the pin and the running receipt together. A move on another
            # process's connection must see either both or neither.
            with self.store.route_admission():
                payload, registry, manual_projection, primary_ordinary = self._select_route(
                    body, request.headers, context, new_turn=kind == 'turn')
                if registry.regrouped_tool_history:
                    native_request["regrouped_tool_history"] = registry.regrouped_tool_history
                receipt = self.store.start(
                    payload["provider"]["only"][0], context_key=payload.get("prompt_cache_key"),
                    network_policy=transport_policy, route_reason=registry.route_reason,
                    structured_output_mapping=registry.structured_output_mapping,
                    native_request=native_request, commit=False)
        except (ValueError, KeyError, TypeError) as exc:
            return error_response(str(exc))
        monitor_primary = self.ordinary_fallback_provider is not None and primary_ordinary and kind == 'turn'
        result = await self._respond(
            request, body, native_request, payload, registry, transport_policy, structured,
            receipt=receipt, manual_projection=manual_projection,
            monitor_primary=monitor_primary, thread=context if kind == 'turn' else None)
        if monitor_primary and self.store.db.execute(
                "SELECT status FROM receipts WHERE id=?", (receipt,)).fetchone() == ('completed',):
            self.breaker.success()
        return result

    async def _respond(self, request, body, native_request, payload, registry, transport_policy, structured,
                       *, receipt, manual_projection, monitor_primary, thread):
        """One upstream request; never retried on another provider."""
        trace = None
        response = None
        events = Events(registry)
        status = None
        captured_key = self.key
        image_stack=AsyncExitStack();prepared_images=None;image_refs=False;image_started=False
        failure_detail = None
        identity_context = None
        trace_event_lines, trace_event_bytes, trace_event_closed = [], 0, False
        def trace_upstream_event(original):
            nonlocal trace_event_bytes, trace_event_closed
            if trace is None or trace_event_closed:
                return
            try:
                failure_event = isinstance(original, dict) and original.get('type') in {'response.failed', 'error'}
                safe = (redact_provider_failure(original, captured_key) if failure_event
                        else redact_provider_trace(original, captured_key))
                encoded = (json.dumps(safe, ensure_ascii=False, allow_nan=False, sort_keys=True,
                                      separators=(",", ":")) + "\n").encode("utf-8")
                limit = 1024 * 1024
                if trace_event_bytes + len(encoded) > limit - 2048:
                    value = safe.get("response") if isinstance(safe, dict) else None
                    value = value if isinstance(value, dict) else {}
                    error = value.get("error")
                    summary = {name: value.get(name) for name in ("id", "model", "status")}
                    summary["error_code"] = error.get("code") if isinstance(error, dict) else None
                    summary = {name: item[:256] if isinstance(item, str) else item
                               if item is None or type(item) in (int, float, bool) else None
                               for name, item in summary.items()}
                    marker = {"_codex_trace": {"truncated": True, "limit_bytes": limit,
                              "captured_event_bytes": trace_event_bytes, "next_event_bytes": len(encoded)},
                              "event_type": safe.get("type") if isinstance(safe, dict) else None,
                              "response_summary": summary}
                    encoded = (json.dumps(marker, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                    trace_event_closed = True
                if trace_event_bytes + len(encoded) > limit:
                    raise ValueError("Bounded upstream event trace marker exceeds its budget")
                trace_event_lines.append(encoded)
                trace_event_bytes += len(encoded)
                private_write(trace / (receipt + "-upstream-events.jsonl"), b"".join(trace_event_lines))
            except Exception as error:
                trace_event_closed = True
                logging.getLogger(__name__).warning("Upstream event trace incomplete (%s); forward outcome unchanged",
                                                   type(error).__name__)
        def reject_credential_response_id(value):
            identifier = value.get("id") if isinstance(value, dict) else None
            if isinstance(identifier, str) and captured_key and sensitive_http_value(identifier, captured_key):
                raise ProtocolError("Provider response identity is invalid")
        def observe_response(value):
            if not isinstance(value, dict):
                return
            reject_credential_response_id(value)
            events.observe_response_id(value)
            if events.response_id is not None:
                self.store.progress(receipt, events)
                if self.images is not None:
                    self.images.record(receipt, response_id=events.response_id)
        def check_observed_identity(value, phase, **options):
            nonlocal identity_context
            try:
                validate_observed_identity(value, payload['provider']['only'][0], **options)
            except UpstreamIdentityError:
                status_value = value.get('status') if isinstance(value, dict) else None
                identity_context = upstream_identity_context(phase, status_value)
                raise
        async def deliver(operation, *args):
            try:
                return await getattr(response, operation)(*args)
            except ConnectionError as exc:
                raise _DownstreamDisconnected(operation, type(exc).__name__) from exc
        async def image_terminal(value, expected=None, phase='nonstream'):
            if image_refs:
                validate_response(value, expected)
                # Terminal shape and stable ID prove consumption ended; an
                # incompatible successful model/provider must still fail below.
                check_observed_identity(value, phase, failed=True)
                response_id, terminal = value['id'], value['status']
                self.images.record(receipt,consumption_status='provider_terminal',provider_terminal=terminal,provider_response_id=response_id,response_id=response_id)
                await prepared_images.mark_upstream_settled(response_id,terminal)
        try:
            from .policy import isolated_path
            trace_root = self.trace_source() if self.trace_source else os.environ.get("CODEX_DEEPSEEK_TRACE_DIR")
            trace = isolated_path(Path(trace_root)) if trace_root else None
            captured_key=self.credential_source() if callable(self.credential_source) else self.credential_source
            if not isinstance(captured_key,str) or not captured_key or any(c.isspace() for c in captured_key):
                raise ValueError('No usable provider credential')
            if opaque_items({'output': payload.get('input')}):
                try:
                    if manual_projection:
                        # Verify every omitted item against the original same-key
                        # official receipt. Never send this temporary projection.
                        official = {**payload, 'provider': {'only': [PROVIDER]}, 'input': list(payload['input'])}
                        restore(official, self.store.db, captured_key)
                        payload, dropped = thread_routes.without_official_reasoning(payload)
                        native_request["dropped_official_reasoning_items"] = dropped
                        self.store.record_native_request(receipt, native_request)
                    else:
                        restore(payload, self.store.db, captured_key)
                except ProtocolError as error:
                    self.store.finish(receipt, 'adapter_error', detail=str(error))
                    return error_response(str(error), 400)
            if trace:
                private_directory(trace)
                private_write(trace / (receipt + "-request.json"),json.dumps(payload, ensure_ascii=False, indent=2))
                write_native_peer_trace_evidence(trace, receipt, body, payload, registry, native_request)
            if self.images is not None:
                await self.client.require_raw()
                transport=self.images.transport(transport_policy)
                prepared_images=await image_stack.enter_async_context(transport.prepare(payload,credential=captured_key))
                preparation=prepared_images.receipt(private=False)
                image_refs=bool(preparation.get('consumption_managed',preparation.get('converted_image_occurrences',0)>0))
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
                if image_refs:
                    candidate = upstream.headers.get('X-Generation-Id')
                    if isinstance(candidate, str) and re.fullmatch(r'gen-[0-9A-Za-z-]{1,124}', candidate) and not sensitive_http_value(candidate, captured_key):
                        self.images.record(receipt, generation_header_candidate={
                            'id': candidate, 'provenance': 'http_response_header:X-Generation-Id'})
                transport_headers=response_headers(upstream.headers, captured_key)
                if trace:private_write(trace/(receipt+'-network.json'),json.dumps({'policy':transport_policy,'native_http':True}))
                if status >= 300:
                    # An HTTP error response does not settle consumption of referenced files.
                    raw = await upstream.read()
                    # Diagnostic decoding is separately bounded. Checked ordinary
                    # bytes stay unchanged; sensitive/unverifiable errors use the
                    # same safe form for forwarding and explicit tracing.
                    try:
                        diagnostic_value = _http_error_diagnostic(raw, upstream.content_type, captured_key)
                        diagnostic = json.dumps(diagnostic_value, allow_nan=False, separators=(",", ":"))
                    except Exception:
                        # Observation cannot mask the original HTTP failure.
                        diagnostic_value = {"schema": 1, "event_type": "upstream.http_error", "body_status": "unavailable"}
                        diagnostic = json.dumps(diagnostic_value, separators=(",", ":"))
                    try:
                        safe_raw = safe_error_body(raw, upstream.content_type, captured_key, status, diagnostic_value)
                    except Exception:
                        safe_raw = error_body_fallback(status, diagnostic_value)
                    if trace:
                        private_write(trace / (receipt + "-error.json"), safe_raw)
                    self.store.finish(receipt, "upstream_error", status, detail=diagnostic)
                    if (monitor_primary and status in thread_routes.PROVIDER_FAILURE_STATUSES
                            and diagnostic_value.get("body_status") == "json"
                            and diagnostic_value.get("error_code") == status):
                        # OpenRouter's own error envelope echoes the status code. The
                        # native helper's connection errors carry none, so local
                        # network failures never count. The request still fails as is.
                        await self.breaker.failure(transport_policy)
                        if (self.breaker.tripped() and thread is not None
                                and not sensitive_http_value(thread, captured_key)):
                            safe_raw = _with_outage_hint(safe_raw, self.ordinary_default_provider,
                                                         self.ordinary_fallback_provider, thread)
                    return web.Response(body=safe_raw, status=status,
                                        content_type="application/json", headers={**transport_headers,"X-Adapter-Request-Id": receipt})
                if not body.get("stream"):
                    result = await upstream.json()
                    reject_credential_response_id(result)
                    if isinstance(result, dict) and result.get("status") == "failed":
                        failure_detail = provider_failure_diagnostic({"type": "response.failed", "response": result})
                    validate_response(result)
                    failed = result["status"] == "failed"
                    if failed:
                        result = redact_provider_failure(result, captured_key)
                    observe_response(result)
                    await image_terminal(result)
                    check_observed_identity(result, 'nonstream', failed=failed,
                                            incomplete=result['status'] == 'incomplete')
                    structured.validate(result)
                    normalized=events.transform({'type':'response.'+result['status'],'response':result})
                    if opaque_items(result):
                        if result['status'] != 'completed' or payload['provider']['only'] != [PROVIDER]:
                            raise ProtocolError('Opaque reasoning requires an official DeepSeek completion')
                        events.opaque_credential = captured_key
                    result=next(event['response'] for event in reversed(normalized) if event['type']=='response.'+result['status'])
                    self.store.finish(receipt, result.get("status", "completed"), status, events,
                                      json.dumps(failure_detail) if failure_detail is not None else None)
                    return web.json_response(result, headers={**transport_headers,"X-Adapter-Request-Id": receipt})
                response = web.StreamResponse(headers={**transport_headers,"Content-Type": "text/event-stream", "Cache-Control": "no-store",
                                                        "X-Adapter-Request-Id": receipt})
                await deliver('prepare', request)
                held = None
                held_bytes = 0
                opaque_terminal = False
                async for original in sse_events(upstream.content):
                    if opaque_terminal:
                        raise ProtocolError('Provider sent an event after opaque response completion')
                    reject_credential_response_id(original.get("response") if isinstance(original, dict) else None)
                    trace_upstream_event(original)
                    kind = original.get("type") if isinstance(original, dict) else None
                    opaque = opaque_items(original)
                    if opaque:
                        if payload['provider']['only'] != [PROVIDER]:
                            raise ProtocolError('Opaque reasoning requires an official DeepSeek completion')
                        if kind == 'response.completed' and any(item.get('id') not in events.done for item in opaque):
                            raise ProtocolError('Streamed opaque reasoning lacks response.output_item.done evidence')
                        if held is None:
                            held = []
                    if kind in {"response.failed", "error"}:
                        failure_detail = provider_failure_diagnostic(original)
                    if kind == "response.failed":
                        validate_response(original.get("response"), "failed")
                    if kind in {"response.failed", "error"}:
                        original = redact_provider_failure(original, captured_key)
                    observed = original.get("response") if isinstance(original, dict) else None
                    observe_response(observed)
                    if image_refs and kind in {'response.completed','response.failed','response.incomplete'}:
                        await image_terminal(observed, kind.removeprefix("response."), phase=kind)
                    if kind != "error":
                        check_observed_identity(observed, kind, failed=kind == "response.failed",
                                                incomplete=kind == "response.incomplete")
                    for event in structured.events(original):
                        normalized_events = events.transform(event)
                        frames = [encode_sse(item) for item in normalized_events]
                        if held is not None and events.terminal and events.status == 'failed':
                            failure = normalized_events[-1]
                            failure['response']['output'] = []
                            frames = [encode_sse(failure)]
                        elif held is not None:
                            held_bytes += sum(map(len, frames))
                            if held_bytes > 8 * 1024 * 1024:
                                raise ProtocolError('Opaque response exceeds the replay evidence budget')
                            held.extend(frames)
                            if not events.terminal:
                                continue
                            if events.status != 'completed' or not any(
                                item.get('encrypted_content') for item in events.done.values()
                            ):
                                raise ProtocolError('Opaque reasoning lacks a completed native replay boundary')
                            # The terminal is not authoritative until the full
                            # transport body, including compressed integrity, ends.
                            events.opaque_credential = captured_key
                            opaque_terminal = True
                            continue
                        if events.terminal:
                            # The receipt describes the upstream model result;
                            # native logs remain authoritative for tool effects.
                            self.store.finish(receipt, events.status, status, events,
                                              json.dumps(failure_detail) if failure_detail is not None else None)
                        for frame in frames:
                            await deliver('write', frame)
                        if events.terminal:
                            await deliver('write_eof')
                            return response
                if opaque_terminal:
                    self.store.finish(receipt, events.status, status, events,
                                      json.dumps(failure_detail) if failure_detail is not None else None)
                    for frame in held:
                        await deliver('write', frame)
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
                detail = safe_error_text(str(exc), captured_key) if captured_key else str(exc)
            receipt_detail = detail
            if failure_detail is not None:
                receipt_detail = json.dumps({"message": detail, "provider_failure": failure_detail})
            if identity_context is not None:
                details = {"message": detail, "upstream_identity_context": identity_context}
                if failure_detail is not None:
                    details["provider_failure"] = failure_detail
                receipt_detail = json.dumps(details, separators=(',', ':'))
            budget = getattr(exc, 'diagnostics', None) if isinstance(exc, ImageTransportError) else None
            if isinstance(budget, dict):
                # Only the pure planner's aggregate: no URLs, image data or file IDs.
                receipt_detail = json.dumps({'message': detail, 'image_budget': budget}, separators=(',', ':'))
                if self.images is not None:
                    self.images.record(receipt, status='adapter_error', consumption_status='not_sent',
                                       budget_failure=budget)
            receipt_detail = safe_error_text(receipt_detail, captured_key)
            self.store.finish(receipt, "adapter_error", status, events, receipt_detail)
            if response is not None and response.prepared:
                try:
                    failure = {"status": "failed",
                               "error": {"code": "provider_compatibility", "message": detail}, "output": []}
                    if events.response_id is not None:
                        failure["id"] = events.response_id
                    await response.write(encode_sse({"type": "response.failed", "response": failure}))
                    await response.write_eof()
                except (ConnectionError, RuntimeError):
                    pass  # The native client already closed the stream; the receipt records failure.
                return response
            error_status=413 if isinstance(exc,PayloadBudgetExceeded) else (400 if isinstance(exc,(ImageTransportError,NativeRawUnsupported)) else 502)
            if isinstance(exc,FileOperationError):
                if exc.status == 413:
                    # A Files rejection must not signal native Responses compaction.
                    return web.json_response({'error': {
                        'type': 'invalid_request_error',
                        'code': 'file_operation_rejected',
                        'message': 'The Files API rejected a file operation (HTTP 413).',
                        'file_http_status': 413,
                    }}, status=400)
                error_status=exc.status if exc.status in {400,401,403,404,409,415,422,429} else 502
                result=error_response(detail,error_status)
                for name,value in filter_error_headers(exc.safe_headers, captured_key).items():
                    if name=='retry-after':result.headers['Retry-After']=value
                return result
            return error_response(detail,error_status)
        finally:
            try:await image_stack.aclose()
            except Exception as error:
                if self.images is not None:self.images.record(receipt,release_error_type=type(error).__name__)
