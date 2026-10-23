import os
"""Provider attestation tolerates delayed metadata without repeating inference."""
from contextlib import asynccontextmanager
import pytest
from codex_deepseek.cli import generation_receipt,metadata_delay


class MetadataTransport:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    @asynccontextmanager
    async def request(self,method,url,**options):
        assert method=='GET' and 'id=original-generation' in url
        self.calls.append((method,url,options))
        status,body,headers=next(self.responses)
        class Response:
            async def json(self):return body
        response=Response();response.status=status;response.headers=headers
        yield response


async def test_metadata_delay_then_attested_provider_does_not_resubmit_model(monkeypatch):
    sleeps=[]
    async def pause(delay):sleeps.append(delay)
    monkeypatch.setattr('codex_deepseek.cli.asyncio.sleep',pause)
    transport=MetadataTransport([(404,{},{}),(429,{}, {'Retry-After':'2'}),(200,{'data':{'provider_name':'DeepSeek'}},{})])
    result,rows=await generation_receipt(transport,'FIXTURE','original-generation','system')
    assert result['provider_name']=='DeepSeek' and [row['http_status'] for row in rows]==[404,429,200]
    assert sleeps==[1,2] and len(transport.calls)==3
    assert all(call[2]['policy']=='system' for call in transport.calls)


async def test_permanent_auth_error_is_not_retried():
    transport=MetadataTransport([(401,{}, {})])
    result,rows=await generation_receipt(transport,'FIXTURE','original-generation','default')
    assert result=={} and len(rows)==len(transport.calls)==1 and rows[0]['http_status']==401


async def test_different_provider_is_returned_for_doctor_to_reject():
    transport=MetadataTransport([(200,{'data':{'provider_name':'UnexpectedProvider'}},{})])
    result,rows=await generation_receipt(transport,'FIXTURE','original-generation','default')
    assert result['provider_name']=='UnexpectedProvider' and len(rows)==len(transport.calls)==1


@pytest.mark.parametrize('value',['NaN','inf','-1','not-a-date'])
def test_invalid_retry_advice_uses_finite_bounded_backoff(value):
    assert metadata_delay({'Retry-After':value},2)==4


async def test_malformed_metadata_fails_explicitly_without_attestation():
    transport=MetadataTransport([(200,{'data':[]},{})])
    with pytest.raises(ValueError,match='metadata envelope'):
        await generation_receipt(transport,'FIXTURE','original-generation','default')
    assert len(transport.calls)==1


@pytest.fixture
def doctor_probe(monkeypatch,tmp_path):
    """Exercise doctor with native-shaped events and a real in-memory receipt DB."""
    import json
    import sqlite3
    from types import SimpleNamespace
    import codex_deepseek.cli as cli
    home=tmp_path/'home';(home/'provider-state').mkdir(parents=True)
    binary=tmp_path/'native/bin'/('codex'+cli.EXE)
    calls=[];transports=[]
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE receipts(response_id,network_policy,provider,status,context_key,started)')
    db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',('fixture-response','default','deepseek','completed','fixture-thread',1))
    state=SimpleNamespace(returncode=0,thread='fixture-thread',answer='CONNECTED',db=db,calls=calls,transports=transports,
                          binary=binary,metadata_calls=0,metadata={'id':'fixture-response','provider_name':'DeepSeek','model':'deepseek/deepseek-v4.1-flash-20261017'})
    class Process:
        @property
        def returncode(self):return state.returncode
        async def communicate(self):
            events=[]
            if state.thread is not None:events.append({'type':'thread.started','thread_id':state.thread})
            if state.answer is not None:events.append({'type':'item.completed','item':{'type':'agent_message','text':state.answer}})
            return '\n'.join(json.dumps(event) for event in events).encode(),b''
    async def launch(*arguments,**kwargs):calls.append(arguments);return Process()
    class Transport:
        def __init__(self,url,*,binary):self.binary=binary;self.closed=False;transports.append(self)
        async def close(self):self.closed=True
    async def receipt(transport,key,response_id,policy):
        assert key=='NONFUNCTIONAL_FIXTURE' and response_id=='fixture-response' and policy=='default'
        state.metadata_calls+=1
        return state.metadata,[{'http_status':200}]
    def forbidden_native():raise AssertionError('Doctor revalidated the same immutable package')
    store=SimpleNamespace(db=db)
    monkeypatch.setattr(cli,'native',forbidden_native)
    monkeypatch.setattr(cli,'adapter_command',lambda:['fixture-isolated-entry'])
    monkeypatch.setattr(cli.asyncio,'create_subprocess_exec',launch)
    monkeypatch.setattr(cli,'NativeTransport',Transport)
    monkeypatch.setattr(cli,'generation_receipt',receipt)
    monkeypatch.setattr(cli,'data_root',lambda:tmp_path)
    async def run():return await cli.doctor('NONFUNCTIONAL_FIXTURE',None,store,home,native_binary=binary)
    state.run=run;state.result=lambda:json.loads((tmp_path/'last-doctor.json').read_text())
    try:yield state
    finally:db.close()


