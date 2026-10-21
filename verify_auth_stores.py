"""Native CLI credential-store modes with disposable nonfunctional fixture keys."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
from credential_acceptance import prepare_home,private_file,clear_fixture_secret_key


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if not os.environ.get('CODEX_DEEPSEEK_TEST_KEYRING_PROBE'):raise SystemExit('Native keyring cleanup probe is required before creating credential fixtures')
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True);rows=[]
    for mode,secrets in [('file',False),('keyring',False),('auto',False),('ephemeral',False),('keyring',True)]:
        name=mode+('-secrets' if secrets else '');home=root/name;prepare_home(home)
        (home/'config.toml').write_text('cli_auth_credentials_store='+json.dumps(mode)+'\n[features]\nplugins=false\nsecret_auth_storage='+str(secrets).lower()+'\n')
        fixture='sk-fixture-nonfunctional-'+uuid.uuid4().hex
        environment={**os.environ,'CODEX_DEEPSEEK_HOME':str(home)}
        def run(arguments,stdin=None):
            result=subprocess.run([str(args.entry.resolve()),*arguments],input=stdin,env=environment,capture_output=True,text=True,timeout=40)
            return {'exit':result.returncode,'stdout':result.stdout.replace(fixture,'[FIXTURE_KEY]'),'stderr':result.stderr.replace(fixture,'[FIXTURE_KEY]')}
        try:
            saved=run(['login','--with-api-key'],fixture+'\n')
            status=run(['login','status'])
            other=root/(name+'-other');prepare_home(other);(other/'config.toml').write_text((home/'config.toml').read_text())
            other_status=subprocess.run([str(args.entry.resolve()),'login','status'],env={**os.environ,'CODEX_DEEPSEEK_HOME':str(other)},capture_output=True,text=True,timeout=40)
            persisted=status['exit']==0
            expected=not persisted if mode=='ephemeral' else persisted
            isolated=other_status.returncode!=0
            file=(home/'auth.json');file_mode=oct(file.stat().st_mode&0o777) if file.exists() else None
            no_plain=not file.exists() if mode=='keyring' else True
            private=private_file(file) if file.exists() else True
            row={'mode':mode,'encrypted_secrets':secrets,'passed':saved['exit']==0 and expected and isolated and no_plain and private,'save':saved,'cold_status':status,'other_home_exit':other_status.returncode,'plain_auth_file_present':file.exists(),'plain_file_private':private,'plain_file_mode':file_mode};rows.append(row)
            (root/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
            assert row['passed'],row
        finally:
            removed=run(['logout']);absent=run(['login','status'])
            cleanup={'logout':removed,'cold_logged_out':absent['exit']!=0}
            if secrets:
                cleanup['native_keyring']=clear_fixture_secret_key(home)
                if 'other' in locals() and other.exists():cleanup['other_keyring']=clear_fixture_secret_key(other)
            (home/'cleanup.json').write_text(json.dumps(cleanup,indent=2),encoding='utf-8')
            if removed['exit']!=0 or not cleanup['cold_logged_out']:raise RuntimeError('Fixture native logout failed to remove stored credentials')
    print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
