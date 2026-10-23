import pytest
from verify_cds_skill import final_checks

def report(model=True):
    return {'passed':True,'model_requested':model,'model_receipts':2 if model else 0,
            'receipt_status_counts':{'completed':2} if model else {},
            'native_process_stopped':True,'provider_service_stopped':True}

def check(value, **overrides):
    args=dict(source_unchanged=True,skill_unchanged=True,artifact_unchanged=True,cleanup_errors=[])
    args.update(overrides)
    return final_checks(value,**args)

@pytest.mark.parametrize('model',[False,True])
def test_accepts_complete_owned_skill_run(model):
    assert check(report(model))

@pytest.mark.parametrize('field',['passed','native_process_stopped','provider_service_stopped'])
def test_successful_turn_does_not_hide_lifecycle_failure(field):
    value=report();value[field]=False
    assert not check(value)

@pytest.mark.parametrize('field',['source_unchanged','skill_unchanged','artifact_unchanged'])
def test_input_drift_rejects_previous_success(field):
    assert not check(report(),**{field:False})

def test_cleanup_exception_rejects_success():
    assert not check(report(),cleanup_errors=[{'stage':'native_close','error_type':'RuntimeError'}])

@pytest.mark.parametrize('counts,count',[({},0),({'completed':1,'failed':1},2),({'started':2},2),({'completed':1},2)])
def test_model_status_or_count_mismatch_rejected(counts,count):
    value=report();value.update(receipt_status_counts=counts,model_receipts=count)
    assert not check(value)

def test_discovery_only_must_not_consume_model():
    value=report(False);value.update(receipt_status_counts={'completed':1},model_receipts=1)
    assert not check(value)
