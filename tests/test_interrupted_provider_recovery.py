"""An interrupted turn proves request recovery only through its original RPC connection."""
from copy import deepcopy
import pytest
from verify_complex_runtime import provider_transport_evidence
from test_complex_runtime import recovery_rows


def interrupted_rows():
    model, requests, receipts, evidence = recovery_rows()
    retry = evidence['native_events'][0]
    terminal = evidence['native_events'][1]
    terminal['params']['turn'].update(status='interrupted', error=None)
    terminal['emittedAtMs'] = 7000
    tool = {'id': 'native-command-1', 'type': 'commandExecution', 'status': 'completed',
            'command': 'rg --files', 'exitCode': 0, 'aggregatedOutput': 'actual.py'}
    started = {'method': 'item/started', 'params': {'threadId': 'parent', 'turnId': 'original',
               'item': dict(tool, status='inProgress'), 'startedAtMs': 5500}, 'emittedAtMs': 5501}
    completed = {'method': 'item/completed', 'params': {'threadId': 'parent', 'turnId': 'original',
                 'item': tool, 'completedAtMs': 5600}, 'emittedAtMs': 5601}
    evidence['native_events'] = [
        {'id': 1, 'result': {'codexHome': 'owned'}},
        {'id': 2, 'result': {'turn': {'id': 'original', 'status': 'inProgress', 'error': None}}},
        retry, started, completed, {'id': 3, 'result': {}}, terminal]
    evidence['controller_connections'] = [{
        'requests': [{'id': 1, 'method': 'initialize', 'params': {}},
                     {'method': 'initialized'},
                     {'id': 2, 'method': 'turn/start', 'params': {'threadId': 'parent'}},
                     {'id': 3, 'method': 'turn/interrupt', 'params': {'threadId': 'parent', 'turnId': 'original'}}],
        'events': evidence['native_events']}]
    return model, requests, receipts, evidence


def test_request_recovery_then_explicit_interrupt_has_its_own_preserved_classification():
    model, requests, receipts, evidence = interrupted_rows()
    proof = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
    assert proof['passed'] and not proof['single_run_all_success']
    assert proof['original_error_receipt_ids'] == proof['recovered_error_receipt_ids'] == ['failure']
    assert proof['unresolved_or_failed_receipt_ids'] == []
    error = proof['errors'][0]
    assert error == {'id': 'failure', 'status': 'adapter_error', 'upstream_status': 200,
                     'provider': 'deepseek', 'detail': evidence['receipt_observations'][0]['detail'],
                     'route_reason': 'official_default', 'classification': 'request_recovered_then_explicit_interrupt'}
    link = proof['recovered_errors']['failure']
    assert link['completed_request_id'] == 'success' and link['interrupt_rpc_id'] == 3
    assert link['start_rpc_id'] == 2 and link['terminal_event']['params']['turn']['status'] == 'interrupted'
    assert link['subsequent_native_tools'][0]['item_id'] == 'native-command-1'
    assert 'Request-send wall time is not recorded' in link['timing_scope']


