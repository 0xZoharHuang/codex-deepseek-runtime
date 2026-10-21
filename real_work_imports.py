"""Record actual fixture imports without importing the target before its tests.

pytest's official importlib mode leaves sys.path unchanged. The pinned Jinja
suite imports standalone test resources and asserts test module names, so it
uses the official append mode; the same production-import guard remains strict.
https://docs.pytest.org/en/stable/explanation/pythonpath.html#import-modes
"""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys

from install import _support


def loaded_sources(package,root,modules=None):
    modules=sys.modules if modules is None else modules
    records={};invalid=[];expected=_support.comparison_path(root)
    for name,module in sorted(modules.items()):
        if name!=package and not name.startswith(package+'.'):continue
        filename=getattr(module,'__file__',None)
        path=Path(filename) if filename else None
        valid=path is not None and path.is_file() and _support.comparison_path(path).is_relative_to(expected)
        records[name]={'file':str(path) if path else None,'sha256':hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None}
        if not valid:invalid.append(name)
    return {'package':package,'expected_root':str(Path(root).resolve()),'modules':records,'invalid_modules':invalid,
            'passed':package in records and bool(records) and not invalid}


class ImportReceipt:
    def __init__(self,package,root,path):self.package=package;self.root=root;self.path=path;self.receipt=None
    def record(self):
        self.receipt=loaded_sources(self.package,self.root)
        self.path.write_text(json.dumps(self.receipt,indent=2)+'\n',encoding='utf-8')
        print('ACTUAL_TARGET_IMPORTS '+json.dumps(self.receipt,sort_keys=True),flush=True)
        return self.receipt['passed']
    def pytest_sessionfinish(self,session,exitstatus):
        if not self.record():session.exitstatus=1


def main():
    if not __debug__:raise SystemExit('Real-work import verification requires active assertions')
    parser=argparse.ArgumentParser();parser.add_argument('--package',required=True);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True);parser.add_argument('mode',choices=['pytest','script']);parser.add_argument('args',nargs=argparse.REMAINDER)
    args=parser.parse_args();guard=ImportReceipt(args.package,args.root,args.receipt)
    if args.mode=='pytest':
        import pytest
        import_mode='append' if args.package=='jinja2' else 'importlib'
        code=pytest.main(['--import-mode='+import_mode,*args.args],plugins=[guard])
        if guard.receipt is None:guard.record()
        raise SystemExit(int(code) if code else (0 if guard.receipt['passed'] else 1))
    if len(args.args)!=1:raise SystemExit('A single independent fixture script is required')
    script=Path(args.args[0]).resolve();sys.argv=[str(script)];sys.path[0]=str(script.parent)
    try:runpy.run_path(str(script),run_name='__main__')
    finally:
        if not guard.record():raise SystemExit('Independent script imported target code outside the tested work tree')


if __name__=='__main__':main()
