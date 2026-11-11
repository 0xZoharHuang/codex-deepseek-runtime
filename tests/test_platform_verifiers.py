"""Negative observations must not masquerade as provider-binding acceptance."""
import json
import base64
import os
from pathlib import Path,PureWindowsPath
import sys

sys.path.insert(0,str(Path(__file__).parents[1]))
from verify_profile_audit import binding_rejection,completed_reply
from verify_nested_agents import delivered_peer_messages,nested_fork_evidence
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


def _nested_fork_fixture():
    agents = [
        {"id": "producer-thread", "parent": "root-thread", "path": "/root/producer"},
        {"id": "auditor-thread", "parent": "root-thread", "path": "/root/auditor"},
        {"id": "totals-thread", "parent": "producer-thread", "path": "/root/producer/totals"},
    ]
    calls, outputs = [], []
    for index, (owner, task, fork, path) in enumerate([
        ("root-thread", "producer", "all", "/root/producer"),
        ("root-thread", "auditor", "none", "/root/auditor"),
        ("producer-thread", "totals", "none", "/root/producer/totals"),
    ]):
        metadata = {"turn_id": "turn-" + owner}
        calls.append({"thread_id": owner, "item": {"type": "function_call",
            "id": "native-spawn-" + str(index), "namespace": "collaboration", "name": "spawn_agent",
            "call_id": "call-" + str(index), "internal_chat_message_metadata_passthrough": metadata,
            "arguments": json.dumps({"task_name": task, "message": "synthetic", "fork_turns": fork})}})
        outputs.append({"thread_id": owner, "item": {"type": "function_call_output",
            "id": "native-ack-" + str(index), "call_id": "call-" + str(index),
            "internal_chat_message_metadata_passthrough": metadata,
            "output": json.dumps({"task_name": path})}})
    return agents, calls + outputs


def test_nested_forks_require_all_three_explicit_successful_native_choices():
    agents, calls = _nested_fork_fixture()
    evidence = nested_fork_evidence(calls, root_thread_id="root-thread", agents=agents)
    assert evidence["passed"] and len(evidence["edges"]) == 3
    assert [edge["effective_native_fork_turns"] for edge in evidence["edges"]] == ["all", "none", "none"]
    replayed = nested_fork_evidence(calls + [calls[0], calls[3]], root_thread_id="root-thread", agents=agents)
    assert replayed["passed"] and replayed["edges"][0]["observed_call_count"] == 2
    assert replayed["edges"][0]["unique_native_call_count"] == 1


def test_nested_fork_omission_reports_native_all_instead_of_inventing_none():
    agents, calls = _nested_fork_fixture()
    arguments = json.loads(calls[2]["item"]["arguments"]);del arguments["fork_turns"]
    calls[2]["item"]["arguments"] = json.dumps(arguments)
    evidence = nested_fork_evidence(calls, root_thread_id="root-thread", agents=agents)
    leaf = evidence["edges"][2]
    assert not evidence["passed"] and not leaf["requirement_satisfied"]
    assert leaf["explicit_argument_present"] is False and leaf["model_explicitly_supplied"] is False
    assert leaf["explicit_fork_turns"] is None and leaf["effective_native_fork_turns"] == "all"
    assert leaf["reason"] == "model_omitted_fork_turns"
    assert leaf["selected_successful_call"]["call_id"] == "call-2"


def test_nested_fork_default_all_does_not_count_as_explicit_prompt_compliance():
    agents, calls = _nested_fork_fixture()
    arguments = json.loads(calls[0]["item"]["arguments"]);del arguments["fork_turns"]
    calls[0]["item"]["arguments"] = json.dumps(arguments)
    evidence = nested_fork_evidence(calls, root_thread_id="root-thread", agents=agents)
    edge = evidence["edges"][0]
    assert not evidence["passed"] and edge["effective_native_fork_turns"] == "all"
    assert edge["effective_matches_expected"] is True and edge["requirement_satisfied"] is False


def test_nested_fork_choices_require_native_namespace_and_item_type():
    for mutation in ({"namespace": "other"}, {"type": "custom_tool_call"}, {"type": "message"}):
        agents, calls = _nested_fork_fixture();calls[2]["item"].update(mutation)
        evidence = nested_fork_evidence(calls, root_thread_id="root-thread", agents=agents)
        assert not evidence["passed"] and evidence["edges"][2]["reason"] == "native_spawn_call_missing"


def test_nested_fork_ambiguous_successes_and_invalid_json_do_not_pass():
    agents, calls = _nested_fork_fixture()
    duplicate, ack = json.loads(json.dumps(calls[2])), json.loads(json.dumps(calls[5]))
    duplicate["item"].update(id="other-source", call_id="other-call")
    ack["item"].update(id="other-ack", call_id="other-call")
    evidence = nested_fork_evidence(calls + [duplicate, ack], root_thread_id="root-thread", agents=agents)
    assert not evidence["passed"] and evidence["edges"][2]["reason"] == "ambiguous_successful_native_spawns"
    calls[2]["item"]["arguments"] = '{"task_name":"totals","fork_turns":"all","fork_turns":"none"}'
    evidence = nested_fork_evidence(calls, root_thread_id="root-thread", agents=agents)
    assert not evidence["passed"] and evidence["malformed_native_spawn_calls"]


