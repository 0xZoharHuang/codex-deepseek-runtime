
"""Windows host PTY success cannot disguise a restricted legacy API boundary."""
import base64
import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('runtime_contract_under_test',Path(__file__).resolve().parents[1]/'verify_runtime_contract.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class Peer:
    sandbox_mode='workspace-write'
    def __init__(self,mutation=None):
        self.mutation=mutation;self.counter=0;self.events=[];self.requests=[]
    def output(self,handle,data,stream='stdout'):
        self.events.append({'method':'process/outputDelta','params':{
            'processHandle':handle,'stream':stream,'deltaBase64':base64.b64encode(data).decode()}})
    def call(self,method,params):
        self.counter+=1;self.requests.append({'id':self.counter,'method':method,'params':params})
        if method=='config/read':
            return {'config':{'sandbox_mode':'danger-full-access' if self.mutation=='full-access' else 'workspace-write','windows':{'sandbox':'elevated'}}}
        if method=='command/exec':
            if self.mutation=='boundary-success':return {}
            error=dict(module.WINDOWS_STREAM_ERROR)
            if self.mutation=='wrong-error':error['code']=-32603
            self.events.append({'id':self.counter+(1 if self.mutation=='foreign-error-id' else 0),'error':error})
            raise RuntimeError('native error')
        handle=params['processHandle']
        if method=='process/spawn':
            self.output(handle,b'KILL_READY\n' if handle.endswith('kill') else b'READY\n')
        elif method=='process/writeStdin':
            assert base64.b64decode(params['deltaBase64'])==b'RUNTIME_PTY_9341\n'
            output_handle='foreign' if self.mutation=='foreign-handle' else handle
            stream='stderr' if self.mutation=='stderr-only' else 'stdout'
            payload=b'SIZE:32:120\r\nRESULT:RUNTIME_PTY_9341\r\n'
            if self.mutation=='wrong-size':payload=payload.replace(b'32:120',b'24:80')
            if self.mutation=='substring-result':payload=payload.replace(b'9341',b'93410')
            self.output(output_handle,payload[:7],stream);self.output(output_handle,payload[7:],stream)
            self.events.append({'method':'process/exited','params':{'processHandle':handle,'exitCode':1 if self.mutation=='nonzero-exit' else 0}})
        elif method=='process/kill':
            code=0 if self.mutation=='successful-kill-exit' else None if self.mutation=='unknown-kill-exit' else True if self.mutation=='boolean-kill-exit' else 1
            self.events.append({'method':'process/exited','params':{'processHandle':handle,'exitCode':code}})
        return {}
    def wait(self,predicate,start,timeout):
        for event in self.events[start:]:
            if predicate(event):return event
        raise TimeoutError('No matching owned event')


def test_supported_host_pty_controls_and_legacy_rejection_are_both_required(tmp_path):
    peer=Peer();result=module.windows_pty_contract(peer,tmp_path)
    assert result['passed'] and result['restricted_pty_execution_claimed'] is False
    assert result['legacy_boundary']['positive_execution_claimed'] is False
    assert 'without a Codex sandbox' in result['execution_scope']
    methods=[r['method'] for r in peer.requests]
    assert methods==['config/read','command/exec','process/spawn','process/resizePty','process/writeStdin','process/spawn','process/kill']
    assert peer.requests[1]['params']['sandboxPolicy']['networkAccess'] is False
    assert all('sandboxPolicy' not in r['params'] for r in peer.requests if r['method'].startswith('process/'))


@pytest.mark.parametrize('mutation',['full-access','boundary-success','wrong-error','foreign-error-id','foreign-handle','stderr-only','wrong-size','substring-result','nonzero-exit','successful-kill-exit','unknown-kill-exit','boolean-kill-exit'])
def test_missing_or_foreign_observations_cannot_pass(tmp_path,mutation):
    with pytest.raises((AssertionError,RuntimeError,TimeoutError)):
        module.windows_pty_contract(Peer(mutation),tmp_path)
