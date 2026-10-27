import copy
import json
import sqlite3
from contextlib import closing

import pytest

from verify_real_work import native_reviewer_completions, reviewer_evidence, fresh_reviewer_completed
from verify_real_work_followup import reviewers


def records():
    rollout=[{"type":"event_msg","payload":{"type":"task_complete","turn_id":"child-turn",
        "root_turn_id":"parent-turn","last_agent_message":"Reviewed the patch and its regressions."}}]
    events=[{"method":"turn/completed","params":{"threadId":"reviewer","turn":{
        "id":"child-turn","rootTurnId":"parent-turn","status":"completed","error":None}}}]
    return rollout,events


def test_matches_real_child_turn_and_final_review():
    rollout,events=records()
    result=native_reviewer_completions(rollout,events,"reviewer")
    assert len(result)==1
    assert result[0]["turn_id"]=="child-turn"
    assert result[0]["final_message"]=="Reviewed the patch and its regressions."


def test_actual_429_task_complete_is_not_a_successful_review():
    rollout,events=records()
    rollout[0]["payload"].update(last_agent_message=None,error={
        "message":"exceeded retry limit, last status: 429 Too Many Requests",
        "codex_error_info":{"response_too_many_failed_attempts":{"http_status_code":429}}})
    events[0]["params"]["turn"].update(status="failed",error={
        "message":"exceeded retry limit, last status: 429 Too Many Requests"})
    assert native_reviewer_completions(rollout,events,"reviewer")==[]


@pytest.mark.parametrize("message",[None,""," \n\t ",42])
def test_no_nonempty_final_review_means_no_success(message):
    rollout,events=records();rollout[0]["payload"]["last_agent_message"]=message
    assert native_reviewer_completions(rollout,events,"reviewer")==[]


@pytest.mark.parametrize("status",["failed","interrupted","inProgress",None])
def test_noncompleted_native_terminal_cannot_pass_with_text(status):
    rollout,events=records();events[0]["params"]["turn"]["status"]=status
    assert native_reviewer_completions(rollout,events,"reviewer")==[]


@pytest.mark.parametrize("surface",["rollout","native","missing-native-error"])
def test_errors_are_never_inferred_away(surface):
    rollout,events=records()
    if surface=="rollout":rollout[0]["payload"]["error"]={"message":"failure"}
    elif surface=="native":events[0]["params"]["turn"]["error"]={"message":"failure"}
    else:events[0]["params"]["turn"].pop("error")
    assert native_reviewer_completions(rollout,events,"reviewer")==[]


@pytest.mark.parametrize("mismatch",["thread","turn","root-turn","missing-terminal","missing-rollout"])
def test_unrelated_or_missing_terminal_cannot_qualify_review(mismatch):
    rollout,events=records()
    if mismatch=="thread":events[0]["params"]["threadId"]="unrelated"
    elif mismatch=="turn":events[0]["params"]["turn"]["id"]="unrelated"
    elif mismatch=="root-turn":events[0]["params"]["turn"]["rootTurnId"]="unrelated"
    elif mismatch=="missing-terminal":events=[]
    else:rollout=[]
    assert native_reviewer_completions(rollout,events,"reviewer")==[]


def test_duplicate_delivery_does_not_create_another_review():
    rollout,events=records()
    complete=native_reviewer_completions(rollout*2,events*2,"reviewer")
    assert len(complete)==1
    reviews=[{"thread":"reviewer","verified_completions":complete}]
    assert fresh_reviewer_completed(reviews,"parent-turn")
    assert not fresh_reviewer_completed(reviews,"parent-turn",{"reviewer":{"child-turn"}})
    assert not fresh_reviewer_completed(reviews,"another-parent-turn")


def test_conflicting_terminal_delivery_fails_closed():
    rollout,events=records();bad=copy.deepcopy(events[0])
    bad["params"]["turn"].update(status="failed",error={"message":"429"})
    assert native_reviewer_completions(rollout,events+[bad],"reviewer")==[]


def test_conflicting_rollout_delivery_fails_closed():
    rollout,events=records();bad=copy.deepcopy(rollout[0])
    bad["payload"].update(last_agent_message=None,error={"message":"429"})
    assert native_reviewer_completions(rollout+[bad],events,"reviewer")==[]


def test_both_real_work_collectors_use_the_same_terminal_evidence(tmp_path):
    root=tmp_path/"case";home=root/"home";home.mkdir(parents=True)
    rollout,events=records()
    path=home/"reviewer.jsonl";path.write_text("\n".join(json.dumps(x) for x in rollout))
    source=json.dumps({"subagent":{"thread_spawn":{"parent_thread_id":"parent"}}})
    with closing(sqlite3.connect(home/"state_5.sqlite")) as db, db:
        db.execute("CREATE TABLE threads (id TEXT,source TEXT,rollout_path TEXT,agent_path TEXT)")
        db.execute("INSERT INTO threads VALUES (?,?,?,?)",("reviewer",source,str(path),"/root/reviewer"))
        db.execute("INSERT INTO threads VALUES (?,?,?,?)",("foreign",json.dumps({
            "subagent":{"thread_spawn":{"parent_thread_id":"another-parent"}}}),str(path),"/root/foreign"))
    (root/"events-1.json").write_text(json.dumps(events))
    initial=reviewer_evidence(root,"parent");followup=reviewers(root,"parent")
    assert initial==followup
    assert len(initial)==1 and initial[0]["completed_turns"]==1
    assert initial[0]["completed_turn_ids"]==["child-turn"]
    before={"reviewer":set(initial[0]["completed_turn_ids"])}
    failed=copy.deepcopy(rollout[0]);failed["payload"].update(
        turn_id="429-turn",root_turn_id="followup-turn",last_agent_message=None,error={"message":"429"})
    with path.open("a") as stream:stream.write("\n"+json.dumps(failed))
    failure={"method":"turn/completed","params":{"threadId":"reviewer","turn":{
        "id":"429-turn","rootTurnId":"followup-turn","status":"failed","error":{"message":"429"}}}}
    (root/"events-2.json").write_text(json.dumps([failure]))
    after=reviewers(root,"parent")
    assert after[0]["completed_turns"]==1
    assert not fresh_reviewer_completed(after,"followup-turn",before)


def test_missing_reviewer_database_preserves_primary_startup_failure(tmp_path):
    import verify_real_work as work
    home=tmp_path/'home';home.mkdir()
    report={'error':'ORIGINAL_STARTUP_FAILURE','scope':'native-runtime'}
    work.collect_reviewer_evidence(report,tmp_path,home=home)
    assert report['error']=='ORIGINAL_STARTUP_FAILURE'
    assert report['reviewers']==[] and report['fresh_review'] is False
    assert report['reviewer_evidence_status']['status']=='not_started'
    assert report['reviewer_evidence_status']['error_type']=='RuntimeError'
    report['thread']='parent-started'
    work.collect_reviewer_evidence(report,tmp_path,home=home)
    assert report['error']=='ORIGINAL_STARTUP_FAILURE'
    assert report['reviewer_evidence_status']['status']=='failed'
    assert report['fresh_review'] is False
