"""Process-local CPython Proactor cleanup backport; no event-loop policy changes.

The fixed method derives from CPython 3.13.3, Lib/asyncio/proactor_events.py
(Python Software Foundation License Version 2). Copyright (c) 2001-2024 Python
Software Foundation; All Rights Reserved. See licenses/cpython-proactor-PSF.txt.
Change: a shutdown error must not skip the original close/detach/state cleanup.
Unknown implementations remain unchanged and explicitly diagnosed, not repaired.
"""
from __future__ import annotations
import hashlib
import inspect
import logging
import os
import socket
import sys
import threading

_WINDOWS = sys.platform == "win32"
_REVIEWED_SOURCES = frozenset({
    # Independently verified on the packaged Windows CPython 3.13.3 runtime.
    "21755f1f4d9172c66ccb8ff6787e522916a0375b1e47222cb5f74916f30fa5ef",
})
_lock = threading.Lock()
_installed_target = None
_original_method = None
_warned = False


def _call_connection_lost(self, exc):
    if self._called_connection_lost:
        return
    try:
        self._protocol.connection_lost(exc)
    finally:
        # Preserve CPython's shutdown-before-close ordering for overlapped reads.
        try:
            if hasattr(self._sock, "shutdown") and self._sock.fileno() != -1:
                self._sock.shutdown(socket.SHUT_RDWR)
        finally:
            self._sock.close()
            self._sock = None
            server = self._server
            if server is not None:
                server._detach(self)
                self._server = None
            self._called_connection_lost = True


def _not_applied(reason, source_hash="unavailable"):
    global _warned
    if not _warned:
        logging.getLogger(__name__).warning(
            "Proactor cleanup backport NOT_APPLIED: %s; Python=%s; source_sha256=%s. "
            "Original implementation retained; cleanup fix is not qualified.",
            reason, sys.version.split()[0], source_hash,
        )
        _warned = True
    return "not_applied"


def install_proactor_cleanup_backport():
    """Repair a known method only in this process; retain Python >=3.11 support."""
    global _installed_target, _original_method
    if not _WINDOWS:
        return "not_applicable"
    with _lock:
        if sys.implementation.name != "cpython":
            return _not_applied("unknown_interpreter")
        try:
            from asyncio import proactor_events
        except ImportError:
            return _not_applied("proactor_module_unavailable")
        target = getattr(proactor_events, "_ProactorBasePipeTransport", None)
        if not inspect.isclass(target):
            return _not_applied("proactor_target_unavailable")
        current = target.__dict__.get("_call_connection_lost")
        if current is _call_connection_lost and _installed_target is target:
            return "already_applied"
        try:
            recognized = (
                inspect.isfunction(current)
                and current.__module__ == proactor_events.__name__
                and current.__qualname__ == "_ProactorBasePipeTransport._call_connection_lost"
                and current.__globals__ is vars(proactor_events)
                and current.__closure__ is None
                and current.__defaults__ is None
                and current.__kwdefaults__ is None
                and tuple(inspect.signature(current, follow_wrapped=False).parameters) == ("self", "exc")
                and os.path.normcase(os.path.abspath(current.__code__.co_filename))
                    == os.path.normcase(os.path.abspath(proactor_events.__file__))
            )
        except (AttributeError, TypeError, ValueError):
            return _not_applied("unrecognized_function_metadata")
        if not recognized:
            return _not_applied("unrecognized_function")
        try:
            source_hash = hashlib.sha256(inspect.getsource(current).encode("utf-8")).hexdigest()
        except (OSError, TypeError):
            return _not_applied("source_unavailable")
        if source_hash not in _REVIEWED_SOURCES:
            return _not_applied("unreviewed_source", source_hash)
        _original_method = current
        target._call_connection_lost = _call_connection_lost
        _installed_target = target
        return "applied"
