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


def pytest_execution_evidence(path):
    """Read pytest's private JUnit output; skipped async tests are not execution."""
    import hashlib
    import xml.etree.ElementTree as ET
    from pathlib import Path
    path=Path(path);data=path.read_bytes();root=ET.fromstring(data)
    if root.tag not in {'testsuites','testsuite'}:
        raise ValueError('Unexpected pytest JUnit root')
    cases=[]
    for case in root.iter('testcase'):
        markers=[child.tag for child in case if child.tag in {'failure','error','skipped'}]
        if len(markers)>1:raise ValueError('Ambiguous pytest testcase outcome')
        row={'classname':case.get('classname'),'name':case.get('name'),
             'outcome':markers[0] if markers else 'passed'}
        skipped=case.find('skipped')
        if skipped is not None:
            row.update(skip_type=skipped.get('type'),skip_reason=skipped.get('message'))
        cases.append(row)
    if any(not case['name'] or not case['classname'] for case in cases):
        raise ValueError('Missing pytest testcase identity')
    identities=[(case['classname'],case['name']) for case in cases]
    if len(set(identities))!=len(identities):raise ValueError('Duplicate pytest testcase identity')
    return {'path':str(path),'sha256':hashlib.sha256(data).hexdigest(),'cases':cases,
            'counts':{kind:sum(case['outcome']==kind for case in cases)
                      for kind in ('passed','failure','error','skipped')}}


def _pytest_execution_cases(proof):
    if not isinstance(proof,dict):return None
    cases=proof.get('cases',[])
    if (not isinstance(cases,list) or not cases
        or any(not isinstance(case,dict) or not isinstance(case.get('outcome'),str)
               or case['outcome'] not in {'passed','failure','error','skipped'} for case in cases)):
        return None
    actual={kind:sum(case['outcome']==kind for case in cases) for kind in ('passed','failure','error','skipped')}
    counts=proof.get('counts',{})
    if (counts!=actual or any(type(value) is not int for value in counts.values())
        or actual['passed']+actual['failure']==0):return None
    identities=[(case.get('classname'),case.get('name')) for case in cases]
    if (any(not isinstance(c,str) or not c or not isinstance(n,str) or not n for c,n in identities)
        or len(set(identities))!=len(identities)):return None
    return cases


def _inventory_digest(value):
    import hashlib
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def _sha256_text(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def _skip_policy(entries,required):
    if not isinstance(entries,(list,tuple)):raise ValueError('Invalid baseline skip policy')
    result={}
    for row in entries:
        if (not isinstance(row,dict) or set(row)-{'classname','name','skip_type','skip_reason'}
            or not {'classname','name','skip_type'}<=set(row)):
            raise ValueError('Invalid baseline skip declaration')
        if any(not isinstance(row[field],str) or not row[field] for field in ('classname','name')):
            raise ValueError('Invalid baseline skip identity')
        key=(row['classname'],row['name'])
        if key not in required or key in result:raise ValueError('Unknown or duplicate allowed skip')
        if row['skip_type'] is not None and not isinstance(row['skip_type'],str):
            raise ValueError('Invalid baseline skip type')
        if 'skip_reason' in row and row['skip_reason'] is not None and not isinstance(row['skip_reason'],str):
            raise ValueError('Invalid baseline skip reason')
        result[key]=dict(row)
    return result


def _declared_skip(case,policy):
    rule=policy.get((case['classname'],case['name']))
    return (rule is not None and case.get('skip_type')==rule['skip_type']
            and ('skip_reason' not in rule or case.get('skip_reason')==rule['skip_reason']))


def pytest_inventory_baseline(proof,*,suite,source_sha256,tools_sha256,allowed_skips=()):
    """Freeze a controller-owned pre-model inventory, never a model report claim.

    The caller obtains proof from pinned original source/Tools, qualifies its
    baseline outcomes, and protects both the file and its external SHA before
    starting the model. Only explicitly declared original skips may remain.
    """
    cases=_pytest_execution_cases(proof)
    if (not isinstance(suite,str) or suite not in {'workspace','upstream-heldout'} or cases is None
        or not all(_sha256_text(v) for v in (proof.get('sha256'),source_sha256,tools_sha256))):
        raise ValueError('Invalid pinned pytest baseline')
    required={(case['classname'],case['name']) for case in cases}
    policy=_skip_policy(allowed_skips,required)
    if any(case['outcome']=='skipped' and not _declared_skip(case,policy) for case in cases):
        raise ValueError('Undeclared baseline skip')
    value={'schema':1,'suite':suite,'source_junit_sha256':proof['sha256'],
           'source_sha256':source_sha256,'tools_sha256':tools_sha256,
           'required_cases':[list(key) for key in sorted(required)],
           'allowed_skips':[policy[key] for key in sorted(policy)]}
    return {**value,'sha256':_inventory_digest(value)}


def pytest_inventory_coverage(proof,baseline):
    """Check original identities and skip policy; behavior/failure gates stay separate."""
    invalid={'passed':False,'reason':'invalid_protected_baseline'}
    try:
        if (not isinstance(baseline,dict) or set(baseline)!={'schema','suite','source_junit_sha256',
            'source_sha256','tools_sha256','required_cases','allowed_skips','sha256'}
            or type(baseline['schema']) is not int or baseline['schema']!=1
            or baseline['suite'] not in {'workspace','upstream-heldout'}
            or not all(_sha256_text(baseline[k]) for k in ('source_junit_sha256','source_sha256','tools_sha256','sha256'))
            or baseline['sha256']!=_inventory_digest({k:v for k,v in baseline.items() if k!='sha256'})):
            return invalid
        rows=baseline['required_cases']
        if (not isinstance(rows,list) or not rows or any(not isinstance(row,list) or len(row)!=2
            or any(not isinstance(value,str) or not value for value in row) for row in rows)):
            return invalid
        required={tuple(row) for row in rows}
        if len(required)!=len(rows):return invalid
        policy=_skip_policy(baseline['allowed_skips'],required)
    except (ValueError,TypeError,KeyError):return invalid
    cases=_pytest_execution_cases(proof)
    if cases is None:return {'passed':False,'reason':'invalid_pytest_execution'}
    observed={(case['classname'],case['name']) for case in cases}
    missing=sorted(required-observed)
    unexpected=[case for case in cases if case['outcome']=='skipped' and not _declared_skip(case,policy)]
    return {'passed':not missing and not unexpected,'baseline_sha256':baseline['sha256'],
            'required_count':len(required),'observed_count':len(observed),
            'added_count':len(observed-required),'missing_cases':[list(key) for key in missing],
            'unexpected_skips':unexpected}


def real_work_pytest_executed(suite,*,baseline=None):
    if not isinstance(suite,dict) or not isinstance(suite.get('suite'),str):return False
    if suite.get('suite') not in {'workspace','upstream-heldout','independent-cancellation'}:
        return True
    proof=suite.get('pytest_execution',{});cases=_pytest_execution_cases(proof)
    if cases is None:return False
    if suite['suite']=='independent-cancellation':
        required={'test_cancel_during_expired_connection_close_releases_queue['+backend+']' for backend in ('asyncio','trio')}
        return (len(cases)==2 and {case['name'] for case in cases}==required
                and all(case['classname'].split('.')[-1]=='cancellation_regression'
                        and case['outcome'] in {'passed','failure'} for case in cases))
    return (isinstance(baseline,dict) and baseline.get('suite')==suite['suite']
            and pytest_inventory_coverage(proof,baseline)['passed'])


def real_work_passed(report,*,pytest_baselines=None):
    """Missing recovery, independent suites or oracle isolation cannot pass a task."""
    guard=report.get('oracle_guard',{})
    suites=report.get('validation',[])
    if (not isinstance(suites,list) or any(not isinstance(item,dict) for item in suites)
        or not isinstance(pytest_baselines,dict)):return False
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
            and all(item.get('exit')==0 and item.get('source_imports',{}).get('passed') is True
                    and real_work_pytest_executed(item,baseline=pytest_baselines.get(item.get('suite')))
                    and item.get('pytest_execution',{}).get('counts',{}).get('failure',0)==0
                    and item.get('pytest_execution',{}).get('counts',{}).get('error',0)==0 for item in suites)
            and (not web.get('required') or web.get('current_turn',{}).get('passed') is True))


