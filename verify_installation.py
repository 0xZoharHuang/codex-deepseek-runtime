"""Real immutable installation, import isolation, repair and repeated reuse.

Does not activate or corrupt the current public launcher. Operates on a newly
installed candidate and a separately provisioned shadow project only.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

SOURCE=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--wheelhouse',type=Path);args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True)
    spec=importlib.util.spec_from_file_location('runtime_installer',SOURCE/'install.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    data=root/'runtime-data';data.mkdir(parents=True,mode=0o700)
    current=Path.home()/('.local/bin/codex-deepseek.cmd' if installer.WINDOWS else '.local/bin/codex-deepseek');prior=current.resolve() if current.exists() else None
    prior_bytes=current.read_bytes() if current.exists() else None
    def launcher_unchanged():return current.exists()==(prior is not None) and (prior is None or (current.resolve()==prior and current.read_bytes()==prior_bytes))
    first=installer.install(data,native_archive=args.archive,wheelhouse=args.wheelhouse,activate=False)
    report={'initial_release':str(first),'previous_launcher':str(prior) if prior else None,'checks':[]}
    def passed(name,condition,detail):
        report['checks'].append({'name':name,'passed':bool(condition),'detail':detail});(root/'results.json').write_text(json.dumps(report,indent=2));print(json.dumps(report['checks'][-1]),flush=True)
        if not condition:raise AssertionError(name)
    first_entry=installer.console_entry(first/'venv')
    if prior==first_entry.resolve():raise RuntimeError('Repair fault injection must never corrupt the active launcher release')
    passed('installation_does_not_activate_before_validation',launcher_unchanged(),{})
    shadow=root/'shadow';(shadow/'codex_deepseek').mkdir(parents=True)
    (shadow/'codex_deepseek/__init__.py').write_text('raise RuntimeError("PROJECT_IMPORT_INJECTION")')
    nonexistent=root/'offline-home'
    environment={**os.environ,'CODEX_DEEPSEEK_HOME':str(nonexistent),'PYTHONPATH':str(shadow),'OPENROUTER_API_KEY':''}
    result=subprocess.run([str(first_entry),'--version'],cwd=shadow,env=environment,capture_output=True,text=True,timeout=20)
    passed('installed_entry_isolated_imports_and_offline_version',result.returncode==0 and '0.164.0-alpha.7' in result.stdout and 'PROJECT_IMPORT_INJECTION' not in result.stderr and not nonexistent.exists(),{'stdout':result.stdout,'stderr':result.stderr})
    identity,all_hashes=installer.source_identity();hashes={path:digest for path,digest in all_hashes.items() if path.startswith('src/')};catalog=installer.sha(SOURCE/'catalog.json');native_manifest=installer.runtime_manifest()
    passed('installed_full_integrity_check',installer.valid_release(first,hashes,catalog,native_manifest),{})
    broken=b'BROKEN_CANDIDATE_ENTRY';first_entry.write_bytes(broken)
    passed('corruption_detected',not installer.valid_release(first,hashes,catalog,native_manifest),{})
    repaired=installer.install(data,native_archive=args.archive,wheelhouse=args.wheelhouse,activate=False)
    passed('repair_creates_new_immutable_release',repaired!=first and first_entry.read_bytes()==broken and installer.valid_release(repaired,hashes,catalog,native_manifest),{'repaired_release':str(repaired)})
    again=installer.install(data,native_archive=args.archive,wheelhouse=args.wheelhouse,activate=False)
    passed('repeated_install_reuses_verified_repair',again==repaired,{})
    passed('public_launcher_still_preserved',launcher_unchanged(),{})
    report['candidate']=str(repaired);report['passed']=all(item['passed'] for item in report['checks']);(root/'results.json').write_text(json.dumps(report,indent=2));print('CANDIDATE',repaired,flush=True);print('EVIDENCE',root,flush=True)


if __name__=='__main__':main()