@pytest.mark.parametrize('change', [
    'missing-interrupt', 'fake-passed-only', 'failed-interrupt', 'duplicate-interrupt-request',
    'duplicate-interrupt-reply', 'missing-interrupt-reply', 'failed-start', 'missing-start',
    'duplicate-start', 'wrong-start-turn', 'wrong-start-thread', 'completed-start',
    'wrong-interrupt-turn', 'wrong-interrupt-thread', 'interrupt-before-start',
    'other-connection-interrupt', 'duplicate-terminal', 'failed-terminal', 'terminal-error',
    'terminal-missing-error', 'terminal-other-turn', 'terminal-other-thread',
    'no-success', 'success-other-turn', 'success-other-thread', 'success-after-interrupt',
    'success-after-tool', 'success-bad-http', 'no-tool', 'tool-agent-message', 'tool-not-completed',
    'tool-other-turn', 'tool-other-thread', 'tool-after-interrupt', 'tool-after-terminal-time',
    'tool-before-success', 'tool-no-start', 'tool-duplicate-start', 'tool-duplicate-completion',
    'tool-no-id', 'tool-no-command', 'tool-no-exit', 'tool-bool-time', 'tool-malformed-params',
    'unknown-native-error', 'unknown-receipt-error', 'only-later-turn-success',
    'flatten-different', 'flatten-reordered', 'missing-connections', 'malformed-connections',
    'extra-evidence-field', 'unmatched-response', 'interrupt-extra-result', 'interruption-reply-after-terminal',
    'tool-start-before-retry', 'tool-start-before-success', 'tool-start-after-completion',
    'tool-start-missing-time', 'tool-start-bool-time', 'tool-start-wrong-status',
    'duplicate-rpc-id-without-method', 'extra-request-id-without-method', 'empty-request-method',
    'duplicate-start-bad-status', 'duplicate-start-bad-time', 'duplicate-start-bad-type'
])
def test_interrupted_recovery_rejects_missing_or_ambiguous_raw_causality(change):
    model, requests, receipts, evidence = interrupted_rows()
    connection = evidence['controller_connections'][0]; calls = connection['requests']; events = evidence['native_events']
    start = events[1]; retry = events[2]; began = events[3]; tool = events[4]; interrupt = events[5]; terminal = events[6]
    if change == 'missing-interrupt': calls.pop(); events.remove(interrupt)
    elif change == 'fake-passed-only': evidence['controller_connections'] = [{'passed': True}]
    elif change == 'failed-interrupt': interrupt['error'] = {'message': 'rejected'}
    elif change == 'duplicate-interrupt-request': calls.append(deepcopy(calls[-1]))
    elif change == 'duplicate-interrupt-reply': events.insert(5, deepcopy(interrupt))
    elif change == 'missing-interrupt-reply': events.remove(interrupt)
    elif change == 'failed-start': start['error'] = {'message': 'failed'}
    elif change == 'missing-start': calls.pop(2); events.remove(start)
    elif change == 'duplicate-start':
        calls.insert(2, {'id': 4, 'method': 'turn/start', 'params': {'threadId': 'parent'}})
        events.insert(1, {'id': 4, 'result': deepcopy(start['result'])})
    elif change == 'wrong-start-turn': start['result']['turn']['id'] = 'other'
    elif change == 'wrong-start-thread': calls[2]['params']['threadId'] = 'other'
    elif change == 'completed-start': start['result']['turn']['status'] = 'completed'
    elif change == 'wrong-interrupt-turn': calls[-1]['params']['turnId'] = 'other'
    elif change == 'wrong-interrupt-thread': calls[-1]['params']['threadId'] = 'other'
    elif change == 'interrupt-before-start': calls[2], calls[3] = calls[3], calls[2]
    elif change == 'other-connection-interrupt':
        calls.pop(); events.remove(interrupt)
        other = {'requests': [{'id': 7, 'method': 'initialize'}, {'id': 3, 'method': 'turn/interrupt', 'params': {'threadId': 'parent', 'turnId': 'original'}}],
                 'events': [{'id': 7, 'result': {}}, interrupt]}
        evidence['controller_connections'].append(other); evidence['native_events'] = events + other['events']
    elif change == 'duplicate-terminal': events.append(deepcopy(terminal))
    elif change == 'failed-terminal': terminal['params']['turn']['status'] = 'failed'
    elif change == 'terminal-error': terminal['params']['turn']['error'] = {'message': 'unexpected failure'}
    elif change == 'terminal-missing-error': terminal['params']['turn'].pop('error')
    elif change == 'terminal-other-turn': terminal['params']['turn']['id'] = 'other'
    elif change == 'terminal-other-thread': terminal['params']['threadId'] = 'other'
    elif change == 'no-success': receipts[1] = ('success', 'cancelled', 200, 'deepseek', 'shared-cache', 'official_default')
    elif change in {'success-other-turn', 'only-later-turn-success'}:
        requests['success']['turn'] = 'later'
        if change == 'only-later-turn-success':
            events.append({'method':'turn/completed','params':{'threadId':'parent','turn':{'id':'later','status':'completed','error':None}},'emittedAtMs':9000})
    elif change == 'success-other-thread': requests['success']['thread'] = 'child'
    elif change == 'success-after-interrupt': evidence['receipt_observations'][1].update(started=7.5, finished=8.)
    elif change == 'success-after-tool': evidence['receipt_observations'][1]['finished'] = 6.
    elif change == 'success-bad-http': receipts[1] = ('success', 'completed', 503, 'deepseek', 'shared-cache', 'official_default')
    elif change == 'no-tool': events.remove(tool)
    elif change == 'tool-agent-message': tool['params']['item']['type'] = 'agentMessage'
    elif change == 'tool-not-completed': tool['params']['item']['status'] = 'failed'
    elif change == 'tool-other-turn': tool['params']['turnId'] = 'other'
    elif change == 'tool-other-thread': tool['params']['threadId'] = 'child'
    elif change == 'tool-after-interrupt': events.remove(tool); events.insert(events.index(interrupt) + 1, tool)
    elif change == 'tool-after-terminal-time': tool['params']['completedAtMs'] = 7100; tool['emittedAtMs'] = 7101
    elif change == 'tool-before-success': tool['params']['completedAtMs'] = 4500; tool['emittedAtMs'] = 4501
    elif change == 'tool-start-before-retry':
        events.remove(began); events.insert(events.index(retry), began); began['params']['startedAtMs'] = 1500; began['emittedAtMs'] = 1501
    elif change == 'tool-start-before-success': began['params']['startedAtMs'] = 4500; began['emittedAtMs'] = 4501
    elif change == 'tool-start-after-completion': began['params']['startedAtMs'] = 9000; began['emittedAtMs'] = 9001
    elif change == 'tool-start-missing-time': began['params'].pop('startedAtMs')
    elif change == 'tool-start-bool-time': began['params']['startedAtMs'] = True
    elif change == 'tool-start-wrong-status': began['params']['item']['status'] = 'failed'
    elif change == 'duplicate-rpc-id-without-method': calls.append({'id':3,'params':{}})
    elif change == 'extra-request-id-without-method': calls.append({'id':99,'params':{}})
    elif change == 'empty-request-method': calls[-1]['method'] = ''
    elif change == 'tool-no-start': events.remove(began)
    elif change == 'tool-duplicate-start': events.insert(3, deepcopy(began))
    elif change.startswith('duplicate-start-bad-'):
        duplicate = deepcopy(began)
        if change == 'duplicate-start-bad-status': duplicate['params']['item']['status'] = 'completed'
        elif change == 'duplicate-start-bad-time': duplicate['params']['startedAtMs'] = 9000
        else: duplicate['params']['item']['type'] = 'agentMessage'
        events.insert(3, duplicate)
    elif change == 'tool-duplicate-completion': events.insert(4, deepcopy(tool))
    elif change == 'tool-no-id': tool['params']['item'].pop('id')
    elif change == 'tool-no-command': tool['params']['item'].pop('command')
    elif change == 'tool-no-exit': tool['params']['item'].pop('exitCode')
    elif change == 'tool-bool-time': tool['params']['completedAtMs'] = True
    elif change == 'tool-malformed-params': tool['params'] = None
    elif change == 'unknown-native-error': events.insert(5, {'method': 'error', 'params': {'threadId': 'parent', 'turnId': 'original', 'willRetry': False, 'error': {'message': 'unknown'}}, 'emittedAtMs': 6000})
    elif change == 'unknown-receipt-error':
        requests['unknown'] = dict(requests['failure']); receipts.append(('unknown', 'unknown_failure', 200, 'deepseek', 'shared-cache', 'official_default'))
        evidence['receipt_observations'].append({'id': 'unknown', 'started': 5.7, 'finished': 5.8, 'detail': 'unknown'})
    elif change == 'flatten-different': connection['events'] = deepcopy(events); connection['events'][0]['result'] = {'not': 'same'}
    elif change == 'flatten-reordered': connection['events'] = list(reversed(events))
    elif change == 'missing-connections': evidence['controller_connections'] = None
    elif change == 'malformed-connections': evidence['controller_connections'] = {'requests': calls, 'events': events}
    elif change == 'extra-evidence-field': evidence['passed'] = True
    elif change == 'unmatched-response': events.insert(0, {'id': 999, 'result': {}})
    elif change == 'interrupt-extra-result': interrupt['result'] = {'accepted': False}
    elif change == 'interruption-reply-after-terminal': events.remove(interrupt); events.append(interrupt)
    else: raise AssertionError(change)
    proof = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
    assert proof['passed'] is False, (change, proof)
    assert proof['single_run_all_success'] is False


