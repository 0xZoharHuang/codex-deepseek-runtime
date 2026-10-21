"""QA mode selection is explicit and never implies active OS enforcement."""
import ast
import json
from pathlib import Path
import sys
import tomllib

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
import verify_live as live
from run_acceptance import SANDBOX_OPTION_VERIFIERS,sandbox_gate_commands
from verify_profile_audit import profile_sandbox_mode


@pytest.mark.parametrize('value',['', 'read-only','full-access','disabled','workspaceWrite','danger-full-access --approve'])
def test_invalid_controller_modes_are_rejected_before_launch(tmp_path,monkeypatch,value):
    monkeypatch.setenv(live.QA_SANDBOX_ENV,value)
    monkeypatch.setattr(live.subprocess,'Popen',lambda *a,**k:pytest.fail('invalid mode launched a native process'))
    (tmp_path/'home').mkdir()
    with pytest.raises(ValueError,match='Invalid QA sandbox mode'):live.Peer(tmp_path)


def test_fixture_default_is_inserted_above_tables_and_explicit_security_override_survives(tmp_path,monkeypatch):
    monkeypatch.delenv(live.QA_SANDBOX_ENV,raising=False)
    assert live.qa_sandbox_mode()=='workspace-write'
    monkeypatch.setenv(live.QA_SANDBOX_ENV,'danger-full-access')
    home=tmp_path/'home';home.mkdir();config=home/'config.toml'
    config.write_text('model_reasoning_effort="low"\n[features]\nmemories=false\n')
    assert live.fixture_sandbox_mode(tmp_path)=='danger-full-access'
    actual=tomllib.loads(config.read_text())
    assert actual['sandbox_mode']=='danger-full-access' and actual['features']=={'memories':False}
    config.write_text('sandbox_mode="read-only"\n[features]\nmemories=false\n')
    original=config.read_bytes()
    assert live.fixture_sandbox_mode(tmp_path)=='read-only' and config.read_bytes()==original


@pytest.mark.parametrize('mode',live.QA_SANDBOX_MODES)
def test_prepare_and_raw_process_policy_follow_selected_mode(tmp_path,monkeypatch,mode):
    monkeypatch.setenv(live.QA_SANDBOX_ENV,mode)
    live.prepare(tmp_path)
    assert tomllib.loads((tmp_path/'home/config.toml').read_text())['sandbox_mode']==mode
    assert live.fixture_sandbox_mode(tmp_path)==mode
    policy=live.qa_command_sandbox(tmp_path/'work')
    assert policy['type']==('dangerFullAccess' if mode=='danger-full-access' else 'workspaceWrite')
    if mode=='workspace-write':assert policy['writableRoots']==[str(tmp_path/'work')] and policy['networkAccess'] is False


def test_explicit_prepare_mode_and_safe_profile_override_controller_default(tmp_path,monkeypatch):
    monkeypatch.setenv(live.QA_SANDBOX_ENV,'danger-full-access')
    live.prepare(tmp_path,sandbox_mode='workspace-write')
    assert live.fixture_sandbox_mode(tmp_path)=='workspace-write'
    assert profile_sandbox_mode('safe','danger-full-access')=='read-only'
    assert profile_sandbox_mode('poisoned','danger-full-access')=='danger-full-access'


def test_every_cli_gate_with_sandbox_option_receives_it_and_unsupported_gates_do_not():
    source=Path(__file__).parents[1]
    declared={p.name for p in source.glob('verify_*.py') if any(isinstance(node,ast.Constant) and node.value=='--sandbox-mode' for node in ast.walk(ast.parse(p.read_text())))}
    assert declared==SANDBOX_OPTION_VERIFIERS
    gates={name:[sys.executable,str(source/name),'--output','fixture'] for name in declared}
    gates['guardian']=[sys.executable,str(source/'verify_safety.py'),'--output','fixture']
    selected=sandbox_gate_commands(gates,'danger-full-access')
    for name in declared:assert selected[name][-2:]==['--sandbox-mode','danger-full-access']
    assert selected['guardian']==gates['guardian']
    assert all('--sandbox-mode' not in command for command in gates.values())
    with pytest.raises(ValueError,match='Conflicting'):
        sandbox_gate_commands({'agents':[sys.executable,'verify_agents.py','--sandbox-mode','workspace-write']},'danger-full-access')


def test_native_readback_mismatch_is_not_a_passing_configuration_or_os_proof():
    valid=live.verify_fixture_sandbox({'config':{'sandbox_mode':'danger-full-access','windows':None}},'danger-full-access')
    assert valid['passed'] and valid['windows_backend_config'] is None
    assert 'OS backend activation' in valid['scope']
    for response in [{},{'config':{}},{'config':{'sandbox_mode':'workspace-write'}}]:
        assert not live.verify_fixture_sandbox(response,'danger-full-access')['passed']


@pytest.mark.parametrize('configured',[None,'read-only'])
def test_peer_startup_reads_native_config_and_preserves_explicit_thread_override(tmp_path,monkeypatch,configured):
    monkeypatch.setenv(live.QA_SANDBOX_ENV,'danger-full-access')
    (tmp_path/'home').mkdir();(tmp_path/'work').mkdir()
    if configured:(tmp_path/'home/config.toml').write_text('sandbox_mode='+json.dumps(configured)+'\n')
    # Local JSON-RPC fixture only: no runtime, model, credential store or network.
    server=tmp_path/'rpc_fixture.py'
    server.write_text('''import json,os,sys,tomllib
from pathlib import Path
root=Path(sys.argv[1])
for line in sys.stdin:
    row=json.loads(line)
    if 'id' not in row:continue
    method=row['method'];params=row.get('params',{})
    if method=='initialize':result={'codexHome':str(root/'home'),'qa_env_leaked':'CODEX_DEEPSEEK_TEST_SANDBOX_MODE' in os.environ}
    elif method=='config/read':result={'config':tomllib.loads((root/'home/config.toml').read_text())}
    elif method=='thread/start':result={'thread':{'id':'thread-'+str(row['id'])},'sandbox':{'requested':params['sandbox']}}
    else:raise RuntimeError(method)
    print(json.dumps({'id':row['id'],'result':result}),flush=True)
''')
    peer=live.Peer(tmp_path,command=[sys.executable,'-I',str(server),str(tmp_path)])
    try:
        peer.new();peer.new(sandbox='read-only',approvalPolicy='never')
        assert peer.sandbox_mode==(configured or 'danger-full-access')
        assert peer.sandbox_evidence['passed']
        assert peer.events[0]['result']['qa_env_leaked'] is False
        messages=[json.loads(line) for line in (tmp_path/'client-requests.jsonl').read_text().splitlines()]
        assert any(row.get('method')=='config/read' for row in messages)
        starts=[row['params'] for row in messages if row.get('method')=='thread/start']
        assert starts[0]['sandbox']==(configured or 'danger-full-access')
        assert starts[1]['sandbox']=='read-only' and starts[1]['approvalPolicy']=='never'
        assert list(tmp_path.glob('qa-sandbox-*.json'))
    finally:peer.close()


def test_qa_selection_is_removed_by_installed_native_environment(tmp_path,monkeypatch):
    from codex_deepseek.cli import native_environment
    monkeypatch.setenv(live.QA_SANDBOX_ENV,'danger-full-access')
    assert live.QA_SANDBOX_ENV not in native_environment(tmp_path/'home','local-test-token',tmp_path/'bin/codex',install_shims=False)
