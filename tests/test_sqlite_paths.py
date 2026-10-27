"""SQLite filenames preserve Windows sidecars and read-only URI semantics."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
from urllib.parse import unquote, urlsplit
import pytest
from codex_deepseek import platforms


def test_windows_sqlite_filename_formats_drive_unc_and_preserves_extended(monkeypatch):
    monkeypatch.setattr(platforms,'WINDOWS',True)
    cases=[
        (r'C:\work\one\..\two.sqlite',r'\\?\C:\work\two.sqlite'),
        (r'\\server\share\dir\..\state.sqlite',r'\\?\UNC\server\share\state.sqlite'),
        (r'\\?\C:\资料\state#%&.sqlite',r'\\?\C:\资料\state#%&.sqlite'),
        (r'\\?\UNC\server\share\state.sqlite',r'\\?\UNC\server\share\state.sqlite'),
    ]
    for logical,expected in cases:
        assert platforms.sqlite_filename(logical)==expected
        uri=platforms.sqlite_filename(logical,read_only=True)
        parsed=urlsplit(uri)
        assert parsed.scheme=='file' and not parsed.netloc and parsed.query=='mode=ro'
        assert unquote(parsed.path)==expected
        assert '%3F' in parsed.path
    assert '\\\\?\\' not in uri


def test_nonwindows_sqlite_filename_keeps_relative_writer_and_escapes_reader(tmp_path,monkeypatch):
    monkeypatch.setattr(platforms,'WINDOWS',False)
    logical=Path('relative')/'state.sqlite'
    assert platforms.sqlite_filename(logical)==os.fspath(logical)
    absolute=tmp_path/'资料 space#%&.sqlite'
    assert platforms.sqlite_filename(absolute,read_only=True)==absolute.as_uri()+'?mode=ro'


def test_sqlite_long_filename_keeps_rollback_journal_and_readonly_mode(tmp_path):
    directory=tmp_path
    while len(str(directory))<320:
        directory=directory/('nested-'+('x'*min(80,320-len(str(directory)))))
    physical_directory=Path(platforms.sqlite_filename(directory))
    physical_directory.mkdir(parents=True)
    logical=directory/'资料 space#%&.sqlite'
    assert len(str(logical))>320
    writer=platforms.sqlite_filename(logical)
    with closing(sqlite3.connect(writer,isolation_level=None)) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
        db.execute('CREATE TABLE witness(value INTEGER)')
        db.execute('INSERT INTO witness VALUES(42)')
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE witness SET value=7')
        assert Path(platforms.sqlite_filename(str(logical)+'-journal')).is_file()
        db.execute('ROLLBACK')
        assert db.execute('SELECT value FROM witness').fetchone()==(42,)
    with closing(sqlite3.connect(platforms.sqlite_filename(logical,read_only=True),uri=True)) as db:
        assert db.execute('SELECT value FROM witness').fetchone()==(42,)
        with pytest.raises(sqlite3.OperationalError) as denied:
            db.execute('INSERT INTO witness VALUES(7)')
        assert denied.value.sqlite_errorcode==sqlite3.SQLITE_READONLY
    missing=logical.with_name('missing.sqlite')
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(platforms.sqlite_filename(missing,read_only=True),uri=True)
    assert not Path(platforms.sqlite_filename(missing)).exists()
