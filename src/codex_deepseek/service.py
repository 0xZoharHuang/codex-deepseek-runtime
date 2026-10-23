"""Profile-scoped provider service with a socket-owning restart supervisor.

The listening socket survives a worker crash. Model requests are never replayed.
Idle services exit; native daemon and active client leases retain the service.
"""
import asyncio
from .platforms import WINDOWS, executable, file_lock, private_directory, install_signals, replace_file
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .processes import identity,matches,terminate
from .bootstrap import module_command, module_environment

# Windows can cold-load both supervisor and worker from a newly installed
# environment. Keep the outer deadline longer than the worker's handshake;
# successful readiness still requires a matching process and authenticated health.
STARTUP_SECONDS = 180 if WINDOWS else 20
RECOVERY_SECONDS = 120 if WINDOWS else 10


def code_id(catalog,native_binary=None,home=None):
    digest=hashlib.sha256((sys.prefix+'\0'+sys.version).encode())
    for path in sorted(Path(__file__).parent.glob('*.py')):digest.update(path.name.encode()+b'\0'+path.read_bytes())
    digest.update(Path(catalog).read_bytes())
    if home is not None:
        image_config=Path(home)/'provider-state/image-transport.json'
        digest.update(b'image-transport-config\0'+(image_config.read_bytes() if image_config.exists() else b'disabled'))
    digest.update(b'credential-environment\0'+hashlib.sha256(os.environ.get('OPENROUTER_API_KEY','').strip().encode()).digest())
    for key in ['CODEX_CA_CERTIFICATE','SSL_CERT_FILE','SSL_CERT_DIR','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
                'http_proxy','https_proxy','all_proxy','no_proxy']:
        digest.update(key.encode()+b'\0'+os.environ.get(key,'').encode()+b'\0')
    for key in ['CODEX_CA_CERTIFICATE','SSL_CERT_FILE']:
        path=os.environ.get(key)
        if path:digest.update(Path(path).read_bytes())
    if native_binary:
        package=Path(native_binary).parent.parent
        digest.update(str(package).encode())
        digest.update((package/'codex-package.json').read_bytes())
        helper=executable(package/'bin','codex-http-transport')
        if helper.is_file():
            with helper.open('rb') as stream:digest.update(hashlib.file_digest(stream,'sha256').digest())
    return digest.hexdigest()


def descriptor_path(home):return Path(home)/'provider-state/gateway.json'


def atomic_json(path,value):
    temporary=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    with open(temporary,'w',opener=lambda name,flags:os.open(name,flags,0o600)) as stream:
        json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
    replace_file(temporary,path)
    if path.name=='gateway.json':atomic_json(path.with_name('gateway.last-good.json'),value)


def valid_descriptor(value):
    return (isinstance(value,dict) and value.get('schema')==1
            and type(value.get('pid')) is int and value['pid']>0
            and isinstance(value.get('birth'),str) and bool(value['birth'])
            and type(value.get('port')) is int and 0<value['port']<65536
            and isinstance(value.get('token'),str) and len(value['token'])>=32
            and isinstance(value.get('code_id'),str) and len(value['code_id'])==64)


def read_descriptor(home):
    path=descriptor_path(home)
    for candidate in [path,path.with_name('gateway.last-good.json')]:
        try:
            value=json.loads(candidate.read_text(encoding='utf-8'))
            if valid_descriptor(value):return value
        except (OSError,ValueError):pass
    return {}


def lease_held(state):
    with (state/'gateway.lease').open('a') as lease:
        try:
            with file_lock(lease,blocking=False):return False
        except BlockingIOError:return True


def native_active(home):
    """Only inspect this profile's recorded kernel identities and daemon PID file."""
    state=Path(home)/'provider-state/adapter.sqlite'
    if state.exists():
        try:
            with sqlite3.connect(state,timeout=5) as db:
                rows=db.execute('SELECT native_pid,native_birth FROM runtime_owners WHERE closed IS NULL AND native_pid IS NOT NULL').fetchall()
                requests=db.execute("SELECT pid,birth FROM runtime_owners WHERE owner_id IN (SELECT owner_id FROM receipts WHERE status='running')").fetchall()
        except sqlite3.Error:
            # The worker can have created the file without publishing its schema
            # yet. Unknown ownership must retain the service, never crash its
            # supervisor or authorize replacement of a possibly active runtime.
            return True
        if any(matches(pid,birth) for pid,birth in rows):return True
        if any(matches(pid,birth) for pid,birth in requests):return True
    for name in ['daemon.pid','app-server.pid']:
        try:
            record=json.loads((Path(home)/'app-server-daemon'/name).read_text(encoding='utf-8'))
            if WINDOWS:
                # Upstream backend/pid_start.rs leaves processIdentity absent on
                # Windows; backend/windows.rs serializes creation FILETIME here.
                birth='windows:'+record['processStartTime']
            else:
                detail=record.get('processIdentity') or {}
                birth=('linux:'+detail['bootId']+':'+str(detail['startTicks'])) if 'startTicks' in detail else f'{detail["startSeconds"]}:{detail["startMicroseconds"]}'
            if matches(record['pid'],birth):return True
        except (OSError,ValueError,KeyError,TypeError):continue
    return False


