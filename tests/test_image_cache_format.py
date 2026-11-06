"""Image-cache format fences and reversible, quiescent metadata migration."""
import asyncio
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3

import pytest

from codex_deepseek import image_transport as image
from codex_deepseek.image_provider import maintenance_command
from codex_deepseek.processes import identity


LEGACY_SCHEMA = """
CREATE TABLE assets(digest TEXT PRIMARY KEY,mime TEXT NOT NULL,nbytes INTEGER NOT NULL,
 filename TEXT NOT NULL,state TEXT NOT NULL,file_id TEXT,created REAL NOT NULL,
 last_used REAL NOT NULL,verified REAL NOT NULL,operation_owner TEXT,reason TEXT);
CREATE TABLE pins(lease TEXT NOT NULL,digest TEXT NOT NULL,pid INTEGER NOT NULL,
 birth REAL NOT NULL,phase TEXT NOT NULL DEFAULT 'prepared',request_ref TEXT,
 PRIMARY KEY(lease,digest));
CREATE TABLE receipts(sequence INTEGER PRIMARY KEY,at REAL NOT NULL,digest TEXT NOT NULL,
 event TEXT NOT NULL,detail TEXT);
CREATE TABLE cache_identity(id INTEGER PRIMARY KEY CHECK(id=1),schema_version INTEGER NOT NULL,
 credential_scope TEXT NOT NULL,cache_root_sha256 TEXT NOT NULL);
"""
KEY = "synthetic-format-fixture-key"
SALT = bytes(range(32))


def forbidden(*args, **kwargs):
    raise AssertionError("Offline format maintenance must not use credentials or Files")


def snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        return {table: db.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall()
                for table in ("assets", "pins", "receipts")}


def version(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT schema_version FROM cache_identity").fetchone()[0]


def seed(root, *, schema=2, key=KEY, count=572, owner=lambda pid, birth: False):
    root = Path(root)
    tr = image.ImageTransport(root, forbidden, None, image.TransportPolicy(1), owner_alive=owner)
    if not (root / "scope.salt").exists():
        (root / "scope.salt").write_bytes(SALT)
    scope = hmac.new(SALT, key.encode(), hashlib.sha256).hexdigest()
    directory = root / scope
    directory.mkdir()
    path = directory / "files.sqlite"
    digest = hashlib.sha256(b"existing-owned-image").hexdigest()
    with closing(sqlite3.connect(path)) as db:
        db.executescript(LEGACY_SCHEMA)
        expected = tr._ledger_identity(directory)
        db.execute("INSERT INTO cache_identity VALUES(?,?,?,?)", (1, schema, *expected[2:]))
        db.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (digest, "image/png", 80, "cds-image-existing.png", "ready",
                    "or_file_existing0001", 1, 1, 1, "old-operation", None))
        db.executemany("INSERT INTO pins VALUES(?,?,?,?,?,?)",
                       [(f"old-{i}", digest, 2147483000, 1000.25,
                         "upstream_unknown", "old-provider-request") for i in range(count)])
        db.execute("INSERT INTO receipts VALUES(NULL,1,?,'original_event','original_detail')", (digest,))
        db.commit()
    return tr, path


def register_retired_provider(state):
    state.mkdir(parents=True, exist_ok=True)
    (state / "gateway.json").write_text(json.dumps({
        "schema": 1, "pid": 2147483000, "birth": "windows:1", "port": 12345,
        "token": "synthetic-retired-local-token-not-used",
        "code_id": "a" * 64,
    }))


