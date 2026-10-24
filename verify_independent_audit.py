"""Independent native audits with instrumented effects and fresh private homes."""
from process_acceptance import evidence_root
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

from verify_live import Peer,text
from credential_acceptance import prepare_home,private_file,clear_fixture_secret_key

SOURCE=Path(__file__).resolve().parent


def oauth_fixture_config(base,store,secrets_enabled):
    # Windows enables Secrets by default; absence is not a Direct-backend test.
    feature_config='[features]\nsecret_auth_storage='+str(secrets_enabled).lower()+'\n'
    return f'model_reasoning_effort="low"\nmcp_oauth_credentials_store="{store}"\n{feature_config}[mcp_servers.oauth_audit]\nurl={json.dumps(base+"/mcp")}\nstartup_timeout_sec=10\n'


def effective_secret_storage(response):
    rows=[row for row in response.get('data',[]) if row.get('name')=='secret_auth_storage']
    assert response.get('nextCursor') is None and len(rows)==1 and type(rows[0].get('enabled')) is bool,'Missing unambiguous native Secrets feature readback'
    return {'enabled':rows[0]['enabled'],'default_enabled':rows[0].get('defaultEnabled'),'stage':rows[0].get('stage')}


def read_fixture_events(root,timeout=5):
    # The fixture event loop snapshots its own state. Reading the repeatedly
    # replaced file can hit a Win32 share/delete race even with atomic writes.
    ready=json.loads((root/'ready.json').read_text(encoding='utf-8'))
    request=urllib.request.Request(ready['base']+'/events',
                                   headers={'Authorization':'Bearer '+ready['events_token']})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        value=json.load(response)
    assert isinstance(value,list) and all(isinstance(row,dict) and isinstance(row.get('kind'),str)
                                         for row in value),'Malformed OAuth fixture snapshot'
    return value


def _oauth_receipt_ids(peer,thread,turn):
    ids=[]
    for path in (peer.root/'adapter-traces').glob('*-request.json'):
        metadata=json.loads(path.read_text(encoding='utf-8')).get('client_metadata',{})
        if metadata.get('thread_id')==thread and metadata.get('turn_id')==turn:
            ids.append(path.name.removesuffix('-request.json'))
    return ids


