"""Native Windows console signals must reach Codex unchanged through wrappers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

# A real console is created per probe so broadcasts cannot reach the calling
# shell/App. The endpoint is local and never emits commands or uses an API key.
PROBE = r"""from pathlib import Path
import asyncio,ctypes,json,os,signal,subprocess,sys,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

root=Path(sys.argv[1]);source=Path(sys.argv[2]);binary=Path(sys.argv[3]);mode=sys.argv[4];event=int(sys.argv[5]) if len(sys.argv)>5 else 0
root.mkdir(parents=True);sys.path.insert(0,str(source/'src'))
from codex_deepseek.bootstrap import module_environment
from codex_deepseek.protocol import MODEL
received=threading.Event();stop=threading.Event();requests=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  requests.append({'path':self.path,'body':json.loads(self.rfile.read(int(self.headers['Content-Length'])))})
  self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
  self.wfile.write(b'data: {"type":"response.created","response":{"id":"fixture","status":"in_progress","output":[]}}\n\n');self.wfile.flush();received.set();stop.wait(20)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
threading.Thread(target=server.serve_forever,daemon=True).start();port=server.server_address[1]
base={key:value for key,value in os.environ.items() if not key.startswith('CODEX_') and key not in {'OPENROUTER_API_KEY','OPENAI_API_KEY','__PYVENV_LAUNCHER__'}}
base['PATH']=str(Path(os.environ['SystemRoot'])/'System32')+os.pathsep+os.environ['SystemRoot'];base['FIXTURE_TOKEN']='LOCAL'
(root/'catalog.json').write_bytes((source/'catalog.json').read_bytes())
args=['exec','--json','--skip-git-repo-check','--sandbox','danger-full-access','Stay on this request until interrupted. Do not run commands.']
if mode=='native':
 (root/'home').mkdir();base['CODEX_HOME']=str(root/'home')
 provider='{name="Console fixture",base_url="http://127.0.0.1:'+str(port)+'/v1",env_key="FIXTURE_TOKEN",wire_api="responses",request_max_retries=0,stream_max_retries=0,capabilities={remote_compaction="unsupported"}}'
 flags=['-c','model_provider="fixture"','-c','model="'+MODEL+'"','-c','model_catalog_json='+json.dumps(str(root/'catalog.json')),'-c','model_providers.fixture='+provider,'-c','web_search="disabled"','-c','features.multi_agent_v2=false']
 command=[str(binary),*flags,*args]
else:
 code=f'''from pathlib import Path
import asyncio
import codex_deepseek.cli as cli
class LocalProvider:
 def __init__(self,*args,**kwargs):self.port={port};self.token='LOCAL'
 async def __aenter__(self):return self
 async def __aexit__(self,*args):return False
cli.ProviderService=LocalProvider
cli.native=lambda:Path({str(binary)!r})
cli.data_root=lambda:Path({str(root)!r})
raise SystemExit(asyncio.run(cli.run({args!r})))
'''
 command=[sys._base_executable,'-I','-X','utf8','-c','import sys;sys.path.insert(0,'+repr(str(source/'src'))+');'+code]
 base=module_environment(base)
console_signals=[]
ctypes.windll.kernel32.SetConsoleCtrlHandler(None,False)
signal.signal(signal.SIGINT,lambda *args:console_signals.append('SIGINT'));signal.signal(signal.SIGBREAK,lambda *args:console_signals.append('SIGBREAK'))
with (root/'stdout.jsonl').open('w',encoding='utf-8') as out,(root/'stderr.txt').open('w',encoding='utf-8') as err:
 child=subprocess.Popen(command,env=base,cwd=root,stdin=subprocess.PIPE,stdout=out,stderr=err)
 child.stdin.close()
 outcome={'mode':mode,'pid':child.pid,'request_received':received.wait(12),'console_signal':'CTRL_C_EVENT' if event==0 else 'CTRL_BREAK_EVENT'}
 try:
  started=time.monotonic()
  if outcome['request_received']:
   processes=(ctypes.c_ulong*64)();count=ctypes.windll.kernel32.GetConsoleProcessList(processes,64);outcome['console_processes']=list(processes[:count]);outcome['controller_pid']=os.getpid()
   outcome['generate_event_result']=bool(ctypes.windll.kernel32.GenerateConsoleCtrlEvent(event,0))
   try:outcome['exit_code']=child.wait(timeout=8)
   except subprocess.TimeoutExpired:outcome['timeout']=True
   outcome['seconds_after_signal']=round(time.monotonic()-started,3)
  else:outcome['early_exit']=child.poll()
 finally:
  if child.poll() is None:child.kill();child.wait(timeout=3)
  stop.set();server.shutdown()
  outcome['controller_signals']=console_signals
  outcome['stdout']=(root/'stdout.jsonl').read_text(encoding='utf-8',errors='replace')
  outcome['stderr']=(root/'stderr.txt').read_text(encoding='utf-8',errors='replace')[-4000:]
  (root/'result.json').write_text(json.dumps(outcome,indent=2))
"""


@pytest.mark.skipif(os.name!='nt',reason='Windows console control events; requires a Windows native package')
@pytest.mark.parametrize('event,expected_exit,expected_signal',[(0,1,'SIGINT'),(1,0xC000013A,'SIGBREAK')])
def test_native_console_interrupt_keeps_original_signal_and_exit_status(tmp_path,event,expected_exit,expected_signal):
    from codex_deepseek.bootstrap import module_environment
    from codex_deepseek.native_http import native_helper
    source=Path(__file__).resolve().parents[1]
    binary=native_helper().parent/'codex.exe'
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    results=[]
    for mode in ['native','adapter']:
        root=tmp_path/mode
        process=subprocess.run([sys._base_executable,'-I','-X','utf8','-c',PROBE,str(root),str(source),str(binary),mode,str(event)],
                               env=module_environment(),creationflags=subprocess.CREATE_NEW_CONSOLE,startupinfo=startup,
                               stdin=subprocess.DEVNULL,capture_output=True,timeout=40)
        assert process.returncode==0,process.stderr.decode('utf-8',errors='replace')
        result=json.loads((root/'result.json').read_text(encoding='utf-8'));results.append(result)
        assert result['request_received'] and result['generate_event_result'] and not result.get('timeout'),result
        assert result['pid'] in result['console_processes'],result
        assert result['controller_signals']==[expected_signal],result
        assert any(json.loads(line).get('type')=='turn.started' for line in result['stdout'].splitlines()),result
        assert result['exit_code']==expected_exit,result
    assert results[0]['exit_code']==results[1]['exit_code']
