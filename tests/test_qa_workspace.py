# QA layout tests use owned temporary files and links, never a native runtime.
import json
import os
from pathlib import Path
import pytest
import qa_workspace as layout


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    case=tmp_path/'case spaces & !';case.mkdir()
    (case/'work').mkdir();(case/'work/file.txt').write_text('original')
    for name in ['home','oracle']:
        (case/name).mkdir();(case/name/'canary').write_text(name)
    base=tmp_path/'ordinary projects'
    config={'run_id':'owned-test-run','workspace_root':str(base),'case_roots':[str(case)]}
    monkeypatch.setattr(layout,'WINDOWS',True)
    monkeypatch.setenv(layout.ENV,json.dumps(config))
    if os.name!='nt':monkeypatch.setattr(layout,'_junction',lambda a,p:a.symlink_to(p,target_is_directory=True))
    return case,base,config


def test_copy_reuse_owned_mapping_and_cleanup_preserve_private_inputs(fixture):
    case,base,_=fixture
    physical=layout.prepare_workspace(case)
    assert physical.parent==base and physical!=case/'work'
    assert (physical/'file.txt').read_text()=='original'
    assert (case/'work').resolve()==physical
    (physical/'file.txt').write_text('after')
    assert (case/'work/file.txt').read_text()=='after'
    assert (case/'.qa-work-original/file.txt').read_text()=='original'
    assert layout.prepare_workspace(case)==physical
    layout.cleanup_workspace(case)
    assert not os.path.lexists(case/'work') and not physical.exists()
    assert (case/'home/canary').read_text()=='home'
    assert (case/'oracle/canary').read_text()=='oracle'
    with pytest.raises(ValueError,match='not reusable'):layout.prepare_workspace(case)


def test_only_owned_cwd_fields_are_mapped_not_tool_arguments(fixture):
    case,base,_=fixture
    physical=layout.prepare_workspace(case)
    foreign=str(case/'oracle')
    command=['python','-c','keep '+str(case/'work')]
    request={'id':1,'method':'command/exec','params':{'cwd':str(case/'work'),
        'cwds':[str(case/'work/sub'),foreign],'command':command,'other':str(case/'work')}}
    mapped=layout.workspace_request(case,request)
    assert Path(mapped['params']['cwd'])==physical
    assert Path(mapped['params']['cwds'][0])==physical/'sub'
    assert mapped['params']['cwds'][1]==foreign
    assert mapped['params']['command']==command and mapped['params']['other']==str(case/'work')
    assert request['params']['cwd']==str(case/'work')
    assert layout.workspace_path(case,str(case/'work-foreign'))==str(case/'work-foreign')


def test_disabled_layout_has_no_side_effects(tmp_path,monkeypatch):
    monkeypatch.delenv(layout.ENV,raising=False)
    assert layout.prepare_workspace(tmp_path)==tmp_path/'work'
    assert not list(tmp_path.iterdir())


def test_wrong_case_root_is_rejected_before_writes(fixture,monkeypatch):
    case,base,config=fixture
    config['case_roots']=[str(case/'home')]
    monkeypatch.setenv(layout.ENV,json.dumps(config))
    with pytest.raises(ValueError,match='outside'):layout.prepare_workspace(case)
    assert not base.exists() and (case/'work/file.txt').read_text()=='original'


@pytest.mark.parametrize('field',['case_identity','base_identity','alias_identity','physical_identity'])
def test_foreign_directory_identity_is_rejected(fixture,field):
    case,base,_=fixture;physical=layout.prepare_workspace(case)
    p=case/layout.RECORD;value=json.loads(p.read_text());value[field]=[-1,-1];p.write_text(json.dumps(value))
    with pytest.raises(ValueError):layout.prepare_workspace(case)
    with pytest.raises(ValueError):layout.cleanup_workspace(case)
    assert (physical/'file.txt').read_text()=='original'


def test_foreign_existing_work_junction_is_not_claimed(fixture,tmp_path):
    case,base,_=fixture;outside=tmp_path/'foreign';outside.mkdir();(outside/'canary').write_text('untouched')
    (case/'work/file.txt').unlink();(case/'work').rmdir();layout._junction(case/'work',outside)
    with pytest.raises(ValueError,match='physical'):layout.prepare_workspace(case)
    assert (outside/'canary').read_text()=='untouched' and not (case/layout.RECORD).exists()


def test_linked_input_is_rejected_without_copying_oracle(fixture):
    case,base,_=fixture
    os.link(case/'oracle/canary',case/'work/link')
    with pytest.raises(ValueError,match='linked'):layout.prepare_workspace(case)
    assert not (case/layout.RECORD).exists() and (case/'oracle/canary').read_text()=='oracle'


