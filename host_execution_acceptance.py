"""QA-only evidence about observed native command items, not a runtime policy."""
import re
import sys

FOREIGN_WINDOWS_COMMAND=re.compile(r'(?i)(?<![A-Za-z0-9_])(?:wsl(?:\.exe)?|wslhost(?:\.exe)?|bash(?:\.exe)?|docker(?:\.exe)?|podman(?:\.exe)?|cygwin\d*|msys\d*)(?![A-Za-z0-9_])')


def host_execution_evidence(items,*,platform=None):
    platform=platform or sys.platform
    commands=[{'id':item.get('id'),'command':item.get('command'),'exit_code':item.get('exitCode')}
              for item in items if item.get('type')=='commandExecution']
    malformed=[row for row in commands if not isinstance(row['command'],str)]
    forbidden=[row for row in commands if isinstance(row['command'],str) and FOREIGN_WINDOWS_COMMAND.search(row['command'])] if platform=='win32' else []
    return {'passed':not malformed and not forbidden,'platform':platform,'observed_command_count':len(commands),
            'forbidden_commands':forbidden,'malformed_commands':malformed,
            'scope':'Observed native commandExecution items; not a system-wide process audit.'}


def command_mentions_path(command,path):
    """Diagnostic reference only: a path mention never proves execution."""
    text=str(path);variants={text,text.replace('\\','/'),text.replace('/','\\')}
    variants|={value.replace('\\','\\\\') for value in variants}
    return any(value.casefold() in command.casefold() for value in variants)


def _path_key(value):
    import ntpath
    if not isinstance(value,str) or not value or "\x00" in value:return None
    if value.startswith("\\\\?\\"):value=value[4:]
    return ntpath.normcase(ntpath.normpath(value)) if ntpath.isabs(value) else None


def _literal_powershell_tokens(text):
    """A deliberately small literal-only syntax subset, not a PowerShell evaluator."""
    tokens=[];i=0
    while i<len(text):
        while i<len(text) and text[i].isspace():
            if text[i] in "\r\n":return None
            i+=1
        if i==len(text):break
        quote=text[i] if text[i] in "\"'" else None
        if quote:
            i+=1;value=[]
            while i<len(text):
                char=text[i]
                if char==quote:
                    if quote=="'" and i+1<len(text) and text[i+1]=="'":value.append("'");i+=2;continue
                    i+=1;break
                if quote=='"' and char in '$`':return None
                value.append(char);i+=1
            else:return None
            if i<len(text) and not text[i].isspace():return None
            tokens.append((''.join(value),True))
        else:
            start=i
            while i<len(text) and not text[i].isspace():
                if text[i] in ";|&<>{}()$`#@=\"'":return None
                i+=1
            tokens.append((text[start:i],False))
    return tokens


def interpreter_command_declaration(item,python):
    """Classify only an obvious literal call; even that is not process evidence.

    Native commandActions contains the unescaped shell script. Arbitrary control
    flow, expressions and rendered shell wrappers remain unverified. No text is
    executed and no substring can produce an observed-interpreter result.
    """
    command=item.get('command')
    actions=item.get('commandActions')
    if (isinstance(actions,list) and len(actions)==1 and isinstance(actions[0],dict)
        and actions[0].get('type')=='unknown' and isinstance(actions[0].get('command'),str)):
        command=actions[0]['command']
    if not isinstance(command,str):return {'kind':'unverified','reason':'missing_command'}
    value=command.strip();call_operator=value.startswith('&')
    if call_operator:value=value[1:].lstrip()
    tokens=_literal_powershell_tokens(value)
    if (tokens and (call_operator or not tokens[0][1])
        and _path_key(tokens[0][0])==_path_key(str(python)) and _path_key(str(python)) is not None):
        return {'kind':'literal_invocation_declaration','source':'commandActions' if command!=item.get('command') else 'command',
                'process_execution_verified':False}
    return {'kind':'unverified','reason':'reference_or_nonliteral_script',
            'path_reference':command_mentions_path(command,python),'process_execution_verified':False}


def _instance(row):
    import math
    if not isinstance(row,dict):return None
    pid=row.get('pid');birth=row.get('birth');created=row.get('creation_time')
    if (type(pid) is not int or pid<=0 or not isinstance(birth,str)
        or re.fullmatch(r'windows:[0-9]+',birth) is None
        or type(created) not in {int,float} or not math.isfinite(created) or created<=0):return None
    return pid,birth


