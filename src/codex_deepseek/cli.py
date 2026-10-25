"""Run one authenticated adapter for exactly one native Codex process lifetime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shlex
import signal
import subprocess
import sys
import tomllib
import tempfile

import aiohttp

from .gateway import Gateway, Store
from .state import migrate_searches
from .protocol import MODEL, SERIAL_PROVIDER, STRUCTURED_PROVIDER, PEER_HOSTED_PROVIDER
from .policy import bindings, check_cli, isolated_path
from .bootstrap import module_command, module_environment
from .service import ProviderService
from .arguments import command_index, app_server_command
from .native_http import NativeTransport
from .isolation import check_attachment, check_daemon_package, bind_remote, check_home_ipc
from .platforms import WINDOWS, EXE, executable, console_entry, private_fd, private_directory, install_signals, replace_file
from .credentials import SERVICE, credential, setup_key
from .processes import identity

NATIVE_VERSION = "0.164.0-alpha.7"
NATIVE_ENVIRONMENT = {
    'CODEX_CA_CERTIFICATE','CODEX_APPLY_PATCH_PRESERVE_LINE_ENDINGS','CODEX_APPLY_GIT_CFG',
    'CODEX_TUI_RECORD_SESSION','CODEX_TUI_SESSION_LOG_PATH','CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT',
    'CODEX_TUI_ROUNDED','CODEX_ROLLOUT_TRACE_ROOT','CODEX_ANALYTICS_EVENTS_CAPTURE_FILE',
}
NATIVE_OUTPUT_PATHS = {'CODEX_TUI_SESSION_LOG_PATH','CODEX_ROLLOUT_TRACE_ROOT','CODEX_ANALYTICS_EVENTS_CAPTURE_FILE'}


def data_root() -> Path:
    release=Path(sys.prefix).parent
    if release.parent.name=='releases' and (release/'installation.json').is_file():return release.parent.parent
    return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'codex-deepseek' if WINDOWS else Path.home()/'.local/share/codex-deepseek'


def adapter_command():
    entry=console_entry(sys.prefix)
    return [str(entry)] if entry.is_file() else module_command('codex_deepseek.cli')


def native_environment(home,token,binary, *, install_shims=True, port=None):
    environment=dict(os.environ)
    for name in list(environment):
        if (name.startswith('CODEX_') and name not in NATIVE_ENVIRONMENT) or name in {'OPENROUTER_API_KEY','OPENAI_API_KEY','__PYVENV_LAUNCHER__'}:
            environment.pop(name,None)
    for name in NATIVE_OUTPUT_PATHS:
        if environment.get(name):environment[name]=str(isolated_path(Path(environment[name])))
    environment.update(CODEX_HOME=str(home),CODEX_DEEPSEEK_HOME=str(home),
                       CODEX_DEEPSEEK_POLICY_HOME=str(home),CODEX_DEEPSEEK_BRIDGE_TOKEN=token)
    if port is not None:environment['CODEX_DEEPSEEK_BRIDGE_PORT']=str(port)
    cli_path=home/'provider-state/cli-path'
    if install_shims and WINDOWS:
        from .windows_launchers import profile_launchers
        cli_path=profile_launchers(home)
    elif install_shims:
        cli_path.mkdir(parents=True,exist_ok=True,mode=0o700)
        content='#!/bin/sh\nexport CODEX_DEEPSEEK_HOME='+shlex.quote(str(home))+'\nexec '+shlex.join(adapter_command())+' "$@"\n'
        for name in ['codex','codex-deepseek']:
            path=cli_path/name
            if not path.exists() or path.read_text(encoding='utf-8')!=content:
                with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=cli_path,prefix='.entry-',delete=False) as output:
                    output.write(content);private_fd(output.fileno(),0o700);temporary=Path(output.name)
                replace_file(temporary,path)
    inherited=[part for part in environment.get('PATH','').split(os.pathsep) if part and not any(
        app in Path(part).resolve().as_posix() for app in ['/ChatGPT.app/Contents/','/Codex.app/Contents/'])]
    owned=[cli_path,binary.parent.parent/'codex-path',binary.parent]
    environment['PATH']=os.pathsep.join([str(path) for path in owned if path.is_dir()]+inherited)
    return environment


def offline_native(arguments):
    for token in arguments:
        if token=='--':break
        if token in {'--help','-h','--version','-V'}:return True
    return False




def native() -> Path:
    release=Path(sys.prefix).parent
    try:
        manifest=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
        installation=json.loads((release/'installation.json').read_text(encoding='utf-8'))
        package=Path(installation['native_package']).resolve()
        required={name+EXE for name in ['bin/codex','bin/codex-code-mode-host','codex-path/rg','bin/codex-http-transport']}|{'codex-package.json'}
        if WINDOWS:
            required|={name+EXE for name in ['bin/codex-windows-sandbox-setup','bin/codex-command-runner',
                'bin/codex-windows-managed-deny-probe','bin/codex-windows-sandbox-service',
                'codex-resources/codex-windows-sandbox-setup','codex-resources/codex-command-runner']}
        if sys.platform.startswith('linux'):required.add('codex-resources/bwrap')
        if (manifest.get('status')!='ready' or manifest.get('distribution')!='codex-deepseek-runtime'
            or manifest.get('upstream_version')!=NATIVE_VERSION or not required<=set(manifest['files'])
            or not package.is_relative_to((data_root()/'native').resolve())):
            raise ValueError('Invalid dedicated native package boundary')
        for name,digest in manifest['files'].items():
            relative=PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
                raise ValueError('Invalid native artifact path')
            # Installation verifies the entire archive, including legal notices.
            # Reopening thousands of non-executable notices at every helper
            # launch can exhaust native startup deadlines on slower storage.
            # Every executable, resource and runtime manifest still gets hashed.
            if relative.parts and relative.parts[0]=='licenses':continue
            path=package/name
            if not path.resolve().is_relative_to(package) or path.is_symlink():raise ValueError('Invalid native artifact path')
            with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=digest:raise ValueError('Native artifact checksum mismatch: '+name)
        packaged=json.loads((package/'codex-package.json').read_text(encoding='utf-8'))
        if packaged.get('patchSha256')!=manifest['patch_sha256']:
            raise ValueError('Native package does not match the dedicated policy patch')
        return executable(package/'bin','codex')
    except (OSError,ValueError,KeyError,TypeError) as error:
        raise RuntimeError('Verified dedicated native runtime is unavailable; rerun the installer') from error


def catalog_path() -> Path:
    release_catalog = Path(sys.prefix).parent / 'catalog.json'
    return release_catalog if release_catalog.is_relative_to(data_root()/'releases') and release_catalog.is_file() else data_root() / 'catalog.json'


def catalog() -> dict:
    path = catalog_path()
    if not path.is_file():
        raise RuntimeError("Pinned model catalog is missing; rerun the installer")
    return json.loads(path.read_text(encoding='utf-8'))


def overrides(port: int) -> list[str]:
    provider = ('{name="DeepSeek via OpenRouter",base_url="http://127.0.0.1:' + str(port) +
                '/v1",env_key="CODEX_DEEPSEEK_BRIDGE_TOKEN",wire_api="responses",supports_websockets=false,'
                'capabilities={remote_compaction="unsupported"}}')
    pairs = {
        "model_provider": json.dumps("deepseek_runtime"), "model": json.dumps(MODEL),
        "model_catalog_json": json.dumps(str(catalog_path())),
        "model_providers.deepseek_runtime": provider,
        "memories.extract_model": json.dumps(MODEL),
        "memories.consolidation_model": json.dumps(MODEL),
        "sqlite_home": json.dumps(str(Path(os.environ.get('CODEX_DEEPSEEK_HOME',str(data_root()/'codex-home'))).expanduser().resolve())),
        "log_dir": json.dumps(str(Path(os.environ.get('CODEX_DEEPSEEK_HOME',str(data_root()/'codex-home'))).expanduser().resolve()/'log')),
    }
    return [arg for key, value in pairs.items() for arg in ("-c", key + "=" + value)]


def native_arguments(arguments: list[str], port: int) -> tuple[list[str], bool]:
    """Classify the native entry without adding configuration CLI overrides.

    The opt-in native policy loader already binds the provider. Injecting -c here
    would make the native TUI reject its shared daemon and use embedded mode.
    """
    index = command_index(arguments)
    management = {"exec", "e", "app-server", "agents", "app", "login", "logout", "mcp", "plugin", "completion", "update", "doctor", "sandbox",
                  "debug", "execpolicy", "apply", "a", "queue", "archive", "delete", "unarchive", "features",
                  "migrate-rollouts", "cloud", "cloud-tasks", "responses-api-proxy", "stdio-to-uds", "exec-server", "remote-control"}
    return list(arguments), not (index is not None and arguments[index] in management)


def metadata_delay(headers,attempt):
    from datetime import datetime,timezone
    from email.utils import parsedate_to_datetime
    value=headers.get('Retry-After')
    if value:
        try:delay=float(value)
        except ValueError:
            try:delay=(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()
            except (ValueError,TypeError,OverflowError):delay=None
        import math
        if delay is not None and math.isfinite(delay) and delay>=0:return min(delay,90)
    return min(2**attempt,10)


async def generation_receipt(transport,key,response_id,policy,*,timeout=90):
    """Poll only idempotent billing metadata; never replay the model probe."""
    from urllib.parse import urlencode
    rows=[];generation={};started=asyncio.get_running_loop().time()
    try:
        async with asyncio.timeout(timeout):
            for attempt in range(12):
                async with transport.request('GET','https://openrouter.ai/api/v1/generation?'+urlencode({'id':response_id}),api_key=key,policy=policy) as response:
                    status=response.status
                    if status==200:
                        body=await response.json()
                        if not isinstance(body,dict) or not isinstance(body.get('data',{}),dict):raise ValueError('Invalid provider generation metadata envelope')
                        generation=body.get('data',{})
                    rows.append({'http_status':status,'provider':generation.get('provider_name'),
                                 'elapsed_ms':round((asyncio.get_running_loop().time()-started)*1000)})
                    if generation.get('provider_name'):break
                    if status not in {200,404,408,429} and status<500:break
                    delay=metadata_delay(response.headers,attempt)
                if attempt<11:await asyncio.sleep(delay)
    except TimeoutError:
        rows.append({'error':'metadata_deadline_exceeded','elapsed_ms':round((asyncio.get_running_loop().time()-started)*1000)})
    return generation,rows


async def doctor(key: str, gateway, store, home, *, native_binary: Path) -> int:
    """Probe the actual native runtime, then verify its billed provider receipt."""
    work=home/'provider-state/doctor-work'
    private_directory(work)
    flags={'mcp_servers':'{}','web_search':'"disabled"','model_reasoning_effort':'"low"',
           **{'features.'+name:'false' for name in ['apps','plugins','memories','multi_agent','multi_agent_v2','shell_tool','code_mode','code_mode_only']}}
    arguments=['-a','never','exec','--skip-git-repo-check','--ephemeral','--json','--sandbox','read-only','-C',str(work),*[part for name,value in flags.items() for part in ['-c',name+'='+value]],
               'Reply with exactly CONNECTED. Do not call tools or delegate.']
    process=await asyncio.create_subprocess_exec(*adapter_command(),*arguments,
        env={**os.environ,'CODEX_DEEPSEEK_HOME':str(home)},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        output,errors=await asyncio.wait_for(process.communicate(),120)
    finally:
        if process.returncode is None:
            process.terminate()
            try:await asyncio.wait_for(process.wait(),6)
            except TimeoutError:process.kill();await process.wait()
    events=[]
    for line in output.splitlines():
        try:events.append(json.loads(line))
        except ValueError:continue
    thread=next((event.get('thread_id') for event in events if event.get('type')=='thread.started'),None)
    answer=''.join(event.get('item',{}).get('text','') for event in events if event.get('type')=='item.completed' and event.get('item',{}).get('type')=='agent_message')
    row=store.db.execute("SELECT response_id,network_policy,provider,status FROM receipts WHERE context_key=? ORDER BY started DESC LIMIT 1",(thread,)).fetchone() if thread else None
    if (process.returncode or not answer.strip() or not row or row[3]!='completed'
            or not isinstance(row[0],str) or not row[0]):
        raise RuntimeError('Native connectivity probe failed; no successful model/provider receipt was established')
    response_id,policy,selected,_=row
    transport=NativeTransport('https://openrouter.ai/api/v1/responses',binary=executable(native_binary.parent,'codex-http-transport'))
    try:generation,verification=await generation_receipt(transport,key,response_id,policy or 'default')
    finally:await transport.close()
    provider=generation.get('provider_name')
    generation_id=generation.get('id');generation_model=generation.get('model')
    verified=(generation_id==response_id
              and generation_model in (MODEL,'deepseek/deepseek-v4.1-flash-20261017')
              and provider=='DeepSeek' and selected=='deepseek')
    result={'status':'connected' if verified else 'provider_verification_failed',
            'model':MODEL,'provider':provider,'fallbacks':False,'response_id':response_id,'native_version':NATIVE_VERSION,
            'generation_id':generation_id,'generation_model':generation_model,'selected_provider':selected,
            'native_thread':thread,'network_policy':policy,'text':answer,'echo_expected':'CONNECTED','echo_exact_match':answer.strip()=='CONNECTED',
            'provider_verification':verification,'structured_provider':STRUCTURED_PROVIDER,'serial_provider':SERIAL_PROVIDER}
    (data_root()/'last-doctor.json').write_text(json.dumps(result,indent=2), encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='connected' else 1


async def run(arguments: list[str]) -> int:
    if arguments[:2] == ['runtime', 'image-budget']:
        # Pure offline diagnostic: before home IPC, Store, native or provider startup.
        from .image_budget import image_budget_command
        return image_budget_command(arguments[2:])
    if arguments==['status']:arguments=['runtime','status']
    image_operations={'image-status':'status','image-cleanup':'cleanup','image-reconcile-uploads':'reconcile-uploads','image-reconcile-calls':'reconcile-calls'}
    is_image_command=len(arguments)>=2 and arguments[0]=='runtime' and arguments[1] in image_operations
    if is_image_command and arguments[2:] not in ([],['--all-unpinned']):raise RuntimeError('Unknown image maintenance arguments')
    if is_image_command and arguments[2:] and arguments[1]!='image-cleanup':raise RuntimeError('--all-unpinned is only for image-cleanup')
    if arguments[:1]==['runtime'] and not is_image_command and arguments[1:] not in (['status'],['doctor']):
        raise RuntimeError('Runtime management commands: runtime status, runtime doctor, runtime setup-key, runtime image-budget. Native doctor remains available as doctor.')
    root = data_root()
    runtime_home = isolated_path(Path(os.environ.get("CODEX_DEEPSEEK_HOME", str(root / "codex-home"))))
    if is_image_command:
        from .image_provider import maintenance_command,load_image_config
        state_root=runtime_home/'provider-state'
        if load_image_config(state_root) is None:
            print(json.dumps({'enabled':False,'status':'disabled','complete':False}));return 0 if arguments[1]=='image-status' else 1
        binary=native();transport=NativeTransport('https://openrouter.ai/api/v1/responses',binary=executable(binary.parent,'codex-http-transport'))
        try:
            result,code=await maintenance_command(state_root,transport,credential,image_operations[arguments[1]],all_unpinned='--all-unpinned' in arguments)
            print(json.dumps(result,ensure_ascii=False,indent=2));return code
        finally:await transport.close()
    if os.environ.get('CODEX_DEEPSEEK_TRACE_DIR'):
        isolated_path(Path(os.environ['CODEX_DEEPSEEK_TRACE_DIR']))
    if not offline_native(arguments):check_home_ipc(runtime_home)
    if offline_native(arguments) or app_server_command(arguments)[:2] in (['daemon','stop'],['daemon','version']):
        binary=native()
        process=await asyncio.create_subprocess_exec(str(binary),*arguments,env=native_environment(runtime_home,'',binary,install_shims=False))
        return await process.wait()
    # Match the installer's inheriting Windows container; private data is below runtime_home.
    root.mkdir(parents=True, exist_ok=True, mode=0o777 if WINDOWS else 0o700)
    private_directory(runtime_home)
    from .web_config import ensure_browser
    ensure_browser(runtime_home)
    arguments=bind_remote(arguments,check_attachment(arguments,runtime_home))
    # This invocation uses one immutable native package. Verify it once here;
    # every subsequent run (including the doctor's child) verifies independently.
    binary=native()
    check_daemon_package(arguments,runtime_home,binary)
    store = Store(runtime_home / 'provider-state' / 'adapter.sqlite')
    try:
        migrate_searches(root / 'adapter.sqlite', runtime_home, store)
        async with ProviderService(runtime_home,catalog_path(),native_binary=binary,owner=store.owner) as gateway:
            values = dict(arg.split('=',1) for arg in overrides(gateway.port)[1::2])
            expected = bindings(tomllib.loads('value='+values['model_providers.deepseek_runtime'])['value'],catalog_path(),runtime_home)
            check_cli(arguments,expected)
            policy_home=runtime_home/'runtime-policy';private_directory(policy_home)
            policy=policy_home/'config.toml'
            content='\n'.join(key+'='+value for key,value in values.items() if key in expected)+'\n'
            # Windows replaces even identical generated content so an older BA/OW-only
            # file inherits the same-user directory ACL during a normal elevated upgrade.
            if WINDOWS or not policy.exists() or policy.read_text(encoding='utf-8')!=content:
                temporary=None
                try:
                    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=policy_home,prefix='.config-',delete=False) as output:
                        temporary=Path(output.name);private_fd(output.fileno())
                        output.write(content);output.flush();os.fsync(output.fileno())
                    replace_file(temporary,policy)
                finally:
                    if temporary is not None:temporary.unlink(missing_ok=True)
            if arguments == ["runtime","doctor"]:
                return await doctor(credential(), gateway, store, runtime_home,native_binary=binary)
            if arguments == ["runtime","status"]:
                print(json.dumps({"native": str(binary), "version": NATIVE_VERSION, "model": MODEL,
                                  "provider": "deepseek", "structured_provider": STRUCTURED_PROVIDER,
                                  "serial_provider": SERIAL_PROVIDER,
                                  "peer_hosted_provider": PEER_HOSTED_PROVIDER,
                                  "fallbacks": False, "state": str(store.path), "runtime_home":str(runtime_home),
                                  "release_environment":sys.prefix,"model_catalog":str(catalog_path())}, indent=2))
                return 0
            environment=native_environment(runtime_home,gateway.token,binary,port=gateway.port)
            argv, interactive = native_arguments(arguments, gateway.port)
            read_fd, owner_fd = (None,None) if WINDOWS else os.pipe()
            control='process:'+str(os.getpid())+':'+identity(os.getpid()) if WINDOWS else str(read_fd)
            try:
                process = await asyncio.create_subprocess_exec(
                    *module_command('codex_deepseek.supervisor',control,store.path,store.owner,str(binary),*argv),
                    env=module_environment(environment), **({} if WINDOWS else {'pass_fds':(read_fd,)}))
            except BaseException:
                if owner_fd is not None:os.close(owner_fd)
                raise
            finally:
                if read_fd is not None:os.close(read_fd)
            loop = asyncio.get_running_loop()
            def forward(signum):
                if process.returncode is None:
                    process.send_signal(signum)
            # Windows console events reach the native child even with piped stdin.
            # Translating Ctrl-C into Ctrl-Break changes native cancellation semantics.
            handlers={signal.SIGINT:(lambda: None) if WINDOWS or sys.stdin.isatty() else lambda:forward(signal.SIGINT),signal.SIGTERM:lambda:forward(signal.SIGTERM)}
            if WINDOWS:handlers[signal.SIGBREAK]=lambda:None
            restore_signals=install_signals(loop,handlers)
            try:
                code = await process.wait()
                if interactive and "--help" not in arguments and "--version" not in arguments:
                    prefix = ""
                    if os.environ.get("CODEX_DEEPSEEK_HOME"):
                        prefix = "CODEX_DEEPSEEK_HOME=" + shlex.quote(str(runtime_home)) + " "
                    print("\nDeepSeek 独立环境的恢复命令：" + prefix + "codex-deepseek resume --last\n"
                          "原生提示中的 codex 命令应通过 codex-deepseek 执行，以保持与 App 隔离。", file=sys.stderr)
                return code
            finally:
                if owner_fd is not None:os.close(owner_fd)
                restore_signals()
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                await asyncio.to_thread(store.cleanup_native)
    finally:
        store.close()


def main() -> None:
    arguments = sys.argv[1:]
    try:
        if arguments in (["setup-key"],["runtime","setup-key"]):
            raise SystemExit(setup_key())
        raise SystemExit(asyncio.run(run(arguments)))
    except (RuntimeError, OSError, ValueError) as exc:
        print("codex-deepseek: " + (str(exc) or type(exc).__name__), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