async def test_doctor_reuses_verified_transport_path_but_keeps_child_entry(doctor_probe):
    """No metadata-path rehash or direct-native bypass of the probe child."""
    import codex_deepseek.cli as cli
    probe=doctor_probe
    assert await probe.run()==0
    assert len(probe.calls)==1 and probe.calls[0][0]=='fixture-isolated-entry' and 'exec' in probe.calls[0]
    assert len(probe.transports)==1 and probe.transports[0].binary==cli.executable(probe.binary.parent,'codex-http-transport')
    assert probe.transports[0].closed
    assert probe.result()['status']=='connected' and probe.result()['echo_exact_match'] is True


@pytest.mark.parametrize('field,wrong_value',[
    ('id','another-generation'),('id',None),('model','deepseek/other-model'),('model',None),
    ('provider_name','Morph'),('provider_name',None),
])
async def test_doctor_rejects_wrong_or_missing_generation_identity_model_and_provider(doctor_probe,field,wrong_value):
    if wrong_value is None:doctor_probe.metadata.pop(field)
    else:doctor_probe.metadata[field]=wrong_value
    assert await doctor_probe.run()==1
    result=doctor_probe.result()
    assert result['status']=='provider_verification_failed' and result['echo_exact_match'] is True
    assert result['generation_id']==doctor_probe.metadata.get('id')
    assert result['generation_model']==doctor_probe.metadata.get('model')
    assert result['provider']==doctor_probe.metadata.get('provider_name')
    assert doctor_probe.metadata_calls==1 and doctor_probe.transports[0].closed


@pytest.mark.parametrize('model',['deepseek/deepseek-v4.1-flash-1017','deepseek/deepseek-v4.1-flash-20261017'])
async def test_doctor_accepts_only_explicit_model_aliases_without_confusing_echo_with_connectivity(doctor_probe,model):
    doctor_probe.metadata['model']=model;doctor_probe.answer='Connected successfully.'
    assert await doctor_probe.run()==0
    result=doctor_probe.result()
    assert result['status']=='connected' and result['generation_model']==model
    assert result['echo_exact_match'] is False and result['text']=='Connected successfully.'


@pytest.mark.parametrize('failure',[
    'native_failure','no_message','empty_reply','whitespace_reply','missing_thread','other_thread',
    'missing_receipt','incomplete_receipt','missing_response_id',
])
async def test_doctor_requires_successful_native_output_and_this_threads_completed_receipt(doctor_probe,failure):
    if failure=='native_failure':doctor_probe.returncode=1
    elif failure=='no_message':doctor_probe.answer=None
    elif failure=='empty_reply':doctor_probe.answer=''
    elif failure=='whitespace_reply':doctor_probe.answer=' \n'
    elif failure=='missing_thread':doctor_probe.thread=None
    elif failure=='other_thread':doctor_probe.thread='unrelated-thread'
    elif failure=='missing_receipt':doctor_probe.db.execute('DELETE FROM receipts')
    elif failure=='incomplete_receipt':doctor_probe.db.execute("UPDATE receipts SET status='running'")
    elif failure=='missing_response_id':doctor_probe.db.execute('UPDATE receipts SET response_id=NULL')
    with pytest.raises(RuntimeError,match='Native connectivity probe failed'):await doctor_probe.run()
    assert doctor_probe.metadata_calls==0 and not doctor_probe.transports


async def test_doctor_cannot_claim_official_route_when_local_receipt_selected_another_provider(doctor_probe):
    doctor_probe.db.execute("UPDATE receipts SET provider='morph/fp8'")
    assert await doctor_probe.run()==1 and doctor_probe.result()['status']=='provider_verification_failed'
    assert doctor_probe.result()['selected_provider']=='morph/fp8' and doctor_probe.result()['provider']=='DeepSeek'


async def test_doctor_workspace_uses_same_user_private_permissions(doctor_probe,tmp_path,assert_private):
    assert await doctor_probe.run()==0
    work=tmp_path/'home/provider-state/doctor-work'
    assert_private(work)
    probe=work/'private-fixture'
    probe.write_bytes(b'NONFUNCTIONAL_FIXTURE')
    if os.name == 'nt':
        assert_private(probe)  # Windows propagates the protected directory DACL.
    else:
        # POSIX private directories deny traversal; new file modes still follow
        # the caller's umask, so an arbitrary write need not produce mode 0600.
        assert work.stat().st_mode & 0o077 == 0
        assert probe.stat().st_uid == os.getuid()
        assert probe.read_bytes() == b'NONFUNCTIONAL_FIXTURE'