def test_failed_junction_restores_original_and_refuses_implicit_retry(fixture,monkeypatch):
    case,base,_=fixture
    def fail(*a):raise RuntimeError('fixture junction failure')
    monkeypatch.setattr(layout,'_junction',fail)
    with pytest.raises(RuntimeError,match='fixture junction'):layout.prepare_workspace(case)
    assert (case/'work/file.txt').read_text()=='original' and not layout._link(case/'work')
    assert json.loads((case/layout.RECORD).read_text())['state']=='failed'
    with pytest.raises(ValueError,match='not reusable'):layout.prepare_workspace(case)


def test_retargeted_alias_cannot_clean_foreign_target(fixture,tmp_path):
    case,base,_=fixture;owned=layout.prepare_workspace(case)
    outside=tmp_path/'foreign';outside.mkdir();(outside/'canary').write_text('untouched')
    (case/'work').rmdir() if os.name=='nt' else (case/'work').unlink()
    layout._junction(case/'work',outside)
    with pytest.raises(ValueError):layout.cleanup_workspace(case)
    assert (outside/'canary').read_text()=='untouched' and owned.is_dir()


def test_named_space_workspace_keeps_physical_space_and_reuses_in_peer(fixture):
    case,base,_=fixture
    (case/'work').rename(case/'work with spaces')
    physical=layout.prepare_workspace(case,'work with spaces')
    assert ' ' in physical.name and layout.prepare_workspace(case)==physical
    assert Path(layout.workspace_path(case,str(case/'work with spaces/file.txt')))==physical/'file.txt'
    assert layout.workspace_path(case,str(case/'work/foreign.txt'))==str(case/'work/foreign.txt')
    layout.cleanup_workspace(case)


def test_foreign_workspace_parent_link_rejected_before_child_creation(fixture,tmp_path,monkeypatch):
    case,base,config=fixture
    outside=tmp_path/'foreign';outside.mkdir();(outside/'canary').write_text('untouched')
    link=tmp_path/'linked-root';layout._junction(link,outside)
    config['workspace_root']=str(link/'must-not-create')
    monkeypatch.setenv(layout.ENV,json.dumps(config))
    with pytest.raises(ValueError,match='physical'):layout.prepare_workspace(case)
    assert not (outside/'must-not-create').exists() and (outside/'canary').read_text()=='untouched'


def test_prior_pending_binding_is_not_implicitly_repaired(fixture):
    case,base,_=fixture
    (case/layout.RECORD).write_text(json.dumps({'state':'pending'}))
    with pytest.raises(ValueError,match='not reusable'):layout.prepare_workspace(case)
    assert (case/'work/file.txt').read_text()=='original'


def test_cleanup_rejects_case_root_alias_even_to_another_allowed_case(fixture,tmp_path,monkeypatch):
    case,base,config=fixture
    config['case_roots']=[str(tmp_path)]
    monkeypatch.setenv(layout.ENV,json.dumps(config))
    other=tmp_path/'other-owned-case';other.mkdir();(other/'work').mkdir();(other/'work/canary').write_text('preserve')
    physical=layout.prepare_workspace(other)
    redirected=tmp_path/'case-root-alias';layout._junction(redirected,other)
    with pytest.raises(ValueError,match='physical'):layout.cleanup_workspace(redirected)
    assert (physical/'canary').read_text()=='preserve' and (other/'work').resolve()==physical


@pytest.mark.parametrize('kind',['hardlink','symlink'])
def test_cleanup_rejects_linked_record_even_when_its_json_is_valid(fixture,kind):
    case,base,_=fixture;physical=layout.prepare_workspace(case)
    source=case/'original-record.json';(case/layout.RECORD).rename(source)
    if kind=='hardlink':os.link(source,case/layout.RECORD)
    else:(case/layout.RECORD).symlink_to(source)
    with pytest.raises(ValueError,match='record'):layout.cleanup_workspace(case)
    assert (physical/'file.txt').read_text()=='original' and (case/'work').resolve()==physical


def test_cleanup_removes_owned_nested_junction_without_deleting_foreign_target(fixture,tmp_path):
    case,base,_=fixture;physical=layout.prepare_workspace(case)
    outside=tmp_path/'foreign-nested-target';outside.mkdir();(outside/'canary').write_text('untouched')
    layout._junction(physical/'foreign-link',outside)
    layout.cleanup_workspace(case)
    assert (outside/'canary').read_text()=='untouched' and not physical.exists()


def test_parent_alias_cannot_place_copy_back_inside_private_source(fixture,monkeypatch):
    case,base,config=fixture
    parent=case.parent/'existing-parent';parent.mkdir()
    config['workspace_root']=str(parent/'..'/case.name/'work'/'recursive-copy')
    monkeypatch.setenv(layout.ENV,json.dumps(config))
    with pytest.raises(ValueError,match='outside'):layout.prepare_workspace(case)
    assert not (case/'work/recursive-copy').exists() and (case/'work/file.txt').read_text()=='original'
