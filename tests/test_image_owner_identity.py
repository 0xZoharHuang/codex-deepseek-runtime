"""Stable owner pins, conservative legacy state, and cache version boundaries."""
import os
import sqlite3
from contextlib import closing
import psutil
import pytest

from codex_deepseek import image_transport as module
from codex_deepseek.processes import identity
from test_image_transport import FakeFiles, payload, transport


def pin_rows(path):
    with closing(sqlite3.connect(path)) as db, db:
        return db.execute("SELECT lease,digest,pid,birth,typeof(birth),phase,request_ref FROM pins ORDER BY lease").fetchall()


@pytest.mark.asyncio
async def test_kernel_owner_survives_epoch_clock_shift(tmp_path, monkeypatch):
    backend = FakeFiles()
    tr = transport(tmp_path, backend)
    stable = identity(os.getpid())
    real = module.psutil.Process

    class ShiftedEpoch:
        def __init__(self, pid=None):
            self.real = real(pid)

        def create_time(self):
            return self.real.create_time() + 1

    async with tr.prepare(payload()) as call:
        path = next((tmp_path / "cache").rglob("files.sqlite"))
        before = pin_rows(path)
        monkeypatch.setattr(module.psutil, "Process", ShiftedEpoch)
        await call.mark_upstream_started("clock-shift-request")
        assert identity(os.getpid()) == stable
        after = pin_rows(path)
        assert len(before) == len(after) == 1
        assert after[0][3:6] == (stable, "text", "upstream_unknown")
        await call.mark_upstream_settled("clock-shift-response", "completed")
    assert (await tr.cleanup(all_unpinned=True))["all_owned_files_deleted"]


def test_stable_identity_distinguishes_reused_pid(monkeypatch):
    monkeypatch.setattr(module, "process_identity", lambda pid: "linux:boot:new")
    assert module._owner_alive(123, "linux:boot:old") is False
    assert module._owner_alive(123, "linux:boot:new") is True


def test_unreadable_identity_does_not_prove_death(monkeypatch):
    monkeypatch.setattr(module, "process_identity", lambda pid: None)
    assert module._owner_alive(os.getpid(), "linux:boot:unreadable") is None


def test_legacy_epoch_mismatch_is_not_proof_of_pid_reuse(monkeypatch):
    # Both a matching and an offset epoch retain a live legacy owner; neither
    # can be upgraded to the current kernel identity just because its PID exists.
    stamp = psutil.Process().create_time()
    assert module._owner_alive(os.getpid(), stamp) is None
    assert module._owner_alive(os.getpid(), stamp - 1) is None


