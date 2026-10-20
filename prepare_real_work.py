"""Recreate separate public-source fixtures; never overwrite an existing acceptance run."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

SOURCE=Path(__file__).resolve().parent/'fixtures/real-work'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--python',default='python3.11')
    args=parser.parse_args();dest=args.output.resolve()
    if dest.exists():raise SystemExit('Use a fresh output directory; existing evidence is preserved')
    dest.mkdir(parents=True)
    for name,subdir in [('jinja','src'),('httpcore','')]:
        root=dest/name;root.mkdir();meta=json.loads((SOURCE/name/'upstream.json').read_text());(root/'upstream.json').write_text(json.dumps(meta,indent=2))
        archives={}
        for label,commit in [('work',meta['base']),('oracle',meta['fix'])]:
            target=root/label;target.mkdir()
            data=urllib.request.urlopen(f'https://api.github.com/repos/{meta["repository"]}/tarball/{commit}',timeout=60).read()
            archives[label]={'commit':commit,'archive_sha256':hashlib.sha256(data).hexdigest()}
            with tarfile.open(fileobj=io.BytesIO(data)) as bundle:
                for member in bundle:
                    parts=Path(member.name).parts[1:]
                    if not parts:continue
                    if member.issym() or member.islnk():continue
                    output=target.joinpath(*parts)
                    if not output.resolve().is_relative_to(target.resolve()):raise ValueError('Invalid archive path')
                    if member.isdir():output.mkdir(parents=True,exist_ok=True)
                    elif member.isfile():
                        output.parent.mkdir(parents=True,exist_ok=True)
                        with bundle.extractfile(member) as inp,output.open('wb') as out:shutil.copyfileobj(inp,out)
                        output.chmod(member.mode & 0o777)
        (root/'archives.json').write_text(json.dumps(archives,indent=2))
        subprocess.run(['git','init','-q','-b','codex/runtime-acceptance',str(root/'work')],check=True)
        subprocess.run(['git','add','.'],cwd=root/'work',check=True)
        subprocess.run(['git','-c','user.name=Acceptance Fixture','-c','user.email=fixture@localhost','commit','-qm','Fixed upstream pre-fix snapshot'],cwd=root/'work',check=True)
        subprocess.run([args.python,'-m','venv',str(root/'tools311')],check=True)
        shutil.copyfile(SOURCE/name/'dependencies-frozen.txt',root/'dependencies-frozen.txt')
        shutil.copyfile(SOURCE/name/'dependencies.lock',root/'dependencies.lock')
        subprocess.run([str(root/'tools311/bin/python'),'-m','pip','install','--require-hashes','--only-binary=:all:','-r',str(SOURCE/'build-requirements.lock')],check=True)
        subprocess.run([str(root/'tools311/bin/python'),'-m','pip','install','--require-hashes','--no-build-isolation','-r',str(root/'dependencies.lock')],check=True)
        if name=='httpcore':
            for filename in ['cancellation_regression.py','network_stress.py','tls_socket_regression.py']:
                shutil.copyfile(SOURCE/name/filename,root/filename)
        checks=[]
        for label,tests,code in [('base',root/'work/tests',root/'work'),('heldout',root/'oracle/tests',root/'work'),('oracle',root/'oracle/tests',root/'oracle')]:
            pythonpath=code/'src' if name=='jinja' else code
            environment={**__import__('os').environ,'PYTHONPATH':str(pythonpath)}
            result=subprocess.run([str(root/'tools311/bin/python'),'-m','pytest','-q',str(tests)],cwd=code,env=environment,capture_output=True,text=True,timeout=180)
            (root/('baseline-'+label+'.txt')).write_text(result.stdout+result.stderr)
            checks.append({'stage':label,'exit':result.returncode,'tail':(result.stdout+result.stderr)[-1600:]})
        (root/'baseline.json').write_text(json.dumps(checks,indent=2))
        expected=[0,1,0] if name=='jinja' else [0,0,0]
        if [item['exit'] for item in checks]!=expected:raise RuntimeError('Baseline/oracle separation failed for '+name)
        if name=='httpcore':
            independent=[]
            for label in ['work','oracle']:
                environment={**__import__('os').environ,'PYTHONPATH':str(root/label)}
                result=subprocess.run([str(root/'tools311/bin/python'),'-m','pytest','-q',str(root/'cancellation_regression.py')],cwd=root/label,env=environment,capture_output=True,text=True,timeout=30)
                (root/('baseline-independent-'+label+'.txt')).write_text(result.stdout+result.stderr)
                independent.append({'source':label,'exit':result.returncode,'tail':(result.stdout+result.stderr)[-1600:]})
            (root/'baseline-independent.json').write_text(json.dumps(independent,indent=2))
            if [item['exit'] for item in independent]!=[1,0]:raise RuntimeError('Independent cancellation fixture did not distinguish base and fixed upstream sources')

    print(dest)

if __name__=='__main__':main()
