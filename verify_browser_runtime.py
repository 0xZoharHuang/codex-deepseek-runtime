"""Native MCP browsing with live HTTP effects, model use and cold reference recovery."""
from install import python_entry,console_entry,valid_release,_support
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import hashlib
from pathlib import Path
import select
import socket
import subprocess
import threading
import time
import uuid
from verify_live import Peer,text
from acceptance_assertions import browser_chain


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def unwrap(result):
    if result.get('isError'):raise RuntimeError('Native MCP tool error: '+json.dumps(result,ensure_ascii=False))
    if result.get('structuredContent') is not None:return result['structuredContent']
    return json.loads(''.join(x.get('text','') for x in result.get('content',[]) if x.get('type')=='text'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--native-lifetime-proof',type=Path,required=True);parser.add_argument('--sandbox-mode',choices=['workspace-write','danger-full-access'],default='workspace-write');args=parser.parse_args()
    lifetime=json.loads(args.native_lifetime_proof.read_text(encoding='utf-8'))
    if not lifetime['passed']:raise SystemExit('A passing same-version official native lifetime comparison is required')
    entry=args.entry.resolve();python=python_entry(entry.parent.parent);release=entry.parent.parent.parent
    metadata=json.loads((release/'installation.json').read_text(encoding='utf-8'));native=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
    if entry!=console_entry(release/'venv') or not valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native):
        raise SystemExit('Browser runtime acceptance requires an intact installed entry')
    if lifetime['binary_sha256']['patched']!=sha(_support.executable(Path(metadata['native_package'])/'bin','codex')):
        raise SystemExit('Native lifetime proof belongs to a different patched binary')
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True);(root/'work').mkdir()
    verifiers={name:sha(Path(__file__).with_name(name)) for name in ['verify_browser_runtime.py','verify_live.py','install.py','acceptance_assertions.py']}
    artifact={'entry':str(entry),'entry_sha256':sha(entry),'installation':metadata,'native_manifest':native,'target':native['target'],'sandbox_mode':args.sandbox_mode,'verifier_sha256':verifiers,'native_lifetime_proof_sha256':sha(args.native_lifetime_proof)}
    (root/'artifact.json').write_text(json.dumps(artifact,indent=2),encoding='utf-8')
    nonce='ACTUAL_LINK_'+uuid.uuid4().hex;hits=[];results=[]
    slow_started=threading.Event();slow_closed=threading.Event();release_slow=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path=='/slow':
                slow_started.set()
                while not release_slow.wait(.05):
                    try:disconnected=select.select([self.connection],[],[],0)[0] and not self.connection.recv(1,socket.MSG_PEEK)
                    except (ConnectionResetError,ConnectionAbortedError):disconnected=True
                    if disconnected:slow_closed.set();return
            content=('<html><body><p>release checkpoint: read the linked migration note.</p><a href="next.html">Migration note</a></body></html>' if self.path=='/start.html' else '<html><body><h1>Migration note</h1><p>Verified migration token '+nonce+'</p></body></html>')
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(content.encode())
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    url='http://127.0.0.1:'+str(server.server_port)+'/start.html'
    config='model_reasoning_effort="high"\nweb_search="disabled"\n[features]\napps=false\nplugins=false\nmemories=false\nmulti_agent=false\n'
    config+='[mcp_servers.fixture_web]\ncommand='+json.dumps(str(python))+'\nargs='+json.dumps(['-I','-m','codex_deepseek.web_mcp','--home',str(root/'home'),'--allow-private-host','127.0.0.1'])+'\ndefault_tools_approval_mode="approve"\n'
    (root/'home/config.toml').write_text(config,encoding='utf-8')
    peer=None
    def check(name,condition,detail):
        row={'name':name,'passed':bool(condition),'detail':detail};results.append(row)
        (root/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'name':name,'passed':bool(condition)}),flush=True)
        if not condition:raise AssertionError(name)
    try:
        peer=Peer(root,command=[str(entry)]);thread=peer.new(sandbox=args.sandbox_mode)
        def call(tool,arguments):return unwrap(peer.call('mcpServer/tool/call',{'threadId':thread,'server':'fixture_web','tool':tool,'arguments':arguments}))
        opened=call('open',{'url_or_ref':url});found=call('find',{'ref_id':opened['ref_id'],'pattern':'release checkpoint'})
        linked=call('click',{'ref_id':opened['ref_id'],'link_id':opened['links'][0]['id']})
        check('native_direct_mcp_actual_open_find_click',found['match_count']==1 and nonce in linked['content'] and hits==['/start.html','/next.html'],{'opened':opened,'found':found,'linked':linked,'hits':list(hits)})
        peer.turn(thread,'Do not use tools or delegate. Reply exactly BROWSER_CHECKPOINT so this native thread has a durable model turn.')
        peer.close();peer=Peer(root,command=[str(entry)]);peer.call('thread/resume',{'threadId':thread})
        found=call('find',{'ref_id':linked['ref_id'],'pattern':nonce})
        check('cold_native_mcp_page_reference_recovery',found['match_count']==1 and hits==['/start.html','/next.html'],found)
        agent=peer.new(sandbox=args.sandbox_mode);start=len(hits)
        items=peer.turn(agent,'This is a native browser capability acceptance task. All THREE native fixture_web MCP calls are mandatory in this exact order: (1) open '+url+'; (2) call the find TOOL on the returned page reference with pattern exactly "release checkpoint"; (3) click that page\'s Migration note link and read the verification token from the linked page. Even if open already shows the phrase, you MUST still execute find; visually noticing text is not the required tool call. Then use native apply_patch to create browser-result.txt in this workspace containing that exact token followed by an optional final newline. Never read fixture server code or use shell/Python/curl to fetch the URL. Do not delegate. Report the token and source URL.')
        target=root/'work/browser-result.txt'
        check('model_native_browse_find_follow_and_file_effect',target.is_file() and target.read_text(encoding='utf-8').strip()==nonce and '/start.html' in hits[start:] and '/next.html' in hits[start:] and browser_chain(items,'fixture_web',url,'release checkpoint',nonce),{'answer':text(items),'hits':hits[start:],'items':items,'file_content':target.read_text(encoding='utf-8') if target.exists() else None})
        public=unwrap(peer.call('mcpServer/tool/call',{'threadId':thread,'server':'codex_web','tool':'open','arguments':{'url_or_ref':'https://docs.python.org/3/library/asyncio-task.html'}}))
        public_find=unwrap(peer.call('mcpServer/tool/call',{'threadId':thread,'server':'codex_web','tool':'find','arguments':{'ref_id':public['ref_id'],'pattern':'Changed in version 3.13'}}))
        link=next(x for x in public['links'] if 'library/index.html' in x['url'])
        public_click=unwrap(peer.call('mcpServer/tool/call',{'threadId':thread,'server':'codex_web','tool':'click','arguments':{'ref_id':public['ref_id'],'link_id':link['id']}}))
        check('bundled_public_browser_actual_https_find_and_link',public_find['match_count']>0 and public_click['url'].startswith('https://docs.python.org/') and public_click['source_sha256']!=public['source_sha256'],{'opened':public,'find':public_find,'clicked':public_click})
        cancelling=peer.new(sandbox=args.sandbox_mode)
        turn,start=peer.start(cancelling,'Do not delegate or use shell. Use the fixture_web MCP browser to open '+url.rsplit('/',1)[0]+'/slow and wait for the document. Do not substitute another URL.')
        check('slow_native_browser_request_actually_started',slow_started.wait(45),{'hits':list(hits)})
        peer.call('turn/interrupt',{'threadId':cancelling,'turnId':turn});peer.complete(cancelling,start)
        interrupted=[e for e in peer.events[start:] if e.get('method')=='turn/completed' and e.get('params',{}).get('threadId')==cancelling][-1]
        closed_on_interrupt=slow_closed.wait(2)
        check('native_interrupt_preserves_official_mcp_lifetime',interrupted['params']['turn']['status']=='interrupted' and closed_on_interrupt==lifetime['results']['official']['http_closed_on_turn_interrupt'],{'terminal':interrupted,'http_connection_closed':closed_on_interrupt,'official_baseline':str(args.native_lifetime_proof),'note':'Upstream native MCP calls can outlive sampling-turn interruption; this is not an HTTP abort guarantee.'})
        after=call('find',{'ref_id':linked['ref_id'],'pattern':nonce})
        check('browser_remains_usable_after_turn_interruption',after['match_count']==1,after)
        peer.close();peer=None
        check('native_owner_shutdown_closes_browser_http_request',slow_closed.wait(8),{'http_connection_closed':slow_closed.is_set()})
    finally:
        if peer:peer.close()
        release_slow.set()
        server.shutdown();server.server_close();worker.join(3)
        unchanged=(sha(entry)==artifact['entry_sha256']
                   and json.loads((release/'installation.json').read_text(encoding='utf-8'))==metadata
                   and valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native)
                   and sha(args.native_lifetime_proof)==artifact['native_lifetime_proof_sha256']
                   and all(sha(Path(__file__).with_name(name))==digest for name,digest in verifiers.items()))
        verdict={'passed':len(results)==8 and all(row['passed'] for row in results) and unchanged,
                 'installation':metadata,'target':native['target'],'sandbox_mode':args.sandbox_mode,'verifier_and_artifact_unchanged':unchanged}
        (root/'verdict.json').write_text(json.dumps(verdict,indent=2),encoding='utf-8')
        (root/'http-hits.json').write_text(json.dumps(hits),encoding='utf-8');print('EVIDENCE',root,flush=True)
    if not verdict['passed']:raise AssertionError('Browser runtime artifact changed or scenario collection incomplete')


if __name__=='__main__':main()
