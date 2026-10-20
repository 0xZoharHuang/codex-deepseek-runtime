"""Native profile layering cannot route model traffic past the pinned provider."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import uuid
from aiohttp import web


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir();traps=[]
    async def trap(request):traps.append(request.path);return web.json_response({'error':'A pinned runtime must never call this trap'},status=599)
    app=web.Application();app.router.add_route('*','/{path:.*}',trap);runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
    url=f'http://127.0.0.1:{runner.addresses[0][1]}/trap'
    (root/'home/config.toml').write_text('model_reasoning_effort="low"\n')
    (root/'home/poisoned.config.toml').write_text('model="gpt-unowned"\nmodel_provider="openai"\n[model_providers.deepseek_runtime]\nbase_url='+json.dumps(url)+'\n')
    (root/'home/safe.config.toml').write_text('model_reasoning_effort="low"\nsandbox_mode="read-only"\n')
    results=[];process=None
    try:
        for profile in ['poisoned','safe']:
            process=await asyncio.create_subprocess_exec(str(args.entry.resolve()),'exec','-p',profile,'--skip-git-repo-check','--json','-C',str(root/'work'),'Do not use tools or delegate. Reply exactly PROFILE_PIN_VERIFIED.',env={**os.environ,'CODEX_DEEPSEEK_HOME':str(root/'home')},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout,stderr=await asyncio.wait_for(process.communicate(),120)
            (root/(profile+'-stdout.jsonl')).write_bytes(stdout);(root/(profile+'-stderr.txt')).write_bytes(stderr)
            with sqlite3.connect(root/'home/provider-state/adapter.sqlite') as db:receipts=db.execute('SELECT provider,status FROM receipts').fetchall()
            successful=process.returncode==0 and b'PROFILE_PIN_VERIFIED' in stdout
            rejected=process.returncode!=0 and any(word in stderr.lower() for word in [b'isolated',b'requirement',b'provider',b'bound'])
            passed=not traps and (successful or (profile=='poisoned' and rejected)) and all(provider=='deepseek' for provider,status in receipts)
            row={'profile':profile,'passed':passed,'exit':process.returncode,'model_request_receipts':receipts,'trap_requests':traps,'successful':successful,'rejected_with_binding_error':rejected};results.append(row)
            (root/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(row),flush=True)
            if not passed:raise AssertionError('Native profile escaped provider binding or safe profile lost runtime behavior')
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:await asyncio.wait_for(process.wait(),6)
            except TimeoutError:process.kill();await process.wait()
        await runner.cleanup();print('EVIDENCE',root,flush=True)


if __name__=='__main__':asyncio.run(main())
