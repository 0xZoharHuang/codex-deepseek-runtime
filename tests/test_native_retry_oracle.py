"""Mutation checks for the independent native compaction request oracle."""
import copy,hashlib
import pytest
from test_native_payload_retry import COMPACT,capacity_exact,compaction_wire_capacity_progress

BASE={'type':'message','id':'msg_fixture','role':'system','content':[{'type':'input_text','text':'OWN_NATIVE_BASE'}]}
PROMPT={'type':'message','role':'user','content':[{'type':'input_text','text':COMPACT}]}
ITEMS=[{'type':'message','role':'user','content':[{'type':'input_text','text':'original-'+str(i)}]} for i in range(6)]

def row(payload,index):
    items=copy.deepcopy([BASE,*payload,PROMPT])
    return {'compaction':True,'input':items,
            'input_item_sha256':[hashlib.sha256(capacity_exact(item)).hexdigest() for item in items],
            'bytes':10000-index*1000,'sha256':'body-'+str(index)}

def valid():
    return [row(ITEMS,0),row(ITEMS[:4],1),row(ITEMS[1:4],2),row(ITEMS[2:4],3),row(ITEMS[3:4],4)]

def reseal(entry):
    entry['input_item_sha256']=[hashlib.sha256(capacity_exact(item)).hexdigest() for item in entry['input']]

def test_native_compaction_oracle_accepts_confirmed_prefix_then_local_suffix():
    assert compaction_wire_capacity_progress(valid(),BASE)['all_full_item_json_hashes_preserved']

@pytest.mark.parametrize('mutation',[
    'base','prompt','evidence','first-suffix','later-prefix','business','order',
    'duplicate-body','same-size','no-progress','duplicate-base'])
def test_native_compaction_oracle_rejects_changed_evidence(mutation):
    rows=valid()
    if mutation=='base':
        rows[0]['input'][0]['content'][0]['text']='different';reseal(rows[0])
    elif mutation=='prompt':
        rows[1]['input'][-1]['role']='assistant';reseal(rows[1])
    elif mutation=='evidence':rows[2]['input_item_sha256'][1]='forged'
    elif mutation=='first-suffix':rows[1]=row(ITEMS[2:],1)
    elif mutation=='later-prefix':rows[2]=row(ITEMS[:3],2)
    elif mutation=='business':
        rows[2]['input'][1]['content'][0]['text']='business-mutated';reseal(rows[2])
    elif mutation=='order':
        rows[2]['input'][1:3]=reversed(rows[2]['input'][1:3]);reseal(rows[2])
    elif mutation=='duplicate-body':rows[3]['sha256']=rows[2]['sha256']
    elif mutation=='same-size':rows[3]['bytes']=rows[2]['bytes']
    elif mutation=='no-progress':rows[2]=row(ITEMS[:4],2)
    elif mutation=='duplicate-base':
        rows[2]['input'].insert(1,copy.deepcopy(BASE));reseal(rows[2])
    else:raise AssertionError('Undeclared mutation')
    with pytest.raises(AssertionError):
        compaction_wire_capacity_progress(rows,BASE)
