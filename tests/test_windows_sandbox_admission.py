"""Admission failures must precede native setup on a shared Windows host."""
from types import SimpleNamespace
from pathlib import Path
import os
import pytest
import verify_windows_sandbox as verifier

pytestmark=pytest.mark.skipif(os.name!="nt",reason="Windows-only lifecycle admission contract")

@pytest.fixture
def admission(tmp_path,monkeypatch):
    allowed=tmp_path/"owned";allowed.mkdir()
    args=SimpleNamespace(owned_preparation=tmp_path/"admission.json",owned_preparation_sha256="a"*64,output=allowed/"effects")
    manifest={"target_sid":"approved","session_id":1,"allowed_home_roots":[str(allowed)],"run_id":"b"*32}
    monkeypatch.setattr(verifier.preparation,"_read_manifest",lambda *a:manifest)
    monkeypatch.setattr(verifier.preparation,"managed_entry",lambda *a:True)
    identity={"sid":"approved","session_id":1,"elevated":False}
    monkeypatch.setattr(verifier.preparation,"_token",lambda *a:identity)
    monkeypatch.delenv(verifier.preparation.MANIFEST_ENV,raising=False)
    monkeypatch.delenv(verifier.preparation.HASH_ENV,raising=False)
    return args,identity

@pytest.mark.parametrize("field,value",[("sid","another"),("session_id",0),("elevated",True)])
def test_wrong_identity_never_arms_preparation(admission,field,value):
    args,identity=admission;identity[field]=value
    with pytest.raises(SystemExit,match="Limited interactive"):
        verifier.qualification_isolation(args,Path("entry.exe"))
    assert verifier.preparation.MANIFEST_ENV not in os.environ

def test_unrelated_output_refused_before_arming(admission,tmp_path):
    args,_=admission;args.output=tmp_path/"unowned"
    with pytest.raises(SystemExit,match="outside"):
        verifier.qualification_isolation(args,Path("entry.exe"))
    assert verifier.preparation.MANIFEST_ENV not in os.environ

def test_unadmitted_verifier_refused(admission,monkeypatch):
    args,_=admission
    monkeypatch.setattr(verifier.preparation,"managed_entry",lambda *a:False)
    with pytest.raises(SystemExit,match="not admitted"):
        verifier.qualification_isolation(args,Path("entry.exe"))

def test_partial_admission_cannot_fall_back_to_guest(admission,monkeypatch):
    args,_=admission;args.owned_preparation_sha256=None
    monkeypatch.setattr(verifier.platform,"node",lambda:"CDS-QA")
    with pytest.raises(SystemExit,match="manifest and SHA256"):
        verifier.qualification_isolation(args,Path("entry.exe"))

def test_other_host_still_requires_explicit_admission(tmp_path,monkeypatch):
    args=SimpleNamespace(owned_preparation=None,owned_preparation_sha256=None,output=tmp_path)
    monkeypatch.setattr(verifier.platform,"node",lambda:"SHARED-HOST")
    with pytest.raises(SystemExit,match="task-owned CDS-QA"):
        verifier.qualification_isolation(args,Path("entry.exe"))

def test_declared_limited_owned_scope(admission):
    args,_=admission
    proof=verifier.qualification_isolation(args,Path("entry.exe"))
    assert proof["kind"]=="owned_native_namespace"
    assert proof["controller"]["elevated"] is False
    assert os.environ[verifier.preparation.MANIFEST_ENV]==str(args.owned_preparation.resolve())


class ClosingPeer:
    def __init__(self, *, exit_code=0, reader_alive=False, wait_error=None, close_error=None):
        self.order=[]
        self.process=SimpleNamespace(stdin=SimpleNamespace(close=lambda:self.order.append("stdin_eof")))
        def wait(timeout):
            self.order.append("wait")
            if wait_error:raise wait_error
            return exit_code
        self.process.wait=wait
        self.reader=SimpleNamespace(join=lambda timeout:self.order.append("reader_join"),is_alive=lambda:reader_alive)
        self.close_error=close_error
    def close(self):
        self.order.append("peer_close")
        if self.close_error:raise self.close_error


def test_native_eof_precedes_generic_cleanup():
    peer=ClosingPeer()
    assert verifier.close_peer_gracefully(peer)=={"stdin_eof":True,"exit_code":0,"reader_eof":True}
    assert peer.order==["stdin_eof","wait","reader_join","peer_close"]


@pytest.mark.parametrize("options", [{"exit_code":1},{"reader_alive":True},{"wait_error":TimeoutError("native still running")}])
def test_shutdown_failure_cannot_be_repaired_into_pass_by_cleanup(options):
    peer=ClosingPeer(**options)
    with pytest.raises((RuntimeError,TimeoutError)):
        verifier.close_peer_gracefully(peer)
    assert peer.order[-1]=="peer_close"


def test_shutdown_preserves_primary_error_when_cleanup_also_fails():
    original=TimeoutError("native still running")
    peer=ClosingPeer(wait_error=original,close_error=RuntimeError("cleanup failure"))
    with pytest.raises(TimeoutError) as caught:
        verifier.close_peer_gracefully(peer)
    assert caught.value is original
    assert "cleanup failure" in str(original.__notes__)


def test_cleanup_error_cannot_be_a_successful_shutdown():
    peer=ClosingPeer(close_error=RuntimeError("protocol failure"))
    with pytest.raises(RuntimeError,match="protocol failure"):
        verifier.close_peer_gracefully(peer)


def test_main_retains_failure_receipt_when_final_cleanup_also_raises(tmp_path,monkeypatch):
    import json
    import sys
    release=tmp_path/"release";entry=release/"venv"/"Scripts"/"codex-deepseek.exe"
    entry.parent.mkdir(parents=True);entry.write_bytes(b"fixture")
    (release/"installation.json").write_text(json.dumps({"adapter_source_sha256":{},"model_catalog_sha256":"catalog"}))
    (release/"native-runtime.json").write_text(json.dumps({"target":"x86_64-pc-windows-msvc"}))
    monkeypatch.setattr(verifier,"qualification_isolation",lambda *args:{"kind":"disposable_guest"})
    monkeypatch.setattr(verifier,"console_entry",lambda path:entry)
    monkeypatch.setattr(verifier,"valid_release",lambda *args:True)
    monkeypatch.setattr(verifier,"private_directory",lambda path:path.mkdir(parents=True))
    original=RuntimeError("actual readiness failure")
    peer=ClosingPeer(close_error=RuntimeError("actual cleanup failure"))
    peer.windows_prepare_evidence=None
    def call(*args):raise original
    peer.call=call
    monkeypatch.setattr(verifier,"Peer",lambda *args,**kwargs:peer)
    output=tmp_path/"output"
    monkeypatch.setattr(sys,"argv",["verify_windows_sandbox.py","--entry",str(entry),"--output",str(output)])
    with pytest.raises(RuntimeError) as caught:
        verifier.main()
    assert caught.value is original
    receipts=list(output.glob("*/failure.json"))
    assert len(receipts)==1
    receipt=json.loads(receipts[0].read_text())
    assert receipt["passed"] is False
    assert receipt["failure"]["error"]=="actual readiness failure"
    assert receipt["cleanup_failure"]["error"]=="actual cleanup failure"
    assert not list(output.glob("*/verdict.json"))
