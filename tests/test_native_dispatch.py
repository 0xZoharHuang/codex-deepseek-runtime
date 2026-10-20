"""Drive real native apply_patch dispatch from controlled provider SSE sequences."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess

from aiohttp import web
import pytest
import codex_deepseek.gateway as gateway_module
from codex_deepseek.gateway import Gateway
import codex_deepseek.native_http as native_module
from codex_deepseek.protocol import MODEL
from codex_deepseek.state import Store

spec=importlib.util.spec_from_file_location('native_dispatch_peer',Path(__file__).resolve().parents[1]/'verify_live.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


@pytest.mark.parametrize('mode',['invalid_arguments_done','missing_arguments_done','terminal_snapshot_only','failed_snapshot_only','incomplete_snapshot_only'])
async def test_native_custom_dispatch_respects_validation_and_missing_event_recovery(tmp_path,monkeypatch,mode):
    root=tmp_path/mode;(root/'home').mkdir(parents=True);(root/'work').mkdir()
    (root/'home/config.toml').write_text('model_reasoning_effort="low"\nweb_search="disabled"\n[features]\nplugins=false\ncode_mode=false\ncode_mode_only=false\nmulti_agent_v2=false\n')
    subprocess.run(['git','init','-q',str(root/'work')],check=True)
    calls=0
    patch='*** Begin Patch\n*** Add File: native-effect.txt\n+NATIVE_EXECUTED\n*** End Patch'
    async def upstream(request):
        nonlocal calls
        calls+=1;body=await request.json()
        assert body['model']==MODEL
        if calls==1:
            raw=json.dumps({'input':patch if mode!='invalid_arguments_done' else 'THIS IS NOT A PATCH'})
            item={'id':'call-item','type':'function_call','name':'apply_patch','call_id':'fixture-call','arguments':raw,'status':'completed'}
            events=[{'type':'response.created','response':{'id':'fixture-response','status':'in_progress','output':[]}}]
            if mode not in {'terminal_snapshot_only','failed_snapshot_only','incomplete_snapshot_only'}:
                events.append({'type':'response.output_item.added','output_index':0,'item':{**item,'arguments':'','status':'in_progress'}})
            if mode=='invalid_arguments_done':
                events.append({'type':'response.function_call_arguments.done','item_id':'call-item','output_index':0,'arguments':raw})
            if mode not in {'terminal_snapshot_only','failed_snapshot_only','incomplete_snapshot_only'}:events.append({'type':'response.output_item.done','output_index':0,'item':item})
            status='failed' if mode=='failed_snapshot_only' else 'incomplete' if mode=='incomplete_snapshot_only' else 'completed'
            terminal={'id':'fixture-response','status':status,'output':[item]}
            if status=='failed':terminal['error']={'code':'fixture_failure','message':'This failed snapshot must never execute'}
            if status=='incomplete':terminal['incomplete_details']={'reason':'max_output_tokens'}
            events.append({'type':'response.'+status,'response':terminal})
        else:
            message={'id':'answer','type':'message','role':'assistant','content':[{'type':'output_text','text':'DISPATCH_FINISHED'}]}
            events=[{'type':'response.output_item.done','output_index':0,'item':message},{'type':'response.completed','response':{'id':'fixture-final','status':'completed','output':[message]}}]
        return web.Response(body=''.join('data: '+json.dumps(event)+'\n\n' for event in events).encode(),content_type='text/event-stream')
    app=web.Application();app.router.add_post('/responses',upstream)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',0).start()
    monkeypatch.setattr(gateway_module,'UPSTREAM',f'http://127.0.0.1:{runner.addresses[0][1]}/responses')
    store=Store(root/'state.sqlite')
    try:
        async with Gateway('NO_REAL_CREDENTIAL','LOCAL',store,{}) as gateway:
            native=native_module.native_helper().parent/'codex'
            assert native.is_file(),'A complete native package is required for real dispatch tests'
            provider='{name="Native SSE fixture",base_url="http://127.0.0.1:'+str(gateway.port)+'/v1",env_key="FIXTURE_TOKEN",wire_api="responses",request_max_retries=0,stream_max_retries=0,capabilities={remote_compaction="unsupported"}}'
            flags=['-c','model_provider="fixture"','-c','model="'+MODEL+'"','-c','model_catalog_json='+json.dumps(str(Path(__file__).resolve().parents[1]/'catalog.json')),'-c','model_providers.fixture='+provider]
            environment={key:value for key,value in os.environ.items() if not key.startswith('CODEX_') and key not in {'OPENAI_API_KEY','OPENROUTER_API_KEY'}}
            environment.update(CODEX_HOME=str(root/'home'),FIXTURE_TOKEN='LOCAL')
            def drive():
                peer=module.Peer(root,command=[str(native)],environment=environment,server_args=['app-server',*flags])
                try:
                    thread=peer.new();return peer.turn(thread,'Apply the requested local change and report its result. Do not delegate.')
                finally:peer.close()
            if mode in {'invalid_arguments_done','failed_snapshot_only','incomplete_snapshot_only'}:
                with pytest.raises(RuntimeError):await asyncio.to_thread(drive)
                assert not (root/'work/native-effect.txt').exists()
                expected='failed' if mode=='failed_snapshot_only' else 'incomplete' if mode=='incomplete_snapshot_only' else 'adapter_error'
                assert store.db.execute('SELECT status FROM receipts').fetchall()==[(expected,)]
            else:
                items=await asyncio.to_thread(drive)
                assert (root/'work/native-effect.txt').read_text()=='NATIVE_EXECUTED\n'
                assert any(item.get('type')=='fileChange' for item in items)
                assert calls==2
    finally:store.close();await runner.cleanup()
