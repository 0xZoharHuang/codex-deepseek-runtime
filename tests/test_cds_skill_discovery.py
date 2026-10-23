"""Exact candidate discovery must not silently select a global namesake."""
from copy import deepcopy
from pathlib import Path
import pytest
from verify_cds_skill import fixture_skill_selection


def rows(tmp_path):
    candidate=tmp_path/'candidate'/'SKILL.md'
    global_file=tmp_path/'global'/'SKILL.md'
    for path in [candidate,global_file]:
        path.parent.mkdir();path.write_text('skill',encoding='utf-8')
    own={'name':'cds','enabled':True,'path':str(candidate),'scope':'user','pluginId':None}
    other={'name':'cds','enabled':True,'path':str(global_file),'scope':'user','pluginId':'origin'}
    return candidate,own,other


def response(*skills):
    return {'data':[{'skills':list(skills),'errors':[]}]}


def test_global_namesake_is_recorded_without_replacing_owned_candidate(tmp_path):
    candidate,own,other=rows(tmp_path)
    value=response(other,own);before=deepcopy(value)
    selected,others=fixture_skill_selection(value,candidate)
    assert selected==own and value==before
    assert others==[{'path':other['path'],'canonical_path':str(Path(other['path']).resolve()),
                    'scope':'user','plugin_id':'origin','enabled':True}]


def test_candidate_is_matched_by_canonical_path(tmp_path):
    candidate,own,other=rows(tmp_path)
    own['path']=str(candidate.parent/'..'/candidate.parent.name/candidate.name)
    selected,others=fixture_skill_selection(response(other,own),candidate)
    assert Path(selected['path']).resolve()==candidate.resolve()
    assert len(others)==1


@pytest.mark.parametrize('failure',['only_global','disabled','duplicate_exact','wrong_name','wrong_path'])
def test_ambiguous_missing_disabled_or_wrong_candidate_is_rejected(tmp_path,failure):
    candidate,own,other=rows(tmp_path)
    skills=[other,own]
    if failure=='only_global':skills=[other]
    elif failure=='disabled':own['enabled']=False
    elif failure=='duplicate_exact':
        alias=dict(own,path=str(candidate.parent/'..'/candidate.parent.name/candidate.name))
        skills.append(alias)
    elif failure=='wrong_name':own['name']='another-skill'
    elif failure=='wrong_path':own['path']=str(tmp_path/'unexpected'/'SKILL.md')
    with pytest.raises(AssertionError):
        fixture_skill_selection(response(*skills),candidate)
