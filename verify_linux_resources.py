"""Force native bundled bubblewrap, enforce permissions, and reject tampered bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from install import valid_release,console_entry


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    if not __debug__:raise SystemExit('Acceptance assertions must be enabled')
    p=argparse.ArgumentParser();p.add_argument('--entry',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not sys.platform.startswith('linux'):raise SystemExit('Native bundled-bwrap acceptance requires Linux')
    entry=args.entry.resolve();release=entry.parent.parent.parent
    installation=json.loads((release/'installation.json').read_text());native=json.loads((release/'native-runtime.json').read_text())
    if entry!=console_entry(release/'venv') or not valid_release(release,installation['adapter_source_sha256'],installation['model_catalog_sha256'],native):raise SystemExit('Verified immutable Linux runtime required')
    package=Path(installation['native_package']);binary=package/'bin/codex';resource=package/'codex-resources/bwrap'
    root=args.output.resolve()/str(uuid.uuid4());(root/'home').mkdir(parents=True,mode=0o700);(root/'work').mkdir()
    artifact={'installation':installation,'native_manifest':native,'target':native['target'],'entry_sha256':sha(entry),
              'verifier_sha256':sha(Path(__file__)),'binary_sha256':sha(binary),'bwrap_sha256':sha(resource)}
    (root/'artifact.json').write_text(json.dumps(artifact,indent=2));outside=root/'outside.txt';outside.write_text('PRESERVE')
    program='''from pathlib import Path
import errno
import sys
Path('inside.txt').write_text('NATIVE_ALLOWED')
try:Path(sys.argv[1]).write_text('ESCAPED')
except OSError as exc:
    if exc.errno not in {errno.EACCES,errno.EPERM,errno.EROFS}:raise
    print('NATIVE_OUTSIDE_DENIED')
else:raise SystemExit('outside permission unexpectedly granted')
'''
    environment={k:v for k,v in os.environ.items() if not k.startswith('CODEX_') and k not in {'OPENROUTER_API_KEY','OPENAI_API_KEY'}}
    # No bwrap is discoverable through PATH; the native resource fallback must run.
    environment.update(CODEX_HOME=str(root/'home'),PATH=str(root/'empty-path'))
    (root/'empty-path').mkdir()
    arguments=['sandbox','-c','sandbox_mode="workspace-write"','-c','features.use_linux_sandbox_bwrap=true','--',sys._base_executable,'-I','-c',program,str(outside)]
    result=subprocess.run([str(binary),*arguments],env=environment,cwd=root/'work',capture_output=True,text=True,timeout=45)
    (root/'native-sandbox.txt').write_text(result.stdout+result.stderr)
    assert result.returncode==0 and 'NATIVE_OUTSIDE_DENIED' in result.stdout,(result.returncode,result.stdout,result.stderr)
    assert (root/'work/inside.txt').read_text()=='NATIVE_ALLOWED' and outside.read_text()=='PRESERVE'
    corrupt=root/'corrupt-package';(corrupt/'bin').mkdir(parents=True);(corrupt/'codex-resources').mkdir()
    os.link(binary,corrupt/'bin/codex');shutil.copy2(package/'codex-package.json',corrupt/'codex-package.json');shutil.copy2(resource,corrupt/'codex-resources/bwrap')
    with (corrupt/'codex-resources/bwrap').open('r+b') as f:
        f.seek(-1,2);value=f.read(1);f.seek(-1,2);f.write(bytes([value[0]^1]))
    (root/'work/inside.txt').unlink()
    rejected=subprocess.run([str(corrupt/'bin/codex'),*arguments],env=environment,cwd=root/'work',capture_output=True,text=True,timeout=45)
    (root/'native-digest-rejection.txt').write_text(rejected.stdout+rejected.stderr)
    assert rejected.returncode!=0 and 'bundled bubblewrap digest mismatch' in rejected.stderr
    assert not (root/'work/inside.txt').exists() and outside.read_text()=='PRESERVE'
    unchanged=(sha(binary)==artifact['binary_sha256'] and sha(resource)==artifact['bwrap_sha256']
               and sha(entry)==artifact['entry_sha256'] and sha(Path(__file__))==artifact['verifier_sha256'])
    assert unchanged
    proof={'passed':True,'installation':installation,'target':native['target'],'bundled_resource_forced':True,
           'native_inside_write':True,'native_outside_write_denied':True,'tampered_bwrap_rejected_before_execution':True,
           'native_exit_code':result.returncode,'tampered_exit_code':rejected.returncode,
           'verifier_and_artifact_unchanged':unchanged}
    (root/'verdict.json').write_text(json.dumps(proof,indent=2));shutil.rmtree(corrupt)
    print(json.dumps({k:v for k,v in proof.items() if k!='installation'}));print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
