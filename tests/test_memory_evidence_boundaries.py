"""Native memory and raw QA evidence must never enter directory links."""
import os
from pathlib import Path
import pytest
import qa_workspace as workspace
import verify_memory_audit as memory

def directory_link(alias, target):
    if os.name=="nt":workspace._junction(alias,target)
    else:alias.symlink_to(target,target_is_directory=True)

def layout(root,version):
    home=root/"home";home.mkdir(parents=True)
    folder=home/("memories_v2" if version=="v2" else "memories");folder.mkdir()
    if version=="v2":
        (folder/"rollout_summaries").mkdir();artifact=folder/"rollout_summaries/current.md"
    else:artifact=folder/"MEMORY.md"
    artifact.write_text("current artifact")
    summary=folder/"memory_summary.md";summary.write_text("current summary")
    return home,folder,artifact,summary

@pytest.mark.parametrize("version",["v1","v2"])
def test_memory_only_reads_exact_native_version_output(tmp_path,monkeypatch,version):
    home,folder,artifact,summary=layout(tmp_path/"owned",version)
    outside=tmp_path/"old";outside.mkdir();(outside/"memory_summary.md").write_text("foreign")
    (home/".sandbox/cwd").mkdir(parents=True);directory_link(home/".sandbox/cwd/root",outside)
    (home/"memory_summary.md").write_text("wrong native location")
    other=home/("memories" if version=="v2" else "memories_v2");other.mkdir()
    (other/"memory_summary.md").write_text("other version")
    def forbidden(*args,**kwargs):raise AssertionError("Do not recursively traverse native home")
    monkeypatch.setattr(Path,"rglob",forbidden)
    assert memory.memory_output_files(home,version)==([artifact],[summary])
    summary.unlink()
    assert memory.memory_output_files(home,version)==([artifact],[])

@pytest.mark.parametrize("version",["v1","v2"])
@pytest.mark.parametrize("part",["home","memory","summary","artifact"])
def test_memory_linked_outputs_are_rejected(tmp_path,version,part):
    home,folder,artifact,summary=layout(tmp_path/"owned",version)
    outside=tmp_path/"foreign";outside.mkdir()
    if part in {"home","memory"}:
        path=home if part=="home" else folder
        target=outside/path.name;path.rename(target);directory_link(path,target)
    else:
        path=summary if part=="summary" else artifact
        target=outside/path.name;path.rename(target);path.symlink_to(target)
    with pytest.raises(ValueError,match="physical|Linked"):memory.memory_output_files(home,version)

def test_memory_rollout_summary_directory_cannot_escape(tmp_path):
    home,folder,artifact,summary=layout(tmp_path/"owned","v2")
    path=folder/"rollout_summaries";target=tmp_path/"foreign";path.rename(target);directory_link(path,target)
    with pytest.raises(ValueError,match="Linked"):memory.memory_output_files(home,"v2")

@pytest.mark.parametrize("version",["v1","v2"])
def test_absent_native_memory_does_not_reuse_unrelated_summaries(tmp_path,version):
    home=tmp_path/"home";home.mkdir();(home/"memory_summary.md").write_text("unrelated")
    assert memory.memory_output_files(home,version)==([],[])

def test_evidence_walk_prunes_junctions_symlinks_and_cycles_before_scanning(tmp_path,monkeypatch):
    root=tmp_path/"owned";root.mkdir();(root/"client-requests.jsonl").write_text("current")
    sub=root/"nested";sub.mkdir();(sub/"receipt.json").write_text("{}")
    outside=tmp_path/"other-work";outside.mkdir();(outside/"client-requests.jsonl").write_text("foreign")
    (root/"home/.sandbox/cwd").mkdir(parents=True)
    directory_link(root/"home/.sandbox/cwd/foreign",outside)
    directory_link(sub/"cycle",root)
    (sub/"linked.json").symlink_to(outside/"client-requests.jsonl")
    scanned=[];scandir=os.scandir;physical=root.resolve()
    def guarded(path):
        assert Path(path).resolve().is_relative_to(physical),"Foreign directory was scanned"
        scanned.append(Path(path));return scandir(path)
    monkeypatch.setattr(workspace.os,"scandir",guarded)
    assert {p.relative_to(root).as_posix() for p in workspace.evidence_files(root)}=={"client-requests.jsonl","nested/receipt.json"}
    assert list(workspace.evidence_files(root,"client-requests.jsonl"))==[root/"client-requests.jsonl"]
    assert all(not workspace._link(path) for path in scanned)

def test_evidence_walk_rejects_a_linked_root(tmp_path):
    physical=tmp_path/"physical";physical.mkdir()
    alias=tmp_path/"alias";directory_link(alias,physical)
    with pytest.raises(ValueError,match="physical"):list(workspace.evidence_files(alias))

def test_evidence_walk_does_not_silently_hide_scan_errors(tmp_path,monkeypatch):
    def denied(path):raise PermissionError("owned tree unavailable")
    monkeypatch.setattr(workspace.os,"scandir",denied)
    with pytest.raises(PermissionError):list(workspace.evidence_files(tmp_path))
