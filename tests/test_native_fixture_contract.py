"""Keep the no-owner native fixture aligned with the real receipt API."""
import json
from types import SimpleNamespace
import pytest
from test_native_payload_retry import NoOwnerState


def test_no_owner_state_finish_matches_real_store_contract(tmp_path):
    import inspect
    from codex_deepseek.state import Store
    assert inspect.signature(NoOwnerState.finish)==inspect.signature(Store.finish)
    state=NoOwnerState(tmp_path/'synthetic.sqlite')
    failed=state.start()
    diagnostic=json.dumps({'schema':1,'event_type':'upstream.http_error','error_code':400})
    state.finish(failed,'upstream_error',400,detail=diagnostic)
    assert state.rows[failed]=={'status':'upstream_error','upstream_status':400,
        'response_id':None,'usage':{},'detail':diagnostic}
    completed=state.start()
    event=SimpleNamespace(response_id='synthetic-response',usage={'input_tokens':1})
    state.finish(completed,'completed',200,event)
    assert state.rows[completed]=={'status':'completed','upstream_status':200,
        'response_id':'synthetic-response','usage':{'input_tokens':1},'detail':None}
    with pytest.raises(TypeError):state.finish(completed,'completed',unknown=True)

