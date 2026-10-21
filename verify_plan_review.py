"""Native plan events and review mode on an actual local change."""
from process_acceptance import evidence_root
import json
from pathlib import Path
import subprocess
import uuid
from verify_live import Peer,prepare,text
root=evidence_root('plan-review')/str(uuid.uuid4())
prepare(root);work=root/'work'
subprocess.run(['git','init','-q',str(work)],check=True)
(work/'calculation.py').write_text('def divide(a, b):\n    return a / b\n')
subprocess.run(['git','add','.'],cwd=work,check=True)
subprocess.run(['git','-c','user.name=Acceptance Fixture','-c','user.email=fixture@localhost','commit','-qm','initial'],cwd=work,check=True)
(work/'calculation.py').write_text('def divide(a, b):\n    return a * b\n')
peer=Peer(root);results=[]
def passed(name,ok,detail):
    results.append({'name':name,'passed':bool(ok),'sandbox_mode':peer.sandbox_mode,'detail':detail});(root/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(results[-1]),flush=True)
    if not ok:raise AssertionError(name)
try:
    thread=peer.new(config={'tools':{'update_plan':{'enabled':True}}});start=len(peer.events)
    items=peer.turn(thread,'Do not delegate. Use the native update_plan tool to create a two-step plan: inspect calculation.py and report what divide currently does. Then inspect the file using a native tool, update the plan so both steps are completed, and report the observed implementation. This is inspection only; do not fix the file.')
    updates=[e['params'] for e in peer.events[start:] if e.get('method')=='turn/plan/updated']
    passed('native_plan_events',bool(updates) and all(p['status']=='completed' for p in updates[-1]['plan']),{'updates':updates,'answer':text(items)})
    start=len(peer.events);peer.call('review/start',{'threadId':thread,'target':{'type':'uncommittedChanges'},'delivery':'inline'})
    items=peer.complete(thread,start);answer=text(items)
    events=[e for e in peer.events[start:] if e.get('method')=='item/completed']
    passed('native_review_mode',any(e['params']['item'].get('type') in {'enteredReviewMode','exitedReviewMode'} for e in events) and ('division' in answer.lower() or 'multiply' in answer.lower() or 'multiplication' in answer.lower()),answer)
finally:
    peer.close();print('EVIDENCE',root,flush=True)
