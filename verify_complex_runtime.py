"""Reconstruct runtime mechanisms from preserved real tasks, without rerunning them.

Application correctness is a separate outcome. A failed application suite or an
explicit controller budget interruption remains a failure in that outcome; it
cannot be turned into successful application work by this collector.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing
import subprocess

from acceptance_assertions import (complete_work_patch, current_browser_evidence,
                                   real_work_input_hashes, real_work_input_names, real_work_tools_binding)
from install import valid_release, _support
from verify_real_work import native_reviewer_completions

canonical = _support.comparison_path

VERSION = 2
HELPERS = ('verify_complex_runtime.py', 'acceptance_assertions.py', 'install.py',
           'host_execution_acceptance.py', 'real_work_imports.py', 'verify_real_work.py',
           'verify_live.py', 'windows_acceptance_setup.py', 'qa_workspace.py')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()



def producer_provenance(artifact, *, followup, source):
    required = {'verify_real_work.py', 'verify_live.py', 'acceptance_assertions.py',
                'host_execution_acceptance.py', 'real_work_imports.py',
                'windows_acceptance_setup.py', 'qa_workspace.py'}
    if followup:
        required.add('verify_real_work_followup.py')
    declared = artifact.get('verifier_sha256', {})
    return (set(declared) == required and all(
        (source / name).is_file() and sha(source / name) == value
        for name, value in declared.items()))


def review_delivery_evidence(history, events, child, agent_path, deliveries, parent_turn):
    completions = [item for item in native_reviewer_completions(history, events, child)
                   if parent_turn and item['root_turn_id'] == parent_turn]
    messages = {item['final_message'] for item in completions}
    delivered = sorted({call for path, text, call in deliveries
                        if path == agent_path and text in messages})
    return {'delivered_call_ids': delivered,
            'completed_turn_ids': [item['turn_id'] for item in completions],
            'verified_completions': completions,
            'passed': bool(completions and delivered)}


def tool_environment_evidence(root,report):
    declared=report.get('tool_environment',{})
    before=declared.get('before',{})
    selected=before.get('paths',{}).get('selected',{}).get('root')
    if not selected:return {'passed':False,'reason':'Missing declared real-work tool environment'}
    try:current=real_work_tools_binding(root,selected)
    except (OSError,ValueError) as error:return {'passed':False,'reason':str(error)}
    return {'passed':(declared.get('unchanged') is True and before==declared.get('after')==current
                      and current['same_bytes'] is True
                      and report.get('native_host_execution',{}).get('declared_python')==current['paths']['selected']['python']),
            'current':current}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def request_sessions(requests):
    sessions = []
    for request in requests:
        if request.get('method') == 'initialize':
            sessions.append([])
        if not sessions:
            raise ValueError('Request stream does not begin with initialize')
        sessions[-1].append(request)
    return sessions


def file_effect_evidence(root, changed, patch):
    """Bind native edits to the verified physical workspace, rejecting escapes."""
    import qa_workspace as layout
    root = canonical(Path(root))
    alias = root/'work'
    record_path = root/layout.RECORD
    try:
        layout._ancestry(root)
        if record_path.exists():
            if layout._link(record_path) or record_path.stat().st_nlink != 1:
                raise ValueError('Foreign QA workspace record')
            record = read(record_path)
            layout._ancestry(Path(record['config']['workspace_root']))
            physical = layout._validate(root, record['config'], record)
            if canonical(Path(record['alias'])) != canonical(alias):
                raise ValueError('QA workspace alias differs')
            work = canonical(physical)
            mapping_sha = sha(record_path)
        else:
            layout._ancestry(alias)
            work = canonical(alias)
            mapping_sha = None
        paths = {}
        for item in changed:
            for change in item.get('changes', []):
                requested = Path(change['path'])
                if not requested.is_absolute():
                    raise ValueError('Native file effect path is not absolute')
                path = canonical(requested)
                if not path.is_relative_to(work):
                    raise ValueError('Native file effect escapes the physical workspace')
                if path.is_file():
                    paths[path.relative_to(work).as_posix()] = sha(path)
        patch_ok = bool(patch) and complete_work_patch(alias) == patch
        # Match a complete Git path line; a filename substring is not evidence.
        names_in_patch = [name for name in paths if
            b'\n+++ b/'+name.encode()+b'\n' in patch or
            b'\n+++ "b/'+name.encode()+b'"\n' in patch]
        return {'passed': bool(patch_ok and paths and names_in_patch),
                'patch_matches_worktree': patch_ok, 'files': paths,
                'physical_workspace': str(work), 'workspace_record_sha256': mapping_sha}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'passed': False, 'files': {}, 'reason': str(error)}


def process_observation(pid, birth):
    """Never interpret permission failure as proof that an owner exited."""
    import psutil
    from codex_deepseek.processes import identity
    observed = identity(pid)
    if observed:
        state = 'live' if observed == birth else 'reused'
    else:
        try:
            state = 'unknown' if psutil.pid_exists(pid) else 'gone'
        except (OSError, psutil.Error):
            state = 'unknown'
    return {'pid': pid, 'expected_birth': birth, 'observed_birth': observed, 'state': state}


def control_owner_evidence(events, owners, probe=process_observation):
    """Join startup time to one closed kernel owner and inspect exact identities."""
    times = [e['emittedAtMs']/1000 for e in events
             if e.get('method') == 'remoteControl/status/changed'
             and isinstance(e.get('emittedAtMs'), int)]
    candidates = []
    for owner in owners:
        birth = owner.get('native_birth')
        if not isinstance(birth, str) or not birth.startswith('windows:'):
            continue
        try:
            started = (int(birth.removeprefix('windows:')) - 116444736000000000)/10000000
        except ValueError:
            continue
        closed = owner.get('closed')
        if times and started <= min(times) and (closed is None or max(times) <= closed):
            candidates.append(owner)
    if len(candidates) != 1 or not isinstance(candidates[0].get('closed'), (int, float)):
        return {'passed': False, 'reason': 'Read-only connection has no unique closed kernel owner'}
    owner = candidates[0]
    required = [('pid', 'birth'), ('supervisor_pid', 'supervisor_birth'), ('native_pid', 'native_birth')]
    if not all(isinstance(owner.get(pid), int) and owner[pid] > 0
               and isinstance(owner.get(birth), str) and owner[birth] for pid, birth in required):
        return {'passed': False, 'reason': 'Read-only owner has incomplete process identities'}
    observations = [probe(owner[pid], owner[birth]) for pid, birth in required]
    # Check every owner recorded in this private home; a stale closed=NULL row
    # is retained as such and is never sufficient proof of a live process.
    remaining = [probe(row[pid], row[birth]) for row in owners for pid, birth in required
                 if row.get(pid) and row.get(birth)]
    # Re-observe every expected identity on every reconstruction. A different
    # current owner of a recycled PID is irrelevant to this historical owner.
    # Keep those volatile details in the independent collection audit, not in
    # a proof that must reproduce byte-for-byte from the same task evidence.
    stable = lambda rows: [{'pid': row['pid'], 'expected_birth': row['expected_birth'],
                            'ended': row['state'] in {'gone', 'reused'}} for row in rows]
    return {'passed': all(x['state'] in {'gone', 'reused'} for x in observations + remaining),
            'owner': owner, 'processes': stable(observations),
            'all_home_owner_processes': stable(remaining),
            'native_exit_code': None, 'natural_eof_verified': False,
            'scope': 'Exact native/launcher/supervisor identities have ended; exit code and close reason are not inferred.'}


def readonly_control_evidence(requests, events, owner_proof=None):
    """Classify read-only controls without contributing task-lifecycle evidence."""
    methods = [r.get('method') for r in requests]
    defects = []
    if (methods[:2] != ['initialize', 'initialized']
            or any(m != 'config/read' for m in methods[2:])
            or 'id' not in requests[0] or 'id' in requests[1]
            or any('id' not in r for r in requests[2:])):
        defects.append('Connection is not an explicit read-only initialize/config sequence')
    calls = [r for r in requests if 'id' in r]
    responses = [e for e in events if 'id' in e and 'method' not in e]
    ids = [r['id'] for r in calls]
    response_ids = [r['id'] for r in responses]
    if (len(ids) != len(set(ids)) or len(response_ids) != len(set(response_ids))
            or set(ids) != set(response_ids)
            or any('result' not in r or r.get('error') for r in responses)):
        defects.append('Read-only RPC responses are missing, duplicated, unmatched or failed')
    if any(not ('id' in e and 'method' not in e) and not (
            e.get('method') == 'remoteControl/status/changed' and 'id' not in e
            and e.get('params', {}).get('status') == 'disabled') for e in events):
        defects.append('Unexpected operation or notification on read-only connection')
    if not owner_proof or owner_proof.get('passed') is not True:
        defects.append('Read-only connection owner closure is not verified')
    return {'passed': not defects, 'classification': 'control_only',
            'contributes_task_lifecycle_evidence': False, 'methods': methods,
            'requests_sha256': digest(requests), 'events_sha256': digest(events),
            'owner_closure': owner_proof, 'defects': defects}



def lifecycle(sessions, event_groups, parent, *, require_resume=True, control_owners=None):
    """Bind cancellation and cold resume to their actual RPC connection and turn."""
    interrupts, resumes, defects, controls = [], [], [], []
    control_owners = control_owners or {}
    task_connections = []
    if len(sessions) != len(event_groups):
        return {'passed': False, 'defects': ['RPC connection count mismatch']}
    previous_turns = set()
    for index, (requests, events) in enumerate(zip(sessions, event_groups)):
        responses = [e for e in events if 'id' in e and 'method' not in e]
        ids = [e['id'] for e in responses]
        if len(ids) != len(set(ids)):
            defects.append('Duplicate RPC response in connection ' + str(index))
        replies = {e['id']: e for e in responses}
        terminals = {(e['params']['threadId'], e['params']['turn']['id']): e['params']['turn']
                     for e in events if e.get('method') == 'turn/completed'}
        started_turns = {reply.get('result', {}).get('turn', {}).get('id')
                         for request in requests if request.get('method') == 'turn/start'
                         and request.get('params', {}).get('threadId') == parent
                         for reply in [replies.get(request.get('id'), {})]}
        started_turns.discard(None)
        has_turn = any(r.get('method') == 'turn/start' for r in requests)
        if has_turn:
            task_connections.append(index)
        if not has_turn:
            control = readonly_control_evidence(requests, events, control_owners.get(index))
            controls.append({'connection': index, **control})
            defects.extend('Read-only connection '+str(index)+': '+x for x in control['defects'])
        elif not started_turns or any((parent, turn) not in terminals for turn in started_turns):
            defects.append('Missing started parent turn or terminal in connection ' + str(index))
        for request in requests:
            if 'id' not in request or 'method' not in request:
                continue
            reply = replies.get(request['id'], {})
            if 'result' not in reply or reply.get('error'):
                defects.append('Missing/failed RPC ' + request['method'])
                continue
            params = request.get('params', {})
            if request['method'] == 'turn/interrupt':
                key = (params.get('threadId'), params.get('turnId'))
                terminal = terminals.get(key, {})
                good = key[0] == parent and terminal.get('status') == 'interrupted' and terminal.get('error') is None
                interrupts.append({'connection': index, 'thread_id': key[0], 'turn_id': key[1],
                                   'status': terminal.get('status'), 'error': terminal.get('error'), 'passed': good})
            if request['method'] == 'thread/resume':
                resumed = reply['result'].get('thread', {}).get('id')
                following = [e['params'] for e in events if e.get('method') == 'item/completed'
                             and e.get('params', {}).get('threadId') == parent
                             and e['params'].get('item', {}).get('type') == 'commandExecution'
                             and e['params']['item'].get('exitCode') == 0]
                continued = [p for p in following if p.get('turnId') in started_turns and p.get('turnId') not in previous_turns]
                good = index > 0 and params.get('threadId') == resumed == parent and bool(continued)
                resumes.append({'connection': index, 'thread_id': resumed, 'continued_tool_ids':
                                sorted({p['item']['id'] for p in continued}), 'passed': good})
        previous_turns.update(t for (thread, t) in terminals if thread == parent)
        for (thread, turn), terminal in terminals.items():
            if terminal.get('error') or terminal.get('status') not in {'completed', 'interrupted'}:
                defects.append('Unexpected terminal ' + thread + '/' + turn)
            if thread == parent and terminal.get('status') == 'interrupted' and not any(
                    x['thread_id'] == parent and x['turn_id'] == turn and x['passed'] for x in interrupts):
                defects.append('Unrequested parent interruption ' + turn)
    return {'passed': bool(task_connections) and not defects and (bool(resumes) or not require_resume) and all(x['passed'] for x in interrupts + resumes),
            'interrupts': interrupts, 'cold_resumes': resumes, 'task_connections': task_connections, 'read_only_connections': controls, 'defects': defects}


def source_imports(root, suite, receipt):
    expected = root/'work'/'src' if root.name == 'jinja' else root/'work'
    modules = receipt.get('modules', {})
    package = 'jinja2' if root.name == 'jinja' else 'httpcore'
    return (receipt.get('package') == package and package in modules
            and canonical(Path(receipt.get('expected_root', ''))) == canonical(expected)
            and not receipt.get('invalid_modules') and receipt.get('passed') is True
            and all(canonical(Path(x['file'])).is_relative_to(canonical(expected))
                    and Path(x['file']).is_file() and sha(x['file']) == x['sha256'] for x in modules.values()))


def application_outcome(report, suites, validation):
    return {'original_report_passed': report.get('passed'), 'turn_status': report.get('turn_status'),
            'controller_error': report.get('error'), 'suites': suites, 'diff_check': report.get('diff_check'),
            'application_task_passed': report.get('passed') is True and report.get('turn_status') == 'completed'
            and not report.get('error') and validation and all(x['exit'] == 0 and x.get('pytest_application_passed',True) for x in suites)}


def function_outputs(parts, call):
    """Decode observed native CodeMode projections, retaining omitted exit codes."""
    if not isinstance(parts, list):
        return []
    values = []
    for number, part in enumerate(parts):
        text = part.get('text', '')
        if number + 1 < len(parts):
            code = parts[number + 1].get('text', '')
            match = re.fullmatch(r'(?:exit=)?(-?\d+)', code) if isinstance(code, str) else None
            if match:
                values.append({'exit_code': int(match[1]), 'output': text, 'projection': 'output_and_exit_code'})
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and isinstance(value.get('exit_code'), int) and isinstance(value.get('output'), str):
            values.append({**value, 'projection': 'structured_result'})
    # Some model-authored CodeMode calls intentionally print only r.output.
    # In that case prove the exact output round trip and retain the numeric exit
    # solely from the independent native commandExecution event, never invent it.
    try:
        code = json.loads(call.get('arguments', '{}')).get('input', '')
    except (ValueError, TypeError):
        code = ''
    if isinstance(code, str):
        code = code.replace('\\n', '\n')
        match = re.fullmatch(r'\s*(?:const|let)\s+(\w+)\s*=\s*await\s+tools\.exec_command\(\{[\s\S]*\}\)\s*;\s*text\(\1\.output\)\s*;?\s*', code)
        projection_only = (call.get('name') == 'exec' and match
                           and code.count('tools.exec_command(') == 1
                           and not re.search(r'\b'+re.escape(match[1])+r'\.output\s*=', code))
        if projection_only:
            values.extend({'exit_code': None, 'output': part['text'], 'projection': 'model_selected_output_only'}
                          for part in parts if part.get('type') == 'input_text' and isinstance(part.get('text'), str)
                          and not part['text'].startswith('Script completed\n'))
    return values


def _recovery_connections(evidence):
    """Validate both RPC directions and reproduce the complete native event stream."""
    if 'controller_connections' not in evidence:
        return None, []
    connections = evidence['controller_connections']
    if (not isinstance(connections, list) or not connections
            or any(not isinstance(connection, dict) or set(connection) != {'requests', 'events'}
                   or not isinstance(connection['requests'], list)
                   or not isinstance(connection['events'], list)
                   or any(not isinstance(value, dict) for value in connection['requests'] + connection['events'])
                   for connection in connections)):
        return None, ['Malformed controller connections']
    if [event for connection in connections for event in connection['events']] != evidence['native_events']:
        return None, ['Controller connection events differ from native events']
    for connection in connections:
        requests, events = connection['requests'], connection['events']
        calls = [request for request in requests if 'id' in request and 'method' in request]
        replies = [event for event in events if 'id' in event and 'method' not in event]
        ids, reply_ids = [call['id'] for call in calls], [reply['id'] for reply in replies]
        # The QA client uses a positive integer counter. Server callbacks have
        # their own typed integer/string ID space, which may overlap this counter.
        callbacks = [event for event in events if 'id' in event and 'method' in event]
        answers = [request for request in requests if 'id' in request and 'method' not in request]
        callback_ids, answer_ids = [call['id'] for call in callbacks], [answer['id'] for answer in answers]
        callback_id = lambda value: type(value) in (int, str)
        typed = lambda values: [(type(value).__name__, value) for value in values]
        if (not requests or requests[0].get('method') != 'initialize'
                or any('method' in value and (not isinstance(value['method'], str) or not value['method'])
                       for value in requests + events)
                or any(request.get('method') in {'turn/start', 'turn/interrupt'} and 'id' not in request
                       for request in requests)
                or any(type(value) is not int or value < 1 for value in ids + reply_ids)
                or len(set(ids)) != len(ids) or len(set(reply_ids)) != len(reply_ids)
                or set(ids) != set(reply_ids)
                or any('result' not in reply or 'error' in reply for reply in replies)
                or any(not callback_id(value) for value in callback_ids + answer_ids)):
            return None, ['Controller RPC responses are missing, duplicated, unmatched or failed']
        callback_keys, answer_keys = typed(callback_ids), typed(answer_ids)
        if (len(set(callback_keys)) != len(callback_keys) or len(set(answer_keys)) != len(answer_keys)
                or set(callback_keys) != set(answer_keys)
                or any(set(answer) - {'jsonrpc', 'id', 'result', 'error'}
                       or ('result' in answer) == ('error' in answer)
                       or ('error' in answer and not isinstance(answer['error'], dict)) for answer in answers)):
            return None, ['Native callback replies are malformed, duplicated or unmatched']
    return connections, []


def _recovered_before_explicit_interrupt(connections, thread, turn, retry, terminal, success_finished):
    """Prove progress before an accepted interrupt; no request-send time is invented."""
    if connections is None:
        return None
    turn_value = terminal['params']['turn']
    if (turn_value.get('status') != 'interrupted' or 'error' not in turn_value
            or turn_value['error'] is not None or type(terminal.get('emittedAtMs')) is not int):
        return None
    starts, interrupts = [], []
    for connection_index, connection in enumerate(connections):
        requests, events = connection['requests'], connection['events']
        replies = {event['id']: (index, event) for index, event in enumerate(events)
                   if 'id' in event and 'method' not in event}
        for request_index, request in enumerate(requests):
            params = request.get('params', {})
            if not isinstance(params, dict) or request.get('id') not in replies:
                continue
            reply_index, reply = replies[request['id']]
            result = reply.get('result')
            if (request.get('method') == 'turn/start' and params.get('threadId') == thread
                    and isinstance(result, dict) and isinstance(result.get('turn'), dict)
                    and result['turn'].get('id') == turn):
                starts.append((connection_index, request_index, reply_index, request, reply))
            if (request.get('method') == 'turn/interrupt'
                    and params.get('threadId') == thread and params.get('turnId') == turn):
                interrupts.append((connection_index, request_index, reply_index, request, reply))
    if len(starts) != 1 or len(interrupts) != 1:
        return None
    start, interrupt = starts[0], interrupts[0]
    if (start[0] != interrupt[0] or start[1] >= interrupt[1]
            or start[4]['result']['turn'].get('status') != 'inProgress'
            or start[4]['result']['turn'].get('error') is not None
            or interrupt[4]['result'] != {}):
        return None
    events = connections[start[0]]['events']
    retry_indices = [index for index, event in enumerate(events) if event == retry]
    terminal_indices = [index for index, event in enumerate(events) if event == terminal]
    if (len(retry_indices) != 1 or len(terminal_indices) != 1
            or not start[2] < retry_indices[0] < interrupt[2] < terminal_indices[0]
            or success_finished * 1000 >= terminal['emittedAtMs']):
        return None
    tools = []
    for index, event in enumerate(events):
        params = event.get('params', {})
        item = params.get('item', {}) if isinstance(params, dict) else {}
        if (event.get('method') != 'item/completed' or not isinstance(params, dict) or not isinstance(item, dict)
                or params.get('threadId') != thread or params.get('turnId') != turn
                or not isinstance(item.get('id'), str) or not item['id']
                or item.get('status') != 'completed'
                or type(params.get('completedAtMs')) is not int
                or type(event.get('emittedAtMs')) is not int
                or not retry_indices[0] < index < interrupt[2]
                or not success_finished * 1000 < params['completedAtMs'] <= event['emittedAtMs'] < terminal['emittedAtMs']):
            continue
        actual = ((item.get('type') == 'commandExecution' and type(item.get('exitCode')) is int
                   and isinstance(item.get('command'), str) and bool(item['command']))
                  or (item.get('type') == 'fileChange' and isinstance(item.get('changes'), list) and bool(item['changes']))
                  or (item.get('type') == 'mcpToolCall' and item.get('error') is None
                      and item.get('result') is not None))
        began = [(position, candidate) for position, candidate in enumerate(events)
                 if candidate.get('method') == 'item/started'
                 and isinstance(candidate.get('params'), dict)
                 and candidate['params'].get('threadId') == thread
                 and candidate['params'].get('turnId') == turn
                 and isinstance(candidate['params'].get('item'), dict)
                 and candidate['params']['item'].get('id') == item['id']]
        ends = [candidate for candidate in events if candidate.get('method') == 'item/completed'
                and isinstance(candidate.get('params'), dict)
                and candidate['params'].get('threadId') == thread
                and candidate['params'].get('turnId') == turn
                and isinstance(candidate['params'].get('item'), dict)
                and candidate['params']['item'].get('id') == item['id']]
        # Count every matching start before inspecting its contents: a malformed
        # duplicate must not disappear from the uniqueness proof.
        if not actual or len(began) != 1 or len(ends) != 1:
            continue
        begin_index, beginning = began[0]
        started = beginning['params']
        if (started['item'].get('type') != item.get('type')
                or started['item'].get('status') != 'inProgress'
                or type(started.get('startedAtMs')) is not int
                or type(beginning.get('emittedAtMs')) is not int
                or not retry_indices[0] < begin_index < index
                or not success_finished * 1000 < started['startedAtMs']
                    <= beginning['emittedAtMs'] <= params['completedAtMs']):
            continue
        tools.append({'item_id': item['id'], 'type': item['type'], 'event_index': index,
                      'started_at_ms': started['startedAtMs'],
                      'start_event_index': begin_index, 'start_event_sha256': digest(beginning),
                      'completed_at_ms': params['completedAtMs'], 'event_sha256': digest(event)})
        break
    if not tools:
        return None
    return {'classification': 'request_recovered_then_explicit_interrupt',
            'connection_index': start[0], 'start_rpc_id': start[3]['id'],
            'interrupt_rpc_id': interrupt[3]['id'], 'start_request_sha256': digest(start[3]),
            'start_reply_sha256': digest(start[4]), 'interrupt_request': interrupt[3],
            'interrupt_reply': interrupt[4], 'interrupt_reply_event_index': interrupt[2],
            'interrupted_terminal_emitted_at_ms': terminal['emittedAtMs'],
            'subsequent_native_tools': tools,
            'timing_scope': 'Success receipt finished before actual native tool start and completion; tool event precedes the accepted interrupt reply and interrupted terminal. Request-send wall time is not recorded or inferred.'}


def _provider_recoveries(provider_requests, receipts, evidence):
    """Join raw receipt timing to one native retry hop; no cache-key inference."""
    import math
    from collections import Counter
    if evidence is None:
        return {}, []
    if (not isinstance(evidence, dict)
            or set(evidence) not in ({'receipt_observations', 'native_events'},
                                     {'receipt_observations', 'native_events', 'controller_connections'})
            or not isinstance(evidence['receipt_observations'], list)
            or not isinstance(evidence['native_events'], list)
            or any(not isinstance(event, dict) for event in evidence['native_events'])):
        return {}, ['Malformed recovery evidence']
    connections, defects = _recovery_connections(evidence)
    if defects:
        return {}, defects
    observations, events = evidence['receipt_observations'], evidence['native_events']
    if any(event.get('method') in {'error', 'turn/completed'}
           and (not isinstance(event.get('params'), dict)
                or event['method'] == 'turn/completed' and not isinstance(event['params'].get('turn'), dict))
           for event in events):
        return {}, ['Malformed native recovery event']
    valid_time = lambda value: type(value) in (int, float) and math.isfinite(value)
    if any(not isinstance(row, dict) or set(row) != {'id', 'started', 'finished', 'detail'}
           or not isinstance(row['id'], str) or not row['id']
           or not valid_time(row['started']) or not valid_time(row['finished'])
           or row['finished'] < row['started']
           or (row['detail'] is not None and not isinstance(row['detail'], str))
           for row in observations):
        return {}, ['Malformed receipt timing']
    ids = [row['id'] for row in observations]
    if len(ids) != len(set(ids)) or set(ids) != {row[0] for row in receipts}:
        return {}, ['Receipt timing identity mismatch']
    timing = {row['id']: row for row in observations}
    rows = {row[0]: row for row in receipts}
    eligible = {'adapter_error', 'upstream_error'}
    def scope(key):
        values = (provider_requests[key].get('thread'), provider_requests[key].get('turn'))
        return tuple(value if isinstance(value, str) and value else None for value in values)
    groups = {}
    for key in rows:
        groups.setdefault(scope(key), []).append(key)
    for keys in groups.values():
        keys.sort(key=lambda key: (timing[key]['started'], key))
    links = {}
    for key, row in rows.items():
        thread, turn = scope(key)
        detail = timing[key]['detail']
        if (row[1] not in eligible or not isinstance(thread, str) or not thread
                or not isinstance(turn, str) or not turn or not isinstance(detail, str) or not detail.strip()):
            continue
        if not ((row[1] == 'adapter_error' and row[2] in (None, 200))
                or (row[1] == 'upstream_error' and type(row[2]) is int and 400 <= row[2] <= 599)):
            continue
        sequence = groups[(thread, turn)]
        position = sequence.index(key)
        if position + 1 == len(sequence):
            continue
        next_key = sequence[position + 1]
        if sum(timing[other]['started'] == timing[next_key]['started'] for other in sequence) != 1:
            continue
        retry = [(index, event) for index, event in enumerate(events)
                 if event.get('method') == 'error'
                 and event.get('params', {}).get('threadId') == thread
                 and event.get('params', {}).get('turnId') == turn
                 and type(event.get('emittedAtMs')) is int
                 and timing[key]['finished'] <= event['emittedAtMs'] / 1000 <= timing[next_key]['started']]
        terminals = [event for event in events if event.get('method') == 'turn/completed'
                     and event.get('params', {}).get('threadId') == thread
                     and event.get('params', {}).get('turn', {}).get('id') == turn]
        if len(retry) != 1 or len(terminals) != 1:
            continue
        retry_index, event = retry[0]
        if event['params'].get('willRetry') is not True:
            continue
        error = event['params'].get('error', {})
        if not isinstance(error, dict):
            continue
        info = error.get('codexErrorInfo', {})
        if not isinstance(info, dict):
            continue
        if (not isinstance(error.get('additionalDetails'), str)
                or not error['additionalDetails'].endswith(detail)
                or not any(isinstance(info.get(kind), dict)
                           for kind in ('responseStreamDisconnected', 'httpConnectionFailed'))):
            continue
        codes = [info[kind].get('httpStatusCode') for kind in ('responseStreamDisconnected', 'httpConnectionFailed')
                 if isinstance(info.get(kind), dict)]
        if (row[1] == 'upstream_error' and not any(type(code) is int and code == row[2] for code in codes)
                or row[1] == 'adapter_error' and any(code not in (None, 200) for code in codes)):
            continue
        terminal = terminals[0]
        terminal_turn = terminal['params']['turn']
        if (terminal_turn.get('error') or type(terminal.get('emittedAtMs')) is not int
                or terminal_turn.get('status') not in ({'completed', 'interrupted'} if connections is not None else {'completed'})):
            continue
        success = next((other for other in sequence[position + 1:] if rows[other][1] == 'completed'), None)
        if (success is None or rows[success][2] != 200 or not rows[success][3]
                or timing[success]['finished'] > terminal['emittedAtMs'] / 1000):
            continue
        interrupted = None
        if terminal_turn.get('status') == 'interrupted':
            interrupted = _recovered_before_explicit_interrupt(
                connections, thread, turn, event, terminal, timing[success]['finished'])
            if interrupted is None:
                continue
        between = sequence[position + 1:sequence.index(success)]
        if any(rows[other][1] not in eligible for other in between):
            continue
        links[key] = {'thread_id': thread, 'turn_id': turn, 'retry_event_index': retry_index,
                      'retry_event': event, 'next_request_id': next_key,
                      'completed_request_id': success, 'intervening_error_ids': between,
                      'terminal_event': terminal}
        if interrupted is not None:
            links[key].update(interrupted)
    use_counts = Counter(link['retry_event_index'] for link in links.values())
    valid = {key for key, link in links.items() if use_counts[link['retry_event_index']] == 1}
    # A recovered chain cannot hide a second unproved error between this hop and success.
    while True:
        proven_retries = {links[key]['retry_event_index'] for key in valid}
        remaining = {key for key in valid if all(other in valid for other in links[key]['intervening_error_ids'])
                     and (links[key].get('classification') != 'request_recovered_then_explicit_interrupt'
                          or all(index in proven_retries for index, event in enumerate(events)
                                 if event.get('method') == 'error'
                                 and event.get('params', {}).get('threadId') == links[key]['thread_id']
                                 and event.get('params', {}).get('turnId') == links[key]['turn_id']))}
        if remaining == valid:
            break
        valid = remaining
    return {key: links[key] for key in sorted(valid)}, []


def provider_transport_evidence(provider_requests, receipts, installed_model, *, recovery_evidence=None):
    """v2 joins raw recovery evidence; legacy three-argument calls remain strict.

    Recovered terminal errors keep their IDs, statuses, details and routes. They
    never become successful individual requests or single-run success claims.
    """
    from collections import Counter
    requested = set(provider_requests)
    counts = Counter(row[0] for row in receipts)
    observed = set(counts)
    missing = sorted(requested - observed)
    untraced = sorted(observed - requested)
    duplicates = sorted(key for key, count in counts.items() if count != 1)
    original_errors = sorted(row[0] for row in receipts
                             if row[1] not in {'completed', 'cancelled'})
    routes = [{'status': row[1], 'upstream_status': row[2],
               'provider': row[3], 'route_reason': row[5]} for row in receipts]
    valid_ids = all(isinstance(key, str) and key for key in requested | observed)
    joined = (bool(requested) and valid_ids and not missing and not untraced and not duplicates
              and all(row[2] == 200 and row[3] for row in receipts if row[1] == 'completed')
              and all(value['model'] == installed_model for value in provider_requests.values()))
    recovered, defects = _provider_recoveries(provider_requests, receipts, recovery_evidence) if joined else ({}, [])
    unrecovered = [key for key in original_errors if key not in recovered]
    observations = recovery_evidence.get('receipt_observations', []) if isinstance(recovery_evidence, dict) else []
    details = {row['id']: row.get('detail') for row in observations
               if isinstance(row, dict) and isinstance(row.get('id'), str)}
    errors = [{'id': row[0], 'status': row[1], 'upstream_status': row[2],
               'provider': row[3], 'detail': details.get(row[0]), 'route_reason': row[5],
               'classification': recovered[row[0]].get('classification', 'recovered') if row[0] in recovered else 'unrecovered'}
              for row in receipts if row[0] in original_errors]
    return {'passed': bool(joined and not unrecovered and not defects),
            'provider_evidence_version': 2,
            'single_run_all_success': bool(joined and all(row[1] == 'completed' for row in receipts)),
            'receipt_rows_sha256': digest(receipts), 'errors': errors,
            'routes': sorted({json.dumps(row, sort_keys=True) for row in routes}),
            'request_ids': sorted(requested), 'receipt_ids': sorted(observed),
            'missing_receipt_ids': missing, 'untraced_receipt_ids': untraced,
            'duplicate_receipt_ids': duplicates,
            'original_error_receipt_ids': original_errors,
            'recovered_error_receipt_ids': sorted(recovered),
            'unresolved_or_failed_receipt_ids': unrecovered,
            'recovered_errors': recovered, 'recovery_evidence_defects': defects,
            'recovery_evidence_sha256': digest(recovery_evidence) if recovery_evidence is not None else None}


def reconstruct_case(root, entry):
    """Read-only reconstruction. Missing or inconsistent evidence fails closed."""
    root, entry = canonical(Path(root)), canonical(Path(entry))
    report = read(root/'results.json')
    raw = {}
    def bind(path):
        path = Path(path)
        raw[str(path.relative_to(root)) if path.is_relative_to(root) else str(path)] = sha(path)
        return path
    def load(path):
        return read(bind(path))
    bind(root/'results.json')
    artifact = load(root/'artifact.json')
    artifacts = [artifact] + [load(p) for p in sorted(root.glob('followup-attempts/*/artifact.json'))]
    release = entry.parent.parent.parent
    installed = load(release/'installation.json')
    native = load(release/'native-runtime.json')
    identity = all(canonical(Path(a.get('entry', ''))) == entry and a.get('entry_sha256') == sha(entry)
                   and a.get('installation') == installed for a in artifacts)
    immutable = identity and valid_release(release, installed['adapter_source_sha256'],
                                          installed['model_catalog_sha256'], native)
    active_artifact = artifact
    previous_report_link = True
    if report.get('followup'):
        attempt = root / report['followup']['evidence']
        if not canonical(attempt).is_relative_to(root/'followup-attempts'):
            raise ValueError('Followup evidence escapes fixture')
        active_artifact = load(attempt/'artifact.json')
        previous_report_link = sha(bind(attempt/'results.json')) == report['followup']['previous_report_sha256'] == active_artifact.get('previous_report_sha256')
    verifier_matches = previous_report_link and producer_provenance(
        active_artifact, followup=bool(report.get('followup')), source=Path(__file__).parent)
    launches = [load(p) for p in sorted(root.glob('launch-*.json'))]
    event_groups = [load(p) for p in sorted(root.glob('events-*.json'))]
    events = [event for group in event_groups for event in group]
    requests = [json.loads(line) for line in bind(root/'client-requests.jsonl').read_text(encoding='utf-8').splitlines()]
    sessions = request_sessions(requests)
    launch_ok = len(launches) == len(sessions) and all(
        canonical(Path(x.get('resolved_entry', ''))) == entry and x.get('entry_sha256') == sha(entry)
        and canonical(Path(x.get('runtime_home', ''))) == root/'home'
        and x.get('server_args') == ['app-server', '--strict-config'] for x in launches)
    parent = report['thread']
    state = root/'home/provider-state/adapter.sqlite'; bind(state)
    for path in state.parent.glob(state.name+'-wal'): bind(path)
    with closing(readonly(state)) as db, db:
        db.row_factory = sqlite3.Row
        owners = [dict(row) for row in db.execute('SELECT * FROM runtime_owners')]
    control_owners = {index: control_owner_evidence(group, owners)
                      for index, (session, group) in enumerate(zip(sessions, event_groups))
                      if not any(r.get('method') == 'turn/start' for r in session)}
    life = lifecycle(sessions, event_groups, parent, require_resume=root.name == 'httpcore',
                     control_owners=control_owners)
    if root.name == 'httpcore' and not life.get('interrupts'):
        life['passed'] = False
    completed = [e['params'] for e in events if e.get('method') == 'item/completed']
    commands = [p for p in completed if p['item'].get('type') == 'commandExecution']
    changed = [p['item'] for p in completed if p.get('threadId') == parent
               and p['item'].get('type') == 'fileChange' and p['item'].get('status') == 'completed']
    patch = bind(root/'agent.patch').read_bytes()
    effect_proof = file_effect_evidence(root, changed, patch)
    web = current_browser_evidence(events, parent, turn=report.get('final_turn_id'))
    # Provider input is the actual history delivered to the model; use thread_id,
    # never prompt_cache_key, which is intentionally shared by nested agents.
    outputs, provider_requests, function_calls = {}, {}, {}
    for path in sorted((root/'adapter-traces').glob('*-request.json')):
        payload = load(path); meta = payload.get('client_metadata', {})
        request_id = path.name.removesuffix('-request.json')
        provider_requests[request_id] = {'thread': meta.get('thread_id'), 'turn': meta.get('turn_id'), 'model': payload.get('model')}
        for item in payload.get('input', []):
            if item.get('type') == 'function_call_output':
                outputs[(meta.get('thread_id'), item.get('call_id'), digest(item.get('output')))] = item.get('output')
            elif item.get('type') == 'function_call':
                function_calls[(meta.get('thread_id'), item.get('call_id'))] = item
    parsed_outputs = []
    deliveries = []
    for (thread, call, _), parts in outputs.items():
        if not isinstance(parts, list):
            continue
        parsed_outputs.extend((thread, call, value) for value in function_outputs(parts, function_calls.get((thread, call), {})))
        for part in parts:
            text = part.get('text', '')
            if thread == parent and text.startswith('Message Type: FINAL_ANSWER\nTask name: /root\nSender: '):
                header, message = text.split('\nPayload:\n', 1)
                deliveries.append((header.split('Sender: ', 1)[1], message.strip(), call))
    failures = [p for p in commands if isinstance(p['item'].get('exitCode'), int) and p['item']['exitCode'] != 0
                and p['item'].get('aggregatedOutput')]
    failure_proofs = []
    for p in failures:
        item = p['item']; output = item['aggregatedOutput'].strip()
        matches = [(call, value['projection']) for thread, call, value in parsed_outputs if thread == p.get('threadId')
                   and (value['exit_code'] == item['exitCode'] or value['projection'] == 'model_selected_output_only')
                   and value['output'].strip() == output]
        if matches:
            failure_proofs.append({'item_id': item['id'], 'thread_id': p['threadId'], 'exit_code': item['exitCode'],
                                   'output_sha256': digest(output), 'provider_call_ids': sorted({x[0] for x in matches}),
                                   'projections': sorted({x[1] for x in matches}),
                                   'model_received_numeric_exit_code': any(x[1] != 'model_selected_output_only' for x in matches)})
    # At least one complete nonzero-output round trip, not a count of tool calls.
    failure_delivery = bool(failure_proofs) if failures else root.name == 'jinja'
    database = next((root/'home').glob('state_*.sqlite')); bind(database)
    for path in database.parent.glob(database.name+'-wal'): bind(path)
    reviews = []
    with closing(readonly(database)) as db, db:
        rows = db.execute('SELECT id,source,rollout_path,agent_path FROM threads WHERE agent_path IS NOT NULL').fetchall()
    for child, source, rollout_path, agent_path in rows:
        source = json.loads(source) if source.startswith('{') else {}
        if source.get('subagent', {}).get('thread_spawn', {}).get('parent_thread_id') != parent:
            continue
        rollout = canonical(Path(rollout_path))
        if not rollout.is_relative_to(root/'home'):
            continue
        history = [json.loads(x) for x in bind(rollout).read_text(encoding='utf-8').splitlines()]
        review = review_delivery_evidence(history, events, child, agent_path, deliveries,
                                          report.get('final_turn_id'))
        seen_child = any(x['thread'] == child for x in provider_requests.values())
        reviews.append({'thread_id': child, 'agent_path': agent_path, 'rollout_sha256': sha(rollout),
                        **review, 'passed': review['passed'] and seen_child})
    state = root/'home/provider-state/adapter.sqlite'; bind(state)
    for path in state.parent.glob(state.name+'-wal'): bind(path)
    with closing(readonly(state)) as db, db:
        receipts = db.execute('SELECT id,status,upstream_status,provider,context_key,route_reason FROM receipts ORDER BY id').fetchall()
        observations = [dict(zip(('id', 'started', 'finished', 'detail'), row)) for row in db.execute(
            'SELECT id,started,finished,detail FROM receipts ORDER BY id')]
    provider_proof = provider_transport_evidence(provider_requests, receipts, installed['model'],
        recovery_evidence={'receipt_observations': observations, 'native_events': events,
                           'controller_connections': [{'requests': requests, 'events': group}
                                                      for requests, group in zip(sessions, event_groups)]
                                                      if len(sessions) == len(event_groups) else None})
    controls = report.get('fixture_inputs', {})
    current = real_work_input_hashes(root, root.name)
    for name in real_work_input_names(root.name): bind(root/name)
    inputs_ok = controls.get('unchanged') is True and controls.get('before') == controls.get('after') == current
    oracle_guard = report.get('oracle_guard', {})
    oracle_ok = all(oracle_guard.get(k) is True for k in ('read_probe_denied', 'restored_probe_unchanged', 'source_unchanged'))
    oracle_files = {p.relative_to(root/'oracle').as_posix(): sha(p) for p in sorted((root/'oracle').rglob('*'))
                    if p.is_file() and not any(x in {'__pycache__', '.pytest_cache', '.git'} for x in p.parts)}
    from acceptance_assertions import pytest_execution_evidence,real_work_pytest_executed
    suites = []
    for suite in report['validation']:
        label = suite['suite']; imports = load(root/f'final-{label}-imports.json'); text = bind(root/f'final-{label}.txt').read_text(encoding='utf-8')
        pytest_suite=label in {'workspace','upstream-heldout','independent-cancellation'}
        executed=pytest_execution_evidence(bind(root/f'final-{label}.junit.xml')) if pytest_suite else None
        execution_ok=(not pytest_suite or (executed==suite.get('pytest_execution')
                      and real_work_pytest_executed({**suite,'pytest_execution':executed})))
        pytest_ok=(not pytest_suite or (execution_ok and executed['counts']['failure']==executed['counts']['error']==0))
        suites.append({'suite': label, 'exit': suite['exit'],
                       'pytest_execution':executed,'test_execution_verified':execution_ok,'pytest_application_passed':pytest_ok, 'tail': suite.get('tail'), 'output_sha256': sha(root/f'final-{label}.txt'),
                       'source_imports_verified': source_imports(root, label, imports),
                       'receipt_matches_report': imports == suite.get('source_imports'),
                       'output_matches_report': text.endswith(suite.get('tail', '')) and bool(suite.get('tail'))})
    required = {'workspace', 'upstream-heldout'}
    if root.name == 'httpcore': required |= {'independent-cancellation', 'real-tcp-0', 'real-tcp-1', 'real-tcp-2', 'tls-socket'}
    validation = set(x['suite'] for x in suites) == required and all(
        x['source_imports_verified'] and x['receipt_matches_report'] and x['output_matches_report']
        and x['test_execution_verified'] for x in suites)
    from acceptance_assertions import native_host_execution
    tool_proof=tool_environment_evidence(root,report)
    host = native_host_execution(events, report['native_host_execution']['declared_python'], report['host_platform'])
    mechanisms = {
        'immutable_artifact': {'passed': immutable, 'source_id': installed['source_id'], 'native_manifest_sha256': sha(release/'native-runtime.json')},
        'verifier_provenance': {'passed': verifier_matches, 'active_producer_sha256': active_artifact.get('verifier_sha256'), 'historical_producers': [a.get('verifier_sha256') for a in artifacts], 'previous_report_link': previous_report_link}, 'isolated_launch': {'passed': launch_ok},
        'native_lifecycle': life, 'file_effect': {**effect_proof, 'patch_sha256': sha(root/'agent.patch')},
        'nonzero_tool_output_delivery': {'passed': failure_delivery, 'proofs': failure_proofs,
            'scope': 'Exact native nonzero command output delivered in model input. Numeric exit delivery and explicit model-selected output-only projections are recorded separately; representative failure round trips.'},
        'reviewer_lifecycle': {'passed': any(x['passed'] for x in reviews), 'reviewers': reviews},
        'linked_browser_navigation': web, 'provider_transport': provider_proof,
        'controlled_inputs': {'passed': inputs_ok, 'sha256': current},
        'tool_environment': tool_proof,
        'oracle_isolation': {'passed': oracle_ok and bool(oracle_files) and verifier_matches,
            'recorded_guard': oracle_guard, 'current_tree_sha256': digest(oracle_files),
            'provenance': 'Historical before/after and denied-read observations from hash-matched task verifier; current tree bound separately.'},
        'suite_execution_evidence': {'passed': validation}, 'native_host_execution': host,
    }
    runtime_passed = all(x.get('passed') is True for x in mechanisms.values())
    mechanisms['passed'] = runtime_passed
    application = application_outcome(report, suites, validation)
    return {'collector_version': VERSION, 'collector_sha256': {name: sha(Path(__file__).with_name(name)) for name in HELPERS},
            'case': root.name, 'fixture_root': str(root), 'entry': str(entry), 'installation': installed,
            'artifact_sha256': sha(root/'artifact.json'),
            'original_report_sha256': sha(root/'results.json'), 'raw_sha256': dict(sorted(raw.items())),
            'runtime_mechanisms': mechanisms, 'runtime_mechanisms_passed': runtime_passed,
            'application_task_outcome': application}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--entry', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not __debug__: raise SystemExit('Active assertions required')
    args.output.mkdir(parents=True, exist_ok=False)
    cases = {name: reconstruct_case(args.fixtures/name, args.entry) for name in ('jinja', 'httpcore')}
    for name, result in cases.items():
        (args.output/(name+'.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    summary = {'collector_version': VERSION, 'cases': {name: {
        'runtime_mechanisms_passed': x['runtime_mechanisms_passed'],
        'application_task_passed': x['application_task_outcome']['application_task_passed'],
        'proof_sha256': sha(args.output/(name+'.json'))} for name, x in cases.items()}}
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))
    raise SystemExit(0 if all(x['runtime_mechanisms_passed'] for x in cases.values()) else 1)


if __name__ == '__main__': main()
