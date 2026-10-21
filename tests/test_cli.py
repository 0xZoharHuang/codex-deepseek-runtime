"""Keep native argument parsing and shared-daemon eligibility intact."""

from codex_deepseek.cli import native_arguments


def test_exec_with_root_options_and_config_keeps_owning_scope():
    arguments=['-C','exec','-c','model_reasoning_effort="low"','exec','--json','fix it']
    actual,interactive=native_arguments(arguments,1234)
    assert actual==arguments
    assert not interactive


def test_prompt_text_and_double_dash_are_never_reinterpreted_as_commands():
    arguments=['--','exec']
    actual,interactive=native_arguments(arguments,1234)
    assert actual[-2:]==arguments
    assert interactive


def test_appserver_stdout_remains_a_protocol_surface():
    actual,interactive=native_arguments(['app-server','--strict-config'],1234)
    assert actual[0]=='app-server' and actual[-1]=='--strict-config'
    assert not interactive


def test_approval_option_value_does_not_hide_exec_scope():
    actual,interactive=native_arguments(['-a','never','exec','--json','inspect'],1234)
    assert actual==['-a','never','exec','--json','inspect'] and not interactive


def test_native_environment_removes_app_ipc_but_preserves_public_ca_and_patch_flags(tmp_path,monkeypatch):
    import os,subprocess,sys
    from pathlib import Path
    from codex_deepseek.cli import native_environment
    monkeypatch.setenv('CODEX_APP_TOOLS_PIPE_PATH','/app/private/socket')
    monkeypatch.setenv('CODEX_SESSION_ID','app-session')
    monkeypatch.setenv('CODEX_CA_CERTIFICATE','/public/ca.pem')
    monkeypatch.setenv('CODEX_APPLY_PATCH_PRESERVE_LINE_ENDINGS','1')
    monkeypatch.setenv('OPENAI_API_KEY','app-secret')
    system_path=[str(Path(os.environ['SystemRoot'])/'System32'),os.environ['SystemRoot']] if os.name=='nt' else ['/usr/bin','/bin']
    monkeypatch.setenv('PATH',os.pathsep.join([str(tmp_path/'Applications/Codex.app/Contents/bin'),*system_path]))
    home=tmp_path/'own-profile';binary=tmp_path/'package/bin/codex';binary.parent.mkdir(parents=True)
    monkeypatch.setattr('codex_deepseek.cli.adapter_command',lambda:[sys._base_executable if os.name=='nt' else sys.executable,'-I','-X','utf8','-c',"import sys;sys.stdout.write('OWN_COMMAND')"])
    if os.name=='nt':
        from codex_deepseek.windows_launchers import profile_launchers
        package=tmp_path/'selected-package/codex_deepseek';package.mkdir(parents=True)
        (package/'__init__.py').write_text('',encoding='utf-8')
        (package/'cli.py').write_text("import sys;sys.stdout.write('OWN_COMMAND')",encoding='utf-8')
        monkeypatch.setattr('codex_deepseek.windows_launchers.profile_launchers',lambda home:profile_launchers(home,package_root=package.parent))
    environment=native_environment(home,'own-token',binary)
    assert environment['CODEX_HOME']==str(home) and environment['CODEX_CA_CERTIFICATE']=='/public/ca.pem'
    assert environment['CODEX_APPLY_PATCH_PRESERVE_LINE_ENDINGS']=='1'
    assert not {'CODEX_APP_TOOLS_PIPE_PATH','CODEX_SESSION_ID','OPENAI_API_KEY'}&environment.keys()
    assert 'Codex.app' not in environment['PATH']
    import shutil
    command=[shutil.which('codex',path=environment['PATH']),'--version']
    assert subprocess.run(command,env=environment,capture_output=True,text=True).stdout=='OWN_COMMAND'
    untouched=tmp_path/'does-not-exist'
    native_environment(untouched,'',binary,install_shims=False)
    assert not untouched.exists()


def test_native_diagnostic_and_terminal_controls_survive_app_isolation(tmp_path,monkeypatch):
    from codex_deepseek.cli import native_environment
    values={'CODEX_APPLY_GIT_CFG':'core.autocrlf=false','CODEX_TUI_RECORD_SESSION':'1',
            'CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT':'1','CODEX_TUI_ROUNDED':'0',
            'CODEX_TUI_SESSION_LOG_PATH':str(tmp_path/'session.jsonl'),
            'CODEX_ROLLOUT_TRACE_ROOT':str(tmp_path/'traces'),
            'CODEX_ANALYTICS_EVENTS_CAPTURE_FILE':str(tmp_path/'analytics.jsonl')}
    for name,value in values.items():monkeypatch.setenv(name,value)
    monkeypatch.setenv('CODEX_APP_TOOLS_PIPE_PATH','/app/private/socket')
    monkeypatch.setenv('OPENROUTER_API_KEY','never-forward-this-key')
    result=native_environment(tmp_path/'profile','local-token',tmp_path/'bin/codex',install_shims=False)
    assert all(result.get(name)==value for name,value in values.items())
    assert 'CODEX_APP_TOOLS_PIPE_PATH' not in result and 'OPENROUTER_API_KEY' not in result


