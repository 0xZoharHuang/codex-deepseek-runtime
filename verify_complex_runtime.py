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
import subprocess

from acceptance_assertions import (complete_work_patch, current_browser_evidence,
                                   real_work_input_hashes, real_work_input_names)
from install import valid_release, _support

canonical = _support.comparison_path

VERSION = 1
HELPERS = ('verify_complex_runtime.py', 'acceptance_assertions.py', 'install.py',
           'host_execution_acceptance.py', 'real_work_imports.py')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


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


def lifecycle(sessions, event_groups, parent, *, require_resume=True):
    """Bind cancellation and cold resume to their actual RPC connection and turn."""
    interrupts, resumes, defects = [], [], []
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
        if not started_turns or any((parent, turn) not in terminals for turn in started_turns):
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
    return {'passed': not defects and (bool(resumes) or not require_resume) and all(x['passed'] for x in interrupts + resumes),
            'interrupts': interrupts, 'cold_resumes': resumes, 'defects': defects}


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
            and not report.get('error') and validation and all(x['exit'] == 0 for x in suites)}


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
    required_producers = {'verify_real_work.py', 'verify_live.py', 'acceptance_assertions.py', 'host_execution_acceptance.py', 'real_work_imports.py'}
    if report.get('followup'): required_producers.add('verify_real_work_followup.py')
    verifier_matches = previous_report_link and set(active_artifact.get('verifier_sha256', {})) == required_producers and all(
        Path(__file__).with_name(name).is_file() and sha(Path(__file__).with_name(name)) == value
        for name, value in active_artifact.get('verifier_sha256', {}).items())
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
    life = lifecycle(sessions, event_groups, parent, require_resume=root.name == 'httpcore')
    if root.name == 'httpcore' and not life.get('interrupts'):
        life['passed'] = False
    completed = [e['params'] for e in events if e.get('method') == 'item/completed']
    commands = [p for p in completed if p['item'].get('type') == 'commandExecution']
    changed = [p['item'] for p in completed if p.get('threadId') == parent
               and p['item'].get('type') == 'fileChange' and p['item'].get('status') == 'completed']
    patch = bind(root/'agent.patch').read_bytes()
    patch_ok = bool(patch) and complete_work_patch(root/'work') == patch
    paths = sorted({str(canonical(Path(c['path']))) for x in changed for c in x.get('changes', [])})
    effect_paths = {Path(p).relative_to(root/'work').as_posix(): sha(p) for p in paths
                    if Path(p).is_relative_to(root/'work') and Path(p).is_file()}
    effect = patch_ok and bool(effect_paths) and any(
        Path(p).relative_to(root/'work').as_posix().encode() in patch for p in paths if Path(p).is_relative_to(root/'work'))
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
    with readonly(database) as db:
        rows = db.execute('SELECT id,source,rollout_path,agent_path FROM threads WHERE agent_path IS NOT NULL').fetchall()
    for child, source, rollout_path, agent_path in rows:
        source = json.loads(source) if source.startswith('{') else {}
        if source.get('subagent', {}).get('thread_spawn', {}).get('parent_thread_id') != parent:
            continue
        rollout = canonical(Path(rollout_path))
        if not rollout.is_relative_to(root/'home'):
            continue
        history = [json.loads(x) for x in bind(rollout).read_text(encoding='utf-8').splitlines()]
        messages = [x['payload'].get('last_agent_message') for x in history if x.get('type') == 'event_msg'
                    and x.get('payload', {}).get('type') == 'task_complete']
        terminals = [e['params']['turn'] for e in events if e.get('method') == 'turn/completed'
                     and e['params'].get('threadId') == child and e['params']['turn'].get('status') == 'completed'
                     and e['params']['turn'].get('error') is None]
        delivered = [call for path, text, call in deliveries if path == agent_path and text in messages]
        seen_child = any(x['thread'] == child for x in provider_requests.values())
        reviews.append({'thread_id': child, 'agent_path': agent_path, 'rollout_sha256': sha(rollout),
                        'delivered_call_ids': sorted(set(delivered)), 'completed_turn_ids': [t['id'] for t in terminals],
                        'passed': bool(messages and terminals and delivered and seen_child)})
    state = root/'home/provider-state/adapter.sqlite'; bind(state)
    for path in state.parent.glob(state.name+'-wal'): bind(path)
    with readonly(state) as db:
        receipts = db.execute('SELECT id,status,upstream_status,provider,context_key,route_reason FROM receipts ORDER BY id').fetchall()
    observed_receipts = [r for r in receipts if r[0] in provider_requests]
    routes = [{'status': r[1], 'upstream_status': r[2], 'provider': r[3], 'route_reason': r[5]} for r in observed_receipts]
    provider_ok = (bool(observed_receipts) and all(r[1] in {'completed', 'cancelled'} for r in observed_receipts)
                   and all(r[2] == 200 and r[3] for r in observed_receipts if r[1] == 'completed')
                   and all(x['model'] == installed['model'] for x in provider_requests.values()))
    controls = report.get('fixture_inputs', {})
    current = real_work_input_hashes(root, root.name)
    for name in real_work_input_names(root.name): bind(root/name)
    inputs_ok = controls.get('unchanged') is True and controls.get('before') == controls.get('after') == current
    oracle_guard = report.get('oracle_guard', {})
    oracle_ok = all(oracle_guard.get(k) is True for k in ('read_probe_denied', 'restored_probe_unchanged', 'source_unchanged'))
    oracle_files = {p.relative_to(root/'oracle').as_posix(): sha(p) for p in sorted((root/'oracle').rglob('*'))
                    if p.is_file() and not any(x in {'__pycache__', '.pytest_cache', '.git'} for x in p.parts)}
    suites = []
    for suite in report['validation']:
        label = suite['suite']; imports = load(root/f'final-{label}-imports.json'); text = bind(root/f'final-{label}.txt').read_text(encoding='utf-8')
        suites.append({'suite': label, 'exit': suite['exit'], 'tail': suite.get('tail'), 'output_sha256': sha(root/f'final-{label}.txt'),
                       'source_imports_verified': source_imports(root, label, imports),
                       'receipt_matches_report': imports == suite.get('source_imports'),
                       'output_matches_report': text.endswith(suite.get('tail', '')) and bool(suite.get('tail'))})
    required = {'workspace', 'upstream-heldout'}
    if root.name == 'httpcore': required |= {'independent-cancellation', 'real-tcp-0', 'real-tcp-1', 'real-tcp-2', 'tls-socket'}
    validation = set(x['suite'] for x in suites) == required and all(
        x['source_imports_verified'] and x['receipt_matches_report'] and x['output_matches_report'] for x in suites)
    from acceptance_assertions import native_host_execution
    host = native_host_execution(events, report['native_host_execution']['declared_python'], report['host_platform'])
    mechanisms = {
        'immutable_artifact': {'passed': immutable, 'source_id': installed['source_id'], 'native_manifest_sha256': sha(release/'native-runtime.json')},
        'verifier_provenance': {'passed': verifier_matches, 'active_producer_sha256': active_artifact.get('verifier_sha256'), 'historical_producers': [a.get('verifier_sha256') for a in artifacts], 'previous_report_link': previous_report_link}, 'isolated_launch': {'passed': launch_ok},
        'native_lifecycle': life, 'file_effect': {'passed': effect, 'patch_sha256': sha(root/'agent.patch'), 'files': effect_paths},
        'nonzero_tool_output_delivery': {'passed': failure_delivery, 'proofs': failure_proofs,
            'scope': 'Exact native nonzero command output delivered in model input. Numeric exit delivery and explicit model-selected output-only projections are recorded separately; representative failure round trips.'},
        'reviewer_lifecycle': {'passed': any(x['passed'] for x in reviews), 'reviewers': reviews},
        'linked_browser_navigation': web, 'provider_transport': {'passed': provider_ok, 'receipt_rows_sha256': digest(receipts),
            'routes': sorted({json.dumps(r, sort_keys=True) for r in routes})},
        'controlled_inputs': {'passed': inputs_ok, 'sha256': current},
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
