"""Per-request native identity facts for a provider wire, never native history."""
from __future__ import annotations

import json
import re
import uuid

from .protocol_errors import ProtocolError
from .request_metadata import decode_native_metadata_envelope

_KEY = "x-codex-turn-metadata"
_ERROR = "Invalid native runtime context"
# The Gateway already accepts a96MiB native HTTP request. The purpose
# observer's separate1MiB budget must never become a functional limit here.
MAX_NATIVE_CONTEXT_BYTES = 96 * 1024 ** 2
_HISTORY = frozenset({
    "new", "cleared", "cold_resume", "warm_fork", "cold_fork", "supplied_history",
})


def _valid_agent_path(value):
    # Exact fe136 protocol/src/agent_path.rs grammar, including /morpheus.
    if not isinstance(value, str):
        return False
    if value in {"/root", "/morpheus"}:
        return True
    if not value.startswith("/root/"):
        return False
    return all(segment != "root" and re.fullmatch(r"[a-z0-9_]+", segment)
               for segment in value[len("/root/"):].split("/"))


def _request_identity(metadata):
    kind = metadata.get("request_kind")
    if kind not in {"turn", "compaction", "prewarm", "memory"}:
        raise ValueError("invalid native kind")
    result = {"request_kind": kind}
    for field in ("thread_id", "turn_id"):
        if field not in metadata:
            if kind in {"turn", "compaction"}:
                raise ValueError("missing native identity")
            continue
        identifier = metadata[field]
        if (not isinstance(identifier, str) or len(identifier) != 36
                or str(uuid.UUID(identifier)) != identifier):
            raise ValueError("invalid native identity")
        result[field] = identifier
    return result


def _validate_facts(metadata):
    if "agent_name" in metadata and not _valid_agent_path(metadata["agent_name"]):
        raise ValueError("invalid agent path")
    if "history_initialization" in metadata:
        mode = metadata["history_initialization"]
        if not isinstance(mode, str) or mode not in _HISTORY:
            raise ValueError("invalid history mode")


def native_runtime_context(body, *, headers=None):
    """Render current facts only; legacy unknown observations remain nonblocking.

    Actual typed AgentMessage input requires an identified native turn, unless
    canonical metadata proves this is auxiliary history rather than execution.
    Ordinary API text/JSON echoes never activate the peer identity requirement.
    """
    items = body.get("input") if isinstance(body, dict) else None
    peer = isinstance(items, list) and any(
        isinstance(item, dict) and item.get("type") == "agent_message" for item in items)
    client = body.get("client_metadata") if isinstance(body, dict) else None
    if not isinstance(client, dict) or _KEY not in client:
        if peer:
            raise ProtocolError(_ERROR)
        return None
    try:
        metadata = decode_native_metadata_envelope(client[_KEY], max_bytes=MAX_NATIVE_CONTEXT_BYTES)
    except (ValueError, TypeError, AttributeError, RecursionError):
        if peer:
            raise ProtocolError(_ERROR) from None
        return None

    kind = metadata.get("request_kind")
    actor_turn = kind == "turn" and "agent_name" in metadata
    if not actor_turn:
        if not peer:
            return None
        if not isinstance(kind, str) or kind not in {"compaction", "prewarm", "memory"}:
            raise ProtocolError(_ERROR)
    try:
        identity = _request_identity(metadata)
        _validate_facts(metadata)
        for field, value in identity.items():
            if field != "request_kind" and field in client and client[field] != value:
                raise ValueError("flat identity conflict")
        for field in ("agent_name", "history_initialization"):
            if field in client and (field not in metadata or client[field] != metadata[field]):
                raise ValueError("flat context conflict")
        if headers is not None:
            values = [value for key, value in headers.items()
                      if isinstance(key, str) and key.casefold() == _KEY]
            if len(values) > 1:
                raise ValueError("ambiguous context header")
            if values:
                header = decode_native_metadata_envelope(values[0], max_bytes=MAX_NATIVE_CONTEXT_BYTES)
                if _request_identity(header) != identity:
                    raise ValueError("identity header conflict")
                # fe136 omits only tool_namespaces_info from the header payload.
                for field in ("agent_name", "history_initialization"):
                    if ((field in metadata) != (field in header)
                            or field in metadata and metadata[field] != header[field]):
                        raise ValueError("context header conflict")
    except (ValueError, TypeError, AttributeError, RecursionError):
        raise ProtocolError(_ERROR) from None
    if not actor_turn:
        return None
    text = "Native runtime context:\nCurrent agent name: " + json.dumps(metadata["agent_name"], ensure_ascii=False) + "\n"
    if "history_initialization" in metadata:
        text += "History initialization: " + json.dumps(metadata["history_initialization"], ensure_ascii=False) + "\n"
    return {"type": "message", "role": "developer",
            "content": [{"type": "input_text", "text": text}]}