@pytest.mark.asyncio
async def test_forward_and_rollback_preserve_all_original_and_new_history(tmp_path):
    tr, path = seed(tmp_path / "cache")
    before = snapshot(path)
    result = await tr.migrate_cache_format(3)
    assert result["complete"] and not result["credentials_read"]
    assert version(path) == 3 and snapshot(path) == before
    assert (tr.root / "scope.salt").read_bytes() == image.V3_SALT_FENCE
    assert (tr.root / "scope.salt.v3-original").read_bytes() == SALT
    # Rollback must preserve post-upgrade information, never restore an old DB.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO receipts VALUES(NULL,2,?,'new_v3_event','new_detail')",
                   (hashlib.sha256(b"new-event").hexdigest(),))
    after_new_work = snapshot(path)
    result = await tr.migrate_cache_format(2)
    assert result["complete"] and version(path) == 2
    assert snapshot(path) == after_new_work
    assert (tr.root / "scope.salt").read_bytes() == SALT
    assert (tr.root / "scope.salt.v3-original").read_bytes() == SALT
    before_scopes = {p.name for p in tr.root.iterdir() if p.is_dir()}
    with pytest.raises(image.ImageTransportError, match="explicit maintenance"):
        await tr._scope("rotated-key-after-rollback")
    assert {p.name for p in tr.root.iterdir() if p.is_dir()} == before_scopes


@pytest.mark.asyncio
async def test_runtime_never_automatically_upgrades_legacy(tmp_path):
    tr, path = seed(tmp_path / "cache")
    before = path.read_bytes()
    with pytest.raises(image.ImageTransportError, match="explicit maintenance"):
        await tr._scope(KEY)
    assert version(path) == 2 and path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("alive", [True, None])
async def test_live_or_unprovable_lease_owner_blocks_before_any_format_write(tmp_path, alive):
    tr, path = seed(tmp_path / "cache", owner=lambda pid, birth: alive)
    before = snapshot(path)
    with pytest.raises(image.ImageTransportError, match="provably retired"):
        await tr.migrate_cache_format(3)
    assert version(path) == 2 and snapshot(path) == before
    assert (tr.root / "scope.salt").read_bytes() == SALT
    assert not (tr.root / "cache-format.json").exists()


@pytest.mark.asyncio
async def test_bound_v1_can_explicitly_upgrade_through_two_without_losing_legacy_rows(tmp_path):
    tr, path = seed(tmp_path / "cache", schema=1)
    before = snapshot(path)
    for target in (2, 3):
        result = await tr.migrate_cache_format(target)
        assert result["complete"] and version(path) == target
        assert snapshot(path) == before


@pytest.mark.asyncio
async def test_busy_second_scope_does_not_partially_migrate_first(tmp_path):
    tr, one = seed(tmp_path / "cache", key="first")
    _, two = seed(tr.root, key="second")
    before = {p: snapshot(p) for p in (one, two)}
    selected = sorted((one.parent, two.parent))[-1]
    digest = hashlib.sha256(b"busy-operation").hexdigest()
    async with image._file_lock(selected / ("asset-" + digest + ".lock"), 120):
        with pytest.raises(image.ImageTransportError, match="lock wait expired"):
            await tr.migrate_cache_format(3)
    assert all(version(p) == 2 and snapshot(p) == before[p] for p in before)
    assert (tr.root / "scope.salt").read_bytes() == SALT


