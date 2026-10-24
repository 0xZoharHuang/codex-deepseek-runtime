"""Original imported offline evidence keeps its snapshot; current qualification stays strict."""
import hashlib
import json
from pathlib import Path
import shutil
import pytest
import assemble_release_acceptance as acceptance
from test_offline_reference import offline_source, import_into_runtime, save

@pytest.mark.parametrize("change", ["none", "no_snapshot", "snapshot_bytes", "wrong_product",
    "standard_gate", "shared_helper", "raw_changed", "offline_subset", "original_failed", "current_stale"])
def test_frozen_original_offline_never_relabels_old_source_as_current(offline_source, tmp_path, change):
    root, installation, native, scope = offline_source
    source = acceptance.SOURCE
    for name, data in {"assemble_release_acceptance.py":b"same collector identity", "verify_hook_trust.py":b"old separate hook producer",
                       "verify_live.py":b"unchanged shared helper","src/pkg/runtime.py":b"unchanged product"}.items():
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    installation["source_sha256"]={"src/pkg/runtime.py":acceptance.sha(source/"src/pkg/runtime.py")}
    artifact=acceptance.load(root/"artifact.json")
    artifact.update(installation=installation,verifier_source_sha256=acceptance.qa_python_hashes(source))
    save(root/"artifact.json",artifact)
    runtime=import_into_runtime(offline_source,tmp_path)
    snapshot=tmp_path/"producer";shutil.copytree(source,snapshot)
    files={p.relative_to(snapshot).as_posix():acceptance.sha(p) for p in snapshot.rglob("*") if p.is_file()}
    receipt={"source_id":installation["source_id"],"files":files,
             "qa_sha256":hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()}
    save(tmp_path/"producer.manifest.json",receipt)
    (source/"verify_hook_trust.py").write_bytes(b"fixed separate hook producer")
    if change=="snapshot_bytes":(snapshot/"verify_live.py").write_bytes(b"tampered")
    elif change=="wrong_product":receipt["source_id"]="other";save(tmp_path/"producer.manifest.json",receipt)
    elif change=="standard_gate":(source/"run_acceptance.py").write_bytes(b"modified standard execution")
    elif change=="shared_helper":(source/"verify_live.py").write_bytes(b"modified shared execution")
    elif change=="raw_changed":(root/"offline.log").write_text("replacement log")
    elif change=="offline_subset":
        artifact["selected_gates"]=["offline","features"];save(root/"artifact.json",artifact)
    elif change=="original_failed":
        verdict=acceptance.load(root/"verdict.json");verdict["passed"]=False;save(root/"verdict.json",verdict)
    if change=="none":
        original=acceptance.validate_offline_run(*runtime,producer_snapshot=snapshot)
        assert original["external_execution"]==acceptance.load(runtime[0]/"artifact.json")["external_offline"]
        current=tmp_path/"current";shutil.copytree(root,current)
        current_artifact=acceptance.load(current/"artifact.json")
        current_artifact["verifier_source_sha256"]=acceptance.qa_python_hashes(source)
        save(current/"artifact.json",current_artifact)
        assert acceptance.validate_offline_run(current,installation,native,scope,current=True)["tests_passed"]==1
        with pytest.raises(SystemExit):acceptance.validate_offline_run(*offline_source,current=True,producer_snapshot=snapshot)
    elif change=="current_stale":
        with pytest.raises(SystemExit):acceptance.validate_offline_run(*offline_source,current=True,producer_snapshot=snapshot)
    else:
        with pytest.raises(SystemExit):
            acceptance.validate_offline_run(*runtime,producer_snapshot=None if change=="no_snapshot" else snapshot)
