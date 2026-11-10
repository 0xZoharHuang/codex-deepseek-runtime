"""Negative observations must not masquerade as provider-binding acceptance."""
import json
import base64
import os
from pathlib import Path,PureWindowsPath
import sys

sys.path.insert(0,str(Path(__file__).parents[1]))
from verify_profile_audit import binding_rejection,completed_reply
from verify_nested_agents import delivered_peer_messages
from host_execution_acceptance import host_execution_evidence,command_mentions_path
from verify_daemon_audit import is_daemon_package_file
from codex_deepseek.platforms import comparison_path
from verify_remaining_local import process_stdout_lines,scoped_migration_items,migration_history_recorded


def test_process_resize_joins_own_stdout_chunks_without_accepting_other_streams():
    def chunk(data,handle='owned',stream='stdout'):
        return {'method':'process/outputDelta','params':{'processHandle':handle,'stream':stream,'deltaBase64':base64.b64encode(data).decode()}}
    assert b'37 111' in process_stdout_lines([chunk(b'37'),chunk(b' 111\r\n')],'owned')
    assert b'37 111' not in process_stdout_lines([chunk(b'37'),chunk(b' 111\r\n',handle='other')],'owned')
    assert b'37 111' not in process_stdout_lines([chunk(b'37'),chunk(b' 111\r\n',stream='stderr')],'owned')
    assert b'37 111' not in process_stdout_lines([chunk(b'137 1118\n')],'owned')
    assert b'37 111' in process_stdout_lines([chunk(b'\x1b[25l\x1b[HPTY_READY\x1b[K\r\n37'),chunk(b' 111\x1b[K\r\n')],'owned')
    for title_end in [b'\x07',b'\x1b\\']:
        assert b'37 111' not in process_stdout_lines([chunk(b'\x1b]0;\r\n37 111\r\n'+title_end+b'24 80\r\n')],'owned')


def test_migration_checks_structured_windows_paths_and_exact_record_identity():
    work=PureWindowsPath(r'D:\owned\fixture')
    item={'itemType':'AGENTS_MD','cwd':str(work),'source':str(work/'CLAUDE.md'),
          'target':str(work/'AGENTS.md'),'title':'EXACT_RECORD'}
    assert scoped_migration_items({'items':[item]},work)==[item]
    assert not scoped_migration_items({'items':[{**item,'cwd':str(work)+'-other'}]},work)
    assert not scoped_migration_items({'items':[{'description':json.dumps(item)}]},work)
    def history(value):return {'data':[{'providerId':'isolated-native-audit','successes':[value],'failures':[]}]}
    assert migration_history_recorded(history(item),work,'EXACT_RECORD')
    for key in ['cwd','source','target','title','itemType']:
        assert not migration_history_recorded(history({**item,key:item[key]+'-other'}),work,'EXACT_RECORD')
    wrong=history(item);wrong['data'][0]['providerId']='other'
    assert not migration_history_recorded(wrong,work,'EXACT_RECORD')


def test_daemon_path_ownership_accepts_native_alias_and_rejects_neighbor(tmp_path):
    home=tmp_path/'profile';package=home/'packages/app-server-daemon'
    owned=package/'current/bin/rg.exe';owned.parent.mkdir(parents=True);owned.write_bytes(b'fixture')
    neighbor=home/'packages/app-server-daemon-other/rg.exe';neighbor.parent.mkdir();neighbor.write_bytes(b'fixture')
    assert is_daemon_package_file(owned,home)
    assert not is_daemon_package_file(neighbor,home)
    assert not is_daemon_package_file(package/'missing.exe',home)
    assert not is_daemon_package_file(None,home)
    if os.name=='nt':
        # Rust canonicalize() exposes this real filesystem alias after native
        # daemon startup/cold resume; plain Path equality rejects the spelling.
        alias=Path('\\\\?\\'+str(owned.resolve()))
        home_alias=Path('\\\\?\\'+str(home.resolve()))
        assert alias.is_file() and alias.samefile(owned)
        assert alias.resolve()!=owned.resolve()
        assert comparison_path(home_alias)==comparison_path(home)
        assert is_daemon_package_file(alias,home)
        assert is_daemon_package_file(owned,home_alias)
        assert not is_daemon_package_file(Path('\\\\?\\'+str(neighbor.resolve())),home)


