"""Evidence predicates shared by live verifiers and adversarial verifier tests."""
import json
import os
import subprocess
from urllib.parse import urldefrag


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


def browser_chains(items,server='codex_web'):
    """Yield ordered, reference-linked navigation backed by the browser's receipts."""
    calls=browser_calls(items,server)
    for index,(opened,page) in enumerate(calls):
        if opened.get('tool')!='open':continue
        if not page.get('url') or not page.get('source_sha256') or not page.get('ref_id'):continue
        for following,(found,matches) in enumerate(calls[index+1:],index+1):
            args=found.get('arguments',{})
            if (found.get('tool')!='find' or args.get('ref_id')!=page['ref_id']
                or matches.get('ref_id')!=page['ref_id'] or matches.get('match_count',0)<1):continue
            for clicked,target in calls[following+1:]:
                args=clicked.get('arguments',{})
                links=[link for link in page.get('links',[]) if link.get('id')==args.get('link_id') and link.get('url')]
                if (clicked.get('tool')=='click' and args.get('ref_id')==page['ref_id']
                    and links and target.get('url') and target.get('source_sha256')
                    and target.get('followed_from')=={'ref_id':page['ref_id'],'link_id':args['link_id']}):
                    # Browser.click validates this link before fetching. Its final URL
                    # may differ after an HTTP redirect; the receipt retains the edge.
                    yield opened,page,found,matches,clicked,target,links[0]


def browser_chain(items,server,url,phrase,nonce):
    """Require the controlled fixture's exact page, phrase, link and fetched nonce."""
    for opened,page,found,_,_,target,link in browser_chains(items,server):
        if (opened.get('arguments',{}).get('url_or_ref')==url and page['url']==url
            and found.get('arguments',{}).get('pattern')==phrase
            and target['url']==link['url'] and nonce in target.get('content','')):return True
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


def current_browser_evidence(events,thread,start=0,*,turn=None):
    """A previous turn, sibling reviewer or isError result cannot satisfy a new turn."""
    items=[e['params']['item'] for e in events[start:] if e.get('method')=='item/completed'
           and e.get('params',{}).get('threadId')==thread
           and isinstance(turn,str) and turn and e.get('params',{}).get('turnId')==turn]
    calls=browser_calls(items)
    actions=sorted({item['tool'] for item,_ in calls})
    pages=[{key:value.get(key) for key in ['url','source_sha256','retrieved_at']}
           for item,value in calls if item.get('tool') in {'open','click'} and value.get('source_sha256') and value.get('url')]
    positive_find=any(item.get('tool')=='find' and value.get('match_count',0)>0 for item,value in calls)
    chains=[{'source_ref':page['ref_id'],'source_url':page['url'],'source_sha256':page['source_sha256'],
             'link_id':link['id'],'link_url':link['url'],'target_url':target['url'],'target_sha256':target['source_sha256']}
            for _,page,_,_,_,target,link in browser_chains(items) if urldefrag(page['url'])[0]!=urldefrag(target['url'])[0]]
    return {'turn_id':turn,'native_completed_actions':actions,'pages':pages,'distinct_actual_urls':len({p['url'] for p in pages}),
            'positive_find':positive_find,'required':True,
            'linked_chains':chains,'passed':bool(chains)}


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


def real_work_passed(report):
    """Missing recovery, independent suites or oracle isolation cannot pass a task."""
    guard=report.get('oracle_guard',{})
    suites=report.get('validation',[])
    required={'workspace','upstream-heldout'}
    if report.get('case')=='httpcore':
        required|={'independent-cancellation','real-tcp-0','real-tcp-1','real-tcp-2','tls-socket'}
        if not report.get('restarted'):return False
    web=report.get('web_research',{})
    inputs=report.get('fixture_inputs',{});before=inputs.get('before');after=inputs.get('after')
    if report.get('host_platform')=='win32' and report.get('native_host_execution',{}).get('passed') is not True:return False
    return (all(guard.get(key) is True for key in ('source_unchanged','read_probe_denied','restored_probe_unchanged'))
            and report.get('case') in {'jinja','httpcore'} and inputs.get('unchanged') is True
            and isinstance(before,dict) and before==after and set(before)==set(real_work_input_names(report['case']))
            and report.get('fresh_review') is True and report.get('turn_status')=='completed'
            and not report.get('error') and report.get('diff_check')==0
            and required<={item.get('suite') for item in suites}
            and all(item.get('exit')==0 and item.get('source_imports',{}).get('passed') is True for item in suites)
            and (not web.get('required') or web.get('current_turn',{}).get('passed') is True))


