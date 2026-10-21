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


async def scenario(args):
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True);started=threading.Event();closed=threading.Event();release=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            started.set()
            while not release.wait(.05):
                if select.select([self.connection],[],[],0)[0] and not self.connection.recv(1,socket.MSG_PEEK):closed.set();return
            self.send_response(200);self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    params=StdioServerParameters(command=str(args.python.resolve()),args=['-I','-m','codex_deepseek.web_mcp','--home',str(root/'home'),'--allow-private-host','127.0.0.1'])
    python=args.python.absolute();installed_release=python.parent.parent.parent
    report={'passed':False,'http_started':False,'http_cancelled_by_mcp':False,'interpreter':str(python),'installation':json.loads((installed_release/'installation.json').read_text())}
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
                report['passed']=report['http_cancelled_by_mcp'] and report['anticipated_error_readable']
    finally:
        release.set();server.shutdown();server.server_close();thread.join(3)
        (root/'verdict.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True);print('EVIDENCE',root,flush=True)
    if not report['passed']:raise AssertionError('Browser MCP cancellation/error contract failed')


def main():
    p=argparse.ArgumentParser();p.add_argument('--python',type=Path,required=True);p.add_argument('--output',type=Path,required=True);asyncio.run(scenario(p.parse_args()))


if __name__=='__main__':main()
