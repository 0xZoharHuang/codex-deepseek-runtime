"""Negative observations must not masquerade as provider-binding acceptance."""
import json
import base64
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).parents[1]))
from verify_profile_audit import binding_rejection,completed_reply
from verify_nested_agents import delivered_peer_messages
from host_execution_acceptance import host_execution_evidence,command_mentions_path
from verify_daemon_audit import is_daemon_package_file
from codex_deepseek.platforms import comparison_path
from verify_remaining_local import process_stdout_lines


def test_process_resize_joins_own_stdout_chunks_without_accepting_other_streams():
    def chunk(data,handle='owned',stream='stdout'):
        return {'method':'process/outputDelta','params':{'processHandle':handle,'stream':stream,'deltaBase64':base64.b64encode(data).decode()}}
    assert b'37 111' in process_stdout_lines([chunk(b'37'),chunk(b' 111\r\n')],'owned')
    assert b'37 111' not in process_stdout_lines([chunk(b'37'),chunk(b' 111\r\n',handle='other')],'owned')
    assert b'37 111' not in process_stdout_lines([chunk(b'37'),chunk(b' 111\r\n',stream='stderr')],'owned')
    assert b'37 111' not in process_stdout_lines([chunk(b'137 1118\n')],'owned')


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
    header='Message Type: MESSAGE\nTask name: /root/auditor\nSender: /root\nPayload:\nproducer uses integer cents'
    def request(call_id,text=header):return [{'input':[{'type':'function_call_output','call_id':call_id,'output':[{'type':'input_text','text':text}]}]}]
    args=('/root/auditor','/root','MESSAGE',['producer','integer'])
    assert not delivered_peer_messages(request('exec_fake'),*args)
    assert not delivered_peer_messages(request('peer_123',header.replace('MESSAGE','FINAL_ANSWER')),*args)
    assert not delivered_peer_messages(request('peer_123',header.replace('Sender: /root','Sender: /root/producer')),*args)
    assert delivered_peer_messages(request('peer_123'),*args)==['peer_123']


def test_windows_host_evidence_does_not_accept_foreign_commands_or_missing_command_data():
    for command in ['wsl.exe python3 test.py','WSL python3 test.py',r'C:\msys64\usr\bin\python.exe test.py','docker run image']:
        assert not host_execution_evidence([{'type':'commandExecution','command':command}],platform='win32')['passed']
    assert not host_execution_evidence([{'type':'commandExecution'}],platform='win32')['passed']
    assert host_execution_evidence([{'type':'commandExecution','command':r'& "D:\own\python.exe" test.py'}],platform='win32')['passed']
    assert host_execution_evidence([{'type':'commandExecution','command':'bash -c true'}],platform='linux')['passed']
    assert command_mentions_path(r'& "D:\\own\\python.exe" test.py',r'D:\own\python.exe')
    assert not command_mentions_path(r'& "D:\other\python.exe" test.py',r'D:\own\python.exe')
