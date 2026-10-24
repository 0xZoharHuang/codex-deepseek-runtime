"""Native local API-key account RPCs, with a nonfunctional key and no model calls."""
from contextlib import closing
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from install import console_entry,valid_release
from verify_live import Peer

METHODS={'account/login/start','account/read','account/logout','account/login/cancel'}


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    if not __debug__:raise SystemExit('Acceptance must run with Python assertions enabled')
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    entry=args.entry.resolve();release=entry.parent.parent.parent
    metadata=json.loads((release/'installation.json').read_text(encoding='utf-8'))
    native=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
    if entry!=console_entry(release/'venv') or not valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native):
        raise SystemExit('A verified immutable installed entry is required')
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True,mode=0o700)
    fixture='NONFUNCTIONAL_NATIVE_AUTH_FIXTURE_'+uuid.uuid4().hex
    environment={'OPENROUTER_API_KEY':'NONFUNCTIONAL_NO_MODEL_CALLS_ALLOWED','OPENAI_API_KEY':''}
    peers=[];results=[];passed=False
    config='cli_auth_credentials_store="file"\nweb_search="disabled"\n[features]\napps=false\nplugins=false\nmemories=false\n[mcp_servers.codex_web]\nenabled=false\n'
    def start(name):
        place=root/name;(place/'home').mkdir(parents=True,exist_ok=True);(place/'work').mkdir(exist_ok=True)
        (place/'home/config.toml').write_text(config,encoding='utf-8')
        peer=Peer(place,command=[str(entry)],environment=environment);peers.append(peer);return peer
    def record(method,detail):
        row={'method':method,'status':'passed','detail':detail};results.append(row)
        (root/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    artifact={'entry':str(entry),'entry_sha256':digest(entry),'installation':metadata,'target':native['target'],'native_manifest':native,
              'verifier_sha256':{name:digest(Path(__file__).with_name(name)) for name in ['verify_local_account.py','verify_live.py','windows_acceptance_setup.py','qa_workspace.py','install.py']}}
    (root/'artifact.json').write_text(json.dumps(artifact,indent=2),encoding='utf-8')
    try:
        primary=start('primary');before=primary.call('account/read',{'refreshToken':False})
        assert before['account'] is None and before['requiresOpenaiAuth'] is False,before
        saved=primary.call('account/login/start',{'type':'apiKey','apiKey':fixture})
        auth=root/'primary/home/auth.json'
        assert saved=={'type':'apiKey'} and json.loads(auth.read_text(encoding='utf-8'))['OPENAI_API_KEY']==fixture,saved
        record('account/login/start',{'variant':'apiKey','response':saved,'own_auth_file_saved_exactly':True,'real_credential_used':False})
        primary.close();primary=start('primary')
        cold=primary.call('account/read',{'refreshToken':False})
        # The native custom-provider account view does not require OpenAI auth.
        # Persistence is checked against the native auth file, independently of
        # whether that provider exposes an account in its public read response.
        assert cold['requiresOpenaiAuth'] is False and cold['account'] in (None,{'type':'apiKey'}),cold
        assert json.loads(auth.read_text(encoding='utf-8'))['OPENAI_API_KEY']==fixture
        other=start('other');separate=other.call('account/read',{'refreshToken':False})
        assert separate['account'] is None and not (root/'other/home/auth.json').exists(),separate
        record('account/read',{'refresh_token':False,'cold_response':cold,'other_home_response':separate,'native_auth_file_survived_cold_start':True,'other_home_auth_file_absent':True})
        login_id=str(uuid.uuid4());cancel=[primary.call('account/login/cancel',{'loginId':login_id}) for _ in range(2)]
        assert cancel==[{'status':'notFound'},{'status':'notFound'}],cancel
        try:primary.call('account/login/cancel',{'loginId':'invalid-fixture-login-id'})
        except RuntimeError as error:invalid='invalid login id' in str(error)
        else:invalid=False
        assert invalid
        assert json.loads(auth.read_text(encoding='utf-8'))['OPENAI_API_KEY']==fixture
        record('account/login/cancel',{'variant':'local no-active-login and invalid-ID handling','responses':cancel,'invalid_id_rejected':True,'saved_api_key_preserved':True,'hosted_oauth_lifecycle_claimed':False})
        removed=primary.call('account/logout',{});after=primary.call('account/read',{'refreshToken':False})
        assert removed=={} and after['account'] is None
        assert not auth.exists() or not json.loads(auth.read_text(encoding='utf-8')).get('OPENAI_API_KEY')
        primary.close();primary=start('primary');cold_after=primary.call('account/read',{'refreshToken':False})
        assert cold_after['account'] is None
        record('account/logout',{'response':removed,'auth_key_removed':True,'cold_response':cold_after})
        for peer in peers:peer.close()
        receipts=[]
        for path in root.glob('*/home/provider-state/adapter.sqlite'):
            with closing(sqlite3.connect(path)) as db, db:receipts.extend(db.execute('SELECT status FROM receipts').fetchall())
        assert not receipts,'Local account operations unexpectedly submitted model traffic'
        unchanged=(digest(entry)==artifact['entry_sha256']
                   and json.loads((release/'installation.json').read_text(encoding='utf-8'))==metadata
                   and valid_release(release,metadata['adapter_source_sha256'],metadata['model_catalog_sha256'],native)
                   and all(digest(Path(__file__).with_name(name))==checksum for name,checksum in artifact['verifier_sha256'].items()))
        passed={row['method'] for row in results}==METHODS and len(results)==len(METHODS) and unchanged
        report={'passed':passed,'installation':metadata,'target':native['target'],'methods':results,'model_requests':len(receipts),
                'verifier_and_artifact_unchanged':unchanged,
                'scope':'Local API-key save, provider account read, cold persistence, isolated logout, no-active-login cancellation and invalid-ID rejection; hosted OAuth/account entitlements are not exercised.'}
        (root/'verdict.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'passed':passed,'local_account_methods':len(results),'model_requests':len(receipts)}),flush=True)
    finally:
        for peer in peers:peer.close()
        # Only disposable fixture credentials were saved. Remove any remaining
        # native auth file on a failed run without touching OS credential stores.
        for path in root.glob('*/home/auth.json'):
            data=json.loads(path.read_text(encoding='utf-8'))
            if data.get('OPENAI_API_KEY')==fixture:path.unlink()
        print('EVIDENCE',root,flush=True)
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
