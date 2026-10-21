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
        thread=peer.new();items=peer.turn(thread,'Do not delegate. Call the OAuth MCP tool oauth_audit.read_nonce exactly once, then read the audit://ledger MCP resource from that same server. Report both exact values returned. Do not infer them.')
        actual=json.loads((fixture_root/'fixture-events.json').read_text())
        executions=[e for e in actual if e['kind']=='tool_executed']
        passed('native_http_mcp_authenticated_tool_and_resource',len(executions)==1 and ready['nonce'] in text(items) and 'RESOURCE_'+ready['nonce'] in text(items) and any(e.get('method')=='resources/read' for e in actual),{'executions':executions,'answer':text(items)})
        peer.close();peer=Peer(root/'primary',command=command)
        cold_feature=effective_secret_storage(peer.call('experimentalFeature/list',{}))
        before=len(json.loads((fixture_root/'fixture-events.json').read_text()))
        resumed=peer.new();items=peer.turn(resumed,'Do not delegate. Call oauth_audit.read_nonce once and return its exact value.')
        actual=json.loads((fixture_root/'fixture-events.json').read_text())[before:]
        credentials=root/'primary/home/.credentials.json'
        file_valid=private_file(credentials)
        selected_valid=file_valid if args.store=='file' else not credentials.exists() if args.store=='keyring' else True
        passed('native_oauth_selected_store_survives_cold_start',ready['nonce'] in text(items) and any(e['kind']=='tool_executed' for e in actual) and not any(e['kind']=='token' for e in actual) and selected_valid and cold_feature['enabled'] is args.secrets,{'events':actual,'store':args.store,'encrypted_secrets':args.secrets,'effective_secret_auth_storage':cold_feature,'credential_file_present':credentials.exists(),'mode':oct(credentials.stat().st_mode&0o777) if credentials.exists() else None})
        before=len(json.loads((fixture_root/'fixture-events.json').read_text()))
        other=Peer(root/'other',command=command);other.new()
        # Thread creation returns before its async MCP startup has necessarily
        # reached the server. Wait for the actual authentication attempt; an
        # empty slice must never count as an isolation success or a failure.
        other.call('mcpServerStatus/list',{})
        deadline=time.monotonic()+15
        while True:
            actual=json.loads((fixture_root/'fixture-events.json').read_text())[before:]
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
                    before=len(json.loads((fixture_root/'fixture-events.json').read_text(encoding='utf-8')))
                    cold=Peer(root/name,command=command)
                    try:
                        cold.new();cold.call('mcpServerStatus/list',{})
                        deadline=time.monotonic()+15
                        while True:
                            events=json.loads((fixture_root/'fixture-events.json').read_text(encoding='utf-8'))[before:]
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
