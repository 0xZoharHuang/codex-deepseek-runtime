"""Native CLI credential-store modes with disposable nonfunctional fixture keys."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--entry',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True);rows=[]
    for mode,secrets in [('file',False),('keyring',False),('auto',False),('ephemeral',False),('keyring',True)]:
        name=mode+('-secrets' if secrets else '');home=root/name;home.mkdir(mode=0o700)
        (home/'config.toml').write_text('cli_auth_credentials_store='+json.dumps(mode)+'\n[features]\nplugins=false\nsecret_auth_storage='+str(secrets).lower()+'\n')
        fixture='sk-fixture-nonfunctional-'+uuid.uuid4().hex
        environment={**os.environ,'CODEX_DEEPSEEK_HOME':str(home)}
        def run(arguments,stdin=None):
            result=subprocess.run([str(args.entry.resolve()),*arguments],input=stdin,env=environment,capture_output=True,text=True,timeout=40)
            return {'exit':result.returncode,'stdout':result.stdout.replace(fixture,'[FIXTURE_KEY]'),'stderr':result.stderr.replace(fixture,'[FIXTURE_KEY]')}
        try:
            saved=run(['login','--with-api-key'],fixture+'\n')
            status=run(['login','status'])
            other=root/(name+'-other');other.mkdir(mode=0o700);(other/'config.toml').write_text((home/'config.toml').read_text())
            other_status=subprocess.run([str(args.entry.resolve()),'login','status'],env={**os.environ,'CODEX_DEEPSEEK_HOME':str(other)},capture_output=True,text=True,timeout=40)
            persisted=status['exit']==0
            expected=not persisted if mode=='ephemeral' else persisted
            isolated=other_status.returncode!=0
            file=(home/'auth.json');file_mode=oct(file.stat().st_mode&0o777) if file.exists() else None
            no_plain=not file.exists() if mode=='keyring' else True
            row={'mode':mode,'encrypted_secrets':secrets,'passed':saved['exit']==0 and expected and isolated and no_plain,'save':saved,'cold_status':status,'other_home_exit':other_status.returncode,'plain_auth_file_present':file.exists(),'plain_file_mode':file_mode};rows.append(row)
            (root/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
            assert row['passed'],row
        finally:
            run(['logout'])
            if not secrets:
                account='cli|'+hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:16]
                subprocess.run(['security','delete-generic-password','-s','Codex Auth','-a',account],capture_output=True)
            else:
                account='secrets|'+hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:16]
                subprocess.run(['security','delete-generic-password','-s','codex','-a',account],capture_output=True)
    print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
