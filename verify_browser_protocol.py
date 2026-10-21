"""MCP cancellation notifications must reach the document fetch operation."""
import argparse
import asyncio
from contextlib import suppress
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import hashlib
from pathlib import Path
import select
import socket
import threading
import uuid
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from install import python_entry,valid_release


async def scenario(args):
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True);started=threading.Event();closed=threading.Event();release=threading.Event()
    python=args.python.absolute();installed_release=python.parent.parent.parent
    metadata=json.loads((installed_release/'installation.json').read_text(encoding='utf-8'))
    native=json.loads((installed_release/'native-runtime.json').read_text(encoding='utf-8'))
    if python!=python_entry(installed_release/'venv') or not valid_release(installed_release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native):
        raise SystemExit('Browser protocol acceptance requires an intact installed interpreter')
    files=[Path(__file__),Path(__file__).with_name('install.py')]
    digests={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    report={'passed':False,'http_started':False,'http_cancelled_by_mcp':False,'interpreter':str(python),'installation':metadata,'target':native['target'],'native_manifest':native,'verifier_sha256':digests}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            started.set()
            while not release.wait(.05):
                try:disconnected=select.select([self.connection],[],[],0)[0] and not self.connection.recv(1,socket.MSG_PEEK)
                except (ConnectionResetError,ConnectionAbortedError):disconnected=True
                if disconnected:closed.set();return
            self.send_response(200);self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    params=StdioServerParameters(command=str(python),args=['-I','-m','codex_deepseek.web_mcp','--home',str(root/'home'),'--allow-private-host','127.0.0.1'])
    try:
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                task=asyncio.create_task(session.call_tool('open',{'url_or_ref':'http://127.0.0.1:'+str(server.server_port)+'/slow'}))
                report['http_started']=await asyncio.to_thread(started.wait,10)
                assert report['http_started']
                task.cancel()
                with suppress(asyncio.CancelledError):await task
                report['http_cancelled_by_mcp']=await asyncio.to_thread(closed.wait,8)
                bad=await session.call_tool('find',{'ref_id':'page_missing','pattern':'fixture'})
                rendered=bad.model_dump(by_alias=True,mode='json')
                report['anticipated_error_readable']=rendered.get('isError') is True and 'Unknown or evicted page reference' in json.dumps(rendered)
                unchanged=(all(hashlib.sha256(p.read_bytes()).hexdigest()==digests[p.name] for p in files)
                           and json.loads((installed_release/'installation.json').read_text(encoding='utf-8'))==metadata
                           and valid_release(installed_release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native))
                report['verifier_and_artifact_unchanged']=unchanged
                report['passed']=report['http_cancelled_by_mcp'] and report['anticipated_error_readable'] and unchanged
    finally:
        release.set();server.shutdown();server.server_close();thread.join(3)
        (root/'verdict.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k not in {'installation','native_manifest'}}),flush=True);print('EVIDENCE',root,flush=True)
    if not report['passed']:raise AssertionError('Browser MCP cancellation/error contract failed')


def main():
    p=argparse.ArgumentParser();p.add_argument('--python',type=Path,required=True);p.add_argument('--output',type=Path,required=True);asyncio.run(scenario(p.parse_args()))


if __name__=='__main__':main()
