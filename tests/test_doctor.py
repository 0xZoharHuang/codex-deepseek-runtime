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
