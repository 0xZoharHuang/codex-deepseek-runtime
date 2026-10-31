"""The source inventory helper is loaded from the producer checkout under -I."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("poison_cached_build", [False, True])
def test_native_source_snapshot_uses_exact_checkout_and_restores_import_path(tmp_path, poison_cached_build):
    root=Path(__file__).resolve().parents[1]
    checkout=tmp_path/"checkout";checkout.mkdir()
    (checkout/"build_native.py").write_bytes((root/"build_native.py").read_bytes())
    (checkout/"verify_native_source.py").write_bytes(
        b'import build_native as build\n'
        b'def upstream_inventory(archive): return {}, {"known":"link"}\n'
        b'def source_inventory(tree, links):\n'
        b'    assert links=={"known":"link"}\n'
        b'    return {"source":"exact-checkout-value"}\n'
        b'def inventory_digest(files): return "exact-checkout-digest"\n')
    unrelated=tmp_path/"unrelated";unrelated.mkdir()
    (unrelated/"verify_native_source.py").write_bytes(b'raise AssertionError("untrusted cwd verifier")')
    (unrelated/"build_native.py").write_bytes(b'raise AssertionError("untrusted cwd builder")')
    code='import importlib.util,json,pathlib,sys,types;'
    if poison_cached_build:
        code+='sys.modules["build_native"]=types.SimpleNamespace(SOURCE=pathlib.Path(sys.argv[2]));'
    code+='spec=importlib.util.spec_from_file_location("ownedbuilder",sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);before=list(sys.path);'
    code+='result=m.native_source_snapshot(pathlib.Path("source"),pathlib.Path("archive"));assert sys.path==before;print(json.dumps(result))'
    process=subprocess.run([sys.executable,"-I","-B","-c",code,str(checkout/"build_native.py"),str(unrelated)],
                           cwd=unrelated,capture_output=True,text=True,
                           env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    if poison_cached_build:
        assert process.returncode!=0 and "another source checkout" in process.stderr
    else:
        assert process.returncode==0,process.stderr
        assert json.loads(process.stdout)=={"files":{"source":"exact-checkout-value"},"sha256":"exact-checkout-digest"}