def interpreter_process_evidence(evidence,python,*,python_sha256=None,native_roots=(),interpreter_binding=None):
    """Read trusted controller OS observations; never accept model/tool prose.

    native_roots comes from the owned native runtime launch, with its physical
    exe and pinned SHA. `observed` reuses the existing PID/birth/stable-edge
    receipt. `exits` must come from retained OS process handles, not shell exit
    codes. Missing/partial evidence stays unverified. A base image needs a pinned launcher/config/base binding and an actual
    launcher parent instance; argv[0] never independently proves the environment.
    """
    import hashlib
    from pathlib import Path
    def digest(path):
        with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    result={'status':'unverified','witnesses':[],
            'scope':'Selected interpreter image in a qualified native-runtime descendant, plus same-instance OS exit; not a system-wide process audit.'}
    if (not isinstance(evidence,dict) or not isinstance(evidence.get('observed'),list)
        or not isinstance(evidence.get('exits'),list)
        or evidence.get('exit_evidence_source')!='retained_os_process_handles'
        or not isinstance(native_roots,(list,tuple)) or not native_roots
        or not isinstance(python_sha256,str) or re.fullmatch(r'[0-9a-f]{64}',python_sha256) is None):
        return {**result,'reason':'missing_independent_process_evidence'}
    try:
        if digest(python)!=python_sha256:return {**result,'reason':'interpreter_binding_changed'}
    except OSError:return {**result,'reason':'interpreter_binding_unavailable'}
    bound_base=None
    if interpreter_binding is not None:
        try:
            launcher=interpreter_binding['launcher'];cfg=interpreter_binding['pyvenv_cfg'];base=interpreter_binding['base_executable']
            if (_path_key(launcher['path'])!=_path_key(str(python)) or launcher['sha256']!=python_sha256
                or _path_key(cfg['path'])!=_path_key(str(Path(python).parent.parent/'pyvenv.cfg'))
                or re.fullmatch(r'[0-9a-f]{64}',cfg['sha256']) is None or digest(cfg['path'])!=cfg['sha256']
                or re.fullmatch(r'[0-9a-f]{64}',base['sha256']) is None or digest(base['path'])!=base['sha256']):
                raise ValueError('invalid pinned venv inputs')
            values={}
            for line in Path(cfg['path']).read_text(encoding='utf-8-sig').splitlines():
                if not line.strip() or line.lstrip().startswith('#'):continue
                key,separator,value=line.partition('=')
                if not separator:raise ValueError('invalid venv config')
                key=key.strip().casefold()
                if key in values:raise ValueError('duplicate venv config key')
                values[key]=value.strip()
            import ntpath
            declared=values.get('executable') or (ntpath.join(values['home'],'python.exe') if values.get('home') else None)
            if (_path_key(declared) is None or _path_key(declared)!=_path_key(base['path'])
                or values.get('home') and _path_key(values['home'])!=_path_key(ntpath.dirname(base['path']))):
                raise ValueError('venv base does not match declared pinned runtime')
            bound_base={'path':base['path'],'sha256':base['sha256'],'cfg_sha256':cfg['sha256']}
        except (OSError,ValueError,KeyError,TypeError,UnicodeError):
            return {**result,'reason':'invalid_interpreter_binding'}
    observed={}
    for row in evidence['observed']:
        key=_instance(row)
        if key is None:continue
        if key in observed:return {**result,'reason':'duplicate_process_identity'}
        observed[key]=row
    roots=set()
    try:
        for binding in native_roots:
            key=(binding['pid'],binding['birth']);row=observed.get(key)
            if (row is None or _path_key(row.get('exe')) is None
                or _path_key(row['exe'])!=_path_key(binding['exe'])
                or re.fullmatch(r'[0-9a-f]{64}',binding['sha256']) is None
                or digest(binding['exe'])!=binding['sha256']):
                return {**result,'reason':'unqualified_native_root'}
            roots.add(key)
    except (OSError,ValueError,KeyError,TypeError):return {**result,'reason':'unqualified_native_root'}
    exits={}
    for row in evidence['exits']:
        if not isinstance(row,dict):return {**result,'reason':'invalid_process_exit'}
        if (type(row.get('pid')) is not int or row['pid']<=0 or not isinstance(row.get('birth'),str)
            or re.fullmatch(r'windows:[0-9]+',row['birth']) is None):
            return {**result,'reason':'invalid_process_exit'}
        key=(row['pid'],row['birth'])
        if key in exits:return {**result,'reason':'duplicate_process_exit'}
        exits[key]=row.get('exit_code')
    def qualified(key):
        seen=set();chain=[]
        while key not in roots:
            if key in seen or key not in observed:return None
            seen.add(key);row=observed[key]
            if (row.get('ownership')!='stable_direct_parent_edge' or row.get('stable') is not True
                or row.get('birth_after')!=row['birth'] or row.get('ppid')!=row.get('parent_pid')):return None
            if (type(row.get('parent_pid')) is not int or row['parent_pid']<=0
                or not isinstance(row.get('parent_birth'),str)):return None
            parent=(row['parent_pid'],row['parent_birth']);parent_row=observed.get(parent)
            if (parent_row is None or row.get('parent_creation_time')!=parent_row.get('creation_time')
                or row['creation_time']<parent_row['creation_time']):return None
            chain.append({'pid':key[0],'birth':key[1]});key=parent
        return chain if chain else None
    for key,row in observed.items():
        selected=_path_key(row.get('exe'))==_path_key(str(python))
        base=bound_base is not None and _path_key(row.get('exe'))==_path_key(bound_base['path'])
        if not selected and not base:continue
        if type(exits.get(key)) is not int or exits[key]!=0:continue
        chain=qualified(key)
        if chain is None:continue
        witness={'pid':key[0],'birth':key[1],'exe':row['exe'],
                 'native_ancestry':chain,'os_exit_code':0}
        if selected:
            witness.update(kind='selected_entry',image_sha256=python_sha256)
        else:
            parent=(row.get('parent_pid'),row.get('parent_birth'));launcher=observed.get(parent)
            if launcher is None or _path_key(launcher.get('exe'))!=_path_key(str(python)):continue
            argv=row.get('argv')
            witness.update(kind='bound_venv_base',image_sha256=bound_base['sha256'],
                launcher_pid=parent[0],launcher_birth=parent[1],launcher_sha256=python_sha256,
                pyvenv_cfg_sha256=bound_base['cfg_sha256'],
                argv0_matches_launcher=bool(isinstance(argv,list) and argv and _path_key(argv[0])==_path_key(str(python))))
        result['witnesses'].append(witness)
    if result['witnesses']:result['status']='verified'
    else:result['reason']='no_successful_selected_interpreter_process_in_native_branch'
    return result
