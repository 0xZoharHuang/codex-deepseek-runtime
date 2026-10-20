"""Independent real TCP/TLS cancellation check; observe EOF at the server."""
import json
import socket
import ssl
import threading
import anyio
import httpcore

class Peer:
    def __init__(self):
        self.socket=socket.socket();self.socket.bind(('127.0.0.1',0));self.socket.listen(1);self.socket.settimeout(3)
        self.port=self.socket.getsockname()[1];self.hello=threading.Event();self.eof=threading.Event();self.connection=None;self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        try:
            conn,_=self.socket.accept();self.connection=conn;conn.settimeout(.1)
            while not self.stop.is_set():
                try:data=conn.recv(16384)
                except socket.timeout:continue
                if not data:self.eof.set();return
                self.hello.set()
        except OSError:pass
    def close(self):
        self.stop.set()
        if self.connection:
            try:self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            self.connection.close()
        self.socket.close();self.thread.join(1)

async def check(server):
    context=ssl.create_default_context();context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
    scope_ready=anyio.Event();request_finished=anyio.Event();state={}
    async with httpcore.AsyncConnectionPool(ssl_context=context,max_connections=1) as pool:
        async def request():
            with anyio.CancelScope() as scope:
                state['scope']=scope;scope_ready.set()
                await pool.request('GET',f'https://127.0.0.1:{server.port}/')
            request_finished.set()
        with anyio.fail_after(4):
            async with anyio.create_task_group() as group:
                group.start_soon(request);await scope_ready.wait()
                hello=await anyio.to_thread.run_sync(server.hello.wait,2)
                assert hello,'Client did not begin TLS handshake'
                state['scope'].cancel();await request_finished.wait()
            eof=await anyio.to_thread.run_sync(server.eof.wait,.5)
        result={'tls_client_hello':hello,'server_observed_client_eof_after_cancellation':eof,'queued_requests':len(pool._requests),'connections':len(pool.connections)}
        assert eof,'Cancelled TLS handshake left the real TCP connection open'
        assert not pool._requests and not pool.connections
        return result

out={}
for backend in ['asyncio','trio']:
    server=Peer()
    try:
        out[backend]={'passed':True,**anyio.run(check,server,backend=backend)}
    except Exception as exc:out[backend]={'passed':False,'error':str(exc)}
    finally:server.close()
print(json.dumps(out,indent=2))
raise SystemExit(0 if all(x['passed'] for x in out.values()) else 1)
