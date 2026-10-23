"""Native automatic approval and cancellation of a known disposable process tree."""

import argparse
import contextlib
import hashlib
import sqlite3
import json
import os
import shlex
import math
from datetime import datetime
from pathlib import Path
import subprocess
import time
import uuid

from verify_live import Peer, prepare, text
from codex_deepseek.processes import identity,matches,terminate
from process_acceptance import python_command,shell_command,fixture_identity,write_process_tree_fixture


def approval_execution_evidence(events,thread,turn,marker):
    """An approved review must lead to that exact item's successful execution."""
    reviews=[];executions=[]
    for index,event in enumerate(events):
        params=event.get('params',{})
        if params.get('threadId')!=thread or params.get('turnId')!=turn:continue
        if event.get('method')=='item/autoApprovalReview/completed':
            if params.get('review',{}).get('status')=='approved' and params.get('action',{}).get('type')=='command':
                reviews.append((index,params))
        elif event.get('method')=='item/completed':
            item=params.get('item',{})
            if (item.get('type')=='commandExecution' and item.get('status')=='completed'
                    and item.get('exitCode')==0 and (item.get('aggregatedOutput') or '').strip()==marker):
                for review_index,review in reviews:
                    if (review_index<index and review.get('targetItemId')==item.get('id')
                            and isinstance(item.get('command'),str) and review['action'].get('command')==item['command']):
                        executions.append({'review':review,'command':item})
    return {'passed':bool(executions),'thread':thread,'turn':turn,'approved_executions':executions}



# Reuse the same 180-second budget as the native parent completion wait.
GUARDIAN_REQUEST_TIMEOUT_SECONDS=180


def guardian_rollout_records(root,thread):
    records=[]
    for path in (root/'home/sessions').rglob('*'+thread+'.jsonl'):
        content=path.read_text(encoding='utf-8')
        lines=content.splitlines()
        for index,line in enumerate(lines):
            try:records.append(json.loads(line))
            except json.JSONDecodeError:
                if index==len(lines)-1 and not content.endswith('\n'):continue
                raise
    return records


def guardian_planned_action(payload):
    actions=[];inside=False
    for item in payload.get('input',[]):
        if item.get('role')!='user':continue
        for part in item.get('content',[]):
            text=part.get('text','')
            if text.strip()=='>>> APPROVAL REQUEST START':inside=True;continue
            if text.strip()=='>>> APPROVAL REQUEST END':inside=False;continue
            if inside and text.strip().startswith('{'):
                action=json.loads(text)
                if isinstance(action,dict) and isinstance(action.get('tool'),str):actions.append(action)
    if len(actions)!=1:raise AssertionError('Guardian request must contain one explicit planned action')
    return actions[0]


