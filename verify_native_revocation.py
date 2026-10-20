"""Observe native policy revocation across gateway, native helper and upstream TCP."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import uuid
from aiohttp import web
from codex_deepseek.gateway import Gateway
import codex_deepseek.gateway as gateway_module
from codex_deepseek.state import Store


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe',type=Path,required=True);parser.add_argument('--http-binary',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True);stopped=asyncio.Event();started=asyncio.Event();calls=[]
    async def upstream(request):
        calls.append(request.headers.get('Authorization'))
        response=web.StreamResponse(headers={'Content-Type':'text/event-stream'});await response.prepare(request)
        await response.write(b'data: {"type":"response.created","response":{"id":"native-revocation","status":"in_progress","output":[]}}\n\n');started.set()
        try:await asyncio.Event().wait()
        finally:stopped.set()
    app=web.Application();app.router.add_post('/responses',upstream);runner=web.AppRunner(app,handler_cancellation=True,shutdown_timeout=1)
    await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
    original=gateway_module.UPSTREAM;gateway_module.UPSTREAM=f'http://127.0.0.1:{runner.addresses[0][1]}/responses'
    store=Store(root/'state.sqlite');result={};process=None
    try:
        async with Gateway('NO_REAL_SECRET','LOCAL',store,{},native_http_binary=args.http_binary.resolve()) as gateway:
            environment={key:value for key,value in os.environ.items() if not key.startswith('CODEX_') and key not in {'OPENAI_API_KEY','OPENROUTER_API_KEY'}}
            environment.update(CODEX_DEEPSEEK_POLICY_HOME=str(root),CODEX_DEEPSEEK_BRIDGE_PORT=str(gateway.port),CODEX_DEEPSEEK_E2E_GATEWAY=f'http://127.0.0.1:{gateway.port}/v1/responses',CODEX_DEEPSEEK_E2E_TOKEN='LOCAL')
            process=await asyncio.create_subprocess_exec(str(args.probe.resolve()),'--ignored','--exact','native_policy_revocation_propagates_to_upstream','--nocapture',env=environment,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout,stderr=await asyncio.wait_for(process.communicate(),30)
            (root/'native-test.txt').write_bytes(stdout+stderr)
            await asyncio.wait_for(started.wait(),3);await asyncio.wait_for(stopped.wait(),10)
            for _ in range(50):
                receipts=store.db.execute('SELECT status FROM receipts').fetchall()
                if receipts==[('cancelled',)]:break
                await asyncio.sleep(.05)
            result={'passed':process.returncode==0 and calls==['Bearer NO_REAL_SECRET'] and stopped.is_set() and receipts==[('cancelled',)],'native_test_exit':process.returncode,'upstream_calls':len(calls),'upstream_socket_cancelled':stopped.is_set(),'gateway_receipts':receipts}
            assert result['passed'],result
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:await asyncio.wait_for(process.wait(),5)
            except TimeoutError:process.kill();await process.wait()
        store.close();await runner.cleanup();gateway_module.UPSTREAM=original
        (root/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True);print('EVIDENCE',root,flush=True)


if __name__=='__main__':asyncio.run(main())