def test_old_three_argument_and_two_field_contracts_remain_strict():
    for interrupted in (False, True):
        model, requests, receipts, evidence = interrupted_rows() if interrupted else recovery_rows()
        evidence.pop('controller_connections', None)
        new = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
        assert new['passed'] is (not interrupted)
        assert new['provider_evidence_version'] == 2 and new['single_run_all_success'] is False
        assert new['errors'][0]['classification'] == ('unrecovered' if interrupted else 'recovered')
        assert not any('interrupt_rpc_id' in link for link in new['recovered_errors'].values())
        legacy = provider_transport_evidence(requests, receipts, model)
        assert not legacy['passed'] and legacy['unresolved_or_failed_receipt_ids'] == ['failure']
        assert legacy['errors'][0]['classification'] == 'unrecovered'


@pytest.mark.parametrize('identifier', [8, '8', 3])
@pytest.mark.parametrize('reply_kind', ['result', 'error'])
def test_native_callbacks_have_an_independent_typed_rpc_id_space(identifier, reply_kind):
    model, requests, receipts, evidence = interrupted_rows()
    call = {'id': identifier, 'method': 'item/tool/requestUserInput', 'params': {'questions': []}}
    answer = ({'id': identifier, 'result': {'answers': {}}} if reply_kind == 'result' else
              {'id': identifier, 'error': {'code': -32601, 'message': 'Fixture declines unsupported interaction'}})
    evidence['controller_connections'][0]['requests'].insert(3, answer)
    evidence['native_events'].insert(3, call)
    proof = provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)
    assert proof['passed'] and not proof['single_run_all_success']
    assert proof['errors'][0]['classification'] == 'request_recovered_then_explicit_interrupt'


