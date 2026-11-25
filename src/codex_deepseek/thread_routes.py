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
_MODEL_ITEMS = frozenset({"reasoning", "function_call", "custom_tool_call", "openrouter:web_search",
                          "web_search_call", "local_shell_call"})

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


def pin(db, context_key, provider, origin, *, replace=False):
    if provider not in PINNABLE or origin not in ORIGINS:
        raise ValueError("Unsupported thread pin")
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    db.execute(f"{verb} INTO thread_routes VALUES (?,?,?,?)", (context_key, provider, origin, time.time()))
    db.commit()
    return get(db, context_key)


def is_new_thread(db, context_key, inputs):
    """True only for a thread with no completed model turn and no model output in its history."""
    if not isinstance(context_key, str) or not context_key:
        return False
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict):
                return False
            if item.get("type") in _MODEL_ITEMS or item.get("role") == "assistant":
                return False
    return db.execute("SELECT 1 FROM receipts WHERE context_key=? AND status='completed' LIMIT 1",
                      (context_key,)).fetchone() is None


def without_official_reasoning(body):
    """Return (body, dropped) with official encrypted reasoning items removed from the input."""
    inputs = body.get("input")
    if not isinstance(inputs, list):
        return body, 0
    kept = [item for item in inputs if not (isinstance(item, dict) and item.get("type") == "reasoning"
                                            and item.get("encrypted_content"))]
    return ({**body, "input": kept}, len(inputs) - len(kept)) if len(kept) != len(inputs) else (body, 0)


# --- command ---------------------------------------------------------------

USAGE = ("Usage: runtime thread list | runtime thread show <THREAD|last> | "
         "runtime thread move <THREAD|last> deepseek|morph/fp8|default")


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
    rows = db.execute("SELECT provider,status,started FROM receipts WHERE context_key=? ORDER BY started",
                      (context_key,)).fetchall()
    official = sum(1 for provider, status, _ in rows if provider == PROVIDER and status == "completed")
    return {"thread": context_key, "name": names.get(context_key),
            "last_request": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rows[-1][2])) if rows else None,
            "last_provider": rows[-1][0] if rows else None,
            "official_completed_requests": official,
            "pin": get(db, context_key)}


def _resolve(db, value):
    if value == "last":
        row = db.execute("SELECT context_key FROM receipts WHERE context_key IS NOT NULL "
                         "ORDER BY started DESC LIMIT 1").fetchone()
        if row is None:
            raise ValueError("No thread has made a model request yet")
        return row[0]
    if db.execute("SELECT 1 FROM receipts WHERE context_key=? LIMIT 1", (value,)).fetchone() is None:
        raise ValueError("No model request is recorded for thread " + value)
    return value


def move(db, context_key, provider):
    if provider != "default" and provider not in PINNABLE:
        raise ValueError("Thread provider must be deepseek, morph/fp8 or default")
    if db.execute("SELECT 1 FROM receipts WHERE context_key=? AND status='running' LIMIT 1",
                  (context_key,)).fetchone():
        raise RuntimeError("This thread has a model request in progress; move it after the turn ends")
    if provider == "default":
        db.execute("DELETE FROM thread_routes WHERE context_key=?", (context_key,))
        db.commit()
        return None
    return pin(db, context_key, provider, "manual", replace=True)


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
                "SELECT context_key FROM receipts WHERE context_key IS NOT NULL "
                "GROUP BY context_key ORDER BY MAX(started) DESC LIMIT 20")]
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
                                  + target + ". Messages, tool calls and tool results are kept.")
            elif target is None:
                result["note"] = "The thread follows the configured ordinary default provider again."
        else:
            raise ValueError(USAGE)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
