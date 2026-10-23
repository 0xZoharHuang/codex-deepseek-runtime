import anyio
import pytest
import httpcore
from httpcore._backends.mock import AsyncMockStream

@pytest.fixture(params=['asyncio', 'trio'])
def anyio_backend(request):
    return request.param

class DelayedCloseStream(AsyncMockStream):
    async def aclose(self):
        await anyio.sleep(0.05)
        await super().aclose()

class Backend(httpcore.AsyncMockBackend):
    async def connect_tcp(self, *args, **kwargs):
        return DelayedCloseStream([b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n'])

@pytest.mark.anyio
async def test_cancel_during_expired_connection_close_releases_queue():
    async with httpcore.AsyncConnectionPool(network_backend=Backend([]), max_connections=1, keepalive_expiry=0.001) as pool:
        await pool.request('GET', 'http://example.com/')
        # Windows' coarse monotonic clock can leave the connection unexpired
        # after a 10 ms sleep. Establish the actual precondition before canceling.
        connection, = pool.connections
        with anyio.fail_after(1):
            while not connection.has_expired():
                await anyio.lowlevel.checkpoint()
        with anyio.move_on_after(0.01) as scope:
            await pool.request('GET', 'http://example.com/')
        assert scope.cancel_called
        assert not pool._requests, 'Cancelled request leaked in the pool queue'
        with anyio.fail_after(1):
            response = await pool.request('GET', 'http://example.com/')
        assert response.status == 200