def test_native_callback_integer_and_string_ids_remain_distinct():
    model, requests, receipts, evidence = interrupted_rows()
    for identifier in [1, '1']:
        evidence['controller_connections'][0]['requests'].insert(3, {'id': identifier, 'result': {}})
        evidence['native_events'].insert(3, {'id': identifier, 'method': 'item/commandExecution/requestApproval', 'params': {}})
    assert provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)['passed']


@pytest.mark.parametrize('change', ['unmatched-answer', 'missing-answer', 'duplicate-answer', 'duplicate-callback',
    'conflicting-answer', 'answer-extra-params', 'answer-wrong-type', 'bool-callback-id', 'float-callback-id',
    'bad-callback-method', 'client-counter-string', 'client-counter-bool', 'duplicate-interrupt-no-id',
    'duplicate-bad-completion', 'duplicate-bad-terminal', 'duplicate-bad-start-reply'])
def test_bidirectional_rpc_and_target_event_uniqueness_fail_closed(change):
    model, requests, receipts, evidence = interrupted_rows()
    calls = evidence['controller_connections'][0]['requests']; events = evidence['native_events']
    callback = {'id': 'callback-7', 'method': 'item/tool/requestUserInput', 'params': {'questions': []}}
    answer = {'id': 'callback-7', 'result': {'answers': {}}}
    calls.insert(3, answer); events.insert(3, callback)
    if change == 'unmatched-answer': events.remove(callback)
    elif change == 'missing-answer': calls.remove(answer)
    elif change == 'duplicate-answer': calls.append(deepcopy(answer))
    elif change == 'duplicate-callback': events.insert(3, deepcopy(callback))
    elif change == 'conflicting-answer': answer['error'] = {'code': -1, 'message': 'conflict'}
    elif change == 'answer-extra-params': answer['params'] = {}
    elif change == 'answer-wrong-type': callback['id'] = 1; answer['id'] = '1'
    elif change == 'bool-callback-id': callback['id'] = answer['id'] = True
    elif change == 'float-callback-id': callback['id'] = answer['id'] = 1.5
    elif change == 'bad-callback-method': callback['method'] = None
    elif change in {'client-counter-string', 'client-counter-bool'}:
        value = '2' if change == 'client-counter-string' else True
        calls[2]['id'] = value; events[1]['id'] = value
    elif change == 'duplicate-interrupt-no-id': calls.append({'method':'turn/interrupt','params':{'threadId':'parent','turnId':'original'}})
    elif change == 'duplicate-bad-completion':
        duplicate = deepcopy(next(x for x in events if x.get('method') == 'item/completed'))
        duplicate['params']['item']['status'] = 'failed'; duplicate['params']['completedAtMs'] = 9000
        events.insert(5, duplicate)
    elif change == 'duplicate-bad-terminal':
        duplicate = deepcopy(events[-1]); duplicate['params']['turn']['status'] = 'failed'
        events.append(duplicate)
    elif change == 'duplicate-bad-start-reply':
        duplicate = deepcopy(events[1]); duplicate['result']['turn']['status'] = 'failed'
        events.insert(1, duplicate)
    else: raise AssertionError(change)
    assert provider_transport_evidence(requests, receipts, model, recovery_evidence=evidence)['passed'] is False
