"""Pure failure-path receipts: no native processes, filesystem ACL changes or model calls."""
import json

from verify_real_work import cleanup_real_work


class Guard:
    def __init__(self,*,lock_error=None,restore_error=None):
        self.lock_error=lock_error;self.restore_error=restore_error
        self.restores=0;self.evidence_reads=0
    def lock(self):
        if self.lock_error:raise self.lock_error
    def restore(self):
        self.restores+=1
        if self.restore_error:raise self.restore_error
    @property
    def evidence(self):
        self.evidence_reads+=1
        raise AssertionError("Protected oracle must not be read after cleanup failure")


class Peer:
    def __init__(self,error=None):self.error=error;self.closes=0
    def close(self):
        self.closes+=1
        if self.error:raise self.error


def receipt(root):return json.loads((root/"results.json").read_text(encoding="utf-8"))


def test_lock_and_restore_failure_preserves_primary_and_persists_without_oracle_read(tmp_path):
    guard=Guard(lock_error=PermissionError("primary lock denied"),
                restore_error=PermissionError("cleanup restore denied"))
    report={"case":"fixture","passed":True}
    try:guard.lock()
    except Exception as error:
        report["error"]=str(error)
        report["primary_error"]={"type":type(error).__name__,"error":str(error)}
    assert cleanup_real_work(report,None,guard,tmp_path) is False
    saved=receipt(tmp_path)
    assert saved["error"]=="primary lock denied"
    assert saved["primary_error"]=={"type":"PermissionError","error":"primary lock denied"}
    assert saved["cleanup_errors"]==[{"phase":"oracle.restore","type":"PermissionError","error":"cleanup restore denied"}]
    assert saved["passed"] is False and saved["oracle_guard"]["restore_succeeded"] is False
    assert guard.restores==1 and guard.evidence_reads==0


def test_peer_close_failure_still_restores_oracle_and_keeps_task_error(tmp_path):
    peer=Peer(RuntimeError("close failed"));guard=Guard()
    report={"error":"original task failure","passed":True}
    assert cleanup_real_work(report,peer,guard,tmp_path) is False
    saved=receipt(tmp_path)
    assert peer.closes==guard.restores==1 and guard.evidence_reads==0
    assert saved["error"]=="original task failure" and saved["passed"] is False
    assert saved["cleanup_errors"][0]["phase"]=="peer.close"
    assert saved["oracle_guard"]["restore_succeeded"] is True


def test_cleanup_only_failure_cannot_leave_a_passing_report(tmp_path):
    peer=Peer();guard=Guard(restore_error=PermissionError("restore denied"))
    report={"error":None,"passed":True}
    assert cleanup_real_work(report,peer,guard,tmp_path) is False
    saved=receipt(tmp_path)
    assert peer.closes==guard.restores==1 and guard.evidence_reads==0
    assert saved["error"]=="Real-work cleanup failed" and saved["passed"] is False
    assert saved.get("primary_error") is None
    assert saved["cleanup_errors"][0]["error"]=="restore denied"
    assert saved["oracle_guard"]["source_unchanged"] is False