def test_native_diagnostic_paths_cannot_write_into_app_state(tmp_path,monkeypatch):
    import pytest
    from pathlib import Path
    from codex_deepseek.cli import native_environment,NATIVE_OUTPUT_PATHS
    from codex_deepseek.protocol_errors import ProtocolError
    for name in NATIVE_OUTPUT_PATHS:
        with monkeypatch.context() as selected:
            selected.setenv(name,str(Path.home()/'.codex/must-not-be-written'))
            with pytest.raises(ProtocolError,match='Codex App'):
                native_environment(tmp_path/'profile','local-token',tmp_path/'bin/codex',install_shims=False)


def test_private_helper_imports_ignore_project_pythonpath(tmp_path,monkeypatch):
    import subprocess,os
    from codex_deepseek.bootstrap import module_command,module_environment
    shadow=tmp_path/'codex_deepseek';shadow.mkdir()
    (shadow/'__init__.py').write_text('raise RuntimeError("PROJECT_IMPORT_INJECTION")')
    monkeypatch.setenv('PYTHONPATH',str(tmp_path))
    # This legitimate helper validates argv before any state or network writes.
    result=subprocess.run(module_command('codex_deepseek.supervisor'),cwd=tmp_path,env=module_environment(),capture_output=True,text=True)
    assert result.returncode!=0 and 'PROJECT_IMPORT_INJECTION' not in result.stderr
    assert 'supervisor.py' in result.stderr and 'IndexError' in result.stderr


def test_native_transport_settings_remain_configurable_without_changing_provider(tmp_path):
    import pytest
    from codex_deepseek.policy import bindings,check_cli
    from codex_deepseek.protocol import ProtocolError
    expected=bindings({'base_url':'http://127.0.0.1:1/v1'},tmp_path/'catalog')
    check_cli(['exec','-c','model_providers.deepseek_runtime.stream_idle_timeout_ms=900000','task'],expected)
    with pytest.raises(ProtocolError):check_cli(['exec','-c','model_providers.deepseek_runtime.base_url="https://elsewhere.invalid"','task'],expected)


def test_native_management_flags_cannot_hide_update_or_proxy_scope(tmp_path):
    import pytest
    from codex_deepseek.arguments import app_server_command
    from codex_deepseek.isolation import check_attachment
    from codex_deepseek.protocol_errors import ProtocolError
    assert app_server_command(['-C','app-server','app-server','-c','unrelated=true','daemon','-c','quiet=true','stop'])==['daemon','stop']
    for arguments in [['app-server','-c','unrelated=true','daemon','update'],['app-server','-c','unrelated=true','proxy','--sock','/tmp/unowned.sock']]:
        with pytest.raises(ProtocolError):check_attachment(arguments,tmp_path/'own-home')


def test_native_unix_remote_literal_and_default_syntax(tmp_path,monkeypatch):
    import pytest
    from codex_deepseek.isolation import check_attachment
    from codex_deepseek.protocol_errors import ProtocolError
    home=tmp_path/'profile';home.mkdir();monkeypatch.chdir(tmp_path)
    assert check_attachment(['--remote','unix://'],home)=='unix://'
    assert check_attachment(['--remote','unix://profile/native.sock'],home)=='unix://profile/native.sock'
    assert check_attachment(['--remote','unix://'+str(home/'native.sock')],home)=='unix://'+str(home/'native.sock')
    with pytest.raises(ProtocolError):check_attachment(['--remote','unix://unowned.sock'],home)


def test_explicit_local_model_selection_is_rejected_before_launch():
    import pytest
    from codex_deepseek.policy import check_cli
    from codex_deepseek.protocol_errors import ProtocolError
    for arguments in [['--oss','exec','task'],['--local-provider','ollama','exec','task'],['exec','--local-provider=lmstudio','task']]:
        with pytest.raises(ProtocolError,match='pins DeepSeek'):check_cli(arguments,{})
    check_cli(['--','--local-provider'],{})