def healthy(value):
    if not valid_descriptor(value):return False
    if not matches(value.get('pid'),value.get('birth')):return False
    request=urllib.request.Request(f'http://127.0.0.1:{value["port"]}/health',headers={'Authorization':'Bearer '+value['token']})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=.5) as response:return response.status==200
    except (OSError,ValueError):return False


def ensure(home,catalog,native_binary=None):
    from .policy import isolated_path
    home=isolated_path(Path(home));private_directory(home);state=home/'provider-state';private_directory(state)
    expected=code_id(catalog,native_binary,home)
    with (state/'gateway.lock').open('a') as lock, file_lock(lock):
        trace=os.environ.get('CODEX_DEEPSEEK_TRACE_DIR')
        # A daemon keeps operating after its launcher exits. An unrelated
        # maintenance command must not silently disable that profile's tracing.
        # An explicitly empty variable turns tracing off; unset preserves it.
        if trace is not None or not (state/'trace.json').exists():
            atomic_json(state/'trace.json',{'directory':str(isolated_path(Path(trace))) if trace else None})
        old=read_descriptor(home)
        if not old and lease_held(state):
            raise RuntimeError('Provider metadata and its backup are invalid while the profile service lease is held; restore gateway metadata before starting another service')
        if matches(old.get('pid'),old.get('birth')):
            if old.get('code_id')!=expected:
                if native_active(home):raise RuntimeError('The provider runtime or its network/credential environment changed. Close native sessions and stop this profile daemon, or use a separate isolated home')
                terminate(old['pid'],old['birth'])
            else:
                end=time.monotonic()+RECOVERY_SECONDS
                while time.monotonic()<end:
                    if healthy(old):
                        atomic_json(descriptor_path(home),old)
                        return old
                    if not matches(old['pid'],old['birth']):break
                    time.sleep(.1)
                if native_active(home):raise RuntimeError('Provider service is unhealthy while native sessions are active; inspect this profile gateway.log')
                terminate(old['pid'],old['birth'])
        log_path=state/'gateway.log'
        with open(log_path,'a',opener=lambda name,flags:os.open(name,flags,0o600)) as log:
            environment=dict(os.environ)
            for name in ['CODEX_HOME','CODEX_DEEPSEEK_BRIDGE_TOKEN','CODEX_DEEPSEEK_TRACE_DIR']:environment.pop(name,None)
            process=subprocess.Popen(module_command('codex_deepseek.service',home,catalog,str(native_binary) if native_binary else ''),
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=module_environment(environment),**({'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS} if WINDOWS else {'start_new_session':True}))
        birth=identity(process.pid);end=time.monotonic()+STARTUP_SECONDS
        while time.monotonic()<end:
            value=read_descriptor(home)
            if value.get('pid')==process.pid and value.get('code_id')==expected and healthy(value):return value
            if process.poll() is not None:break
            time.sleep(.05)
        terminate(process.pid,birth)
        raise RuntimeError(f'Provider service failed startup; inspect {log_path}')


class ProviderService:
    def __init__(self,home,catalog,native_binary=None):self.home=home;self.catalog=catalog;self.native_binary=native_binary
    async def __aenter__(self):
        self.metadata=await asyncio.to_thread(ensure,self.home,self.catalog,self.native_binary)
        self.port=self.metadata['port'];self.token=self.metadata['token']
        return self
    async def __aexit__(self,*exc):pass


async def worker(home,catalog,fd):
    from .cli import credential
    from .gateway import Gateway
    from .state import Store
    value=read_descriptor(home)
    sock=None if WINDOWS else socket.socket(fileno=fd)
    if sock:sock.setblocking(False)
    if code_id(catalog,value.get('native_binary'),home)!=value['code_id']:raise RuntimeError('Provider service code changed; restart from an immutable release')
    store=Store(Path(home)/'provider-state/adapter.sqlite');stopped=asyncio.Event();loop=asyncio.get_running_loop()
    restore_signals=install_signals(loop,{signal.SIGTERM:stopped.set,signal.SIGINT:stopped.set})
    async def watch_owner():
        while not stopped.is_set():
            if not matches(value['pid'],value['birth']):stopped.set();return
            await asyncio.sleep(.25)
    watcher=asyncio.create_task(watch_owner())
    try:
        def trace_source():return json.loads((Path(home)/'provider-state/trace.json').read_text(encoding='utf-8')).get('directory')
        async with Gateway(credential,value['token'],store,json.loads(Path(catalog).read_text(encoding='utf-8')),listen_socket=sock,trace_source=trace_source,native_http_binary=executable(Path(value['native_binary']).parent,'codex-http-transport') if value.get('native_binary') else None,external_port=value['port'] if WINDOWS else None) as gateway:
            if WINDOWS:
                from .windows_sockets import Receiver
                receiver=Receiver(gateway,stopped);receiver.start()
                try:await stopped.wait()
                finally:await receiver.close()
            else:await stopped.wait()
    finally:
        restore_signals();watcher.cancel();await asyncio.gather(watcher,return_exceptions=True);store.close()


def supervise(home,catalog,native_binary=None):
    # Startup serialization alone cannot prevent two watchdogs after damaged
    # metadata or an interrupted launch. Hold this kernel lease for our lifetime.
    state=Path(home)/'provider-state'
    with (state/'gateway.lease').open('a') as lease:
        try:
            with file_lock(lease,blocking=False):_supervise(home,catalog,native_binary)
        except BlockingIOError:raise RuntimeError('This profile already has a provider service owner')


def _supervise(home,catalog,native_binary):
    home=Path(home);path=descriptor_path(home);previous=read_descriptor(home)
    sock=socket.socket();sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE if WINDOWS else socket.SO_REUSEADDR,1)
    # A just-killed watchdog's old worker may retain the inherited socket until
    # its identity watcher runs. Preserve the port while that owner exits.
    end=time.monotonic()+3
    while True:
        try:
            sock.bind(('127.0.0.1',previous.get('port',0)));break
        except OSError:
            if not previous or time.monotonic()>=end:break
            time.sleep(.1)
    if not sock.getsockname()[1]:
        if native_active(home):raise RuntimeError('The stable provider port is unavailable while this profile has active native sessions')
        sock.bind(('127.0.0.1',0))
    sock.listen(128)
    value={'schema':1,'pid':os.getpid(),'birth':identity(os.getpid()),'port':sock.getsockname()[1],
           'token':previous.get('token') or secrets.token_urlsafe(48),'code_id':code_id(catalog,native_binary,home),'native_binary':str(native_binary) if native_binary else None,
           'environment':sys.prefix,'started':time.time(),'worker_pid':None,'worker_birth':None,'restarts':0}
    atomic_json(path,value);stopped=False;child=None;idle=time.monotonic();failures=[]
    def stop(signum,frame):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        while not stopped:
            command=module_command('codex_deepseek.service','--worker',home,catalog,sock.fileno())
            if WINDOWS:
                child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,env=module_environment())
            else:child=subprocess.Popen(command,pass_fds=(sock.fileno(),))
            value.update(worker_pid=child.pid,worker_birth=identity(child.pid));atomic_json(path,value)
            if WINDOWS:
                from .windows_sockets import Broker, HandoffError
                broker=Broker(sock,child);idle=time.monotonic()
            while child.poll() is None and not stopped:
                if native_active(home):idle=time.monotonic()
                if time.monotonic()-idle>float(os.environ.get('CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS','120')):stopped=True
                if WINDOWS:
                    try:broker.accept(.1)
                    except HandoffError:
                        if child.poll() is None:child.terminate();child.wait(timeout=6)
                else:time.sleep(.25)
            if stopped:break
            value['restarts']+=1;atomic_json(path,value)
            failures=[stamp for stamp in failures if time.monotonic()-stamp<60];failures.append(time.monotonic())
            if len(failures)>5:raise RuntimeError('Provider worker repeatedly crashed; refusing an infinite restart loop')
            time.sleep(min(len(failures),5))
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=6)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        sock.close()


if __name__=='__main__':
    if sys.argv[1]=='--worker':asyncio.run(worker(Path(sys.argv[2]),Path(sys.argv[3]),int(sys.argv[4])))
    else:supervise(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3] if len(sys.argv)>3 and sys.argv[3] else None)