def test_profile_marker_requires_actual_completed_agent_reply():
    def wire(*events):return b'\n'.join(json.dumps(event).encode() for event in events)
    assert not completed_reply(wire({'type':'item.completed','item':{'type':'command_execution','text':'PROFILE_PIN_VERIFIED'}},{'type':'turn.completed'}),'PROFILE_PIN_VERIFIED')
    agent={'type':'item.completed','item':{'type':'agent_message','text':'PROFILE_PIN_VERIFIED'}}
    assert not completed_reply(wire(agent,{'type':'turn.failed'}),'PROFILE_PIN_VERIFIED')
    assert completed_reply(wire(agent,{'type':'turn.completed'}),'PROFILE_PIN_VERIFIED')


def test_profile_rejection_requires_binding_diagnostic_not_any_provider_error():
    for error in [b'Provider service failed startup',b'Provider authorization failed',b'Requirement: a credential is needed']:
        assert not binding_rejection(error)
    assert binding_rejection(b'Isolated runtime binding model_provider cannot be overridden')


def test_nested_delivery_requires_native_peer_identity_and_actual_message_kind():
    from codex_deepseek.gateway import native_peer_trace_evidence
    from codex_deepseek.protocol import MODEL, prepare
    from codex_deepseek.request_metadata import native_request_metadata
    owner = "00000000-0000-0000-0000-000000000001"
    other = "00000000-0000-0000-0000-000000000002"
    turn = "00000000-0000-0000-0000-000000000003"
    receipt = "00000000-0000-0000-0000-000000000004"
    header = 'Message Type: MESSAGE\nTask name: /root/auditor\nSender: /root\nPayload:\nproducer uses integer cents'
    peer = {'type': 'agent_message', 'id': 'amsg_synthetic_source', 'author': '/root', 'recipient': '/root/auditor',
            'content': [{'type': 'input_text', 'text': header}]}
    body = {'model': MODEL, 'input': [peer], 'client_metadata': {'x-codex-turn-metadata': json.dumps(
        {'thread_id': owner, 'turn_id': turn, 'request_kind': 'turn',
         'agent_name': '/root/auditor', 'history_initialization': 'new'})}}
    prepared, registry = prepare(body)
    assert prepared['input'][0] == {'type':'message','role':'user','content':[
        {'type':'input_text','text':json.dumps({'type':'agent_message','author':peer['author'],'recipient':peer['recipient']},ensure_ascii=False,separators=(',',':'))},
        *peer['content']]}
    sidecar = native_peer_trace_evidence(receipt, body, prepared, registry, native_request_metadata(body, {}))
    records = {receipt: {'evidence': sidecar, 'prepared_request': prepared}}
    args = ('/root/auditor', '/root', 'MESSAGE', ['producer', 'integer'])
    proof = {'native_messages': [{'thread_id': owner, 'item': peer}], 'owner': owner}
    native = delivered_peer_messages([prepared], *args, **proof, native_peer_evidence=records)
    assert len(native) == 1 and native[0]['native_item_id'] == peer['id']
    assert native[0]['prepared_input_index'] == 0 and native[0]['receipt_id'] == receipt
    assert not delivered_peer_messages([prepared], *args, **proof)  # Old traces cannot gain provenance.
    assert not delivered_peer_messages([prepared], *args, native_messages=[], owner=owner, native_peer_evidence=records)
    assert not delivered_peer_messages([prepared], *args, native_messages=[{'thread_id': other, 'item': peer}],
                                       owner=owner, native_peer_evidence=records)


def test_windows_host_evidence_does_not_accept_foreign_commands_or_missing_command_data():
    for command in ['wsl.exe python3 test.py','WSL python3 test.py',r'C:\msys64\usr\bin\python.exe test.py','docker run image']:
        assert not host_execution_evidence([{'type':'commandExecution','command':command}],platform='win32')['passed']
    assert not host_execution_evidence([{'type':'commandExecution'}],platform='win32')['passed']
    assert host_execution_evidence([{'type':'commandExecution','command':r'& "D:\own\python.exe" test.py'}],platform='win32')['passed']
    assert host_execution_evidence([{'type':'commandExecution','command':'bash -c true'}],platform='linux')['passed']
    assert command_mentions_path(r'& "D:\\own\\python.exe" test.py',r'D:\own\python.exe')
    assert not command_mentions_path(r'& "D:\other\python.exe" test.py',r'D:\own\python.exe')
