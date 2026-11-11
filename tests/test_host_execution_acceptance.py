"""QA evidence cannot promote echo text, argv aliases, or unrelated processes."""
import copy
import hashlib
import ntpath
import os
from pathlib import Path
import pytest
from host_execution_acceptance import interpreter_command_declaration,interpreter_process_evidence
from acceptance_assertions import native_host_execution


def event(command):
    return {'method':'item/completed','params':{'item':{'type':'commandExecution','id':'tool1',
        'command':command,'exitCode':0}}}


@pytest.mark.parametrize('script',[
    "Write-Output 'D:/Fixture/python.exe'",
    "'D:/Fixture/python.exe'",
    "if ($false) { & 'D:/Fixture/python.exe' -m pytest }",
    "Write-Output \"& 'D:/Fixture/python.exe' -m pytest\"",
    "& 'D:/Fixture/python.exe' -m pytest; Write-Output done",
    '$p="D:/Fixture/python.exe"; & $p -m pytest',
])
def test_echo_quoted_values_dead_code_and_compound_scripts_are_not_proven_invocations(script):
    item=event(script)['params']['item']
    assert interpreter_command_declaration(item,'D:/Fixture/python.exe')['kind']=='unverified'
    result=native_host_execution([event(script)],'D:/Fixture/python.exe','win32')
    assert result['native_python_status']=='unverified' and result['native_python_observed'] is False and not result['passed']


def test_native_unescaped_command_action_is_used_only_as_a_declaration():
    item=event('rendered PowerShell wrapper with D:/Fixture/python.exe')['params']['item']
    item['commandActions']=[{'type':'unknown','command':"& 'D:\\Fixture\\python.exe' -B -m pytest"}]
    result=interpreter_command_declaration(item,'D:/Fixture/python.exe')
    assert result['kind']=='literal_invocation_declaration' and result['source']=='commandActions'
    assert result['process_execution_verified'] is False


def windows_fixture_root(tmp_path):
    # These receipts simulate Win32 paths on every test host. POSIX // keeps
    # the same real files while providing valid UNC syntax to ntpath.isabs.
    # Do not resolve() the alias: that removes its two leading slashes.
    root = tmp_path if os.name == 'nt' else Path('//' + tmp_path.as_posix().lstrip('/'))
    assert ntpath.isabs(str(root))
    return root


def fixture(tmp_path):
    tmp_path=windows_fixture_root(tmp_path)
    native=tmp_path/'codex.exe';native.write_bytes(b'PINNED_NATIVE_RUNTIME_FIXTURE')
    python=tmp_path/'python.exe';python.write_bytes(b'PINNED_INTERPRETER_FIXTURE')
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    root={'pid':10,'birth':'windows:100','creation_time':100.0,'ownership':'created_Popen_handle','exe':str(native),'argv':['native']}
    def child(pid,birth,created,parent,exe):
        return {'pid':pid,'birth':birth,'birth_after':birth,'creation_time':created,'stable':True,
            'ownership':'stable_direct_parent_edge','ppid':parent['pid'],'parent_pid':parent['pid'],
            'parent_birth':parent['birth'],'parent_creation_time':parent['creation_time'],'exe':str(exe),'argv':[str(exe)]}
    shell=child(20,'windows:200',101.0,root,tmp_path/'powershell.exe')
    interpreter=child(30,'windows:300',102.0,shell,python)
    evidence={'observed':[root,shell,interpreter],
              'exit_evidence_source':'retained_os_process_handles','exits':[{'pid':30,'birth':'windows:300','exit_code':0}]}
    options={'python_sha256':sha(python),'native_roots':[{'pid':10,'birth':'windows:100','exe':str(native),'sha256':sha(native)}]}
    return python,evidence,options


def test_actual_selected_image_successfully_exits_under_native_branch(tmp_path):
    python,evidence,options=fixture(tmp_path)
    proof=interpreter_process_evidence(evidence,python,**options)
    assert proof['status']=='verified' and proof['witnesses'][0]['pid']==30
    assert proof['witnesses'][0]['os_exit_code']==0
    report=native_host_execution([event('& "'+str(python)+'" -m pytest')],python,'win32',process_evidence=evidence,**options)
    assert report['passed'] and report['native_python_observed'] and report['native_python_status']=='verified'


