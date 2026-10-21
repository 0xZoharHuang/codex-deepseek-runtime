"""Independent native audits with instrumented effects and fresh private homes."""
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

SOURCE=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',action='store_true');parser.add_argument('--native-binary',type=Path);parser.add_argument('--store',choices=['file','keyring','auto'],default='file');parser.add_argument('--secrets',action='store_true');args=parser.parse_args()
    root=SOURCE.parent/'delivery/independent-audit/live'/str(uuid.uuid4());root.mkdir(parents=True)
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
        feature_config='[features]\nsecret_auth_storage=true\n' if args.secrets else ''
        config=f'model_reasoning_effort="low"\nmcp_oauth_credentials_store="{args.store}"\n{feature_config}[mcp_servers.oauth_audit]\nurl={json.dumps(base+"/mcp")}\nstartup_timeout_sec=10\n'
        for name in ['primary','other']:
            target=root/name;(target/'home').mkdir(parents=True);(target/'work').mkdir()
            (target/'home/config.toml').write_text(config)
        peer=Peer(root/'primary',command=command)
        one=peer.call('thread/start',{'cwd':str(root/'primary/work'),'modelProvider':'openai'})
        two=peer.call('thread/start',{'cwd':str(root/'primary/work'),'config':{'model_providers.deepseek_runtime.base_url':base+'/trap'}})
        peer.call('config/value/write',{'keyPath':'mcp_oauth_credentials_store','value':args.store,'mergeStrategy':'replace'})
        effective=peer.call('config/read',{'includeLayers':False})['config']
        passed('native_stdio_provider_pin_and_selected_credential_store',one['modelProvider']==two['modelProvider']=='deepseek_runtime' and effective['mcp_oauth_credentials_store']==args.store and effective['model_providers']['deepseek_runtime']['base_url']!=base+'/trap',{'providers':[one['modelProvider'],two['modelProvider']],'mcp_credentials_store':effective['mcp_oauth_credentials_store']})
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
        before=len(json.loads((fixture_root/'fixture-events.json').read_text()))
        resumed=peer.new();items=peer.turn(resumed,'Do not delegate. Call oauth_audit.read_nonce once and return its exact value.')
        actual=json.loads((fixture_root/'fixture-events.json').read_text())[before:]
        credentials=root/'primary/home/.credentials.json'
        file_valid=credentials.is_file() and credentials.stat().st_mode&0o777==0o600
        selected_valid=file_valid if args.store=='file' else not credentials.exists() if args.store=='keyring' else True
        passed('native_oauth_selected_store_survives_cold_start',ready['nonce'] in text(items) and any(e['kind']=='tool_executed' for e in actual) and not any(e['kind']=='token' for e in actual) and selected_valid,{'events':actual,'store':args.store,'encrypted_secrets':args.secrets,'credential_file_present':credentials.exists(),'mode':oct(credentials.stat().st_mode&0o777) if credentials.exists() else None})
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
        fixture.terminate();fixture.wait(timeout=10)
        if args.store in {'keyring','auto'} and not args.secrets:
            import hashlib
            for name in ['primary','other']:
                service='Codex DeepSeek MCP Credentials|'+hashlib.sha256(str((root/name/'home').resolve()).encode()).hexdigest()[:32]
                subprocess.run(['security','delete-generic-password','-s',service],capture_output=True)
        if args.secrets:
            import hashlib
            from codex_deepseek.service import native_active
            cleanup=[]
            for name in ['primary','other']:
                home=(root/name/'home').resolve()
                if native_active(home):raise RuntimeError('Refusing to remove a fixture encryption key while its native owner is active')
                account='secrets|'+hashlib.sha256(str(home).encode()).hexdigest()[:16]
                removed=subprocess.run(['security','delete-generic-password','-s','codex','-a',account],capture_output=True)
                absent=subprocess.run(['security','find-generic-password','-s','codex','-a',account],capture_output=True)
                cleanup.append({'home':str(home),'account':account,'delete_exit':removed.returncode,'absent':absent.returncode==44})
            (root/'fixture-keychain-cleanup.json').write_text(json.dumps(cleanup,indent=2))
            if not all(r['absent'] for r in cleanup):raise RuntimeError('Fixture encryption key cleanup did not verify absence')

        print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
