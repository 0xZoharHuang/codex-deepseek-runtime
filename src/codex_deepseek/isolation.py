"""Keep native remote attachment within explicitly owned local server lifetimes."""
import asyncio
import fcntl
import hashlib
import ipaddress
import json
import os
import stat
from pathlib import Path
import socket
import subprocess
from urllib.parse import urlsplit

from .arguments import command_index, APP_VALUE_OPTIONS, app_server_command
from .processes import identity, matches
from .protocol_errors import ProtocolError


def option(arguments, name):
    for index, token in enumerate(arguments):
        if token == '--':break
        if token == name and index+1<len(arguments):return arguments[index+1]
        if token.startswith(name+'='):return token.split('=',1)[1]
    return None


def listener(arguments):
    owner=command_index(arguments)
    if owner is None or arguments[owner]!='app-server':return None
    rest=arguments[owner+1:]
    if command_index(rest,values=APP_VALUE_OPTIONS) is not None:return None
    value=option(rest,'--listen')
    return value if value and value.startswith(('ws://','wss://')) else None


def registry(home):
    path=Path(home)/'provider-state/listeners.json'
    try:
        value=json.loads(path.read_text())
        if not isinstance(value,dict):raise ValueError('invalid listener registry')
        return value
    except FileNotFoundError:return {}
    except (OSError,ValueError) as error:raise ProtocolError('Owned listener registry is unreadable or invalid') from error


def addresses(host):
    return {ipaddress.ip_address(entry[4][0].split('%',1)[0]) for entry in socket.getaddrinfo(host,None)}


def local_addresses():
    local=set()
    try:local.update(addresses(socket.gethostname()))
    except OSError:pass
    try:
        import re
        result=subprocess.run(['/sbin/ifconfig','-a'],capture_output=True,text=True,timeout=3)
        for value in re.findall(r'\binet6?\s+([^\s%]+)',result.stdout):
            local.add(ipaddress.ip_address(value))
    except (OSError,ValueError,subprocess.TimeoutExpired):pass
    return local


def normalized(address):
    return address.ipv4_mapped if isinstance(address,ipaddress.IPv6Address) and address.ipv4_mapped else address


def save_listener(home,endpoint,pid,birth):
    from .service import atomic_json
    state=Path(home)/'provider-state'
    with (state/'listeners.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        values={key:value for key,value in registry(home).items()
                if isinstance(value,dict) and matches(value.get('pid'),value.get('birth'))}
        values[endpoint]={'pid':pid,'birth':birth}
        atomic_json(state/'listeners.json',values)


async def register_listener(home,endpoint,process):
    port=urlsplit(endpoint).port
    if port is None:raise ProtocolError('An isolated native WebSocket listener requires an explicit port')
    birth=identity(process.pid)
    for _ in range(100):
        if process.returncode is not None:return
        # Kernel ownership prevents an occupied App port from being registered
        # merely because a new native process was asked to bind that address.
        result=await asyncio.to_thread(subprocess.run,
            ['/usr/sbin/lsof','-nP','-a','-p',str(process.pid),('-iTCP:'+str(port) if port else '-iTCP'),'-sTCP:LISTEN','-Fn'],
            capture_output=True,text=True,timeout=3)
        if result.returncode==0 and any(line.startswith('n') for line in result.stdout.splitlines()):
            if port == 0:
                # Native SocketAddr accepts port zero. Register the actual
                # kernel-assigned endpoint, never the unusable requested zero.
                names=[line[1:] for line in result.stdout.splitlines() if line.startswith('n')]
                host=urlsplit(endpoint).hostname
                candidates=[name for name in names if name.rsplit(':',1)[0].strip('[]')==host]
                if len(candidates)!=1:
                    await asyncio.sleep(.2);continue
                endpoint='ws://'+candidates[0]
            save_listener(home,endpoint,process.pid,birth);return
        await asyncio.sleep(.2)
    raise ProtocolError('Native listener did not publish a kernel-owned socket before the startup deadline')