@pytest.mark.asyncio
async def test_cached_empty_scope_is_seeded_to_fence_an_old_reader(tmp_path):
    tr, path = seed(tmp_path / "cache")
    empty = tr.root / hmac.new(SALT, b"cached-old-scope", hashlib.sha256).hexdigest()
    empty.mkdir()
    assert not (empty / "files.sqlite").exists()
    result = await tr.migrate_cache_format(3)
    assert result["complete"] and version(empty / "files.sqlite") == 3
    assert all(not rows for rows in snapshot(empty / "files.sqlite").values())
    # An old already-scoped reader validates the ledger's exact v2 identity.
    assert version(empty / "files.sqlite") != 2
    # A fresh old reader refuses before deriving any new key's scope.
    assert len((tr.root / "scope.salt").read_bytes()[:33]) != 32


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["trigger", "view", "table", "unknown_version"])
async def test_dirty_or_unknown_schema_refused_before_history_changes(tmp_path, kind):
    tr, path = seed(tmp_path / "cache")
    with closing(sqlite3.connect(path)) as db, db:
        if kind == "trigger":
            db.execute("CREATE TRIGGER corrupt_history AFTER UPDATE ON cache_identity BEGIN DELETE FROM pins; END")
        elif kind == "view":
            db.execute("CREATE VIEW unexpected_view AS SELECT * FROM pins")
        elif kind == "table":
            db.execute("CREATE TABLE unexpected_table(value TEXT)")
        else:
            db.execute("UPDATE cache_identity SET schema_version=99")
    before = snapshot(path)
    with pytest.raises(image.ImageTransportError, match="schema|format|ownership"):
        await tr.migrate_cache_format(3)
    assert snapshot(path) == before
    assert not (tr.root / "cache-format.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("nonempty", [False, True])
async def test_only_known_empty_unbound_ledger_can_be_enrolled(tmp_path, nonempty):
    tr, path = seed(tmp_path / "cache")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE cache_identity")
        if not nonempty:
            for table in ("assets", "pins", "receipts"):
                db.execute('DELETE FROM "' + table + '"')
    before = snapshot(path)
    if nonempty:
        with pytest.raises(image.ImageTransportError, match="Unbound"):
            await tr.migrate_cache_format(3)
    else:
        assert (await tr.migrate_cache_format(3))["complete"]
        assert version(path) == 3
    assert snapshot(path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [2, 3])
async def test_interrupted_publication_stays_fenced_and_explicitly_resumes(tmp_path, monkeypatch, target):
    tr, path = seed(tmp_path / "cache")
    if target == 2:
        assert (await tr.migrate_cache_format(3))["complete"]
    before = snapshot(path)
    normal = tr._write_private_state
    fired = []
    def fail_final(selected, data):
        final = (selected.name == "scope.salt" and len(data) == 32) if target == 2 else (
            selected.name == "cache-format.json" and json.loads(data)["state"] == "ready")
        if final and not fired:
            fired.append(True)
            raise OSError("Injected metadata publication failure")
        return normal(selected, data)
    monkeypatch.setattr(tr, "_write_private_state", fail_final)
    result = await tr.migrate_cache_format(target)
    assert result["status"] == "format_migration_incomplete" and not result["complete"]
    assert tr._root_format() == "transition" and snapshot(path) == before
    with pytest.raises(image.ImageTransportError, match="explicit maintenance"):
        await tr._scope("fresh-key-during-incomplete-maintenance")
    monkeypatch.setattr(tr, "_write_private_state", normal)
    assert (await tr.migrate_cache_format(target))["complete"]
    assert version(path) == target and snapshot(path) == before


@pytest.mark.asyncio
async def test_history_change_is_rolled_back_before_identity_commit(tmp_path, monkeypatch):
    tr, path = seed(tmp_path / "cache")
    before = snapshot(path)
    normal = tr._ledger_projection
    calls = []
    def injected(db):
        calls.append(True)
        if len(calls) == 2:
            assert db.in_transaction
            db.execute("DELETE FROM pins")
        return normal(db)
    monkeypatch.setattr(tr, "_ledger_projection", injected)
    result = await tr.migrate_cache_format(3)
    assert not result["complete"] and snapshot(path) == before and version(path) == 2


@pytest.mark.asyncio
async def test_offline_managed_entry_uses_no_credential_native_or_files(tmp_path):
    state = tmp_path / "home/provider-state"
    tr, path = seed(state / "image-transport/files")
    register_retired_provider(state)
    result, code = await maintenance_command(state, None, forbidden, "migrate-format", target_version=3)
    assert code == 0 and result["complete"]
    assert result["managed_provider_retirement_verified_here"]
    assert not result["credentials_read"] and result["external_HTTP_requests"] == 0
    assert version(path) == 3


@pytest.mark.asyncio
async def test_managed_entry_rejects_actual_live_provider_identity(tmp_path):
    state = tmp_path / "home/provider-state"
    tr, path = seed(state / "image-transport/files")
    descriptor = {"schema": 1, "pid": os.getpid(), "birth": identity(os.getpid()),
                  "port": 12345, "token": "synthetic-local-credential-never-used",
                  "code_id": "a" * 64}
    (state / "gateway.json").write_text(json.dumps(descriptor))
    before = snapshot(path)
    with pytest.raises(RuntimeError, match="retirement is not proven"):
        await maintenance_command(state, None, forbidden, "migrate-format", target_version=3)
    assert version(path) == 2 and snapshot(path) == before


@pytest.mark.asyncio
async def test_public_cli_format_command_never_resolves_native_or_key(tmp_path, monkeypatch, capsys):
    from codex_deepseek import cli
    monkeypatch.setattr(cli, "data_root", lambda: tmp_path)
    monkeypatch.setenv("CODEX_DEEPSEEK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "native", forbidden)
    monkeypatch.setattr(cli, "credential", forbidden)
    monkeypatch.setattr(cli, "NativeTransport", forbidden)
    register_retired_provider(tmp_path / "home/provider-state")
    assert await cli.run(["runtime", "image-cache-format", "--to", "3"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["complete"] and result["managed_provider_retirement_verified_here"]
    assert not result["credentials_read"] and not result["external_HTTP_requests"]


@pytest.mark.asyncio
async def test_backup_publication_failure_keeps_original_salt_and_can_resume(tmp_path, monkeypatch):
    tr, path = seed(tmp_path / "cache")
    before = snapshot(path)
    normal = tr._write_private_state
    def fail_backup(selected, data):
        if selected.name == "scope.salt.v3-original":
            raise OSError("Injected before backup publication")
        return normal(selected, data)
    monkeypatch.setattr(tr, "_write_private_state", fail_backup)
    result = await tr.migrate_cache_format(3)
    assert not result["complete"] and tr._root_format() == "transition"
    assert snapshot(path) == before and version(path) == 2
    assert (tr.root / "scope.salt").read_bytes() == SALT
    assert not (tr.root / "scope.salt.v3-original").exists()
    monkeypatch.setattr(tr, "_write_private_state", normal)
    assert (await tr.migrate_cache_format(3))["complete"]
    assert snapshot(path) == before
    assert (tr.root / "scope.salt.v3-original").read_bytes() == SALT


@pytest.mark.asyncio
async def test_process_death_before_backup_replace_is_recoverable_without_cache_edits(tmp_path):
    import subprocess
    import sys
    tr, path = seed(tmp_path / "cache")
    before = snapshot(path)
    program = (
        "import sys,os,asyncio;"
        "from pathlib import Path;"
        "sys.path.insert(0,sys.argv[1]);"
        "from codex_deepseek import image_transport as image;"
        "normal=image._replace_private_state;"
        "\ndef crash(source,destination):\n"
        " if Path(destination).name=='scope.salt.v3-original':os._exit(74)\n"
        " return normal(source,destination)\n"
        "image._replace_private_state=crash\n"
        "transport=image.ImageTransport(Path(sys.argv[2]),lambda:None,None,image.TransportPolicy(1))\n"
        "asyncio.run(transport.migrate_cache_format(3))\n"
    )
    child = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program,
         str(Path(image.__file__).resolve().parents[1]), str(tr.root)],
        capture_output=True, timeout=15,
    )
    assert child.returncode == 74, child.stderr
    assert tr._root_format() == "transition" and snapshot(path) == before
    assert (tr.root / "scope.salt").read_bytes() == SALT
    assert not (tr.root / "scope.salt.v3-original").exists()
    assert (await tr.migrate_cache_format(3))["complete"]
    assert snapshot(path) == before and version(path) == 3
    assert (tr.root / "scope.salt.v3-original").read_bytes() == SALT


@pytest.mark.asyncio
async def test_unregistered_existing_cache_never_means_provider_is_retired(tmp_path):
    state = tmp_path / "home/provider-state"
    tr, path = seed(state / "image-transport/files")
    before = snapshot(path)
    with pytest.raises(RuntimeError, match="unregistered profile"):
        await maintenance_command(state, None, forbidden, "migrate-format", target_version=3)
    assert version(path) == 2 and snapshot(path) == before
    assert not (tr.root / "cache-format.json").exists()


@pytest.mark.asyncio
async def test_foreign_cached_directory_is_rejected_before_creating_any_file(tmp_path):
    root = tmp_path / "owned-cache"
    tr = image.ImageTransport(root, forbidden, None, image.TransportPolicy(1))
    foreign = tmp_path / ("f" * 64)
    foreign.mkdir()
    digest = hashlib.sha256(b"asset").hexdigest()
    with pytest.raises(image.ImageTransportError, match="outside"):
        async with tr._asset_lock(foreign, digest):
            raise AssertionError("Foreign operation lock must not be entered")
    with pytest.raises(image.ImageTransportError, match="outside"):
        async with tr._db(foreign):
            raise AssertionError("Foreign ledger must not be opened")
    assert list(foreign.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["adapter.sqlite", "gateway.json", "gateway.last-good.json"])
async def test_bad_lifecycle_metadata_rejected_before_any_existing_reader(tmp_path, monkeypatch, name):
    from codex_deepseek import service
    state = tmp_path / "home/provider-state"
    tr, path = seed(state / "image-transport/files")
    register_retired_provider(state)
    outside = tmp_path / "outside-sentinel"
    original = b"outside-owned-sentinel-not-a-database"
    outside.write_bytes(original)
    target = state / name
    if target.exists():
        target.unlink()
    os.link(outside, target)
    reads = []
    monkeypatch.setattr(service, "native_active", lambda home: reads.append(home) or False)
    before = snapshot(path)
    with pytest.raises(image.ImageTransportError, match="owned regular"):
        await maintenance_command(state, None, forbidden, "migrate-format", target_version=3)
    assert not reads and outside.read_bytes() == original
    assert snapshot(path) == before and version(path) == 2


def native_pid_record(pid, birth):
    import sys
    if os.name == "nt":
        return {"pid": pid, "processStartTime": birth.split(":", 1)[1]}
    if sys.platform == "linux":
        _, boot, ticks = birth.split(":")
        return {"pid": pid, "processIdentity": {"bootId": boot, "startTicks": int(ticks)}}
    seconds, micros = birth.split(":")
    return {"pid": pid, "processIdentity": {"startSeconds": int(seconds), "startMicroseconds": int(micros)}}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["daemon.pid", "app-server.pid"])
@pytest.mark.parametrize("kind", ["malformed", "active", "identity_unreadable"])
async def test_native_pid_record_must_prove_retirement_even_when_ordinary_probe_is_false(
    tmp_path, monkeypatch, name, kind
):
    from codex_deepseek import service
    state = tmp_path / "home/provider-state"
    tr, path = seed(state / "image-transport/files")
    register_retired_provider(state)
    target = state.parent / "app-server-daemon" / name
    target.parent.mkdir()
    record = native_pid_record(os.getpid(), identity(os.getpid()))
    target.write_text("{" if kind == "malformed" else json.dumps(record))
    monkeypatch.setattr(service, "native_active", lambda home: False)
    if kind == "identity_unreadable":
        original = image._owner_alive
        monkeypatch.setattr(image, "_owner_alive",
                            lambda pid, birth: None if pid == os.getpid() else original(pid, birth))
    before = snapshot(path)
    with pytest.raises(RuntimeError, match="Native daemon.*(unreadable|not proven)"):
        await maintenance_command(state, None, forbidden, "migrate-format", target_version=3)
    assert version(path) == 2 and snapshot(path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["daemon.pid", "app-server.pid"])
async def test_retired_native_pid_record_is_preserved_and_does_not_block(tmp_path, name):
    state = tmp_path / "home/provider-state"
    tr, path = seed(state / "image-transport/files")
    register_retired_provider(state)
    target = state.parent / "app-server-daemon" / name
    target.parent.mkdir()
    raw = json.dumps(native_pid_record(2147483000, identity(os.getpid())))
    target.write_text(raw)
    result, code = await maintenance_command(state, None, forbidden, "migrate-format", target_version=3)
    assert code == 0 and result["complete"] and version(path) == 3
    assert target.read_text() == raw
