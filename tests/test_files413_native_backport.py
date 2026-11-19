"""P35/native-local test of Files 413 vs Responses capacity; synthetic only."""
import asyncio
import json
import time

import pytest

from codex_deepseek import gateway as gateway_module
from codex_deepseek.image_transport import FileOperationError, ImageTransport, TransportPolicy
from test_image_transport import FakeFiles
from test_native_payload_retry import (
    TOKEN, capacity_checkpoints, capacity_fixture, capacity_native_plan_effects,
    capacity_natural_exit_assertions, invoke,
)


@pytest.mark.parametrize('status,advice', [(413,None),(429,None),(429,'1')],
                         ids=['files413','files429','files429-retry-after'])
async def test_files_domain_on_published_native(tmp_path, monkeypatch, status, advice):
    import test_network
    clear_proxies=test_network.clear_proxies
    def loopback_only(test_patch):
        clear_proxies(test_patch)
        test_patch.setenv('NO_PROXY','localhost,127.0.0.1,::1')
        test_patch.setenv('no_proxy','localhost,127.0.0.1,::1')
    monkeypatch.setattr(test_network,'clear_proxies',loopback_only)
    captured={}
    original_gateway=gateway_module.Gateway
    class CaptureGateway(original_gateway):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            captured['gateway']=self
    monkeypatch.setattr(gateway_module,'Gateway',CaptureGateway)
    provider,chain,home,work,image,prompt=await capacity_fixture(tmp_path,monkeypatch,True,False)
    seen={'attempts':0,'before':None,'checkpoint':None,'first_at':None}
    class FailOnce(FakeFiles):
        async def upload(self,key,data,mime,filename):
            seen['attempts']+=1
            if seen['attempts']==1:
                seen['before']=len(provider.calls)
                seen['checkpoint']=capacity_checkpoints(home)
                seen['first_at']=time.monotonic()
                raise FileOperationError('upload',status,definitely_not_created=True,
                    safe_headers={'retry-after':advice} if advice else {})
            return await super().upload(key,data,mime,filename)
    backend=FailOnce()
    transport=ImageTransport(tmp_path/'synthetic-fake-files',lambda:TOKEN,backend,TransportPolicy(1))
    activate=provider.activate
    def activate_with_files():
        activate()
        provider.budget=128*1024*1024
        captured['gateway'].images.transport=lambda policy=None:transport
    provider.activate=activate_with_files
    try:
        async with asyncio.timeout(110):
            record=await invoke(home,chain.port,work,ephemeral=False,prompt=prompt,images=[image])
        capacity_natural_exit_assertions(record)
        output=record['stdout']+record['stderr']
        observed={'status':status,'advice':advice,'exit':record['exit_code'],
                  'files_attempts':seen['attempts'],'upstream_before':seen['before'],
                  'upstream_after':len(provider.calls),'compactions':sum(r['compaction'] for r in provider.calls),
                  'gateway_statuses':[r.get('status') for r in chain.requests],
                  'plan_effects':capacity_native_plan_effects(record)['counts'],
                  'capacity_warning':'Request-byte capacity was exceeded (HTTP 413)' in output,
                  'mentions_files':'Files API' in output,'mentions_413':'413' in output}
        print('SYNTHETIC_FILES_OBSERVATION '+json.dumps(observed,sort_keys=True))
        assert seen['before']==2 and len(seen['checkpoint'])==1
        assert seen['checkpoint'][0]['origin']=='ordinary_completed'
        assert observed['plan_effects']=={'1':1,'2':1}
        if status==413:
            assert seen['attempts']==1 and len(provider.calls)==2
            assert observed['compactions']==0 and not observed['capacity_warning']
            assert 400 in observed['gateway_statuses'] and record['exit_code']!=0
            assert observed['mentions_files'] and observed['mentions_413']
        elif advice:
            assert observed['compactions']==0 and seen['attempts']==2
            assert record['exit_code']==0 and len(provider.calls)==3
        else:
            assert observed['compactions']==0 and seen['attempts']==1
            assert record['exit_code']!=0 and len(provider.calls)==2
    finally:
        await chain.close()
