"""Native profile layering cannot route model traffic past the pinned provider."""
from contextlib import closing
from verify_live import qa_sandbox_mode
import argparse
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import uuid
from aiohttp import web


def completed_reply(stdout,marker):
    try:events=[json.loads(line) for line in stdout.splitlines() if line.strip()]
    except ValueError:return False
    return (any(event.get('type')=='turn.completed' for event in events)
            and any(event.get('type')=='item.completed' and event.get('item',{}).get('type')=='agent_message'
                    and marker in event['item'].get('text','') for event in events))


def binding_rejection(stderr):
    return any(phrase in stderr.lower() for phrase in [b'isolated runtime binding',b'isolated binding',b'pins deepseek',b'pinned deepseek',b'configrequirementreadonly'])


def profile_sandbox_mode(profile,default):
    return 'read-only' if profile=='safe' else qa_sandbox_mode(default)


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sandbox-mode',choices=['workspace-write','danger-full-access'],default=qa_sandbox_mode());args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir();traps=[]
    async def trap(request):traps.append(request.path);return web.json_response({'error':'A pinned runtime must never call this trap'},status=599)
    app=web.Application();app.router.add_route('*','/{path:.*}',trap);runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
    url=f'http://127.0.0.1:{runner.addresses[0][1]}/trap'
    (root/'home/config.toml').write_text('sandbox_mode='+json.dumps(args.sandbox_mode)+'\nmodel_reasoning_effort="low"\n', encoding='utf-8')
    (root/'home/poisoned.config.toml').write_text('model="gpt-unowned"\nmodel_provider="openai"\n[model_providers.deepseek_runtime]\nbase_url='+json.dumps(url)+'\n', encoding='utf-8')
    (root/'home/safe.config.toml').write_text('model_reasoning_effort="low"\nsandbox_mode="read-only"\n', encoding='utf-8')
    results=[];process=None
    def receipts():
        database=root/'home/provider-state/adapter.sqlite'
        if not database.exists():return []
        with closing(sqlite3.connect(database)) as db, db:return db.execute('SELECT id,provider,status FROM receipts').fetchall()
    try:
        for profile in ['poisoned','safe']:
            previous_ids={row[0] for row in receipts()}
            # The safe profile intentionally tests a narrower permission layer.
            selected_mode=profile_sandbox_mode(profile,args.sandbox_mode)
            sandbox=['--sandbox',selected_mode]
            process=await asyncio.create_subprocess_exec(str(args.entry.resolve()),'exec','-p',profile,*sandbox,'--skip-git-repo-check','--json','-C',str(root/'work'),'Do not use tools or delegate. Reply exactly PROFILE_PIN_VERIFIED.',env={**os.environ,'CODEX_DEEPSEEK_HOME':str(root/'home')},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout,stderr=await asyncio.wait_for(process.communicate(),120)
            (root/(profile+'-stdout.jsonl')).write_bytes(stdout);(root/(profile+'-stderr.txt')).write_bytes(stderr)
            observed=receipts();current=[row for row in observed if row[0] not in previous_ids]
            successful=process.returncode==0 and completed_reply(stdout,'PROFILE_PIN_VERIFIED') and any(row[2]=='completed' for row in current)
            rejected=process.returncode!=0 and binding_rejection(stderr)
            passed=not traps and (successful or (profile=='poisoned' and rejected)) and all(provider=='deepseek' for _,provider,status in observed)
            row={'profile':profile,'passed':passed,'sandbox_mode':selected_mode,'qa_default_sandbox_mode':args.sandbox_mode,'exit':process.returncode,'model_request_receipts':observed,'current_profile_receipts':current,'trap_requests':traps,'successful':successful,'rejected_with_binding_error':rejected};results.append(row)
            (root/'results.json').write_text(json.dumps(results,indent=2), encoding='utf-8');print(json.dumps(row),flush=True)
            if not passed:raise AssertionError('Native profile escaped provider binding or safe profile lost runtime behavior')
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:await asyncio.wait_for(process.wait(),6)
            except TimeoutError:process.kill();await process.wait()
        await runner.cleanup();print('EVIDENCE',root,flush=True)


if __name__=='__main__':asyncio.run(main())