def guardian_classifier_samples(root,thread,turn,events):
    records=guardian_rollout_records(root,thread);samples=[]
    # A nested command inherits this turn's cwd when workdir is omitted.
    # Repeated command text alone cannot identify distinct native invocations.
    turn_cwds={record.get('payload',{}).get('cwd') for record in records
               if record.get('type')=='turn_context' and record.get('payload',{}).get('turn_id')==turn}
    turn_cwds.discard(None)
    if len(turn_cwds)>1:raise AssertionError('Guardian current turn has ambiguous native cwd')
    default_cwd=next(iter(turn_cwds),None)
    for path in sorted((root/'adapter-traces').glob('*-request.json')):
        payload=json.loads(path.read_text())
        if payload.get('prompt_cache_key')!='guardian-v2:'+thread:continue
        metadata=payload.get('client_metadata',{})
        if metadata.get('parent_turn_id')!=turn:continue
        action=guardian_planned_action(payload);ids=set();direct=False
        for record in records:
            value=record.get('payload',{})
            if record.get('type')!='response_item' or value.get('internal_chat_message_metadata_passthrough',{}).get('turn_id')!=turn:continue
            if value.get('type')=='custom_tool_call':
                actual={'tool':value.get('name'),'arguments':value.get('input')}
            elif value.get('type')=='function_call':
                arguments=json.loads(value.get('arguments','{}'))
                actual={**arguments,'tool':value.get('name')} if isinstance(arguments,dict) else None
            else:continue
            if actual==action and value.get('call_id'):ids.add(value['call_id']);direct=True
        if action.get('tool')=='exec_command':
            workdir=action.get('workdir')
            expected_cwd=workdir if workdir is not None else default_cwd
            if expected_cwd is not None:
                if not isinstance(expected_cwd,str) or not expected_cwd:
                    raise AssertionError('Guardian planned action has invalid native cwd')
                if not os.path.isabs(expected_cwd):
                    if not default_cwd:raise AssertionError('Guardian relative workdir lacks native turn cwd')
                    expected_cwd=os.path.join(default_cwd,expected_cwd)
                expected_cwd=os.path.normpath(expected_cwd)
            nested_ids=set()
            for event in events:
                params=event.get('params',{});item=params.get('item',{})
                if (params.get('threadId')==thread and params.get('turnId')==turn
                    and event.get('method')=='item/completed' and item.get('type')=='commandExecution'
                    and any(command.get('command')==action.get('cmd') for command in item.get('commandActions',[]))
                    and (expected_cwd is None or isinstance(item.get('cwd'),str)
                         and os.path.normpath(item['cwd'])==expected_cwd)
                    and item.get('id')):
                    nested_ids.add(item['id'])
            if len(nested_ids)>1:
                # Native publication carries the full planned action and call_id.
                # It may disambiguate equal command/cwd, never an unrelated turn,
                # a different action, or an invocation with no matching event.
                exact={record.get('payload',{}).get('call_id') for record in records
                       if record.get('type')=='security_risk_score'
                       and record.get('payload',{}).get('action')==action}
                nested_ids &= exact
            ids.update(nested_ids)
        if len(ids)!=1:raise AssertionError('Guardian planned action does not identify one native call_id')
        samples.append({'receipt':path.name.removesuffix('-request.json'),'model':payload.get('model'),'provider':payload.get('provider'),
                        'call_id':next(iter(ids)),'action':action,'binding_kind':'direct_model_call' if direct else 'nested_command','request_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return samples


def required_guardian_calls(samples,approval):
    """Require the exact approved execution's action, never a convenient wrapper score."""
    if not approval.get('passed') or not approval.get('approved_executions'):
        raise AssertionError('No exact approved native Guardian execution')
    required=set()
    for execution in approval['approved_executions']:
        review=execution.get('review',{});command=execution.get('command',{})
        target=review.get('targetItemId');action=review.get('action',{})
        if (not isinstance(target,str) or not target or target!=command.get('id')
                or target in required or review.get('review',{}).get('status')!='approved'
                or action.get('type')!='command' or action.get('command')!=command.get('command')):
            raise AssertionError('Approved Guardian target does not identify one native command')
        bound=[sample for sample in samples if sample.get('call_id')==target]
        if len(bound)!=1:
            raise AssertionError('Approved Guardian target has no unique classifier observation')
        planned=bound[0].get('action',{})
        if (planned.get('tool')!='exec_command' or not isinstance(planned.get('cmd'),str)
                or not any(item.get('command')==planned['cmd'] for item in command.get('commandActions',[]))):
            raise AssertionError('Approved Guardian target classifier action differs from its native execution')
        required.add(target)
    return required


def guardian_classification_state(samples,receipts,records,*,required_call_ids=None,require_http_terminal=False):
    """Native score publication and HTTP draining are distinct outcomes."""
    scores=[record for record in records if record.get('type')=='security_risk_score']
    detail={'passed':False,'pending':[],'receipts':[],'scores':[],'classifications':[],
            'scope':'Native published scores, not proof that those scores made the approval decision'}
    if not samples:return {**detail,'error':'No current-turn Guardian classifier requests'}
    ids={sample['call_id'] for sample in samples}
    if len({sample['receipt'] for sample in samples})!=len(samples) or len(ids)!=len(samples):
        return {**detail,'error':'Duplicate Guardian receipt or call_id'}
    required=ids if required_call_ids is None else set(required_call_ids)
    if not required or not required<=ids:return {**detail,'error':'Required Guardian call_id is missing or ambiguous'}
    detail['required_call_ids']=sorted(required)
    for sample in samples:
        row=receipts.get(sample['receipt'])
        if row is None:return {**detail,'error':'Missing exact Guardian receipt: '+sample['receipt']}
        status,upstream,started=row[:3];detail['receipts'].append((status,upstream))
        if status not in {'running','completed','cancelled'}:
            return {**detail,'error':'Guardian request has an abnormal terminal: '+status,'failed_receipt':sample['receipt']}
        if upstream not in (None,200) or status=='completed' and upstream!=200:
            return {**detail,'error':'Guardian HTTP status is incompatible with publication/drain','failed_receipt':sample['receipt']}
        candidates=[score for score in scores if score.get('payload',{}).get('call_id')==sample['call_id']]
        if len(candidates)>1 or any(score['payload'].get('action')!=sample['action'] for score in candidates):
            return {**detail,'error':'Guardian score call_id/action mismatch or duplicate','failed_receipt':sample['receipt']}
        score=candidates[0] if candidates else None
        if score is not None:
            risk=score['payload'].get('scores',{}).get('action_risk')
            if type(risk) not in (int,float) or not math.isfinite(risk) or not 0<=risk<=1:
                return {**detail,'error':'Guardian score has no valid action_risk','failed_receipt':sample['receipt']}
            if status=='cancelled' and upstream!=200:
                return {**detail,'error':'Published score with cancelled transport lacks opened HTTP 200','failed_receipt':sample['receipt']}
            detail['scores'].append(score)
        item={'receipt':sample['receipt'],'call_id':sample['call_id'],'action':sample['action'],
              'required':sample['call_id'] in required,'score':score,'native_publication':'published' if score else 'not_published',
              'http_status':status,'upstream_status':upstream,'http_completed':status=='completed'}
        if score is None:item['publication_absence_cause']='unknown'
        if status=='cancelled':
            item['cancellation_scope']='published_score_with_cancelled_drain' if score else 'unscored_request_cancelled'
            item['cancellation_cause']='not inferred from an HTTP receipt'
        detail['classifications'].append(item)
        if sample['call_id'] in required and score is None:
            if status=='cancelled':
                return {**detail,'error':'Required native Guardian score was not published before request cancellation','failed_receipt':sample['receipt']}
            detail['pending'].append({'receipt':sample['receipt'],'call_id':sample['call_id'],'started':started})
        elif require_http_terminal and status=='running':
            detail['pending'].append({'receipt':sample['receipt'],'call_id':sample['call_id'],'started':started})
    detail['passed']=not detail['pending']
    return detail


def wait_guardian_classifications(root,thread,samples,timeout=GUARDIAN_REQUEST_TIMEOUT_SECONDS,*,required_call_ids=None,require_http_terminal=False):
    started=time.monotonic()
    while True:
        with contextlib.closing(sqlite3.connect((root/'home/provider-state/adapter.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
            receipts={sample['receipt']:db.execute('SELECT status,upstream_status,started FROM receipts WHERE id=?',(sample['receipt'],)).fetchone() for sample in samples}
        detail=guardian_classification_state(samples,receipts,guardian_rollout_records(root,thread),
            required_call_ids=required_call_ids,require_http_terminal=require_http_terminal)
        detail.update(wait_seconds=time.monotonic()-started,request_timeout_seconds=timeout,
                      timeout_basis='Same native completion budget; each receipt deadline is started + timeout')
        if detail['passed'] or detail.get('error'):return detail
        now=time.time()
        if any(type(row['started']) not in (int,float) or not math.isfinite(row['started']) or now>=row['started']+timeout for row in detail['pending']):
            return {**detail,'error':'Guardian classification or score persistence exceeded its existing request budget'}
        time.sleep(.05)


def guardian_assessment(text):
    """Match the fixed native reviewer defaults without inventing missing risk/authorization."""
    try:value=json.loads(text)
    except json.JSONDecodeError:
        start,end=text.find('{'),text.rfind('}')
        if start<0 or end<=start:raise ValueError('Guardian assessment is not JSON')
        value=json.loads(text[start:end+1])
    if not isinstance(value,dict) or value.get('outcome') not in {'allow','deny'}:raise ValueError('Invalid Guardian outcome')
    outcome=value['outcome'];risk=value.get('risk_level')
    if risk is None:risk='low' if outcome=='allow' else 'high'
    authorization=value.get('user_authorization')
    if authorization is None:authorization='unknown'
    rationale=value.get('rationale')
    if not isinstance(risk,str) or risk not in {'low','medium','high','critical'} or not isinstance(authorization,str) or authorization not in {'unknown','low','medium','high'}:
        raise ValueError('Invalid Guardian risk or authorization')
    if rationale is not None and not isinstance(rationale,str):raise ValueError('Invalid Guardian rationale')
    rationale=rationale if rationale and rationale.strip() else (
        'Auto-review returned a low-risk allow decision.' if outcome=='allow' else 'Auto-review returned a deny decision without a rationale.')
    return {'status':'approved' if outcome=='allow' else 'denied','riskLevel':risk,
            'userAuthorization':authorization,'rationale':rationale}


def guardian_approval_evidence(root,thread,turn,approval,events=()):
    """Bind a synchronous review receipt and reviewer output to its exact approved effect."""
    detail={'passed':False,'pending':[],'decisions':[]}
    if not approval.get('passed'):return {**detail,'error':'No exact approved native execution'}
    records=guardian_rollout_records(root,thread)
    for execution in approval['approved_executions']:
        review=execution['review'];native=review['review'];action=review['action']
        # Native cached approval deliberately has no risk or authorization assessment.
        if native.get('rationale')=='Approved using a current low-risk async score.' and native.get('riskLevel') is None and native.get('userAuthorization') is None:
            bound=guardian_classifier_samples(root,thread,turn,events)
            if not bound or any(sample.get('model')!='deepseek/deepseek-v4.1-flash-1017' for sample in bound):
                return {**detail,'error':'Cached approval lacks selected-model classifier provenance'}
            exact={sample['call_id']:sample['action'] for sample in bound}
            if review.get('decisionSource') is not None:
                return {**detail,'error':'Cached approval has a synchronous or user decision source'}
            published=[record for record in records if record.get('type')=='security_risk_score']
            cutoff=review.get('completedAtMs')
            eligible=[]
            for score in published:
                try:
                    timestamp=datetime.fromisoformat(score['timestamp'].replace('Z','+00:00')).timestamp()*1000
                    sampled=datetime.fromisoformat(score['payload']['sampled_at'].replace('Z','+00:00')).timestamp()*1000
                except (KeyError,ValueError,TypeError):return {**detail,'error':'Cached score lacks native publication/sample timestamps'}
                if type(cutoff) not in (int,float) or not math.isfinite(cutoff):
                    return {**detail,'error':'Cached approval lacks a valid native decision timestamp'}
                if timestamp<=cutoff and sampled<=timestamp:eligible.append((sampled,score))
            if not eligible:return {**detail,'error':'Cached approval has no preceding bound current-turn score'}
            # Native publish accepts only strictly newer sampled_at, not the
            # last HTTP completion or the most convenient earlier low score.
            newest=max(sampled for sampled,_ in eligible)
            selected=[score for sampled,score in eligible if sampled==newest]
            if len(selected)!=1 or len({score['payload']['call_id'] for _,score in eligible})!=len(eligible):
                return {**detail,'error':'Cached approval preceding score is ambiguous'}
            score=selected[0]
            if (score['payload'].get('call_id') not in exact
                    or score['payload'].get('action')!=exact[score['payload']['call_id']]):
                return {**detail,'error':'Newest preceding native score is not bound to a current-turn action'}
            risk=score['payload'].get('scores',{}).get('action_risk')
            if type(risk) not in (int,float) or not math.isfinite(risk) or risk!=0.0:
                return {**detail,'error':'Newest preceding native score is not low risk'}
            detail['decisions'].append({'source':'native_cached_async_score','review':review,'command':execution['command'],'score':score,
                'scope':'Explicit native cached-approval result and newest unambiguous preceding current-turn published score; native checks authorization and lag'})
            continue
        if review.get('decisionSource')!='agent':return {**detail,'error':'Synchronous decision did not come from the native reviewer'}
        candidates=[]
        for path in sorted((root/'adapter-traces').glob('*-request.json')):
            payload=json.loads(path.read_text());metadata=payload.get('client_metadata',{})
            if payload.get('prompt_cache_key')!='guardian:'+thread or metadata.get('parent_turn_id')!=turn:continue
            planned=guardian_planned_action(payload)
            if (planned.get('tool')!='exec_command' or planned.get('command')!=shlex.split(action.get('command',''))
                    or planned.get('cwd')!=action.get('cwd')):continue
            reviewer_thread=metadata.get('thread_id');reviewer_turn=metadata.get('turn_id')
            if metadata.get('session_id')!=thread or not reviewer_thread or not reviewer_turn:
                return {**detail,'error':'Synchronous Guardian request has no exact reviewer identity'}
            receipt=path.name.removesuffix('-request.json')
            with contextlib.closing(sqlite3.connect((root/'home/provider-state/adapter.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
                row=db.execute('SELECT status,upstream_status,started,finished FROM receipts WHERE id=?',(receipt,)).fetchone()
            if row is None:return {**detail,'error':'Missing synchronous Guardian receipt'}
            status,http,started,finished=row
            if status not in {'running','completed'}:return {**detail,'error':'Synchronous Guardian review failed: '+status}
            if status=='completed' and http!=200:return {**detail,'error':'Synchronous review completed without HTTP 200'}
            messages=[record for record in guardian_rollout_records(root,reviewer_thread)
                      if record.get('type')=='response_item' and record.get('payload',{}).get('type')=='message'
                      and record['payload'].get('role')=='assistant'
                      and record['payload'].get('internal_chat_message_metadata_passthrough',{}).get('turn_id')==reviewer_turn
                      and record['payload'].get('status')=='completed']
            if status=='running' or not messages:
                detail['pending'].append({'receipt':receipt,'started':started});continue
            if len(messages)!=1:return {**detail,'error':'Ambiguous synchronous Guardian output'}
            output=''.join(part.get('text','') for part in messages[0]['payload'].get('content',[]) if part.get('type')=='output_text')
            try:assessment=guardian_assessment(output)
            except (ValueError,TypeError) as error:return {**detail,'error':'Invalid synchronous Guardian assessment: '+str(error)}
            if any(native.get(key)!=value for key,value in assessment.items()):
                return {**detail,'error':'Native review risk/authorization/rationale differs from its actual model result'}
            if payload.get('model')!='deepseek/deepseek-v4.1-flash-1017':return {**detail,'error':'Synchronous reviewer used another model'}
            completed=review.get('completedAtMs')
            try:response_time=datetime.fromisoformat(messages[0]['timestamp'].replace('Z','+00:00')).timestamp()
            except (KeyError,ValueError,TypeError):return {**detail,'error':'Synchronous reviewer result lacks a native timestamp'}
            if (type(started) not in (int,float) or type(finished) not in (int,float) or type(completed) not in (int,float)
                    or not all(math.isfinite(value) for value in [started,finished,completed,response_time])
                    or finished<started or response_time<started-0.001 or finished*1000>completed+1 or response_time*1000>completed+1):
                return {**detail,'error':'Synchronous receipt/result did not precede its native decision'}
            candidates.append({'source':'synchronous_guardian_review','receipt':receipt,'request_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'reviewer_thread':reviewer_thread,'reviewer_turn':reviewer_turn,'assessment':assessment,
                'review':review,'command':execution['command'],'http_status':status,'upstream_status':http})
        if len(candidates)>1:return {**detail,'error':'Ambiguous synchronous request for one native decision'}
        if not candidates and not detail['pending']:return {**detail,'error':'No exact synchronous Guardian request for the native approved action'}
        detail['decisions'].extend(candidates)
    detail['passed']=bool(detail['decisions']) and not detail['pending']
    return detail


def wait_guardian_approval(root,thread,turn,approval,timeout=GUARDIAN_REQUEST_TIMEOUT_SECONDS,*,events=()):
    while True:
        detail=guardian_approval_evidence(root,thread,turn,approval,events)
        if detail['passed'] or detail.get('error'):return detail
        now=time.time()
        if any(type(row['started']) not in (int,float) or not math.isfinite(row['started']) or now>=row['started']+timeout for row in detail['pending']):
            return {**detail,'error':'Guardian approval evidence exceeded its existing request budget'}
        time.sleep(.05)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--guardian-v2',action='store_true')
    args=parser.parse_args()
    root=args.output.resolve()/str(uuid.uuid4())
    prepare(root)
    if args.guardian_v2:
        config=root/'home/config.toml'
        config.write_text(config.read_text().replace('[features]\n','[features]\nguardianv2 = {enabled=true,persist_scores=true,review_scope={computer_use_only=false,sandboxed_exec_commands=true}}\n'))
    fixture=root/'work/cancellable.py'
    write_process_tree_fixture(fixture,root/'work/processes.json')
    approval_fixture=root/'work/approval_fixture.py'
    approval_fixture.write_text('print("APPROVAL_NATIVE_OK")\n',encoding='utf-8')
    peer=Peer(root)
    peer_closed=False
    results=[]
    thread_sandbox_mode=peer.sandbox_mode
    def check(name,condition,detail):
        result={'name':name,'passed':bool(condition),'sandbox_mode':thread_sandbox_mode,
                'fixture_sandbox_mode':peer.sandbox_mode,'detail':detail};results.append(result)
        (root/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False),flush=True)
        if not condition:raise AssertionError(name)
    try:
        # Full-access fixtures need no escalation review; this thread must exercise it.
        thread=peer.new(sandbox='read-only',approvalsReviewer='auto_review')
        thread_sandbox_mode='read-only'
        # A script file avoids PowerShell 5.1's legacy native argument handling
        # stripping quotes inside a Python -c argument.
        approval_command=shell_command([python_command('')[0],'-I',str(approval_fixture)])
        turn,start=peer.start(thread,'Do not delegate. This is a user-authorized harmless approval fixture. Call exec_command with the exact native-host Python command below, sandbox_permissions `require_escalated`, and justification `isolated native approval fixture`. Do not replace it with another tool or omit the explicit permission request. Report the actual command result.\n```\n'+approval_command+'\n```')
        items=peer.complete(thread,start,timeout=GUARDIAN_REQUEST_TIMEOUT_SECONDS)
        approval=approval_execution_evidence(peer.events[start:],thread,turn,'APPROVAL_NATIVE_OK')
        check('native_automatic_approval',approval['passed'],{**approval,'answer':text(items)})
        decided=wait_guardian_approval(root,thread,turn,approval,events=peer.events[start:])
        check('native_guardian_approval_decision_matches_execution',decided['passed'],decided)
        if args.guardian_v2:
            samples=guardian_classifier_samples(root,thread,turn,peer.events[start:])
            check('native_guardian_v2_classifier_uses_selected_deepseek',bool(samples) and all(sample['model']=='deepseek/deepseek-v4.1-flash-1017' for sample in samples),samples)
            required=required_guardian_calls(samples,approval)
            guardian_thread=thread
            classified=wait_guardian_classifications(root,thread,samples,required_call_ids=required)
            check('native_guardian_v2_required_score_publication',classified['passed'],classified)

        thread=peer.new()
        thread_sandbox_mode=peer.sandbox_mode
        turn,start=peer.start(thread,'Do not delegate. Run '+shell_command([python_command('')[0],'-I',str(fixture)])+' using exec_command with a long yield. Wait for completion.')
        peer.wait(lambda e:e.get('method')=='item/started' and e.get('params',{}).get('threadId')==thread and e.get('params',{}).get('item',{}).get('type')=='commandExecution',start)
        pids_file=root/'work/processes.json'
        end=time.monotonic()+15
        while not pids_file.exists() and time.monotonic()<end:time.sleep(0.05)
        if not pids_file.exists():raise AssertionError('Fixture process did not start')
        markers=json.loads(pids_file.read_text(encoding='utf-8'));assert len(markers)==2
        births=dict(fixture_identity(marker,fixture) for marker in markers);assert len(births)==2
        pids=list(births)
        peer.call('turn/interrupt',{'threadId':thread,'turnId':turn})
        peer.complete(thread,start,expected_status='interrupted',turn_id=turn)
        terminal=[e for e in peer.events[start:] if e.get('method')=='turn/completed' and e.get('params',{}).get('threadId')==thread][-1]
        check('native_turn_interrupt_status',terminal['params']['turn']['status']=='interrupted',terminal['params']['turn']['status'])
        # A yielded unified-exec session is owned by the native process manager,
        # not the sampling turn. Preserve that native lifetime instead of adding
        # an adapter-side kill policy. Closing its owner must clean it up.
        peer.close();peer_closed=True
        if args.guardian_v2:
            terminals=wait_guardian_classifications(root,guardian_thread,samples,required_call_ids=required,require_http_terminal=True)
            check('native_guardian_v2_http_terminals_recorded',terminals['passed'],terminals)
        end=time.monotonic()+5
        alive=[]
        while time.monotonic()<end:
            alive=[]
            for pid in pids:
                if matches(pid,births[pid]):alive.append({'pid':pid,'birth':births[pid]})
            if not alive:break
            time.sleep(0.05)
        check('native_owner_shutdown_cleans_process_tree',not alive,{'fixture_pids':pids,'still_alive':alive})
    finally:
        if not peer_closed:peer.close()
        for pid,birth in locals().get('births',{}).items():terminate(pid,birth)
        print('EVIDENCE',str(root),flush=True)


if __name__=='__main__':main()