def native_host_execution(events,python,platform):
    """Do not count a Windows task that actually delegated execution to Linux."""
    from host_execution_acceptance import host_execution_evidence,command_mentions_path
    items=[e['params']['item'] for e in events if e.get('method')=='item/completed'
           and e.get('params',{}).get('item',{}).get('type')=='commandExecution']
    proof=host_execution_evidence(items,platform=platform)
    observed=any(isinstance(item.get('command'),str) and command_mentions_path(item['command'],python)
                 and item.get('exitCode')==0 for item in items)
    return {**proof,'declared_python':str(python),'native_python_observed':observed,
            'passed':bool(items) and proof['passed'] and (platform!='win32' or observed)}


def real_work_input_names(case):
    names=['upstream.json','archives.json','dependencies.lock','dependencies-frozen.txt']
    if case=='httpcore':names+=['cancellation_regression.py','network_stress.py','tls_socket_regression.py']
    return names


def real_work_input_hashes(root,case):
    """Independent drivers and dependency pins are controls, not editable task files."""
    import hashlib
    result={}
    for name in real_work_input_names(case):
        with (root/name).open('rb') as stream:result[name]=hashlib.file_digest(stream,'sha256').hexdigest()
    return result


class OracleGuard:
    """Deny fixture oracle reads using the host filesystem's native permissions."""
    def __init__(self,path):
        import os
        self.path=path;self.mode=path.stat().st_mode&0o777;self.windows=os.name=='nt';self.descriptor=None
        self.probe=next(path.rglob('*.py'))
        self.probe_hash=__import__('hashlib').sha256(self.probe.read_bytes()).hexdigest()
        self.denied=False

    def lock(self):
        if not self.windows:self.path.chmod(0)
        else:
            import win32api,win32con,win32security
            token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
            try:sid=win32security.GetTokenInformation(token,win32security.TokenUser)[0]
            finally:token.Close()
            self.descriptor=win32security.GetNamedSecurityInfo(str(self.path),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION)
            acl=win32security.ACL();inherit=win32con.OBJECT_INHERIT_ACE|win32con.CONTAINER_INHERIT_ACE
            acl.AddAccessDeniedAceEx(win32security.ACL_REVISION_DS,inherit,win32con.GENERIC_READ|win32con.GENERIC_WRITE|win32con.GENERIC_EXECUTE,sid)
            acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS,inherit,win32con.WRITE_DAC,sid)
            system=win32security.ConvertStringSidToSid('S-1-5-18')
            acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS,inherit,win32con.GENERIC_ALL,system)
            win32security.SetNamedSecurityInfo(str(self.path),win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION|win32security.PROTECTED_DACL_SECURITY_INFORMATION,None,None,acl,None)
        try:self.probe.read_bytes()
        except PermissionError:self.denied=True
        if not self.denied:
            self.restore();raise RuntimeError('Oracle permissions did not deny source reads')

    def restore(self):
        if not self.windows:self.path.chmod(self.mode)
        elif self.descriptor is not None:
            import win32security
            control,_=self.descriptor.GetSecurityDescriptorControl()
            protect=win32security.PROTECTED_DACL_SECURITY_INFORMATION if control&win32security.SE_DACL_PROTECTED else win32security.UNPROTECTED_DACL_SECURITY_INFORMATION
            win32security.SetNamedSecurityInfo(str(self.path),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION|protect,
                None,None,self.descriptor.GetSecurityDescriptorDacl(),None)

    @property
    def evidence(self):
        return {'mechanism':'windows-dacl' if self.windows else 'posix-mode','read_probe_denied':self.denied,
                'restored_probe_unchanged':__import__('hashlib').sha256(self.probe.read_bytes()).hexdigest()==self.probe_hash}