@pytest.mark.parametrize("birth", ["linux:boot:old", 1234.0])
def test_confirmed_missing_owner_can_be_reaped(monkeypatch, birth):
    monkeypatch.setattr(module, "process_identity", lambda pid: None)

    def absent(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(module.psutil, "Process", absent)
    assert module._owner_alive(123, birth) is False


@pytest.mark.parametrize("birth", ["linux:boot:old", 1234.0])
def test_owner_access_denied_keeps_pin(monkeypatch, birth):
    monkeypatch.setattr(module, "process_identity", lambda pid: None)

    def denied(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(module.psutil, "Process", denied)
    assert module._owner_alive(123, birth) is None


def test_new_transport_requires_confirmed_kernel_identity(tmp_path, monkeypatch):
    backend = FakeFiles()
    monkeypatch.setattr(module, "process_identity", lambda pid: None)
    with pytest.raises(module.ImageTransportError, match="Cannot confirm"):
        transport(tmp_path, backend)
    assert not backend.uploaded and not (tmp_path / "cache").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["prepared", "upstream_unknown"])
async def test_bound_v1_upgrade_preserves_legacy_owner_and_receipts(tmp_path, phase):
    backend = FakeFiles()
    tr = transport(tmp_path, backend)
    async with tr.prepare(payload()):
        pass
    path = next((tmp_path / "cache").rglob("files.sqlite"))
    stamp = psutil.Process().create_time() - 10
    with closing(sqlite3.connect(path)) as db, db:
        digest = db.execute("SELECT digest FROM assets").fetchone()[0]
        db.execute("INSERT INTO pins(lease,digest,pid,birth,phase,request_ref) VALUES(?,?,?,?,?,?)",
                   ("legacy-pin", digest, os.getpid(), stamp, phase, "legacy-ref" if phase == "upstream_unknown" else None))
        db.execute("UPDATE cache_identity SET schema_version=1")
        receipts = db.execute("SELECT * FROM receipts ORDER BY sequence").fetchall()
        identity_before = db.execute("SELECT id,credential_scope,cache_root_sha256 FROM cache_identity").fetchall()
    before = pin_rows(path)
    assert before[0][4] == "real"
    status = await tr.diagnostics()
    assert status["active_transport_pins"] == 1
    assert pin_rows(path) == before
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT schema_version FROM cache_identity").fetchone() == (2,)
        assert db.execute("SELECT id,credential_scope,cache_root_sha256 FROM cache_identity").fetchall() == identity_before
        assert db.execute("SELECT * FROM receipts ORDER BY sequence").fetchall() == receipts
    cleanup = await tr.cleanup(all_unpinned=True)
    assert cleanup["pinned"] == 1 and not backend.deleted
    assert pin_rows(path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["credential_scope", "cache_root_sha256"])
async def test_v1_foreign_ledger_is_not_adopted_or_rewritten(tmp_path, field):
    tr = transport(tmp_path)
    await tr.diagnostics()
    path = next((tmp_path / "cache").rglob("files.sqlite"))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE cache_identity SET schema_version=1," + field + "=?", ("f" * 64,))
        before = db.execute("SELECT * FROM cache_identity").fetchall()
    with pytest.raises(module.ImageTransportError, match="ownership"):
        await tr.diagnostics()
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT * FROM cache_identity").fetchall() == before


@pytest.mark.asyncio
async def test_v2_stable_owner_format_rejects_v1_identity_contract(tmp_path, monkeypatch):
    tr = transport(tmp_path)
    async with tr.prepare(payload()) as call:
        path = next((tmp_path / "cache").rglob("files.sqlite"))
        before = pin_rows(path)
        expected = tr._ledger_identity(path.parent)
        assert expected[1] == 2 and before[0][4] == "text"
        # A reader expecting the old exact identity tuple must fail before its
        # stale-owner housekeeping can compare an epoch with the new string.
        with monkeypatch.context() as patch:
            patch.setattr(tr, "_ledger_identity", lambda directory: (expected[0], 1, *expected[2:]))
            with pytest.raises(module.ImageTransportError, match="ownership"):
                await tr.diagnostics()
        assert pin_rows(path) == before
        await call.mark_upstream_started("v2-protected")
        await call.mark_upstream_settled("v2-response", "completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["empty", "deleted", "ready"])
async def test_readonly_v1_foreign_scope_is_counted_without_upgrade(tmp_path, state):
    key = ["old-synthetic-key"]
    backend = FakeFiles()
    tr = module.ImageTransport(tmp_path / "cache", lambda: key[0], backend,
                               module.TransportPolicy(activate_above_bytes=1))
    if state != "empty":
        async with tr.prepare(payload()):
            pass
        if state == "deleted":
            assert (await tr.cleanup(all_unpinned=True))["all_owned_files_deleted"]
    snapshot = await tr.diagnostics()
    path = tmp_path / "cache" / snapshot["scope"] / "files.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE cache_identity SET schema_version=1")
    before = path.read_bytes()
    deleted = list(backend.deleted)
    key[0] = "new-synthetic-key"
    status = await tr.cleanup(all_unpinned=True)
    assert status["foreign_scopes"] == ([snapshot["scope"]] if state == "ready" else [])
    assert status["complete"] is (state != "ready")
    assert path.read_bytes() == before and backend.deleted == deleted
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT schema_version FROM cache_identity").fetchone() == (1,)