def test_isolated_unix_listener_and_default_socket_cannot_alias_another_home(tmp_path):
    import pytest
    from codex_deepseek.isolation import check_attachment,check_home_ipc
    from codex_deepseek.protocol_errors import ProtocolError
    home=tmp_path/'own';home.mkdir();other=tmp_path/'other';other.mkdir()
    with pytest.raises(ProtocolError):check_attachment(['app-server','--listen','unix://'+str(other/'server.sock')],home)
    (home/'app-server-control').symlink_to(other,target_is_directory=True)
    with pytest.raises(ProtocolError):check_home_ipc(home)
    with pytest.raises(ProtocolError):check_attachment(['app-server','proxy'],home)
    with pytest.raises(ProtocolError):check_attachment(['--remote','unix://'],home)


def test_default_tui_and_resume_do_not_force_embedded_server():
    for arguments in [[], ['resume', '--last'], ['fork', '--last']]:
        actual, interactive = native_arguments(arguments,1234)
        assert actual == arguments and interactive
        assert '-c' not in actual


def test_native_hashed_rendezvous_keeps_exact_home_identity(tmp_path,monkeypatch):
    import hashlib,os,pytest
    from codex_deepseek.isolation import owned_unix_path,check_home_ipc,check_attachment
    from codex_deepseek.protocol_errors import ProtocolError
    directory=tmp_path/'socket-root';directory.mkdir(mode=0o700)
    monkeypatch.setattr('codex_deepseek.isolation.native_socket_directory',lambda:directory)
    home=tmp_path/'own';socket=home/'app-server-control/app-server-control.sock';socket.parent.mkdir(parents=True)
    target=directory/hashlib.sha256(os.fsencode(socket)).hexdigest()
    socket.symlink_to(target)
    if os.name=='nt':
        # The native Unix UID rendezvous exception is never a Windows alias.
        assert not owned_unix_path(socket,home)
        with pytest.raises(ProtocolError):check_home_ipc(home)
        return
    assert owned_unix_path(socket,home)
    check_home_ipc(home)
    check_attachment(['app-server','proxy'],home)
    check_attachment(['--remote','unix://'+str(socket)],home)
    directory.chmod(0o755)
    assert not owned_unix_path(socket,home)
    directory.chmod(0o700)
    socket.unlink();socket.symlink_to(directory/('0'*64))
    assert not owned_unix_path(socket,home)
    with pytest.raises(ProtocolError):check_home_ipc(home)


def test_unix_relays_reject_foreign_and_direct_sockets_but_allow_stale_native_alias(tmp_path,monkeypatch):
    import hashlib,os,socket,pytest
    from codex_deepseek.isolation import check_attachment,check_home_ipc,owned_unix_path
    from codex_deepseek.protocol_errors import ProtocolError
    home=tmp_path/'own';home.mkdir();directory=tmp_path/'missing-native-root'
    monkeypatch.setattr('codex_deepseek.isolation.native_socket_directory',lambda:directory)
    for arguments in [['stdio-to-uds',str(tmp_path/'foreign.sock')],['stdio-to-uds','--',str(tmp_path/'foreign.sock')],['stdio-to-uds','-c','x=true',str(tmp_path/'foreign.sock')]]:
        with pytest.raises(ProtocolError):check_attachment(arguments,home)
    logical=home/'app-server-control/app-server-control.sock';logical.parent.mkdir()
    expected=directory/hashlib.sha256(os.fsencode(logical)).hexdigest()
    logical.symlink_to(expected)
    if os.name=='nt':
        assert not owned_unix_path(logical,home)
        with pytest.raises(ProtocolError):check_attachment(['stdio-to-uds',str(logical)],home)
    else:
        assert owned_unix_path(logical,home)
        check_home_ipc(home)
        check_attachment(['stdio-to-uds',str(logical)],home)
    logical.unlink()
    import tempfile
    from pathlib import Path
    # macOS sockaddr_un cannot represent pytest's long generated test path.
    with tempfile.TemporaryDirectory(prefix='cds-',dir=None if os.name=='nt' else '/tmp') as short:
        direct_home=Path(short)/'own'
        direct=direct_home/'app-server-control/app-server-control.sock';direct.parent.mkdir(parents=True)
        if os.name=='nt':
            # codex-uds uses a regular rendezvous path on Windows, not the
            # cfg(unix) UID directory or Python's unavailable AF_UNIX.
            direct.write_bytes(b'fixture native Windows rendezvous')
            assert owned_unix_path(direct,direct_home)
            check_home_ipc(direct_home)
            check_attachment(['app-server','proxy'],direct_home)
            return
        with socket.socket(socket.AF_UNIX) as receiver:
            receiver.bind(str(direct))
            assert not owned_unix_path(direct,direct_home)
            with pytest.raises(ProtocolError):check_home_ipc(direct_home)
            with pytest.raises(ProtocolError):check_attachment(['app-server','proxy'],direct_home)
