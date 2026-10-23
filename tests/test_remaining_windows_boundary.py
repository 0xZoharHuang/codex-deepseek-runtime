from concurrent.futures import Future
from copy import deepcopy
import pytest
from verify_remaining_local import (
    WINDOWS_COMMAND_BOUNDARY_ID,WINDOWS_COMMAND_TARGET,WINDOWS_COMMAND_SCOPE,
    WINDOWS_STREAM_ERROR,WINDOWS_CONTROL_ERROR,windows_command_boundary_detail_valid,
    wait_command_output_or_error,exact_rpc_error,
)

def valid():
    return {'boundary_id':WINDOWS_COMMAND_BOUNDARY_ID,'target':WINDOWS_COMMAND_TARGET,
            'scope':dict(WINDOWS_COMMAND_SCOPE),'workspace':'D:/owned/work','sandbox_policy':{'type':'workspaceWrite','writableRoots':['D:/owned/work'],'networkAccess':False,'excludeTmpdirEnvVar':True,'excludeSlashTmp':True},'streaming_error':dict(WINDOWS_STREAM_ERROR),
            'terminate_error':dict(WINDOWS_CONTROL_ERROR),
            'active_command':{'process_id':'native-windows-boundary','ready_nonce':'a'*32,
                 'ready_file_sha256':'b'*64,'exit_code':0,'stdout':'a'*32+'\n',
                 'sid':'S-1-5-21-1-2-3-1002','owner_sid':'S-1-5-21-1-2-3-1001','is_admin':False},
            'reference':{'path':'reference.json','sha256':'c'*64}}

def test_exact_scoped_boundary_is_valid():
    assert windows_command_boundary_detail_valid(valid(),WINDOWS_COMMAND_TARGET)

@pytest.mark.parametrize('change',[
    lambda d:d.update(target='x86_64-unknown-linux-gnu'),
    lambda d:d.update(boundary_id='generic_error'),
    lambda d:d['scope'].update(sandbox_mode='danger-full-access'),
    lambda d:d['scope'].update(windows_backend='unelevated'),
    lambda d:d['scope'].update(network_access=True),
    lambda d:d['scope'].update(network_access=0),
    lambda d:d['sandbox_policy'].update(writableRoots=[]),
    lambda d:d['sandbox_policy'].update(writableRoots=['D:/other']),
    lambda d:d['sandbox_policy'].update(networkAccess=True),
    lambda d:d['streaming_error'].update(code=-32603),
    lambda d:d['terminate_error'].update(message='process is not running'),
    lambda d:d['active_command'].update(exit_code=False),
    lambda d:d['active_command'].update(stdout='different nonce'),
    lambda d:d['active_command'].update(is_admin=True),
    lambda d:d['active_command'].update(sid='S-1-5-21-1-2-3-1001'),
    lambda d:d['reference'].update(sha256=''),
])
def test_unrelated_error_or_weaker_scope_cannot_pass(change):
    value=valid();change(value)
    assert not windows_command_boundary_detail_valid(value,WINDOWS_COMMAND_TARGET)

def test_platform_cannot_claim_boundary_for_other_target():
    assert not windows_command_boundary_detail_valid(valid(),'aarch64-apple-darwin')

def test_finished_rpc_failure_is_reported_before_waiting_for_output():
    future=Future();future.set_exception(RuntimeError('exact native rejection'))
    class Peer:
        def wait(self,*args):raise AssertionError('must not wait for absent output')
    with pytest.raises(RuntimeError,match='exact native rejection'):
        wait_command_output_or_error(Peer(),0,future)

def test_error_event_propagates_future_error_instead_of_timeout():
    future=Future()
    class Peer:
        def wait(self,predicate,start,timeout):
            event={'id':8,'error':dict(WINDOWS_STREAM_ERROR)}
            assert predicate(event)
            future.set_exception(RuntimeError('exact native rejection'))
            return event
    with pytest.raises(RuntimeError,match='exact native rejection'):
        wait_command_output_or_error(Peer(),0,future)

def test_exact_error_requires_raw_response_match():
    class Peer:
        events=[]
        def call(self,*args):
            self.events.append({'id':1,'error':{'code':-32603,'message':WINDOWS_STREAM_ERROR['message']}})
            raise RuntimeError('wrong code')
    with pytest.raises(RuntimeError,match='wrong code'):
        exact_rpc_error(Peer(),'command/exec',{},WINDOWS_STREAM_ERROR)

def test_unexpected_success_never_becomes_platform_boundary():
    class Peer:
        events=[]
        def call(self,*args):return {}
    with pytest.raises(AssertionError,match='unexpectedly succeeded'):
        exact_rpc_error(Peer(),'command/exec',{},WINDOWS_STREAM_ERROR)
