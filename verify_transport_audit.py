"""Real owned WebSocket listener, native policy, safe tuning and attachment boundary."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import uuid

import aiohttp
from codex_deepseek.isolation import check_attachment,registry
from codex_deepseek.protocol_errors import ProtocolError
from codex_deepseek.processes import identity,terminate


async def audit(root,endpoint):
    results=[]
    def passed(name,condition,detail):
        record={'name':name,'passed':bool(condition),'detail':detail};results.append(record)
        (root/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(record),flush=True)
        if not condition:raise AssertionError(name)
    if endpoint.endswith(':0'):
        end=time.monotonic()+30
        while time.monotonic()<end:
            endpoints=list(registry(root/'home'))
            if endpoints:
                endpoint=endpoints[0];break
            await asyncio.sleep(.1)
        passed('native_ephemeral_port_registered',not endpoint.endswith(':0'),endpoint)
    end=time.monotonic()+30
    while time.monotonic()<end:
        try:check_attachment(['--remote',endpoint],root/'home');break
        except ProtocolError:await asyncio.sleep(.1)
    check_attachment(['--remote',endpoint],root/'home')
    selected=check_attachment(['--remote',endpoint.replace('127.0.0.1','localhost')],root/'home')
    assert selected==endpoint, 'Local aliases must resolve to the verified numeric owner endpoint'
    endpoint=selected
    async with aiohttp.ClientSession() as client, client.ws_connect(endpoint,max_msg_size=64*1024*1024) as ws:
        counter=0
        async def call(method,params):
            nonlocal counter
            counter+=1;current=counter
            await ws.send_json({'jsonrpc':'2.0','id':current,'method':method,'params':params})
            while True:
                answer=await asyncio.wait_for(ws.receive_json(),30)
                if answer.get('id')!=current:continue
                if 'error' in answer:raise RuntimeError(answer['error'])
                return answer['result']
        initialized=await call('initialize',{'clientInfo':{'name':'isolated_transport_audit','version':'1'},'capabilities':{'experimentalApi':True}})
        await ws.send_json({'method':'initialized'})
        passed('owned_websocket_listener_and_home',Path(initialized['codexHome']).resolve()==root/'home',initialized['codexHome'])
        config=await call('config/read',{'includeLayers':False})
        (root/'effective-config.json').write_text(json.dumps(config,indent=2))
        provider=config['config']['model_providers']['deepseek_runtime']
        passed('safe_native_transport_tuning_survives_policy',provider['request_max_retries']==7 and provider['stream_idle_timeout_ms']==900000,provider)
        thread=await call('thread/start',{'cwd':str(root/'work'),'modelProvider':'openai','config':{'model_providers.deepseek_runtime.base_url':'http://127.0.0.1:1/trap'}})
        passed('websocket_per_request_provider_pin',thread['modelProvider']=='deepseek_runtime',thread['modelProvider'])
        try:await call('config/value/write',{'keyPath':'model_provider','value':'openai','mergeStrategy':'replace'})
        except RuntimeError as error:blocked='configRequirementReadonly' in str(error)
        else:blocked=False
        passed('websocket_native_managed_write_protection',blocked,{})
        for arguments in [['--remote','ws://127.0.0.1:1'],['--remote','ws://127.0.0.2:1'],['--remote','ws://[::ffff:127.0.0.1]:1'],['app-server','proxy','--sock','/tmp/unowned-app.sock'],['app-server','daemon','update'],['update']]:
            try:check_attachment(arguments,root/'home')
            except ProtocolError:rejected=True
            else:rejected=False
            passed('unowned_attachment_or_update_rejected_'+str(len(results)),rejected,arguments)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True,mode=0o700);(root/'work').mkdir()
    (root/'home/config.toml').write_text('model_reasoning_effort="low"\n[model_providers.deepseek_runtime]\nrequest_max_retries=7\nstream_idle_timeout_ms=900000\n')
    endpoint='ws://127.0.0.1:0'
    with (root/'stderr.txt').open('w') as log:
        process=subprocess.Popen([str(args.entry.resolve()),'app-server','--listen',endpoint],env={**os.environ,'CODEX_DEEPSEEK_HOME':str(root/'home')},stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log)
        birth=identity(process.pid)
        try:asyncio.run(audit(root,endpoint))
        finally:terminate(process.pid,birth,timeout=10);process.wait(timeout=5)
    endpoint=next(iter(registry(root/'home')))
    try:check_attachment(['--remote',endpoint],root/'home')
    except ProtocolError:pass
    else:raise AssertionError('A dead listener cannot authorize a new attachment')
    print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
