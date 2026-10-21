"""Direct OAuth acceptance must not silently exercise Windows' default Secrets store."""
import tomllib
import pytest
from verify_independent_audit import oauth_fixture_config,effective_secret_storage


@pytest.mark.parametrize('store',['file','keyring','auto'])
@pytest.mark.parametrize('enabled',[False,True])
def test_oauth_fixture_explicitly_pins_both_secret_storage_states(store,enabled):
    value=tomllib.loads(oauth_fixture_config('http://127.0.0.1:1234',store,enabled))
    assert value['mcp_oauth_credentials_store']==store
    assert value['features']['secret_auth_storage'] is enabled
    assert value['mcp_servers']['oauth_audit']['url']=='http://127.0.0.1:1234/mcp'


@pytest.mark.parametrize('enabled',[False,True])
def test_native_effective_flag_is_recorded_independently_of_its_platform_default(enabled):
    result=effective_secret_storage({'data':[{'name':'secret_auth_storage','enabled':enabled,'defaultEnabled':not enabled,'stage':'stable'}],'nextCursor':None})
    assert result=={'enabled':enabled,'default_enabled':not enabled,'stage':'stable'}


@pytest.mark.parametrize('response',[
    {'data':[]},
    {'data':[{'name':'secret_auth_storage'}]},
    {'data':[{'name':'secret_auth_storage','enabled':0}]},
    {'data':[{'name':'secret_auth_storage','enabled':'false'}]},
    {'data':[{'name':'secret_auth_storage','enabled':False}]*2},
    {'data':[{'name':'secret_auth_storage','enabled':False}],'nextCursor':'1'},
])
def test_missing_ambiguous_or_incomplete_feature_readback_fails(response):
    with pytest.raises(AssertionError):effective_secret_storage(response)
