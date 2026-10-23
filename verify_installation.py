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


def launcher_snapshot(path):
    path=Path(path)
    if not path.exists() and not path.is_symlink():return None
    return {'symlink':str(path.readlink()) if path.is_symlink() else None,
            'resolved':str(path.resolve()),'bytes':path.read_bytes() if path.is_file() else None}


def verify(args,installer):
    # Validate the explicit candidate manifest before creating any output.
    native_manifest=installer.runtime_manifest(args.native_manifest)
    manifest_path=args.native_manifest.resolve() if args.native_manifest is not None else None
    manifest_digest=installer.sha(manifest_path) if manifest_path else None
    root=args.output.resolve()/str(uuid.uuid4());root.mkdir(parents=True)
    data=root/'runtime-data';data.mkdir(parents=True,mode=0o700)
    names=['codex-deepseek.exe','codex-deepseek.cmd'] if installer.WINDOWS else ['codex-deepseek']
    launchers={str(Path.home()/'.local/bin'/name):launcher_snapshot(Path.home()/'.local/bin'/name) for name in names}
    def launcher_unchanged():return all(launcher_snapshot(path)==prior for path,prior in launchers.items())
    install_options={'native_archive':args.archive,'native_manifest_path':args.native_manifest,
                     'wheelhouse':args.wheelhouse,'activate':False}
    first=installer.install(data,**install_options)
    report={'initial_release':str(first),'native_manifest':native_manifest,
            'explicit_native_manifest_sha256':manifest_digest,'checks':[]}
    def passed(name,condition,detail):
        report['checks'].append({'name':name,'passed':bool(condition),'detail':detail})
        (root/'results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report['checks'][-1]),flush=True)
        if not condition:raise AssertionError(name)
    first_entry=installer.console_entry(first/'venv')
    if any(prior and prior['resolved']==str(first_entry.resolve()) for prior in launchers.values()):
        raise RuntimeError('Repair fault injection must never corrupt an active launcher release')
    passed('installation_does_not_activate_before_validation',launcher_unchanged(),{})
    shadow=root/'shadow';(shadow/'codex_deepseek').mkdir(parents=True)
    (shadow/'codex_deepseek/__init__.py').write_text('raise RuntimeError("PROJECT_IMPORT_INJECTION")')
    nonexistent=root/'offline-home'
    environment={**os.environ,'CODEX_DEEPSEEK_HOME':str(nonexistent),'PYTHONPATH':str(shadow),'OPENROUTER_API_KEY':''}
    result=subprocess.run([str(first_entry),'--version'],cwd=shadow,env=environment,capture_output=True,text=True,timeout=20)
    passed('installed_entry_isolated_imports_and_offline_version',result.returncode==0 and native_manifest['upstream_version'] in result.stdout and 'PROJECT_IMPORT_INJECTION' not in result.stderr and not nonexistent.exists(),{'stdout':result.stdout,'stderr':result.stderr})
    identity,all_hashes=installer.source_identity()
    hashes={path:digest for path,digest in all_hashes.items() if path.startswith('src/')}
    catalog=installer.sha(SOURCE/'catalog.json')
    passed('installed_full_integrity_check',installer.valid_release(first,hashes,catalog,native_manifest),{})
    broken=b'BROKEN_CANDIDATE_ENTRY';first_entry.write_bytes(broken)
    passed('corruption_detected',not installer.valid_release(first,hashes,catalog,native_manifest),{})
    repaired=installer.install(data,**install_options)
    passed('repair_creates_new_immutable_release',repaired!=first and first_entry.read_bytes()==broken and installer.valid_release(repaired,hashes,catalog,native_manifest),{'repaired_release':str(repaired)})
    again=installer.install(data,**install_options)
    passed('repeated_install_reuses_verified_repair',again==repaired,{})
    passed('public_launchers_still_preserved',launcher_unchanged(),{'paths':list(launchers)})
    passed('source_and_explicit_manifest_unchanged',
           installer.source_identity()==(identity,all_hashes)
           and (manifest_path is None or installer.sha(manifest_path)==manifest_digest),{})
    report['candidate']=str(repaired)
    report['passed']=all(item['passed'] for item in report['checks'])
    (root/'results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('CANDIDATE',repaired,flush=True);print('EVIDENCE',root,flush=True)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--native-manifest',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--wheelhouse',type=Path)
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('runtime_installer',SOURCE/'install.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    verify(args,installer)


if __name__=='__main__':main()
