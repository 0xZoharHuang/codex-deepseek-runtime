"""Own the native process independently of the provider wrapper's lifetime."""

import asyncio
import os
import signal
import sys

from .state import Store
from .isolation import listener, register_listener
from .platforms import WINDOWS, install_signals
from .processes import matches


async def supervise(control_fd, state_path, owner, command):
    # Native policy protects every transport. Inherit native stdio directly;
    # a Python JSONL relay would impose an extra size limit and alter backpressure.
    process = await asyncio.create_subprocess_exec(*command)
    Store.bind_native(state_path,owner,os.getpid(),process.pid)
    loop = asyncio.get_running_loop()
    lost = asyncio.Event()
    parent_lost = False

    def check_owner():
        nonlocal parent_lost
        if not os.read(control_fd, 1):
            parent_lost = True
            lost.set()
            loop.remove_reader(control_fd)

    def forward(signum):
        if process.returncode is None:
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                pass  # The owned child completed between the liveness check and signal.

    async def watch_parent():
        nonlocal parent_lost
        _,pid,birth=control_fd.split(':',2)
        while not lost.is_set():
            if not matches(int(pid),birth):parent_lost=True;lost.set();return
            await asyncio.sleep(.1)
    if WINDOWS:watcher=asyncio.create_task(watch_parent())
    else:loop.add_reader(control_fd,check_owner);watcher=None
    handlers={signal.SIGTERM:lost.set,signal.SIGINT:(lambda:None) if WINDOWS or sys.stdin.isatty() else lambda:forward(signal.SIGINT)}
    if WINDOWS:handlers[signal.SIGBREAK]=lambda:None
    restore_signals=install_signals(loop,handlers)
    completed = asyncio.create_task(process.wait())
    stopped = asyncio.create_task(lost.wait())
    endpoint=listener(command[1:])
    registered=asyncio.create_task(register_listener(os.environ['CODEX_HOME'],endpoint,process)) if endpoint else None
    if registered:
        def registration_finished(task):
            if not task.cancelled() and task.exception() is not None:
                print('codex-deepseek: '+str(task.exception()),file=sys.stderr);lost.set()
        registered.add_done_callback(registration_finished)
    try:
        done, _ = await asyncio.wait([completed, stopped], return_when=asyncio.FIRST_COMPLETED)
        if stopped in done and process.returncode is None:
            forward(signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.shield(completed), 5)
            except TimeoutError:
                process.kill()
        result = await completed
        if parent_lost:
            Store.owner_lost(state_path, owner)
        return result if result >= 0 else 128 - result
    finally:
        if registered:
            registered.cancel();await asyncio.gather(registered,return_exceptions=True)
        stopped.cancel()
        await asyncio.gather(stopped, return_exceptions=True)
        restore_signals()
        if watcher:
            watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
        else:
            loop.remove_reader(control_fd);os.close(control_fd)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(supervise(sys.argv[1] if WINDOWS else int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4:])))
