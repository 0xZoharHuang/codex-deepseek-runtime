"""Per-thread provider pins.

A thread keeps the provider it started on. Two things create a pin:

* ``failover``: a brand-new thread started while the ordinary default was in a
  confirmed outage. It starts, and stays, on the configured fallback.
* ``manual``: ``runtime thread move``. Moving to a non-official provider drops
  official DeepSeek encrypted reasoning items, which no other provider can
  read; messages, tool calls and tool results are always kept.

Threads without a pin follow the configured ordinary default, exactly as before.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from .protocol import PROVIDER, STRUCTURED_PROVIDER
from .platforms import sqlite_filename

PINNABLE = frozenset({PROVIDER, STRUCTURED_PROVIDER})
ORIGINS = frozenset({"failover", "manual"})
# These upstream HTTP errors end before a Responses stream delivers model content.
PROVIDER_FAILURE_STATUSES = (500, 502, 503, 504, 529)

DDL = """CREATE TABLE IF NOT EXISTS thread_routes(
  context_key TEXT PRIMARY KEY, provider TEXT NOT NULL, origin TEXT NOT NULL, pinned REAL NOT NULL)"""


def ensure(db):
    db.execute(DDL)


def get(db, context_key):
    if not isinstance(context_key, str) or not context_key:
        return None
    row = db.execute("SELECT provider,origin FROM thread_routes WHERE context_key=?", (context_key,)).fetchone()
    if row is None or row[0] not in PINNABLE or row[1] not in ORIGINS:
        return None
    return {"provider": row[0], "origin": row[1]}


def pin(db, context_key, provider, origin, *, replace=False, commit=True):
    if provider not in PINNABLE or origin not in ORIGINS:
        raise ValueError("Unsupported thread pin")
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    db.execute(f"{verb} INTO thread_routes VALUES (?,?,?,?)", (context_key, provider, origin, time.time()))
    if commit:
        db.commit()
    return get(db, context_key)


def is_new_thread(db, context_key, inputs):
    """Require fresh input and no earlier request that may have delivered model content."""
    if not isinstance(context_key, str) or not context_key:
        return False
    if isinstance(inputs, list):
        for item in inputs:
            if (not isinstance(item, dict) or item.get("type", "message") != "message"
                    or item.get("role") not in {"user", "developer", "system"}):
                return False
    elif inputs is not None and not isinstance(inputs, str):
        return False
    placeholders = ",".join("?" for _ in PROVIDER_FAILURE_STATUSES)
    return db.execute(
        "SELECT 1 FROM receipts WHERE context_key=? AND "
        "(status IS NOT 'upstream_error' OR response_id IS NOT NULL OR "
        f"COALESCE(upstream_status, 0) NOT IN ({placeholders})) LIMIT 1",
        (context_key, *PROVIDER_FAILURE_STATUSES),
    ).fetchone() is None


# --- command ---------------------------------------------------------------

USAGE = ("Usage: runtime thread list | runtime thread show <THREAD|last> | "
         "runtime thread move <THREAD|last> deepseek|morph/fp8|default")
_UUID_PATTERN = "-".join("[0-9a-f]" * size for size in (8, 4, 4, 4, 12))
_NATIVE_THREAD_RECEIPT = """context_key GLOB ? AND CASE WHEN json_valid(native_request) THEN
    json_extract(native_request, '$.status') = 'recognized'
    AND json_extract(native_request, '$.source') = 'client_metadata.x-codex-turn-metadata'
    AND json_extract(native_request, '$.request_kind') IN ('turn', 'compaction')
    AND json_extract(native_request, '$.thread_id') = context_key
    ELSE 0 END"""


def _names(home):
    names = {}
    try:
        lines = (Path(home) / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return names
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            names[value["id"]] = value.get("thread_name")
    return names


def _summary(db, context_key, names):
    rows = db.execute(
        f"SELECT provider,status,started FROM receipts WHERE context_key=? AND {_NATIVE_THREAD_RECEIPT} "
        "ORDER BY started,id",
        (context_key, _UUID_PATTERN),
    ).fetchall()
    official = sum(1 for provider, status, _ in rows if provider == PROVIDER and status == "completed")
    return {"thread": context_key, "name": names.get(context_key),
            "last_request": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rows[-1][2])) if rows else None,
            "last_provider": rows[-1][0] if rows else None,
            "official_completed_requests": official,
            "pin": get(db, context_key)}


def _resolve(db, value):
    if value == "last":
        row = db.execute(
            f"SELECT context_key FROM receipts WHERE {_NATIVE_THREAD_RECEIPT} ORDER BY started DESC,id DESC LIMIT 1",
            (_UUID_PATTERN,),
        ).fetchone()
        if row is None:
            raise ValueError("No recognized native thread has made a model request yet")
        return row[0]
    if db.execute(
        f"SELECT 1 FROM receipts WHERE context_key=? AND {_NATIVE_THREAD_RECEIPT} LIMIT 1",
        (value, _UUID_PATTERN),
    ).fetchone() is None:
        raise ValueError("No recognized native model request is recorded for thread " + value)
    return value


def move(db, context_key, provider):
    if provider != "default" and provider not in PINNABLE:
        raise ValueError("Thread provider must be deepseek, morph/fp8 or default")
    db.execute("BEGIN IMMEDIATE")
    with db:
        if db.execute("SELECT 1 FROM receipts WHERE context_key=? AND status='running' LIMIT 1",
                      (context_key,)).fetchone():
            raise RuntimeError("This thread has a model request in progress; move it after the turn ends")
        if provider == "default":
            db.execute("DELETE FROM thread_routes WHERE context_key=?", (context_key,))
            return None
        return pin(db, context_key, provider, "manual", replace=True, commit=False)


def command(home, arguments):
    path = Path(home) / "provider-state" / "adapter.sqlite"
    if not path.exists():
        raise ValueError("No adapter state exists for this home yet")
    with closing(sqlite3.connect(sqlite_filename(path), timeout=30)) as db:
        ensure(db)
        db.commit()
        names = _names(home)
        if arguments == ["list"]:
            keys = [row[0] for row in db.execute(
                f"SELECT context_key FROM receipts WHERE {_NATIVE_THREAD_RECEIPT} "
                "GROUP BY context_key ORDER BY MAX(started) DESC,context_key LIMIT 20",
                (_UUID_PATTERN,),
            )]
            result = [_summary(db, key, names) for key in keys]
        elif len(arguments) == 2 and arguments[0] == "show":
            result = _summary(db, _resolve(db, arguments[1]), names)
        elif len(arguments) == 3 and arguments[0] == "move":
            key = _resolve(db, arguments[1])
            before = _summary(db, key, names)
            move(db, key, arguments[2])
            result = _summary(db, key, names)
            target = arguments[2] if arguments[2] != "default" else None
            if target is not None and target != PROVIDER and before["official_completed_requests"]:
                result["note"] = ("Hidden reasoning from earlier official DeepSeek turns will not be sent to "
                                  + target + ", including web search results that only appeared inside it. "
                                  "Messages, tool calls and tool results are kept. Move back with: "
                                  "runtime thread move " + key + " deepseek")
            elif target is None:
                result["note"] = "The thread follows the configured ordinary default provider again."
        else:
            raise ValueError(USAGE)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