def native_host_execution(events,python,platform,*,process_evidence=None,native_roots=(),python_sha256=None,interpreter_binding=None):
    """Require actual selected-interpreter execution on Windows, not echo text."""
    from host_execution_acceptance import host_execution_evidence,interpreter_command_declaration,interpreter_process_evidence
    items=[e['params']['item'] for e in events if e.get('method')=='item/completed'
           and e.get('params',{}).get('item',{}).get('type')=='commandExecution']
    proof=host_execution_evidence(items,platform=platform)
    declarations=[{'id':item.get('id'),**interpreter_command_declaration(item,python)} for item in items]
    actual=interpreter_process_evidence(process_evidence,python,python_sha256=python_sha256,native_roots=native_roots,interpreter_binding=interpreter_binding)
    observed=actual['status']=='verified'
    return {**proof,'declared_python':str(python),'native_python_observed':observed,
            'interpreter_command_declarations':declarations,'interpreter_process_evidence':actual,
            'native_python_status':actual['status'],
            'passed':bool(items) and proof['passed'] and (platform!='win32' or observed)}


def real_work_input_names(case):
    names=['upstream.json','archives.json','dependencies.lock','dependencies-frozen.txt']
    if case=='httpcore':names+=['cancellation_regression.py','network_stress.py','tls_socket_regression.py']
    return names


def real_work_tools_binding(root,selected=None):
    """Bind the original fixture environment and explicitly selected read-only copy."""
    import hashlib
    from pathlib import Path
    from install import python_entry
    source=(root/'tools311').resolve(strict=True)
    selected=Path(selected).resolve(strict=True) if selected is not None else source
    if selected==root.resolve() or selected.is_relative_to((root/'work').resolve()):
        raise ValueError('Real-work interpreter must be outside the writable project')
    def inventory(directory):
        result={}
        for parent,dirs,files in os.walk(directory,followlinks=False):
            for name in dirs+files:
                path=Path(parent)/name;info=path.lstat()
                if path.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:
                    raise ValueError('Linked real-work tool input')
                if name in files:
                    if not path.is_file() or info.st_nlink!=1:raise ValueError('Linked real-work tool file')
                    with path.open('rb') as stream:result[path.relative_to(directory).as_posix()]=hashlib.file_digest(stream,'sha256').hexdigest()
        return result
    paths={}
    for name,path in [('original',source),('selected',selected)]:
        info=path.stat();files=inventory(path);python=python_entry(path)
        if not python.is_file():raise ValueError('Real-work Python is missing')
        paths[name]={'root':str(path),'identity':[info.st_dev,info.st_ino],
                     'python':str(python),'python_sha256':hashlib.sha256(python.read_bytes()).hexdigest(),
                     'files_sha256':files}
    return {'paths':paths,'same_bytes':paths['original']['files_sha256']==paths['selected']['files_sha256']}


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
