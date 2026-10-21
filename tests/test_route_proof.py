"""Provider route evidence must belong to the exact four exercised responses."""
from copy import deepcopy
import pytest
from verify_routes import GENERATION_MODEL,ROUTE_CASES,generation_proof_row,route_evidence_passed


def evidence():
    response_ids=['serial-first','serial-second','structured','ordinary']
    routes=[];proof=[]
    for response_id,(provider,display_name) in zip(response_ids,ROUTE_CASES):
        routes.append((provider,'completed',response_id))
        proof.append(generation_proof_row(response_id,display_name,
            {'id':response_id,'provider_name':display_name,'model':GENERATION_MODEL},[{'http_status':200}]))
    return routes,proof,response_ids


def test_exact_route_response_receipt_and_generation_chain_passes():
    routes,proof,ids=evidence()
    assert route_evidence_passed(routes,proof,ids)
    assert route_evidence_passed([list(row) for row in routes],proof,ids)


@pytest.mark.parametrize('returned_id',['unrelated-generation',None])
def test_wrong_or_missing_generation_id_cannot_attest_matching_provider_and_model(returned_id):
    generation={'provider_name':'DeepSeek','model':GENERATION_MODEL,'total_cost':0.125}
    if returned_id is not None:generation['id']=returned_id
    original=deepcopy(generation)
    row=generation_proof_row('requested-generation','DeepSeek',generation,[{'http_status':200}])
    assert row['passed'] is False and row['expected_response_id']=='requested-generation'
    assert row['id']==returned_id and row['total_cost']==0.125 and generation==original


@pytest.mark.parametrize('mutation',[
    'wrong_metadata_id','missing_metadata_id','wrong_expected_id','wrong_receipt_id',
    'missing_receipt_id','duplicate_response_id','swapped_same_provider_proofs',
    'missing_proof','extra_receipt','wrong_provider','wrong_model','incomplete_receipt',
])
def test_route_chain_rejects_mismatched_or_incomplete_evidence_even_with_passed_flags(mutation):
    routes,proof,ids=evidence()
    if mutation=='wrong_metadata_id':proof[0]['id']='unrelated'
    elif mutation=='missing_metadata_id':proof[0].pop('id')
    elif mutation=='wrong_expected_id':proof[0]['expected_response_id']='unrelated'
    elif mutation=='wrong_receipt_id':routes[0]=(routes[0][0],'completed','unrelated')
    elif mutation=='missing_receipt_id':routes[0]=(routes[0][0],'completed',None)
    elif mutation=='duplicate_response_id':ids[1]=ids[0]
    elif mutation=='swapped_same_provider_proofs':proof[0],proof[1]=proof[1],proof[0]
    elif mutation=='missing_proof':proof.pop()
    elif mutation=='extra_receipt':routes.append(routes[0])
    elif mutation=='wrong_provider':proof[0]['provider_name']='UnexpectedProvider'
    elif mutation=='wrong_model':proof[0]['model']='other-model'
    elif mutation=='incomplete_receipt':routes[0]=(routes[0][0],'running',routes[0][2])
    assert all(row['passed'] for row in proof)
    assert route_evidence_passed(routes,proof,ids) is False
