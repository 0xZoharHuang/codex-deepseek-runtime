"""Retain upstream notices, declared attribution and canonical SPDX license texts."""
from __future__ import annotations
import hashlib
import json
import platform
from pathlib import Path
import re
import shutil
import subprocess

SOURCE=Path(__file__).resolve().parent


def collect(manifest,output,*,metadata=None,target=None):
    platform_target=target or {('Darwin','arm64'):'aarch64-apple-darwin',('Linux','x86_64'):'x86_64-unknown-linux-gnu',('Windows','AMD64'):'x86_64-pc-windows-msvc'}[(platform.system(),platform.machine())]
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if metadata is None:
        result=subprocess.run(['cargo','+1.95.0','metadata','--locked','--format-version','1','--filter-platform',platform_target,'--manifest-path',str(manifest)],check=True,capture_output=True,text=True,encoding='utf-8')
        metadata=json.loads(result.stdout)
    license_data=json.loads((SOURCE/'native/license-data.json').read_text(encoding='utf-8'))
    for row in license_data['texts']:
        path=SOURCE/'licenses/spdx'/(row['id']+'.txt')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('SPDX source integrity mismatch')
        destination=output/'spdx'/path.name;destination.parent.mkdir(exist_ok=True);shutil.copy2(path,destination)
    entries=[]
    for package in sorted(metadata['packages'],key=lambda p:p['id']):
        if not package.get('source'):continue
        source=Path(package['manifest_path']).parent
        key=package['name']+'-'+package['version']+'-'+hashlib.sha256(package['id'].encode()).hexdigest()[:8]
        target=output/'crates'/key;target.mkdir(parents=True)
        candidates={path for path in source.iterdir() if path.is_file() and any(part in path.name.upper() for part in ['LICENSE','NOTICE','COPYING','COPYRIGHT'])}
        if package.get('license_file'):
            path=Path(package['license_file']);path=path if path.is_absolute() else source/path
            if path.is_file():candidates.add(path)
        for directory in ['licenses','LICENSES','third_party/licenses','third-party/licenses']:
            if (source/directory).is_dir():candidates.update(path for path in (source/directory).rglob('*') if path.is_file())
        # Some crates omit license files from their published archive. Preserve
        # their README notices and declared Cargo attribution alongside the SPDX
        # text, without inventing a copyright owner or a license selection.
        if not candidates:candidates.update(path for path in source.glob('README*') if path.is_file())
        notices=[]
        for index,path in enumerate(sorted(candidates)):
            name=str(index)+'-'+path.name;shutil.copy2(path,target/name);notices.append((target/name).relative_to(output).as_posix())
        declarations={'name':package['name'],'version':package['version'],'source':package['source'],'repository':package.get('repository'),'authors':package.get('authors',[]),'declared_license':package.get('license'),'upstream_notice_files':notices,'manifest_sha256':hashlib.sha256(Path(package['manifest_path']).read_bytes()).hexdigest()}
        (target/'attribution.json').write_text(json.dumps(declarations,indent=2), encoding='utf-8');entries.append(declarations)
    index={'scope':'Superset of native workspace external dependencies resolved for '+platform_target+'; includes Code Mode Host and build/test dependencies','spdx_source':license_data,'packages':entries}
    (output/'index.json').write_text(json.dumps(index,indent=2), encoding='utf-8');return len(entries)