def check_attachment(arguments,home):
    owner=command_index(arguments)
    if owner is not None and arguments[owner]=='update':
        raise ProtocolError('Upgrade this dedicated distribution with its verified installer; native update would replace the provider policy fork')
    if owner is not None and arguments[owner]=='stdio-to-uds':
        # This hidden native relay is another attachment surface. Honor native
        # global options and -- without treating their values as a socket path.
        rest=arguments[owner+1:];position=command_index(rest)
        if '--' in rest:
            boundary=rest.index('--')
            if position is None and boundary+1<len(rest):position=boundary+1
        if position is not None and not owned_unix_path(rest[position],home):
            raise ProtocolError('An isolated stdio-to-uds socket must belong to this runtime home')
    if owner is not None and arguments[owner]=='app-server':
        rest=app_server_command(arguments)
        if rest[:2]==['daemon','update'] and '--from-cli' not in rest:
            raise ProtocolError('Use app-server daemon update --from-cli to retain this dedicated runtime package')
        if rest[:2]==['daemon','pid-update-loop']:
            raise ProtocolError('The official production updater cannot replace this dedicated distribution')
        path=(option(rest,'--sock') or str(Path(home)/'app-server-control/app-server-control.sock')) if rest[:1]==['proxy'] else None
        if path and not owned_unix_path(path,home):
            raise ProtocolError('An explicit proxy socket must belong to this isolated runtime home')
    listen=option(arguments,'--listen') if owner is not None and arguments[owner]=='app-server' else None
    if listen and listen.startswith('unix://'):
        path=listen.removeprefix('unix://')
        if path and not owned_unix_path(path,home):
            raise ProtocolError('An isolated Unix listener socket must belong to this runtime home')
    remote=option(arguments,'--remote')
    if not remote:return
    address=urlsplit(remote)
    if address.scheme=='unix':
        # Native syntax is a literal suffix, not URL authority/path parsing.
        # unix:// selects this CODEX_HOME's own daemon socket.
        socket_path=remote.removeprefix('unix://')
        if not socket_path:
            check_home_ipc(home)
            return remote
        if not owned_unix_path(socket_path,home):
            raise ProtocolError('A local remote socket must belong to this isolated runtime home')
        return remote
    if address.scheme not in {'ws','wss'}:return  # Native clap reports unsupported schemes.
    try:
        local={normalized(value) for value in local_addresses()}
        targets={normalized(value) for value in addresses(address.hostname)}
    except OSError as error:raise ProtocolError('Cannot resolve explicit remote endpoint') from error
    if not any(value in local or value.is_loopback or value.is_unspecified for value in targets):return
    for endpoint,value in registry(home).items():
        if not isinstance(value,dict) or not matches(value.get('pid'),value.get('birth')):continue
        owned=urlsplit(endpoint)
        if owned.scheme!=address.scheme or owned.port!=address.port:continue
        try:bound={normalized(value) for value in addresses(owned.hostname)}
        except OSError:continue
        matching=targets&bound or {target for value in bound for target in targets if value.is_unspecified and value.version==target.version}
        if matching:
            # Resolve local aliases to the verified listener's numeric address.
            # Otherwise localhost could try an unrelated IPv6 listener before
            # falling back to the owned IPv4 socket on the same port.
            if address.scheme=='ws':
                selected=sorted(matching,key=str)[0]
                host='['+str(selected)+']' if selected.version==6 else str(selected)
                auth=address.netloc.rsplit('@',1)[0]+'@' if '@' in address.netloc else ''
                return address._replace(netloc=auth+host+':'+str(address.port)).geturl()
            if endpoint==remote:return remote
    raise ProtocolError('Local WebSocket attachment requires a live listener launched by codex-deepseek in this home; refusing an unowned Codex App endpoint')


def bind_remote(arguments,endpoint):
    if not endpoint:return arguments
    result=list(arguments)
    for index,token in enumerate(result):
        if token=='--':break
        if token=='--remote' and index+1<len(result):result[index+1]=endpoint;break
        if token.startswith('--remote='):result[index]='--remote='+endpoint;break
    return result


def native_socket_directory():
    return Path('/tmp').resolve()/('codex-daemon-'+str(os.geteuid()))


def owned_unix_path(path,home):
    """Accept native rendezvous sockets using the frozen upstream path contract."""
    home=Path(home).resolve();path=Path(path).expanduser()
    logical=path.parent.resolve()/path.name
    if not logical.parent.is_relative_to(home):return False
    resolved=path.resolve()
    if resolved.is_relative_to(home):
        # Native listeners advertise a deterministic symlink. A direct socket
        # here may be an SSH/socat forwarder, not this runtime's listener.
        try:return not stat.S_ISSOCK(resolved.stat().st_mode)
        except FileNotFoundError:return True
    # Upstream always binds a SHA256 path under a fixed UID-owned /tmp root.
    # Its advertised home socket is a symlink, including on short home paths.
    # Accept only this exact mapping, never another home's/App's socket hash.
    directory=native_socket_directory()
    expected=directory/hashlib.sha256(os.fsencode(logical)).hexdigest()
    if resolved!=expected or not path.is_symlink():return False
    try:
        try:info=directory.lstat()
        except FileNotFoundError:return True # exact stale alias; native recreates its root
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:return False
        if expected.exists():
            info=expected.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.geteuid():return False
        return True
    except OSError:return False


def check_home_ipc(home):
    """Refuse foreign state aliases, retaining native hashed socket rendezvous."""
    home=Path(home).resolve()
    for relative in ['app-server-control','app-server-daemon',
                     'app-server-daemon/daemon.pid','app-server-daemon/app-server.pid']:
        if not (home/relative).resolve().is_relative_to(home):
            raise ProtocolError('The isolated home IPC state must not resolve into another runtime home')
    if not owned_unix_path(home/'app-server-control/app-server-control.sock',home):
        raise ProtocolError('The isolated home control socket does not belong to this runtime home')


def check_daemon_package(arguments,home,binary):
    owner=command_index(arguments)
    if owner is None or arguments[owner]!='app-server':return
    rest=app_server_command(arguments)
    if rest[:2] in (['daemon','stop'],['daemon','version'],['daemon','update']):return
    if rest[:1] not in (['daemon'],['proxy']):return
    current=Path(home)/'packages/app-server-daemon/current/bin/codex'
    if not current.is_file():return
    def digest(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').digest()
    if digest(current)!=digest(Path(binary)):
        raise ProtocolError('This home has a different native daemon build. Stop it, then run app-server daemon update --from-cli --yes before attaching or starting it')
