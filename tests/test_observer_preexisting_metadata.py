"""Public timestamps may exclude a stale PID edge, never authorize ownership/signals."""
import copy
import psutil
import pytest
from ci import qualify_native as qualify
from codex_deepseek import processes


@pytest.fixture
def observer(monkeypatch):
    parent={'pid':100,'birth':'parent-10','birth_after':'parent-10',
            'creation_time':10.,'ppid':1,'stable':True}
    child={'pid':200,'birth':None,'birth_after':None,'creation_time':None,'ppid':None,'stable':False}
    state={'metadata':[{'creation_time':1.,'ppid':100}]*2,'fresh':[],
           'parent_birth':'parent-10','child_birth':None,'parent_stable':True,'after_edge':100}
    monkeypatch.setattr(processes,'identity',lambda pid:state['parent_birth'] if pid==100 else state['child_birth'])
    monkeypatch.setattr(processes,'matches',lambda pid,birth:bool(birth) and processes.identity(pid)==birth)
    monkeypatch.setattr(processes,'terminate',lambda *args,**kwargs:pytest.fail('Unidentified PID must never be signalled'))
    monkeypatch.setattr(qualify,'_observer_family_batch',lambda seen:({100:copy.copy(parent)},
        {(100,'parent-10'):[copy.copy(child)]},{100:1,200:state['after_edge']}))
    def snapshot(pid,parent_map=None):
        result=copy.copy(parent)
        if not state['parent_stable']:result.update(birth='different-parent',birth_after='different-parent')
        return result
    monkeypatch.setattr(qualify,'_process_snapshot',snapshot)
    monkeypatch.setattr(qualify,'_proven_process_diagnostics',lambda row:({},row['birth'],row['birth']))
    class FreshPublicProcess:
        def __init__(self,pid):
            assert pid==200
            index=min(len(state['fresh']),len(state['metadata'])-1)
            self.data=state['metadata'][index];state['fresh'].append(self)
        def create_time(self):
            if isinstance(self.data,Exception):raise self.data
            if self.data.get('reuse_parent'):state['parent_birth']='reused-parent'
            if self.data.get('reveal_child'):state['child_birth']='now-readable'
            return self.data['creation_time']
        def ppid(self):return self.data['ppid']
        def name(self):pytest.fail('Foreign text is outside exclusion scope')
        exe=cmdline=name
    monkeypatch.setattr(psutil,'Process',FreshPublicProcess)
    seen={(100,'parent-10'):{'pid':100,'birth':'parent-10'}}
    uncertain={};rejected={};errors={}
    def observe():qualify._observe_process_family(seen,uncertain,rejected,errors)
    return state,seen,uncertain,rejected,errors,observe


def test_two_fresh_old_metadata_reads_reject_without_inventing_birth_or_owner(observer):
    state,seen,uncertain,rejected,errors,observe=observer;observe()
    assert len(state['fresh'])==2 and state['fresh'][0] is not state['fresh'][1]
    assert set(seen)=={(100,'parent-10')} and not uncertain and not errors
    row=next(iter(rejected.values()))
    assert row['reason']=='child_predates_parent_without_kernel_identity'
    assert row['birth'] is row['birth_after'] is None and row['stable'] is False
    assert 'ownership' not in row and not any(key in row for key in ('name','exe','argv'))
    assert row['rejection_evidence']['authority']=='exclusion_only_no_kernel_identity'


@pytest.mark.parametrize('metadata',[
    [{'creation_time':20.,'ppid':100}]*2,
    [{'creation_time':10.,'ppid':100}]*2,
    [{'creation_time':1.,'ppid':100},{'creation_time':2.,'ppid':100}],
    [{'creation_time':1.,'ppid':100},{'creation_time':1.,'ppid':999}],
    [{'creation_time':float('nan'),'ppid':100}]*2,
    [{'creation_time':None,'ppid':100}]*2,
    [psutil.AccessDenied(200)],
    [{'creation_time':1.,'ppid':100},psutil.AccessDenied(200)],
    [{'creation_time':1.,'ppid':100},{'creation_time':1.,'ppid':100,'reuse_parent':True}],
    [{'creation_time':1.,'ppid':100},{'creation_time':1.,'ppid':100,'reveal_child':True}],
])
def test_new_changing_or_unreadable_metadata_remains_uncertain(observer,metadata):
    state,seen,uncertain,rejected,errors,observe=observer;state['metadata']=metadata;observe()
    assert set(seen)=={(100,'parent-10')} and not rejected
    assert any(row['pid']==200 for row in uncertain.values())


@pytest.mark.parametrize('boundary',['parent_changed','edge_changed'])
def test_old_metadata_cannot_override_unstable_parent_or_edge(observer,boundary):
    state,seen,uncertain,rejected,errors,observe=observer
    if boundary=='parent_changed':state['parent_stable']=False
    else:state['after_edge']=999
    observe()
    assert state['fresh']==[] and not rejected and uncertain
    assert set(seen)=={(100,'parent-10')}


def test_resolution_is_bound_to_original_parent_and_unidentified_observation(observer):
    state,seen,uncertain,rejected,errors,observe=observer
    original={'pid':200,'birth':None,'birth_after':None,'parent_pid':100,'parent_birth':'parent-10','reason':'previous_unknown'}
    other={**original,'parent_birth':'other-parent-epoch'}
    identified={**original,'birth':'known-instance'}
    uncertain.update(original=original,other=other,identified=identified);observe()
    assert 'original' not in uncertain and uncertain=={'other':other,'identified':identified}
    row=next(iter(rejected.values()))
    assert row['resolved_uncertain_observations']==[original]
    assert row['parent_pid']==100 and row['parent_birth']=='parent-10'


def test_previous_rejection_does_not_exempt_a_later_unidentified_pid(observer):
    state,seen,uncertain,rejected,errors,observe=observer;observe()
    assert rejected and not uncertain
    state['metadata']=[{'creation_time':20.,'ppid':100}]*2;state['fresh']=[];observe()
    assert any(row['pid']==200 for row in uncertain.values())
    assert set(seen)=={(100,'parent-10')}
