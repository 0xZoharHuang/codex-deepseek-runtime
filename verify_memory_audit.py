"""Native persistent memory extraction, consolidation, cold read and sampling."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

from verify_live import Peer,text


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--version',choices=['v1','v2'],default='v2');args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text(f'model_reasoning_effort="low"\n[features]\nmemories=false\n[memories]\nversion="{args.version}"\nmin_rollout_idle_hours=1\nmax_rollouts_per_startup=4\nuse_memories=true\n')
    nonce='MIGRATION_'+uuid.uuid4().hex
    (root/'work/ledger.py').write_text('import sqlite3\ndef credit(db,key,amount):\n    db.execute("INSERT INTO credits VALUES (?,?)",(key,amount))\n    db.commit()\n')
    (root/'work/test_ledger.py').write_text('import sqlite3\nfrom ledger import credit\ndef test_repeated_credit_is_idempotent():\n    db=sqlite3.connect(":memory:")\n    db.execute("CREATE TABLE credits (key TEXT PRIMARY KEY, amount INTEGER NOT NULL)")\n    credit(db,"same",125)\n    credit(db,"same",125)\n    assert db.execute("SELECT sum(amount) FROM credits").fetchone()[0]==125\n')
    results=[];peer=None
    def passed(name,condition,detail):
        record={'name':name,'passed':bool(condition),'detail':detail};results.append(record)
        (root/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(record),flush=True)
        if not condition:raise AssertionError(name)
    try:
        peer=Peer(root);source=peer.new()
        items=peer.turn(source,f'This is the persistent ledger project for {nonce}. My durable project preferences: store money as integer cents, use SQLite transactions, preserve existing customer data, and treat repeated identical idempotency keys as a successful no-op. Never replace those requirements with floating-point arithmetic or destructive migration. Fix ledger.py so the supplied repeat-credit test passes, then run the actual test using the Python standard-library unittest runner or a direct import of the test function. Do not delegate, search, or write memory artifacts yourself. Explain the root cause, fix, and actual verification.')
        verified=subprocess.run([sys.executable,'-I','-c','import sys;sys.path.insert(0,sys.argv[1]);from test_ledger import test_repeated_credit_is_idempotent;t=test_repeated_credit_is_idempotent;t() ',str(root/'work')],capture_output=True,text=True)
        passed('memory_source_real_tool_work',verified.returncode==0 and any(item.get('type') in {'commandExecution','fileChange'} for item in items) and 'idempoten' in text(items).lower(),text(items))
        completed=[event['params']['turn']['id'] for event in peer.events if event.get('method')=='turn/completed' and event.get('params',{}).get('threadId')==source][-1]
        sample=peer.call('thread/sample',{'threadId':source,'expectedTurnId':completed,'prompt':'Without tools, return the persistent project identifier and its integer-money convention.'})
        passed('native_thread_sample_uses_loaded_deepseek_context',nonce in str(sample.get('text')) and sample.get('sourceTurnId')==completed,sample)
        peer.close();peer=None
        # Native clamps the idle threshold to at least one hour. Follow the
        # upstream startup test's aged-candidate fixture; do not alter runtime
        # eligibility rules or wait an hour to test the background pipeline.
        database=next((root/'home').glob('state_*.sqlite'))
        with sqlite3.connect(database,timeout=10) as db:
            before=db.execute('SELECT updated_at,updated_at_ms FROM threads WHERE id=?',(source,)).fetchone()
            db.execute('UPDATE threads SET updated_at=updated_at-7200,updated_at_ms=updated_at_ms-7200000 WHERE id=?',(source,))
            after=db.execute('SELECT updated_at,updated_at_ms FROM threads WHERE id=?',(source,)).fetchone()
        (root/'fixture-clock.json').write_text(json.dumps({'reason':'upstream-style aged idle candidate','thread':source,'before':before,'after':after},indent=2))
        config=root/'home/config.toml'
        config.write_text(config.read_text().replace('memories=false','memories=true'))
        peer=Peer(root);trigger=peer.new()
        peer.turn(trigger,'Do not use tools or delegate. Reply exactly MEMORY_PIPELINE_TRIGGER.')
        end=time.monotonic()+300;selected=[];jobs=[]
        while time.monotonic()<end:
            databases=list((root/'home').glob('memories_v2_*.sqlite' if args.version=='v2' else 'memories_[0-9]*.sqlite'))
            if databases:
                with sqlite3.connect(databases[0],timeout=10) as db:
                    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'stage1_outputs' in tables:
                        db.row_factory=sqlite3.Row
                        selected=[dict(row) for row in db.execute('SELECT * FROM stage1_outputs') if row['thread_id']==source]
                    if 'jobs' in tables:jobs=[list(row) for row in db.execute('SELECT * FROM jobs')]
            memory_root=root/'home'/('memories_v2' if args.version=='v2' else 'memories')
            artifacts=list((memory_root/'rollout_summaries').glob('*.md')) if args.version=='v2' else list(memory_root.glob('MEMORY.md'))
            summaries=[path for path in (root/'home').rglob('memory_summary.md') if '/.git/' not in str(path)]
            if selected and any(row.get('selected_for_phase2') for row in selected) and artifacts and summaries:break
            time.sleep(.25)
        (root/'memory-db-evidence.json').write_text(json.dumps({'source':source,'selected':selected,'jobs':jobs},indent=2))
        passed('native_memory_extraction_and_consolidation',bool(selected) and any(row.get('selected_for_phase2') for row in selected) and bool(artifacts) and bool(summaries),{'selected':selected,'artifacts':[str(path.relative_to(root)) for path in artifacts+summaries]})
        traces=[json.loads(path.read_text()) for path in (root/'adapter-traces').glob('*-request.json')]
        passed('memory_auxiliary_models_remain_pinned_deepseek',bool(traces) and all(body['model']=='deepseek/deepseek-v4.1-flash-1017' for body in traces),{'requests':len(traces),'routes':sorted({body['provider']['only'][0] for body in traces})})
        summary_snapshots={str(path.relative_to(root)):path.read_text() for path in summaries}
        peer.close();peer=Peer(root);reader=peer.new()
        items=peer.turn(reader,'Consult the native memory guidance for this workspace. What are the ledger project identifier and durable money/idempotency conventions? Do not open project source files or delegate.')
        # Summary content selection is model quality. Runtime acceptance binds
        # actual generated artifacts to the new cold thread's native input.
        cold=[]
        for path in (root/'adapter-traces').glob('*-request.json'):
            body=json.loads(path.read_text())
            if body.get('client_metadata',{}).get('thread_id')!=reader:continue
            texts='\n'.join(content['text'] for item in body.get('input',[]) if isinstance(item,dict) and isinstance(item.get('content'),list) for content in item['content'] if isinstance(content,dict) and isinstance(content.get('text'),str))
            for name,summary in summary_snapshots.items():
                if summary.strip() and summary.strip() in texts:
                    cold.append({'request':path.name,'thread':reader,'artifact':name,'artifact_sha256':hashlib.sha256(summary.encode()).hexdigest(),'request_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        passed('native_memory_cold_read',bool(cold) and 'integer' in text(items).lower() and 'idempoten' in text(items).lower(),{'native_exact_artifact_injection':cold,'answer':text(items),'model_quality_observation':{'user_identifier_retained':nonce in text(items),'outside_runtime_pass_criterion':True}})
    finally:
        if peer:peer.close()
        print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