def test_nested_fork_wrong_choice_and_foreign_owner_do_not_pass():
    agents, calls = _nested_fork_fixture()
    calls[2]["item"]["arguments"] = json.dumps({"task_name": "totals", "fork_turns": "all"})
    evidence = nested_fork_evidence(calls, root_thread_id="root-thread", agents=agents)
    assert not evidence["passed"] and evidence["edges"][2]["effective_native_fork_turns"] == "all"
    agents, calls = _nested_fork_fixture();calls[2]["thread_id"] = "foreign-thread"
    assert not nested_fork_evidence(calls, root_thread_id="root-thread", agents=agents)["passed"]
    agents, calls = _nested_fork_fixture();agents[2]["parent"] = "root-thread"
    evidence = nested_fork_evidence(calls, root_thread_id="root-thread", agents=agents)
    assert not evidence["passed"] and evidence["edges"][2]["metadata_bound"] is False


def test_nested_fork_failed_explicit_none_cannot_hide_successful_omission():
    agents, calls = _nested_fork_fixture()
    failed, error_ack = json.loads(json.dumps(calls[2])), json.loads(json.dumps(calls[5]))
    failed["item"].update(id="failed-source", call_id="failed-call")
    error_ack["item"].update(id="failed-ack", call_id="failed-call", output='{"error":"synthetic spawn refused"}')
    arguments = json.loads(calls[2]["item"]["arguments"]);del arguments["fork_turns"]
    calls[2]["item"]["arguments"] = json.dumps(arguments)
    evidence = nested_fork_evidence([failed, error_ack] + calls, root_thread_id="root-thread", agents=agents)
    leaf = evidence["edges"][2]
    assert not evidence["passed"] and leaf["successful_native_spawn_count"] == 1
    assert leaf["selected_successful_call"]["call_id"] == "call-2"
    assert leaf["effective_native_fork_turns"] == "all" and leaf["reason"] == "model_omitted_fork_turns"
    # A failed earlier attempt does not invalidate a later correctly explicit successful spawn.
    repaired = json.loads(calls[2]["item"]["arguments"]);repaired["fork_turns"] = "none"
    calls[2]["item"]["arguments"] = json.dumps(repaired)
    assert nested_fork_evidence([failed, error_ack] + calls, root_thread_id="root-thread", agents=agents)["passed"]


def test_nested_fork_requires_success_ack_with_matching_native_turn_and_canonical_child():
    agents, calls = _nested_fork_fixture()
    assert not nested_fork_evidence(calls[:5], root_thread_id="root-thread", agents=agents)["passed"]
    for bad_ack in (
        {"output": '{"task_name":"/root/totals"}'},
        {"output": '{"task_name":"/root/producer/totals","error":"failed"}'},
        {"internal_chat_message_metadata_passthrough": {"turn_id": "inherited-parent-turn"}},
        {"id": None},
    ):
        agents, calls = _nested_fork_fixture();calls[5]["item"].update(bad_ack)
        evidence = nested_fork_evidence(calls, root_thread_id="root-thread", agents=agents)
        assert not evidence["passed"] and evidence["edges"][2]["reason"] in {"native_success_ack_missing", "native_success_identity_missing"}
    # An inherited parent call/ack cannot create a child owned by producer.
    agents, calls = _nested_fork_fixture()
    calls[2]["item"]["internal_chat_message_metadata_passthrough"] = {"turn_id": "parent-old-turn"}
    calls[5]["item"].update(output='{"task_name":"/root/totals"}',
        internal_chat_message_metadata_passthrough={"turn_id": "parent-old-turn"})
    assert not nested_fork_evidence(calls, root_thread_id="root-thread", agents=agents)["passed"]


def test_nested_fork_ambiguous_or_unidentified_success_is_not_ignored_for_another_call():
    for defect in ("two_acks", "missing_call_source_id", "missing_ack_id"):
        agents, calls = _nested_fork_fixture()
        earlier, ack = json.loads(json.dumps(calls[2])), json.loads(json.dumps(calls[5]))
        earlier["item"].update(id="earlier-source", call_id="earlier-call")
        ack["item"].update(id="earlier-ack", call_id="earlier-call")
        extra = [earlier, ack]
        if defect == "two_acks":
            other_ack = json.loads(json.dumps(ack));other_ack["item"]["id"] = "other-ack-source"
            extra.append(other_ack)
        elif defect == "missing_call_source_id":
            earlier["item"]["id"] = None
        else:
            ack["item"]["id"] = None
        evidence = nested_fork_evidence(extra + calls, root_thread_id="root-thread", agents=agents)
        assert not evidence["passed"]
        assert evidence["edges"][2]["reason"] in {"ambiguous_native_success_acks", "native_success_identity_missing"}
