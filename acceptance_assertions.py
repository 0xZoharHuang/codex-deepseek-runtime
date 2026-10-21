"""Evidence predicates shared by live verifiers and adversarial verifier tests."""
import json
import os
import subprocess


def browser_calls(items,server='codex_web'):
    """Return successful native calls with actual structured document results."""
    calls=[]
    for item in items:
        result=item.get('result') or {}
        if (item.get('type')!='mcpToolCall' or item.get('server')!=server
            or item.get('status')!='completed' or item.get('error') or result.get('isError')):continue
        value=result.get('structuredContent')
        if value is None:
            try:value=json.loads(''.join(part.get('text','') for part in result.get('content',[]) if part.get('type')=='text'))
            except (ValueError,TypeError):continue
        if not isinstance(value,dict):continue
        calls.append((item,value))
    return calls


def browser_chain(items,server,url,phrase,nonce):
    """Require open -> positive find -> follow that saved page's actual link."""
    calls=browser_calls(items,server)
    for index,(opened,page) in enumerate(calls):
        if opened.get('tool')!='open' or opened.get('arguments',{}).get('url_or_ref')!=url:continue
        if page.get('url')!=url or not page.get('source_sha256') or not page.get('ref_id'):continue
        for following,(found,matches) in enumerate(calls[index+1:],index+1):
            args=found.get('arguments',{})
            if found.get('tool')!='find' or args.get('ref_id')!=page['ref_id'] or args.get('pattern')!=phrase or matches.get('match_count',0)<1:continue
            for clicked,target in calls[following+1:]:
                args=clicked.get('arguments',{})
                links=[link for link in page.get('links',[]) if link['id']==args.get('link_id')]
                if (clicked.get('tool')=='click' and args.get('ref_id')==page['ref_id']
                    and links and target.get('url')==links[0]['url'] and target.get('source_sha256')
                    and target.get('followed_from')=={'ref_id':page['ref_id'],'link_id':args['link_id']}
                    and nonce in target.get('content','')):return True
    return False


def parent_deliveries(requests,child_path,markers):
    deliveries={marker:[] for marker in markers}
    header='Message Type: FINAL_ANSWER\nTask name: /root\nSender: '+child_path+'\nPayload:\n'
    for request in requests:
        for item in request.get('input',[]):
            if item.get('type')!='function_call_output':continue
            output=item.get('output')
            if not isinstance(output,list):continue
            for part in output:
                value=part.get('text','')
                if part.get('type')!='input_text' or not value.startswith(header):continue
                payload=value[len(header):].strip()
                if payload in deliveries:deliveries[payload].append(item.get('call_id'))
    return deliveries


def current_browser_evidence(events,thread,start=0):
    """A previous turn, sibling reviewer or isError result cannot satisfy a new turn."""
    items=[e['params']['item'] for e in events[start:] if e.get('method')=='item/completed'
           and e.get('params',{}).get('threadId')==thread]
    calls=browser_calls(items)
    actions=sorted({item['tool'] for item,_ in calls})
    pages=[{key:value.get(key) for key in ['url','source_sha256','retrieved_at']}
           for item,value in calls if item.get('tool') in {'open','click'} and value.get('source_sha256') and value.get('url')]
    positive_find=any(item.get('tool')=='find' and value.get('match_count',0)>0 for item,value in calls)
    return {'native_completed_actions':actions,'pages':pages,'distinct_actual_urls':len({p['url'] for p in pages}),
            'positive_find':positive_find,'required':True,
            'passed':{'open','find','click'}<=set(actions) and positive_find and len({p['url'] for p in pages})>=2}


def complete_work_patch(work):
    """Include staged, unstaged and untracked source without changing the Git index."""
    parts=[subprocess.check_output(['git','diff','--binary','HEAD','--'],cwd=work)]
    names=subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z'],cwd=work).split(b'\0')
    for name in names:
        if not name:continue
        path=os.fsdecode(name)
        result=subprocess.run(['git','diff','--no-index','--binary','--','/dev/null',path],cwd=work,capture_output=True)
        if result.returncode not in {0,1}:raise RuntimeError('Unable to capture untracked fixture source: '+path)
        parts.append(result.stdout)
    return b''.join(parts)
