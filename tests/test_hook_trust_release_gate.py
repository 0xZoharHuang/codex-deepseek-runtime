"""Release assembly must re-read raw hook evidence, not trust passed flags."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from assemble_release_acceptance import validate_hook_trust_cases
from verify_hook_trust import CASE_NAMES

_spec=importlib.util.spec_from_file_location("hook_contract_test_data",Path(__file__).with_name("test_hook_trust_acceptance.py"))
_data=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_data)

def dump(path,value):
    path.write_text(json.dumps(value),encoding="utf-8")

def rehash(case,row):
    row["evidence_sha256"]={p.relative_to(case).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in case.rglob("*") if p.is_file()}

@pytest.fixture
def evidence(tmp_path):
    rows=[];previous=[]
    for index,name in enumerate(CASE_NAMES):
        case=tmp_path/name;case.mkdir()
        data=_data.fixture(name)
        nonce="nonce-"+str(index);reply="FINAL-"+nonce
        data["expected_effect"]={"nonce":nonce,"reply":reply};data["shell_effect"]=data["expected_effect"].copy()
        data["completed"].update(reply=reply)
        item=data["native_thread"]["thread"]["turns"][0]["items"][0]
        item.update(command="python shell-trust-fixture.py "+nonce,aggregatedOutput="ACTUAL_SHELL_"+nonce)
        for stage in ["before_hook","after_hook"]:
            data[stage]["command"]="python hook-trust-fixture.py"
        data["calls_before"]=copy.deepcopy(previous)
        data["hook_nonce"]="HOOK" if index<2 else "CHANGED_HOOK"
        data["calls_after"]=(previous+[{"hook_nonce":data["hook_nonce"],"tool_input":item["command"]}]) if index==1 else copy.deepcopy(previous)
        previous=copy.deepcopy(data["calls_after"])
        for label,raw in {"config-before.toml":data["before_config"],"config-after.toml":data["after_config"],
                          "terminal.pty":data["transcript"]}.items():(case/label).write_bytes(raw)
        for label,value in {
            "expectations.json":{"name":name,"hook_nonce":data["hook_nonce"],"shell_effect":data["expected_effect"],
                                 "before_hook":data["before_hook"],"after_hook":data["after_hook"]},
            "hooks-before.json":{"data":[{"hooks":[data["before_hook"]]}]},
            "hooks-after.json":{"data":[{"hooks":[data["after_hook"]]}]},
            "hook-calls-before.json":data["calls_before"],"hook-calls-after.json":data["calls_after"],
            "shell-effect.json":data["shell_effect"],"inputs.json":data["inputs"],
            "completion.json":data["completed"],"native-thread.json":data["native_thread"],
            "cleanup.json":{"passed":True,"errors":[]},
            "hooks-declaration.json":{"declaration":"changed" if index==2 else "original"},
        }.items():dump(case/label,value)
        # These placeholders are only for offline counterexample tests, never runtime proof.
        (case/"native-session.jsonl").write_text("{}\\n",encoding="utf-8")
        (case/"rollout.jsonl").write_text("{}\\n",encoding="utf-8")
        row={"name":name,"root":name,"passed":True};rehash(case,row);rows.append(row)
    return tmp_path,rows

def test_raw_three_case_reconstruction(evidence):
    root,rows=evidence
    result=validate_hook_trust_cases(root,rows)
    assert result["passed"] and result["raw_reconstructed"] and result["cases"]==list(CASE_NAMES)

@pytest.mark.parametrize("problem",["missing_case","tampered_file","unexpected_file","foreign_root",
                                   "failed_native_turn","choice_before_ui","unchanged_declaration","cleanup_failure"])
def test_saved_success_cannot_hide_raw_failure(evidence,problem):
    root,rows=evidence;case=root/CASE_NAMES[1];row=rows[1]
    if problem=="missing_case":rows.pop()
    elif problem=="tampered_file":(case/"terminal.pty").write_bytes(b"changed")
    elif problem=="unexpected_file":(case/"unrecorded").write_text("unexpected")
    elif problem=="foreign_root":row["root"]="../outside"
    elif problem=="failed_native_turn":
        value=json.loads((case/"native-thread.json").read_text())
        value["thread"]["turns"][0]["status"]="failed";dump(case/"native-thread.json",value);rehash(case,row)
    elif problem=="choice_before_ui":
        value=json.loads((case/"inputs.json").read_text());value[0]["after_output_bytes"]=0
        dump(case/"inputs.json",value);rehash(case,row)
    elif problem=="unchanged_declaration":
        case=root/CASE_NAMES[2];row=rows[2]
        (case/"hooks-declaration.json").write_bytes((root/CASE_NAMES[1]/"hooks-declaration.json").read_bytes());rehash(case,row)
    elif problem=="cleanup_failure":
        dump(case/"cleanup.json",{"passed":True,"errors":["owner still alive"]});rehash(case,row)
    with pytest.raises((AssertionError,SystemExit)):
        validate_hook_trust_cases(root,rows)