@pytest.mark.parametrize('change',['argv_only','unrelated_branch','missing_parent','wrong_parent_birth',
    'unstable','birth_changed','parent_creation_changed','earlier_than_parent','missing_exit','failed_exit',
    'boolean_exit','exit_other_birth','shell_exit_claim','duplicate_process','duplicate_exit','native_image_changed','python_image_changed'])
def test_actual_interpreter_requires_bound_image_lineage_and_same_instance_os_exit(tmp_path,change):
    python,evidence,options=fixture(tmp_path);child=evidence['observed'][2]
    tmp_path=python.parent
    assert interpreter_process_evidence(evidence,python,**options)['status']=='verified'
    if change=='argv_only':child['exe']=str(tmp_path/'base-python.exe');child['argv'][0]=str(python)
    elif change=='unrelated_branch':
        outsider={'pid':40,'birth':'windows:400','creation_time':90.0,'ownership':'created_Popen_handle','exe':str(tmp_path/'controller.exe')}
        evidence['observed'].append(outsider);child.update(ppid=40,parent_pid=40,parent_birth=outsider['birth'],parent_creation_time=90.0)
    elif change=='missing_parent':evidence['observed'].pop(1)
    elif change=='wrong_parent_birth':child['parent_birth']='windows:999'
    elif change=='unstable':child['stable']=False
    elif change=='birth_changed':child['birth_after']='windows:999'
    elif change=='parent_creation_changed':child['parent_creation_time']=99.0
    elif change=='earlier_than_parent':child['creation_time']=99.0
    elif change=='missing_exit':evidence['exits']=[]
    elif change=='failed_exit':evidence['exits'][0]['exit_code']=1
    elif change=='boolean_exit':evidence['exits'][0]['exit_code']=False
    elif change=='exit_other_birth':evidence['exits'][0]['birth']='windows:999'
    elif change=='shell_exit_claim':evidence['exit_evidence_source']='native_command_exitCode'
    elif change=='duplicate_process':evidence['observed'].append(copy.deepcopy(child))
    elif change=='duplicate_exit':evidence['exits'].append(copy.deepcopy(evidence['exits'][0]))
    elif change=='native_image_changed':Path(options['native_roots'][0]['exe']).write_bytes(b'CHANGED')
    elif change=='python_image_changed':python.write_bytes(b'CHANGED')
    proof=interpreter_process_evidence(evidence,python,**options)
    assert proof['status']=='unverified' and proof['witnesses']==[]


def test_controller_baseline_python_does_not_count_as_native_tool_execution(tmp_path):
    python,evidence,options=fixture(tmp_path)
    # The same pinned interpreter may be used by external validation. Its real
    # process alone is insufficient unless it descends from the native task.
    evidence['observed'][2].update(ppid=999,parent_pid=999,parent_birth='windows:999',parent_creation_time=90.0)
    assert interpreter_process_evidence(evidence,python,**options)['status']=='unverified'


def test_observed_interpreter_does_not_override_explicit_foreign_host_command(tmp_path):
    python,evidence,options=fixture(tmp_path)
    result=native_host_execution([event('wsl.exe python3 -m pytest')],python,'win32',process_evidence=evidence,**options)
    assert result['native_python_observed'] and not result['passed'] and result['forbidden_commands']


