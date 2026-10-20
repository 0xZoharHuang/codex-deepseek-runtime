"""Independent real TCP workload, cancellation under contention, both async backends."""
import contextlib
import http.server
import json
import threading
import time
import anyio
import httpcore

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def handle(self):
        with contextlib.suppress(ConnectionResetError):
            super().handle()
    def do_GET(self):
        if self.path=='/slow':time.sleep(.03)
        body=b'pool-still-usable'
        with contextlib.suppress(BrokenPipeError,ConnectionResetError):
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass

server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
server.daemon_threads=True
threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_port}'

async def workload():
    stats={'completed':0,'cancelled':0,'max_pool_connections':0,'phases':0}
    async with httpcore.AsyncConnectionPool(max_connections=3,max_keepalive_connections=2,keepalive_expiry=.005) as pool:
        async def request(index):
            with anyio.move_on_after(.008 if index%3==0 else 4) as scope:
                response=await pool.request('GET',url+('/slow' if index%2 else '/fast'))
                assert response.status==200 and response.content==b'pool-still-usable'
                stats['completed']+=1
            stats['cancelled']+=int(scope.cancel_called)
            stats['max_pool_connections']=max(stats['max_pool_connections'],len(pool.connections))
            assert len(pool.connections)<=3
        for phase in range(4):
            async with anyio.create_task_group() as group:
                for index in range(30):group.start_soon(request,index)
            assert not pool._requests
            await anyio.sleep(.01)
            stats['phases']+=1
        with anyio.fail_after(3):
            final=await pool.request('GET',url+'/fast')
        assert final.status==200 and not pool._requests
    assert stats['completed']>=60 and stats['cancelled']>=20
    return stats
try:
    print(json.dumps({backend:anyio.run(workload,backend=backend) for backend in ['asyncio','trio']},indent=2))
finally:
    server.shutdown();server.server_close()
