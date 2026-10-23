import os
from pathlib import Path
import shutil
import pytest
import acceptance_assertions as checks
import verify_real_work as verifier
from install import python_entry


@pytest.fixture
def tools(tmp_path):
    root=tmp_path/'private/jinja';(root/'work').mkdir(parents=True)
    source=root/'tools311';python=python_entry(source);python.parent.mkdir(parents=True);python.write_bytes(b'python fixture')
    (source/'pyvenv.cfg').write_text('fixture base')
    (source/'module.py').write_text('value=1')
    selected=tmp_path/'ordinary/jinja/tools311';shutil.copytree(source,selected)
    return root,source,selected


def test_tool_binding_compares_all_bytes_and_both_directory_identities(tools):
    root,source,selected=tools;binding=checks.real_work_tools_binding(root,selected)
    assert binding['same_bytes']
    assert set(binding['paths'])=={'original','selected'}
    assert binding['paths']['original']['root']==str(source)
    assert binding['paths']['selected']['python']==str(python_entry(selected))
    assert binding['paths']['original']['identity']!=binding['paths']['selected']['identity']


@pytest.mark.parametrize('side',['original','selected'])
def test_tool_binding_detects_dependency_mutation(tools,side):
    root,source,selected=tools;before=checks.real_work_tools_binding(root,selected)
    target=source if side=='original' else selected
    (target/'module.py').write_text('changed')
    after=checks.real_work_tools_binding(root,selected)
    assert before!=after and not after['same_bytes']


def test_tool_binding_includes_bytecode_instead_of_silently_excluding_it(tools):
    root,source,selected=tools
    (source/'__pycache__').mkdir();(source/'__pycache__/module.pyc').write_bytes(b'original')
    assert not checks.real_work_tools_binding(root,selected)['same_bytes']


def test_tool_binding_rejects_model_writable_interpreter(tools):
    root,source,selected=tools
    copied=root/'work/tools';shutil.copytree(source,copied)
    with pytest.raises(ValueError,match='writable'):checks.real_work_tools_binding(root,copied)


def test_tool_binding_rejects_linked_dependency(tools):
    root,source,selected=tools
    os.link(selected/'module.py',selected/'linked.py')
    with pytest.raises(ValueError,match='Linked'):checks.real_work_tools_binding(root,selected)


def test_explicit_tools_root_controls_model_and_controller_interpreter(tools,monkeypatch):
    root,source,selected=tools
    monkeypatch.setattr(verifier,'TOOLS_ROOT',None)
    assert verifier.tool_python(root)==python_entry(source)
    monkeypatch.setattr(verifier,'TOOLS_ROOT',selected.parent.parent)
    assert verifier.tool_python(root)==python_entry(selected)


@pytest.mark.parametrize('defect',[None,'missing','changed_dependency','wrong_interpreter','forged_after','unchanged_flag','replaced_directory'])
def test_complex_collector_rechecks_tools_instead_of_trusting_producer(tools,defect):
    import copy
    from verify_complex_runtime import tool_environment_evidence
    root,source,selected=tools;binding=checks.real_work_tools_binding(root,selected)
    report={'tool_environment':{'before':binding,'after':copy.deepcopy(binding),'unchanged':True},
            'native_host_execution':{'declared_python':str(python_entry(selected))}}
    if defect=='missing':report.pop('tool_environment')
    elif defect=='changed_dependency':(selected/'module.py').write_text('tampered')
    elif defect=='wrong_interpreter':report['native_host_execution']['declared_python']=str(python_entry(source))
    elif defect=='forged_after':report['tool_environment']['after']['paths']['selected']['identity']=[0,0]
    elif defect=='unchanged_flag':report['tool_environment']['unchanged']=False
    elif defect=='replaced_directory':
        original=selected.with_name('previous');selected.rename(original);shutil.copytree(original,selected)
    assert tool_environment_evidence(root,report)['passed']==(defect is None)