def venv_fixture(tmp_path):
    tmp_path=windows_fixture_root(tmp_path)
    original,evidence,options=fixture(tmp_path)
    env=tmp_path/'venv';(env/'Scripts').mkdir(parents=True)
    launcher=env/'Scripts/python.exe';launcher.write_bytes(original.read_bytes())
    base=tmp_path/'base/python.exe';base.parent.mkdir();base.write_bytes(b'PINNED_BASE_INTERPRETER_FIXTURE')
    cfg=env/'pyvenv.cfg';cfg.write_text('home = '+str(base.parent)+'\nexecutable = '+str(base)+'\nversion = 3.11.9\n',encoding='utf8')
    parent=evidence['observed'][2];parent.update(exe=str(launcher),argv=[str(launcher)])
    child={'pid':40,'birth':'windows:400','birth_after':'windows:400','creation_time':103.0,'stable':True,
        'ownership':'stable_direct_parent_edge','ppid':30,'parent_pid':30,'parent_birth':parent['birth'],
        'parent_creation_time':parent['creation_time'],'exe':str(base),'argv':[str(launcher),'-B','-m','pytest']}
    evidence['observed'].append(child);evidence['exits']=[{'pid':40,'birth':'windows:400','exit_code':0}]
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    options['python_sha256']=sha(launcher)
    options['interpreter_binding']={'launcher':{'path':str(launcher),'sha256':sha(launcher)},
        'pyvenv_cfg':{'path':str(cfg),'sha256':sha(cfg)},'base_executable':{'path':str(base),'sha256':sha(base)}}
    return launcher,evidence,options


def test_pinned_venv_base_image_with_actual_launcher_parent_is_strictly_supported(tmp_path):
    launcher,evidence,options=venv_fixture(tmp_path)
    proof=interpreter_process_evidence(evidence,launcher,**options)
    assert proof['status']=='verified' and len(proof['witnesses'])==1
    witness=proof['witnesses'][0]
    assert witness['kind']=='bound_venv_base' and witness['pid']==40 and witness['launcher_pid']==30
    assert witness['argv0_matches_launcher'] and witness['os_exit_code']==0
    # Actual images/config/parent identity prove it; argv0 is merely auxiliary.
    evidence['observed'][3]['argv'][0]='other rendered argv'
    assert interpreter_process_evidence(evidence,launcher,**options)['status']=='verified'


@pytest.mark.parametrize('change',['no_binding','wrong_launcher_hash','cfg_changed','cfg_declares_other_base',
    'base_changed','wrong_base_image','no_launcher_parent','launcher_parent_reused','unrelated_native_branch','no_base_exit'])
def test_venv_support_does_not_turn_any_python_or_argv0_into_the_selected_environment(tmp_path,change):
    launcher,evidence,options=venv_fixture(tmp_path);binding=options['interpreter_binding'];child=evidence['observed'][3]
    tmp_path=launcher.parent.parent.parent
    assert interpreter_process_evidence(evidence,launcher,**options)['status']=='verified'
    if change=='no_binding':options.pop('interpreter_binding')
    elif change=='wrong_launcher_hash':binding['launcher']['sha256']='0'*64
    elif change=='cfg_changed':Path(binding['pyvenv_cfg']['path']).write_text('changed',encoding='utf8')
    elif change=='cfg_declares_other_base':
        cfg=Path(binding['pyvenv_cfg']['path']);cfg.write_text('home = '+str(tmp_path/'other')+'\nexecutable = '+str(tmp_path/'other/python.exe')+'\n',encoding='utf8')
        binding['pyvenv_cfg']['sha256']=hashlib.sha256(cfg.read_bytes()).hexdigest()
    elif change=='base_changed':Path(binding['base_executable']['path']).write_bytes(b'CHANGED_BASE')
    elif change=='wrong_base_image':child['exe']=str(tmp_path/'unbound/python.exe')
    elif change=='no_launcher_parent':
        shell=evidence['observed'][1];child.update(ppid=shell['pid'],parent_pid=shell['pid'],parent_birth=shell['birth'],parent_creation_time=shell['creation_time'])
    elif change=='launcher_parent_reused':child['parent_birth']='windows:999'
    elif change=='unrelated_native_branch':
        evidence['observed'][2].update(ppid=999,parent_pid=999,parent_birth='windows:999',parent_creation_time=90.0)
    elif change=='no_base_exit':evidence['exits']=[]
    proof=interpreter_process_evidence(evidence,launcher,**options)
    assert proof['status']=='unverified' and proof['witnesses']==[]
