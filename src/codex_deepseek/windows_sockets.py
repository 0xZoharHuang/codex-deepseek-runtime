"""Pass accepted Windows sockets without rebinding a listener's IOCP owner.

The watchdog keeps its synchronous listener. Each new client is duplicated once
into one worker, which uses the normal aiohttp/asyncio HTTP implementation.
There is no HTTP parsing, byte relay, request replay or native tool execution.
"""
import asyncio
import ctypes
import json
import msvcrt
import os
import select
import socket
import sys
import threading
import time
from ctypes import wintypes


class Messages:
    def __init__(self,stream):
        self.stream=stream;self.handle=msvcrt.get_osfhandle(stream.fileno());self.buffer=b''
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.PeekNamedPipe.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
        self.kernel.PeekNamedPipe.restype=wintypes.BOOL

    def read(self,timeout):
        deadline=time.monotonic()+timeout
        while True:
            if b'\n' in self.buffer:
                line,self.buffer=self.buffer.split(b'\n',1);return json.loads(line)
            count=wintypes.DWORD()
            if not self.kernel.PeekNamedPipe(self.handle,None,0,None,ctypes.byref(count),None):
                error=ctypes.get_last_error()
                if error==109:raise EOFError('Owned socket control pipe closed')
                raise ctypes.WinError(error)
            if count.value:
                self.buffer+=os.read(self.stream.fileno(),min(count.value,16384))
                if len(self.buffer)>16384:raise RuntimeError('Invalid owned socket control message')
                continue
            if time.monotonic()>=deadline:return None
            time.sleep(.01)


def send(stream,value):
    stream.write(json.dumps(value,separators=(',',':')).encode()+b'\n');stream.flush()


class HandoffError(RuntimeError):pass


class Broker:
    def __init__(self,listener,process):
        self.listener=listener;self.listener.setblocking(False);self.process=process;self.messages=Messages(process.stdout);self.sequence=0
        ready=self.messages.read(20)
        if ready!={'ready':True,'pid':process.pid}:raise RuntimeError('Windows socket worker did not establish its owned control channel')

    def accept(self,timeout=.1):
        if not select.select([self.listener],[],[],timeout)[0]:return
        try:connection,_=self.listener.accept()
        except BlockingIOError:return
        except OSError as error:
            if error.winerror==10054:return
            raise
        try:
            self.sequence+=1
            send(self.process.stdin,{'id':self.sequence,'socket':connection.share(self.process.pid).hex()})
            if self.messages.read(5)!={'accepted':self.sequence}:raise HandoffError('Windows socket worker did not acknowledge ownership')
        except (OSError,EOFError) as error:raise HandoffError('Windows socket worker lost its owned control channel') from error
        finally:connection.close()


class Receiver:
    def __init__(self,gateway,stopped):
        self.gateway=gateway;self.stopped=stopped;self.loop=asyncio.get_running_loop();self.finished=threading.Event();self.tasks=set()
        self.messages=Messages(sys.stdin.buffer)
        self.thread=threading.Thread(target=self.receive,name='owned-windows-sockets',daemon=True)

    def start(self):
        self.thread.start();send(sys.stdout.buffer,{'ready':True,'pid':os.getpid()})

    def attach(self,connection):
        task=asyncio.create_task(self.gateway.accept_socket(connection));self.tasks.add(task)
        def complete(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:self.stopped.set()
        task.add_done_callback(complete)

    def receive(self):
        try:
            while not self.finished.is_set():
                message=self.messages.read(.1)
                if message is None:continue
                connection=socket.fromshare(bytes.fromhex(message['socket']))
                # Acknowledge only after the target owns an independent handle.
                send(sys.stdout.buffer,{'accepted':message['id']})
                self.loop.call_soon_threadsafe(self.attach,connection)
        except (EOFError,OSError,ValueError,KeyError):self.loop.call_soon_threadsafe(self.stopped.set)

    async def close(self):
        self.finished.set();await asyncio.to_thread(self.thread.join,1)
        if self.thread.is_alive():raise RuntimeError('Windows socket control reader did not exit')
        if self.tasks:await asyncio.gather(*self.tasks,return_exceptions=True)
