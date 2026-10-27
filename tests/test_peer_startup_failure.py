"""A failed native initialize preserves its real exit/stderr before reviewer checks."""
import hashlib,json,os,sys
from pathlib import Path
import pytest
from verify_live import Peer


def test_failed_initialize_preserves_original_exit_and_stderr(tmp_path,monkeypatch):
    monkeypatch.delenv('CODEX_DEEPSEEK_TEST_WINDOWS_PREPARE_MANIFEST',raising=False)
    monkeypatch.delenv('CODEX_DEEPSEEK_TEST_WINDOWS_PREPARE_SHA256',raising=False)
    (tmp_path/'work').mkdir();(tmp_path/'home').mkdir()
    fake=tmp_path/'failed-native.py'
    fake.write_text("import sys\nsys.stdin.readline()\nsys.stderr.write('ORIGINAL_NATIVE_STARTUP_FAILURE\\n');sys.stderr.flush();raise SystemExit(42)\n",encoding='utf-8')
    with pytest.raises((RuntimeError,BrokenPipeError)):
        Peer(tmp_path,command=[sys.executable,'-I','-B',str(fake)])
    receipts=list(tmp_path.glob('startup-failure-*.json'))
    assert len(receipts)==1
    receipt=json.loads(receipts[0].read_text(encoding='utf-8'))
    assert receipt['initialized'] is False and receipt['returncode_after_cleanup']==42
    stderr=tmp_path/receipt['stderr_file']
    assert stderr.read_text(encoding='utf-8')=='ORIGINAL_NATIVE_STARTUP_FAILURE\n'
    assert receipt['stderr_sha256']==hashlib.sha256(stderr.read_bytes()).hexdigest()
    assert receipt['primary_error'] and receipt['cleanup_error'] is None
    assert not list((tmp_path/'home').glob('state_*.sqlite'))
