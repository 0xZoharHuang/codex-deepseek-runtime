"""Compare MCP interruption ownership with the unmodified same-version CLI."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import threading
import time
import uuid
from verify_live import Peer
from codex_deepseek.cli import native_environment,NATIVE_VERSION
from install import python_entry,valid_release


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def scenario(root,binary,python,catalog):
    (root/'home').mkdir(parents=True);(root/'work').mkdir();started=threading.Event();closed=threading.Event();release=threading.Event();requests=[]
    def tool(definitions,namespace=None):
        for spec in definitions:
            if spec['type']=='namespace':
                found=tool(spec['tools'],spec['name'])
                if found:return found
            elif spec['type']=='function' and (spec.get('name','').endswith('__open') or spec.get('name')=='open'):
                return spec['name'],namespace
        return None
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/slow':
                started.set()
                while not release.wait(.05):
                    try:disconnected=select.select([self.connection],[],[],0)[0] and not self.connection.recv(1,socket.MSG_PEEK)
                    except (ConnectionResetError,ConnectionAbortedError):disconnected=True
                    if disconnected:closed.set();return
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(b'<p>DONE</p>')
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(request)
            found=tool(request.get('tools',[]))
            if not found:
                for item in request.get('input',[]):
                    if isinstance(item,dict) and item.get('type') in {'tool_search_output','additional_tools'}:
                        found=tool(item.get('tools',[]))
                        if found:break
            if not found:
                call={'type':'tool_search_call','id':'ts_lifetime','call_id':'call_discover','execution':'client','arguments':{'query':'fixture_web open','limit':3},'status':'completed'}
            else:
                name,namespace=found
                call={'type':'function_call','id':'fc_lifetime','call_id':'call_lifetime','name':name,'arguments':json.dumps({'url_or_ref':url+'/slow'}),'status':'completed'}
                if namespace:call['namespace']=namespace
            result={'id':'resp_lifetime_'+str(len(requests)),'object':'response','status':'completed','output':[call],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
            if request.get('stream'):
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                for e in [{'type':'response.created','response':{'id':result['id'],'status':'in_progress','output':[]}}, {'type':'response.output_item.done','output_index':0,'item':call},{'type':'response.completed','response':result}]:
                    self.wfile.write(('data: '+json.dumps(e)+'\n\n').encode());self.wfile.flush()
            else:
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(result).encode())
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_port)
    config='model="deepseek/deepseek-v4.1-flash-1017"\nmodel_provider="lifetime_fixture"\nmodel_catalog_json='+json.dumps(str(catalog))+'\nweb_search="disabled"\n'
    config+='[features]\ncode_mode=false\ncode_mode_only=false\nplugins=false\napps=false\nmemories=false\nmulti_agent=false\n'
    config+='[model_providers.lifetime_fixture]\nname="Owned local model fixture"\nbase_url='+json.dumps(url+'/v1')+'\nwire_api="responses"\nsupports_websockets=false\n'
    config+='[mcp_servers.fixture_web]\ncommand='+json.dumps(str(python))+'\nargs='+json.dumps(['-I','-m','codex_deepseek.web_mcp','--home',str(root/'home'),'--allow-private-host','127.0.0.1'])+'\ndefault_tools_approval_mode="approve"\n'
    (root/'home/config.toml').write_text(config,encoding='utf-8')
    env=native_environment(root/'home','',binary,install_shims=False)
    for k in ['CODEX_DEEPSEEK_POLICY_HOME','CODEX_DEEPSEEK_BRIDGE_TOKEN','CODEX_DEEPSEEK_BRIDGE_PORT']:env.pop(k,None)
    peer=None;report={'binary':str(binary),'runtime_home':str(root/'home')}
    try:
        version=subprocess.run([str(binary),'--version'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        if version.returncode or version.stdout.strip()!='codex-cli '+NATIVE_VERSION:raise AssertionError('Native comparison requires the same frozen CLI version')
        report['version']=version.stdout.strip()
        peer=Peer(root,command=[str(binary)],environment=env)
        tid=peer.new();turn,start=peer.start(tid,'Run the actual browser open tool against the owned slow document.')
        if not started.wait(20):raise AssertionError('Native MCP request never reached HTTP server')
        peer.call('turn/interrupt',{'threadId':tid,'turnId':turn});peer.complete(tid,start)
        report['http_closed_on_turn_interrupt']=closed.wait(2)
        peer.close();peer=None
        report['http_closed_on_owner_exit']=closed.wait(8)
    finally:
        if peer:peer.close()
        release.set();server.shutdown();server.server_close();thread.join(3)
        (root/'model-fixture-requests.json').write_text(json.dumps(requests,indent=2),encoding='utf-8');(root/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--official',type=Path,required=True);p.add_argument('--patched',type=Path,required=True);p.add_argument('--python',type=Path,required=True);p.add_argument('--catalog',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True)
    python=args.python.absolute();release=python.parent.parent.parent
    installation=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    native=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
    if python!=python_entry(release/'venv') or not valid_release(release,installation['adapter_source_sha256'],installation['model_catalog_sha256'],native):
        raise SystemExit('Native MCP comparison requires an intact installed browser interpreter')
    digests={name:sha(binary) for name,binary in [('official',args.official),('patched',args.patched)]}
    if digests['patched']!=native['files']['bin/codex'+('.exe' if native['target']=='x86_64-pc-windows-msvc' else '')]:
        raise SystemExit('Native MCP comparison binary differs from its installed browser runtime')
    verifiers={name:sha(Path(__file__).parent/name) for name in ['verify_native_mcp_lifetime.py','verify_live.py','install.py','src/codex_deepseek/cli.py']}
    results={}
    for name,binary in [('official',args.official),('patched',args.patched)]:
        results[name]=scenario(root/name,binary.resolve(),python,args.catalog.resolve());print(name,json.dumps(results[name]),flush=True)
    fields=['http_closed_on_turn_interrupt','http_closed_on_owner_exit']
    unchanged=all(sha(binary)==digests[name] for name,binary in [('official',args.official),('patched',args.patched)]) and all(sha(Path(__file__).parent/name)==digest for name,digest in verifiers.items())
    unchanged=unchanged and json.loads((release/'installation.json').read_text(encoding='utf-8'))==installation and valid_release(release,installation['adapter_source_sha256'],installation['model_catalog_sha256'],native)
    result={'passed':all(results['official'][k]==results['patched'][k] for k in fields) and results['patched']['http_closed_on_owner_exit'] and unchanged,'installation':installation,'target':native['target'],'interpreter':str(python),'results':results,'binary_sha256':digests,'verifier_sha256':verifiers,'binary_and_verifier_unchanged':unchanged,'scope':'Raw native MCP lifetime comparison; model fixture is deterministic, no external model or App credentials used.'}
    (root/'verdict.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print('EVIDENCE',root,flush=True)
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
