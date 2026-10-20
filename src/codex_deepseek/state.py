"""Profile-scoped recovery evidence. An interrupted model request is never replayed here."""

import json
import io
import os
from pathlib import Path
import sqlite3
import time
import uuid
import zstandard

from .protocol import PROVIDER, ProtocolError
from .processes import identity, matches, terminate

SCHEMA_VERSION = 3


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.owner = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=30)
        os.chmod(path, 0o600)
        if self.db.execute('PRAGMA user_version').fetchone()[0] > SCHEMA_VERSION:
            self.db.close()
            raise RuntimeError('Adapter state was created by a newer version')
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS searches(id TEXT PRIMARY KEY, item TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts(
              id TEXT PRIMARY KEY, started REAL, finished REAL, status TEXT,
              upstream_status INTEGER, response_id TEXT, usage TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS runtime_owners(
              owner_id TEXT PRIMARY KEY, pid INTEGER, birth TEXT,
              supervisor_pid INTEGER, supervisor_birth TEXT,
              native_pid INTEGER, native_birth TEXT, closed REAL);
            CREATE TABLE IF NOT EXISTS adapter_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        self.db.execute('BEGIN IMMEDIATE')
        self.db.execute('INSERT OR IGNORE INTO adapter_metadata VALUES (?,?)',('database_id',str(uuid.uuid4())))
        self.database_id=self.db.execute("SELECT value FROM adapter_metadata WHERE key='database_id'").fetchone()[0]
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(receipts)')}
        for name in ('provider', 'owner_id', 'context_key', 'network_policy'):
            if name not in columns:
                self.db.execute(f'ALTER TABLE receipts ADD COLUMN {name} TEXT')
        self.db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        self.db.commit()
        self.reconcile()
        self.db.execute('INSERT INTO runtime_owners(owner_id,pid,birth) VALUES (?,?,?)',
                        (self.owner,os.getpid(),identity(os.getpid())))
        self.db.commit()

    def reconcile(self):
        rows=self.db.execute('SELECT owner_id,pid,birth,supervisor_pid,supervisor_birth,native_pid,native_birth FROM runtime_owners WHERE closed IS NULL').fetchall()
        for owner,pid,birth,supervisor,supervisor_birth,native,native_birth in rows:
            if matches(pid,birth):continue
            if not matches(supervisor,supervisor_birth):terminate(native,native_birth)
            Store.owner_lost(self.path,owner)
        self.db.execute("UPDATE receipts SET status='legacy_owner_unknown',detail=? WHERE status='running' AND (owner_id IS NULL OR owner_id NOT IN (SELECT owner_id FROM runtime_owners))",
                        ('Legacy receipt has no process identity; upstream/native outcome unknown',))
        self.db.commit()

    @staticmethod
    def bind_native(path,owner,supervisor_pid,native_pid):
        with sqlite3.connect(path,timeout=30) as db:
            db.execute('UPDATE runtime_owners SET supervisor_pid=?,supervisor_birth=?,native_pid=?,native_birth=? WHERE owner_id=?',
                       (supervisor_pid,identity(supervisor_pid),native_pid,identity(native_pid),owner))

    def cleanup_native(self):
        with sqlite3.connect(self.path,timeout=30) as db:
            row=db.execute('SELECT native_pid,native_birth FROM runtime_owners WHERE owner_id=?',(self.owner,)).fetchone()
        if row:terminate(*row)

    def get(self, key):
        row = self.db.execute('SELECT item FROM searches WHERE id=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        previous = self.get(key)
        if previous and previous.get('status') == 'completed' and previous != value:
            raise ProtocolError('Provider reused a completed search ID with different evidence')
        self.db.execute('INSERT OR REPLACE INTO searches VALUES (?,?)', (key, json.dumps(value)))
        self.db.commit()

    def start(self, provider=PROVIDER, *, context_key=None, network_policy=None):
        key = str(uuid.uuid4())
        self.db.execute('INSERT INTO receipts(id,started,status,provider,owner_id,context_key,network_policy) VALUES (?,?,?,?,?,?,?)',
                        (key, time.time(), 'running', provider, self.owner, context_key, network_policy))
        self.db.commit()
        return key

    def progress(self, key, events):
        self.db.execute('UPDATE receipts SET response_id=? WHERE id=? AND owner_id=?',
                        (events.response_id, key, self.owner))
        self.db.commit()

    def finish(self, key, status, upstream=None, events=None, detail=None):
        self.db.execute('''UPDATE receipts SET finished=?,status=?,upstream_status=?,response_id=?,usage=?,detail=?
                           WHERE id=? AND owner_id=? AND status='running' ''',
                        (time.time(), status, upstream, events.response_id if events else None,
                         json.dumps(events.usage if events else {}), detail, key, self.owner))
        self.db.commit()

    @staticmethod
    def owner_lost(path, owner):
        with sqlite3.connect(path, timeout=30) as db:
            db.execute('''UPDATE receipts SET finished=?,status='owner_lost',detail=?
                          WHERE owner_id=? AND status='running' ''',
                       (time.time(), 'Owner exited without a terminal response; upstream outcome unknown', owner))
            db.execute('UPDATE runtime_owners SET closed=? WHERE owner_id=?',(time.time(),owner))

    def close(self):
        self.db.execute('''UPDATE receipts SET finished=?,status='owner_closed',detail=?
                           WHERE owner_id=? AND status='running' ''',
                        (time.time(), 'No terminal response recorded before owner shutdown', self.owner))
        self.db.commit()
        self.db.execute('UPDATE runtime_owners SET closed=? WHERE owner_id=?',(time.time(),self.owner))
        self.db.commit()
        self.db.close()


def session_lines(path):
    """Read concatenated zstd frames without accepting an unfinished last frame."""
    if path.suffix!='.zst':
        with path.open('r',encoding='utf-8') as stream:yield from stream
        return
    import codecs
    decoder=codecs.getincrementaldecoder('utf-8')()
    frame=zstandard.ZstdDecompressor().decompressobj()
    buffer=''
    with path.open('rb') as stream:
        while chunk:=stream.read(64*1024):
            while chunk:
                if frame.eof:frame=zstandard.ZstdDecompressor().decompressobj()
                output=frame.decompress(chunk)
                chunk=frame.unused_data if frame.eof else b''
                buffer+=decoder.decode(output)
                while '\n' in buffer:
                    line,buffer=buffer.split('\n',1);yield line
    if not frame.eof:raise zstandard.ZstdError('Truncated native session compression frame')
    buffer+=decoder.decode(b'',final=True)
    if buffer:yield buffer


def migrate_searches(legacy: Path, home: Path, store: Store):
    """Migrate known own-history references; preserve and report damaged evidence."""
    marker=store.path.parent/'migration-v1.json'
    if not legacy.exists() or legacy.resolve()==store.path.resolve():return
    ids=set();issues=[];paths=[];history_state={}
    try:previous=json.loads(marker.read_text())
    except (OSError,ValueError):previous={}
    if not isinstance(previous,dict):previous={}
    for directory in ('sessions','archived_sessions'):
        for path in (home/directory).rglob('*'):
            if not path.is_file() or not (path.name.endswith('.jsonl') or path.name.endswith('.jsonl.zst')):continue
            try:
                if not path.resolve().is_relative_to(home.resolve()):
                    issues.append({'path':str(path.relative_to(home)),'error':'foreign_history_reference'});continue
                stat=path.stat()
                paths.append(path)
                history_state[str(path.relative_to(home))]=[stat.st_size,stat.st_mtime_ns]
            except OSError as error:
                issues.append({'path':str(path.relative_to(home)),'error':type(error).__name__})
    import hashlib
    signature=hashlib.sha256()
    for path in sorted([*paths,legacy,legacy.with_name(legacy.name+'-wal')]):
        try:stat=path.stat()
        except OSError:continue
        signature.update(str(path).encode()+str((stat.st_size,stat.st_mtime_ns)).encode())
    source_signature=signature.hexdigest()
    same_database=previous.get('database_id')==store.database_id and previous.get('complete') is True
    if not issues and same_database and previous.get('source_signature')==source_signature:return
    unchanged=previous.get('history_state',{}) if same_database else {}
    if not isinstance(unchanged,dict):unchanged={}
    for path in paths:
        if unchanged.get(str(path.relative_to(home)))==history_state[str(path.relative_to(home))]:continue
        try:
            for line in session_lines(path):
                try:event=json.loads(line)
                except json.JSONDecodeError:
                    issues.append({'path':str(path.relative_to(home)),'error':'uncommitted_or_invalid_json'});continue
                if not isinstance(event,dict):
                    issues.append({'path':str(path.relative_to(home)),'error':'non_object_record'});continue
                payload=event.get('payload',{})
                if event.get('type')=='response_item' and isinstance(payload,dict) and payload.get('type')=='web_search_call':
                    if isinstance(payload.get('id'),str):ids.add(payload['id'])
        except (OSError,UnicodeError,zstandard.ZstdError) as error:
            issues.append({'path':str(path.relative_to(home)),'error':type(error).__name__})
    copied=0
    try:
        with sqlite3.connect(legacy.resolve().as_uri()+'?mode=ro',uri=True) as db:
            for key in ids:
                row=db.execute('SELECT item FROM searches WHERE id=?',(key,)).fetchone()
                if row:
                    try:item=json.loads(row[0])
                    except (ValueError,TypeError):
                        issues.append({'search_id':key,'error':'invalid_legacy_search'});continue
                    try:store.put(key,item);copied+=1
                    except (ProtocolError,sqlite3.Error,ValueError,AttributeError) as error:
                        store.db.rollback()
                        issues.append({'search_id':key,'error':'target_evidence_conflict_'+type(error).__name__})
                else:
                    try:available=store.get(key) is not None
                    except (ValueError,TypeError):available=False
                    if not available:issues.append({'search_id':key,'error':'missing_search_evidence'})
    except sqlite3.DatabaseError as error:
        issues.append({'path':'legacy_search_database','error':type(error).__name__})
    result={'version':1,'database_id':store.database_id,'source_signature':source_signature,'history_state':history_state,'copied_searches':copied,'source_preserved':True,'complete':not issues,'issues':issues}
    from .service import atomic_json
    target=marker if not issues else marker.with_name('migration-v1-incomplete.json')
    atomic_json(target,result)
    if issues:
        import warnings
        warnings.warn('Some isolated history evidence is damaged; unaffected sessions remain available. Affected search replay requires evidence repair. See '+str(target),RuntimeWarning)
    return result
