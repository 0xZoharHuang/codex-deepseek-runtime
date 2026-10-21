"""Recreate separate public-source fixtures; never overwrite an existing acceptance run."""
import argparse
from install import python_entry
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import xml.etree.ElementTree as ET

SOURCE=Path(__file__).resolve().parent/'fixtures/real-work'

# Both pinned HTTPcore revisions predate the Windows zero-expiry fix. A coarse
# monotonic clock exposes these real production regressions. Preserve them in
# the baseline and require the agent's final full suites to repair them.
WINDOWS_EXPIRY_FAILURES={
    ('tests._async.test_connection_pool','test_connection_pool_with_immediate_expiry[asyncio]'),
    ('tests._async.test_connection_pool','test_connection_pool_with_immediate_expiry[trio]'),
    ('tests._sync.test_connection_pool','test_connection_pool_with_immediate_expiry'),
}


def baseline_observation(xml_path,returncode):
    cases=list(ET.parse(xml_path).getroot().iter('testcase'))
    failures=[(c.get('classname'),c.get('name')) for c in cases if c.find('failure') is not None]
    errors=[(c.get('classname'),c.get('name')) for c in cases if c.find('error') is not None]
    skipped=[(c.get('classname'),c.get('name')) for c in cases if c.find('skipped') is not None]
    return {'exit':returncode,'collected':len(cases),'failures':failures,'errors':errors,'skipped':skipped}


def valid_httpcore_baseline(observation,windows):
    failures={tuple(v) for v in observation['failures']}
    known=WINDOWS_EXPIRY_FAILURES if windows else set()
    # Oracle tests against the pre-fix production tree must expose a genuine
    # upstream behavioral difference; an incidental Windows clock failure is
    # not sufficient baseline/oracle separation.
    expected=bool(failures-known) if observation.get('stage')=='heldout' else failures<=known
    return (observation['collected']==213 and not observation['errors'] and not observation['skipped']
            and len(failures)==len(observation['failures'])
            and observation['exit']==(1 if failures else 0)
            and expected)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--python',default='python3.11')
    args=parser.parse_args();dest=args.output.resolve()
    version=json.loads(subprocess.check_output([args.python,'-I','-c','import sys,json;print(json.dumps(list(sys.version_info[:3])))'],text=True,encoding='utf-8'))
    if version[:2]!=[3,11]:raise SystemExit('Pinned real-work fixtures require Python 3.11; select that interpreter explicitly')
    if dest.exists():raise SystemExit('Use a fresh output directory; existing evidence is preserved')
    dest.mkdir(parents=True)
    for name,subdir in [('jinja','src'),('httpcore','')]:
        root=dest/name;root.mkdir();meta=json.loads((SOURCE/name/'upstream.json').read_text(encoding='utf-8'));(root/'upstream.json').write_text(json.dumps(meta,indent=2), encoding='utf-8')
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
        (root/'archives.json').write_text(json.dumps(archives,indent=2), encoding='utf-8')
        subprocess.run(['git','init','-q','-b','codex/runtime-acceptance',str(root/'work')],check=True)
        subprocess.run(['git','config','core.autocrlf','false'],cwd=root/'work',check=True)
        subprocess.run(['git','add','.'],cwd=root/'work',check=True)
        subprocess.run(['git','-c','user.name=Acceptance Fixture','-c','user.email=fixture@localhost','commit','-qm','Fixed upstream pre-fix snapshot'],cwd=root/'work',check=True)
        subprocess.run([args.python,'-m','venv',str(root/'tools311')],check=True)
        shutil.copyfile(SOURCE/name/'dependencies-frozen.txt',root/'dependencies-frozen.txt')
        shutil.copyfile(SOURCE/name/'dependencies.lock',root/'dependencies.lock')
        subprocess.run([str(python_entry(root/'tools311')),'-m','pip','install','--require-hashes','--only-binary=:all:','-r',str(SOURCE/'build-requirements.lock')],check=True)
        subprocess.run([str(python_entry(root/'tools311')),'-m','pip','install','--require-hashes','--no-build-isolation','-r',str(root/'dependencies.lock')],check=True)
        if name=='httpcore':
            for filename in ['cancellation_regression.py','network_stress.py','tls_socket_regression.py']:
                shutil.copyfile(SOURCE/name/filename,root/filename)
        checks=[]
        for label,tests,code in [('base',root/'work/tests',root/'work'),('heldout',root/'oracle/tests',root/'work'),('oracle',root/'oracle/tests',root/'oracle')]:
            pythonpath=code/'src' if name=='jinja' else code
            environment={**__import__('os').environ,'PYTHONPATH':str(pythonpath)}
            xml=root/('baseline-'+label+'.xml')
            imports=root/('baseline-'+label+'-imports.json')
            result=subprocess.run([str(python_entry(root/'tools311')),str(Path(__file__).with_name('real_work_imports.py')),
                '--package','jinja2' if name=='jinja' else 'httpcore','--root',str(pythonpath),'--receipt',str(imports),
                'pytest','-q',str(tests),'--junitxml='+str(xml)],cwd=code,env=environment,capture_output=True,text=True,timeout=180)
            (root/('baseline-'+label+'.txt')).write_text(result.stdout+result.stderr, encoding='utf-8')
            witness=json.loads(imports.read_text(encoding='utf-8'))
            if witness.get('passed') is not True:raise RuntimeError('Baseline imported production code outside selected '+label+' tree')
            checks.append({'stage':label,**baseline_observation(xml,result.returncode),'source_imports':witness,'tail':(result.stdout+result.stderr)[-1600:]})
        (root/'baseline.json').write_text(json.dumps(checks,indent=2), encoding='utf-8')
        expected=[0,1,0]
        if name=='httpcore':
            if not all(valid_httpcore_baseline(item,os.name=='nt') for item in checks):raise RuntimeError('Unexpected HTTPcore baseline failure or test collection')
        elif [item['exit'] for item in checks]!=expected:raise RuntimeError('Baseline/oracle separation failed for '+name)
        if name=='httpcore':
            independent=[]
            for label in ['work','oracle']:
                environment={**__import__('os').environ,'PYTHONPATH':str(root/label)}
                result=subprocess.run([str(python_entry(root/'tools311')),'-m','pytest','-q',str(root/'cancellation_regression.py')],cwd=root/label,env=environment,capture_output=True,text=True,timeout=30)
                (root/('baseline-independent-'+label+'.txt')).write_text(result.stdout+result.stderr, encoding='utf-8')
                independent.append({'source':label,'exit':result.returncode,'tail':(result.stdout+result.stderr)[-1600:]})
            (root/'baseline-independent.json').write_text(json.dumps(independent,indent=2), encoding='utf-8')
            if [item['exit'] for item in independent]!=[1,0]:raise RuntimeError('Independent cancellation fixture did not distinguish base and fixed upstream sources')

    print(dest)

if __name__=='__main__':main()