def _oauth_turn_observation(peer,thread,turn,start,items,prompt,fixture_events):
    """Read exact-turn evidence; missing or unfamiliar observations fail closed."""
    from contextlib import closing
    import sqlite3
    import stat

    sessions=peer.runtime_home/'sessions'
    def ordinary(path):
        info=path.lstat()
        return not path.is_symlink() and not getattr(info,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT
    assert ordinary(sessions),'Native sessions directory is a link'
    directories=[sessions]
    for _ in range(3):
        directories=[p for parent in directories for p in parent.iterdir()
                     if ordinary(p) and p.is_dir() and p.name.isdigit()]
    paths=[p for parent in directories for p in parent.iterdir()
           if p.name.endswith('-'+thread+'.jsonl') and ordinary(p) and p.is_file()]
    assert len(paths)==1,'Missing unambiguous physical native session'
    records=[json.loads(line) for line in paths[0].read_text(encoding='utf-8').splitlines()]
    assert records[0].get('type')=='session_meta' and records[0]['payload'].get('id')==thread
    starts=[i for i,row in enumerate(records) if row.get('type')=='event_msg'
            and row['payload'].get('type')=='task_started' and row['payload'].get('turn_id')==turn]
    ends=[i for i,row in enumerate(records) if row.get('type')=='event_msg'
          and row['payload'].get('type')=='task_complete' and row['payload'].get('turn_id')==turn]
    assert len(starts)==1 and len(ends)<=1 and (not ends or starts[0]<ends[0]),'Ambiguous native turn recording'
    end=ends[0] if ends else len(records)
    assert not any(row.get('type')=='event_msg' and row['payload'].get('type')=='task_started'
                   for row in records[starts[0]+1:end]),'A different native turn started before the terminal record'
    raw_types=[row['payload'].get('type') for row in records[starts[0]:end]
               if row.get('type')=='response_item']
    receipt_ids=_oauth_receipt_ids(peer,thread,turn)
    assert receipt_ids and len(receipt_ids)==len(set(receipt_ids)),'Missing exact-turn model receipts'
    state=peer.runtime_home/'provider-state/adapter.sqlite'
    with closing(sqlite3.connect(state.as_uri()+'?mode=ro',uri=True,timeout=0)) as db:
        receipts=[dict(zip(('id','status','upstream_status'),row)) for row in db.execute(
            'SELECT id,status,upstream_status FROM receipts WHERE id IN ('+
            ','.join('?' for _ in receipt_ids)+')',receipt_ids)]
    events=[]
    for event in peer.events[start:]:
        params=event.get('params',{})
        if params.get('threadId')==thread and (params.get('turnId')==turn or params.get('turn',{}).get('id')==turn):
            events.append(event)
    ready=[event['params'] for event in peer.events
           if event.get('method')=='mcpServer/startupStatus/updated'
           and event.get('params',{}).get('threadId')==thread
           and event['params'].get('name')=='oauth_audit']
    return {'items':items,'prompt':prompt,'events':events,'raw_response_types':raw_types,
            'receipts':receipts,'expected_receipt_ids':receipt_ids,
            'fixture_events':fixture_events,'oauth_server_ready':bool(ready) and
            ready[-1].get('status')=='ready' and ready[-1].get('error') is None,
            'native_session':str(paths[0]),'native_turn_record_complete':bool(ends)}


def _oauth_action_free(observation):
    """A conservative shallow guard: observed or unfamiliar actions never get replayed."""
    items=observation['items']
    echoes=[item for item in items if item.get('type')=='userMessage']
    if len(echoes)!=1 or echoes[0].get('content')!=[
            {'type':'text','text':observation['prompt'],'text_elements':[]}]:return False
    if not text(items).strip() or any(item.get('type') not in
            {'userMessage','reasoning','agentMessage'} for item in items):return False
    safe_methods={'item/started','item/completed','item/agentMessage/delta',
                  'item/reasoning/textDelta','item/reasoning/summaryTextDelta',
                  'item/reasoning/summaryPartAdded','thread/tokenUsage/updated','turn/started','turn/completed'}
    for event in observation['events']:
        params=event.get('params',{})
        if ('id' in event or event.get('error') is not None or params.get('error') is not None
                or event.get('method') not in safe_methods):return False
        if event.get('method') in {'item/started','item/completed'}:
            if params.get('item',{}).get('type') not in {'userMessage','reasoning','agentMessage'}:return False
        if event.get('method')=='turn/completed':
            terminal=params.get('turn',{})
            if terminal.get('status')!='completed' or terminal.get('error') is not None:return False
    for event in observation['fixture_events']:
        if event.get('kind')=='unauthenticated' and event.get('method') in {'GET','POST'}:continue
        if event.get('kind')=='mcp' and event.get('method') in {
                'initialize','notifications/initialized','tools/list'}:continue
        return False
    return True


def _oauth_no_observed_action(observation):
    """Only a completed, read-back message-only turn may receive one user correction."""
    if not _oauth_action_free(observation):return False
    if not observation['native_turn_record_complete'] or not observation['oauth_server_ready']:return False
    if not observation['raw_response_types'] or any(kind not in {'message','reasoning'} for kind in observation['raw_response_types']):return False
    receipts=observation['receipts']
    return (bool(receipts) and {row['id'] for row in receipts}==set(observation['expected_receipt_ids'])
            and all(row['status']=='completed' and row['upstream_status']==200 for row in receipts))


def _oauth_known_pending(observation):
    """Only native terminal persistence and already-bound running receipts may settle."""
    if not _oauth_action_free(observation) or not observation['oauth_server_ready']:return False
    if not observation['raw_response_types'] or any(kind not in {'message','reasoning'}
            for kind in observation['raw_response_types']):return False
    receipts=observation['receipts']
    if (not receipts or len(receipts)!=len(observation['expected_receipt_ids'])
            or {row['id'] for row in receipts}!=set(observation['expected_receipt_ids'])):return False
    if any(not (row['status']=='completed' and row['upstream_status']==200
                or row['status']=='running' and row['upstream_status'] in {None,200}) for row in receipts):
        return False
    return not observation['native_turn_record_complete'] or any(row['status']=='running' for row in receipts)



def oauth_action_turn(peer,thread,prompt,fixture_root,attempts_path,phase):
    """A task may receive one explicit correction, never a tool or transport retry."""
    attempts=json.loads(attempts_path.read_text(encoding='utf-8')) if attempts_path.exists() else []
    current_prompt=prompt
    for number in (1,2):
        before=len(read_fixture_events(fixture_root))
        attempt={'phase':phase,'attempt':number,'thread_id':thread,'prompt':current_prompt}
        attempts.append(attempt)
        try:
            turn,start=peer.start(thread,current_prompt)
            attempt['turn_id']=turn
            items=peer.complete(thread,start,turn_id=turn)
            attempt.update({'reply':text(items),'item_types':[item.get('type') for item in items],
                            'native_status':'completed'})
            try:attempt['receipt_ids']=_oauth_receipt_ids(peer,thread,turn)
            except Exception as error:
                attempt['receipt_observation_error']={'type':type(error).__name__,'message':str(error)}
            fixture_events=read_fixture_events(fixture_root)[before:]
            attempt['fixture_events']=fixture_events
            events=[event for event in peer.events[start:] if event.get('params',{}).get('threadId')==thread
                    and (event['params'].get('turnId')==turn or event['params'].get('turn',{}).get('id')==turn)]
            shallow={'items':items,'prompt':current_prompt,'events':events,'fixture_events':fixture_events}
            if not _oauth_action_free(shallow):
                attempt['feedback_decision']='observed_action_or_unqualified_observation'
                return items
            # Native terminal delivery may precede rollout flush. Observe only
            # known bookkeeping states; this never runs or retries model/tool work.
            observed_at=time.monotonic();deadline=observed_at+5.0;observations=0
            while True:
                if observations and time.monotonic()>=deadline:
                    attempt.update({'feedback_decision':'uncertain_no_feedback',
                                    'observation_timeout_seconds':5.0,
                                    'observation_seconds':time.monotonic()-observed_at})
                    return items
                if getattr(peer,'protocol_error',None) is not None:
                    attempt['feedback_decision']='native_protocol_error_no_feedback'
                    return items
                observations+=1
                fixture_events=read_fixture_events(fixture_root,timeout=max(.001,min(1,deadline-time.monotonic())))[before:]
                events=[event for event in peer.events[start:] if event.get('params',{}).get('threadId')==thread
                        and (event['params'].get('turnId')==turn or event['params'].get('turn',{}).get('id')==turn)]
                shallow={'items':items,'prompt':current_prompt,'events':events,'fixture_events':fixture_events}
                attempt.update({'fixture_events':fixture_events,'observation_count':observations,
                                'observation_seconds':time.monotonic()-observed_at})
                if not _oauth_action_free(shallow):
                    attempt['feedback_decision']='late_action_or_unqualified_observation'
                    return items
                try:
                    observation=_oauth_turn_observation(peer,thread,turn,start,items,current_prompt,fixture_events)
                except Exception as error:
                    attempt.update({'feedback_decision':'uncertain_no_feedback',
                                    'observation_error':{'type':type(error).__name__,'message':str(error)}})
                    return items
                attempt.update({'receipt_ids':observation['expected_receipt_ids'],
                                'receipts':observation['receipts'],'native_session':observation['native_session'],
                                'raw_response_types':observation['raw_response_types'],
                                'native_turn_record_complete':observation['native_turn_record_complete'],
                                'observation_seconds':time.monotonic()-observed_at})
                if _oauth_no_observed_action(observation):
                    attempt['no_observed_tool_action']=True
                    break
                if not _oauth_known_pending(observation):
                    attempt['feedback_decision']='unqualified_observation_no_feedback'
                    return items
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    attempt['feedback_decision']='uncertain_no_feedback'
                    attempt['observation_timeout_seconds']=5.0
                    return items
                time.sleep(min(.05,remaining))
            if number==2:raise AssertionError('OAuth task performed no observed tool action after one explicit feedback')
            current_prompt=('The previous turn completed with text only and no observed tool or resource action. '
                'Execute the requested task now in this same thread. Use native tool discovery (ALL_TOOLS when '
                'available) to locate oauth_audit.read_nonce, then actually invoke it exactly once. '
                'Do not merely describe future actions. Original task: '+prompt)
            attempt['explicit_feedback_follows']=True
            attempt['feedback_decision']='one_explicit_user_feedback'
        except BaseException as error:
            attempt['error']={'type':type(error).__name__,'message':str(error)}
            raise
        finally:
            attempts_path.write_text(json.dumps(attempts,indent=2,ensure_ascii=False),encoding='utf-8')
    raise AssertionError('Unreachable bounded OAuth task')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',action='store_true');parser.add_argument('--native-binary',type=Path);parser.add_argument('--store',choices=['file','keyring','auto'],default='file');parser.add_argument('--secrets',action='store_true');args=parser.parse_args()
    if args.secrets and not os.environ.get('CODEX_DEEPSEEK_TEST_KEYRING_PROBE'):raise SystemExit('Native keyring cleanup probe is required before creating encrypted credential fixtures')
    root=evidence_root('independent-audit')/str(uuid.uuid4());root.mkdir(parents=True)
    command=[sys.executable,'-m','codex_deepseek.cli'] if args.source else None
    if args.source:os.environ['PYTHONPATH']=str(SOURCE/'src')
    if args.native_binary:
        assert args.source,'Native source audits must explicitly select the source adapter'
        command=[sys.executable,'-c','import asyncio,sys;from pathlib import Path;import codex_deepseek.cli as cli;cli.native=lambda:Path('+repr(str(args.native_binary.resolve()))+');raise SystemExit(asyncio.run(cli.run(sys.argv[1:])))']
    results=[]
    def passed(name,condition,detail):
        result={'name':name,'passed':bool(condition),'detail':detail};results.append(result)
        (root/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False));print(json.dumps(result,ensure_ascii=False),flush=True)
        if not condition:raise AssertionError(name)
    fixture_root=root/'fixture';fixture_root.mkdir()
    with (fixture_root/'stderr.txt').open('w') as log:
        fixture=subprocess.Popen([sys.executable,str(SOURCE/'tests/oauth_fixture.py'),str(fixture_root)],stderr=log,stdout=log)
    peer=None;other=None
    try:
        end=time.monotonic()+10
        while not (fixture_root/'ready.json').exists() and time.monotonic()<end:time.sleep(.05)
        ready=json.loads((fixture_root/'ready.json').read_text());base=ready['base']
        config=oauth_fixture_config(base,args.store,args.secrets)
        for name in ['primary','other']:
            target=root/name;prepare_home(target/'home');(target/'work').mkdir()
            (target/'home/config.toml').write_text(config)
        peer=Peer(root/'primary',command=command)
        one=peer.call('thread/start',{'cwd':str(root/'primary/work'),'modelProvider':'openai'})
        two=peer.call('thread/start',{'cwd':str(root/'primary/work'),'config':{'model_providers.deepseek_runtime.base_url':base+'/trap'}})
        peer.call('config/value/write',{'keyPath':'mcp_oauth_credentials_store','value':args.store,'mergeStrategy':'replace'})
        effective=peer.call('config/read',{'includeLayers':False})['config']
        feature=effective_secret_storage(peer.call('experimentalFeature/list',{}))
        passed('native_stdio_provider_pin_and_selected_credential_store',one['modelProvider']==two['modelProvider']=='deepseek_runtime' and effective['mcp_oauth_credentials_store']==args.store and effective['model_providers']['deepseek_runtime']['base_url']!=base+'/trap' and feature['enabled'] is args.secrets,{'providers':[one['modelProvider'],two['modelProvider']],'mcp_credentials_store':effective['mcp_oauth_credentials_store'],'requested_secret_auth_storage':args.secrets,'effective_secret_auth_storage':feature})
        login=peer.call('mcpServer/oauth/login',{'name':'oauth_audit','clientRegistration':'dcr','timeoutSecs':90})
        start=len(peer.events)
        with urllib.request.urlopen(login['authorizationUrl'],timeout=20) as response:response.read()
        complete=peer.wait(lambda e:e.get('method')=='mcpServer/oauthLogin/completed',start,30)
        passed('native_mcp_oauth_pkce_callback',complete['params'].get('success') is True,complete)
        peer.call('config/mcpServer/reload',{})
        thread=peer.new();items=oauth_action_turn(peer,thread,'Do not delegate. Call the OAuth MCP tool oauth_audit.read_nonce exactly once, then read the audit://ledger MCP resource from that same server. Report both exact values returned. Do not infer them.',fixture_root,root/'model-action-attempts.json','authenticated-tool-resource')
        actual=read_fixture_events(fixture_root)
        executions=[e for e in actual if e['kind']=='tool_executed']
        passed('native_http_mcp_authenticated_tool_and_resource',len(executions)==1 and ready['nonce'] in text(items) and 'RESOURCE_'+ready['nonce'] in text(items) and any(e.get('method')=='resources/read' for e in actual),{'executions':executions,'answer':text(items)})
        peer.close();peer=Peer(root/'primary',command=command)
        cold_feature=effective_secret_storage(peer.call('experimentalFeature/list',{}))
        before=len(read_fixture_events(fixture_root))
        resumed=peer.new();items=oauth_action_turn(peer,resumed,'Do not delegate. Call oauth_audit.read_nonce once and return its exact value.',fixture_root,root/'model-action-attempts.json','cold-store-tool')
        actual=read_fixture_events(fixture_root)[before:]
        credentials=root/'primary/home/.credentials.json'
        file_valid=private_file(credentials)
        selected_valid=file_valid if args.store=='file' else not credentials.exists() if args.store=='keyring' else True
        passed('native_oauth_selected_store_survives_cold_start',ready['nonce'] in text(items) and any(e['kind']=='tool_executed' for e in actual) and not any(e['kind']=='token' for e in actual) and selected_valid and cold_feature['enabled'] is args.secrets,{'events':actual,'store':args.store,'encrypted_secrets':args.secrets,'effective_secret_auth_storage':cold_feature,'credential_file_present':credentials.exists(),'mode':oct(credentials.stat().st_mode&0o777) if credentials.exists() else None})
        before=len(read_fixture_events(fixture_root))
        other=Peer(root/'other',command=command);other.new()
        # Thread creation returns before its async MCP startup has necessarily
        # reached the server. Wait for the actual authentication attempt; an
        # empty slice must never count as an isolation success or a failure.
        other.call('mcpServerStatus/list',{})
        deadline=time.monotonic()+15
        while True:
            actual=read_fixture_events(fixture_root)[before:]
            if any(e['kind'] in {'unauthenticated','mcp'} for e in actual) or time.monotonic()>=deadline:break
            time.sleep(.1)
        passed('same_mcp_url_and_name_do_not_share_oauth_across_homes',not any(e['kind']=='mcp' for e in actual) and any(e['kind']=='unauthenticated' for e in actual) and not (root/'other/home/.credentials.json').exists(),actual)
    finally:
        if peer:peer.close()
        if other:other.close()
        cleanup=[]
        try:
            entry=command or [os.environ.get('CODEX_DEEPSEEK_TEST_ENTRY') or str(Path.home()/'.local/bin/codex-deepseek')]
            for name in ['primary','other']:
                home=(root/name/'home').resolve()
                if not home.exists():continue
                removed=subprocess.run([*entry,'mcp','logout','oauth_audit'],env={**os.environ,'CODEX_DEEPSEEK_HOME':str(home)},capture_output=True,text=True,encoding='utf-8',timeout=45)
                row={'home':str(home),'logout_exit':removed.returncode,'stdout':removed.stdout,'stderr':removed.stderr}
                if removed.returncode==0 and 'base' in locals():
                    before=len(read_fixture_events(fixture_root))
                    cold=Peer(root/name,command=command)
                    try:
                        cold.new();cold.call('mcpServerStatus/list',{})
                        deadline=time.monotonic()+15
                        while True:
                            events=read_fixture_events(fixture_root)[before:]
                            if any(e['kind'] in {'unauthenticated','mcp'} for e in events) or time.monotonic()>=deadline:break
                            time.sleep(.1)
                        row['cold_logout_verified']=any(e['kind']=='unauthenticated' for e in events) and not any(e['kind']=='mcp' for e in events)
                        row['cold_authentication_events']=events
                    finally:cold.close()
                if args.secrets:row['native_keyring']=clear_fixture_secret_key(home)
                cleanup.append(row)
            (root/'fixture-credential-cleanup.json').write_text(json.dumps(cleanup,indent=2),encoding='utf-8')
            if any(row['logout_exit']!=0 or not row.get('cold_logout_verified') for row in cleanup):raise RuntimeError('Native MCP credential logout did not survive cold readback')
        finally:
            fixture.terminate();fixture.wait(timeout=10)
        print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
