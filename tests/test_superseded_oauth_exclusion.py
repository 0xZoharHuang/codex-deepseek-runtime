"""A missing old failed-gate marker permits only frozen, uniquely bound exclusion."""
from pathlib import Path
import json
import os
import uuid
import pytest
import assemble_release_acceptance as a

@pytest.fixture
def failed_old(tmp_path,monkeypatch):
    root=tmp_path/'old';root.mkdir()
    parent=root/'evidence/independent-audit';parent.mkdir(parents=True)
    variants=[('oauth-file','file',False),('oauth-auto','auto',False),('oauth-keyring','keyring',False),('oauth-secrets','keyring',True)]
    cases={};nonces={};save=lambda p,v:p.write_text(json.dumps(v),encoding='utf-8')
    for index,(gate,store,secret) in enumerate(variants):
        case=parent/str(uuid.UUID(int=index+1));case.mkdir();cases[gate]=case;nonces[gate]='nonce_'+str(index)
        (case/'fixture').mkdir();home=case/'primary/home';home.mkdir(parents=True)
        (home/'config.toml').write_text('mcp_oauth_credentials_store="'+store+'"\n[features]\nsecret_auth_storage='+str(secret).lower()+'\n')
        save(case/'fixture/ready.json',{'nonce':nonces[gate]})
        if gate!='oauth-secrets':(root/(gate+'.log')).write_text('EVIDENCE '+str(case)+'\n')
    answer=nonces['oauth-secrets']+' RESOURCE_'+nonces['oauth-secrets']
    rows=[{'name':name,'passed':True,'detail':{'answer':answer} if index==2 else {}} for index,name in enumerate(
        ['provider','pkce','native_http_mcp_authenticated_tool_and_resource','cold','isolation'])]
    failed=cases['oauth-secrets'];save(failed/'results.json',rows);save(failed/'model-action-attempts.json',{'preserved':True})
    (root/'oauth-secrets.log').write_text('\n'.join(json.dumps(row) for row in rows)+'\nTraceback: cleanup timed out after 45 seconds\n')
    save(root/'artifact.json',{'target':'aarch64-apple-darwin','evidence_roots':[str(root)],'installation':{'source_id':'2d43ed50050d0a687f55'}})
    save(root/'verdict.json',{'passed':False,'gates':[{'gate':name,'exit':int(name=='oauth-secrets')} for name in a.GATES]})
    monkeypatch.setattr(a,'MACOS_OLD_OAUTH_CLEANUP_FILES',{name:a.sha(root/name) for name in a.MACOS_OLD_OAUTH_CLEANUP_FILES})
    monkeypatch.setattr(a,'MACOS_OLD_OAUTH_CASE_FILES',{name:a.sha(failed/name) for name in a.MACOS_OLD_OAUTH_CASE_FILES})
    monkeypatch.setattr(a.os,'getuid',lambda:root.stat().st_uid,raising=False)
    return root,cases,rows,save

@pytest.mark.parametrize('change',['none','artifact_bytes','failed_log','case_results','case_attempts','case_config','other_selector',
    'ambiguous_nonce','extra_case','other_marker','fresh_missing_marker'])
def test_only_bound_old_failure_is_excluded_and_never_replaces_fresh_marker(failed_old,change):
    root,cases,rows,save=failed_old
    if change=='artifact_bytes':(root/'artifact.json').write_text('{}')
    elif change=='failed_log':(root/'oauth-secrets.log').write_text('truncated')
    elif change=='case_results':save(cases['oauth-secrets']/'results.json',rows[:-1])
    elif change=='case_attempts':save(cases['oauth-secrets']/'model-action-attempts.json',{})
    elif change=='case_config':(cases['oauth-secrets']/'primary/home/config.toml').write_text('mcp_oauth_credentials_store="file"\n')
    elif change=='other_selector':
        (cases['oauth-file']/'primary/home/config.toml').write_bytes((cases['oauth-keyring']/'primary/home/config.toml').read_bytes())
    elif change=='ambiguous_nonce':
        (cases['oauth-file']/'fixture/ready.json').write_bytes((cases['oauth-secrets']/'fixture/ready.json').read_bytes())
    elif change=='extra_case':(cases['oauth-file'].parent/str(uuid.uuid4())).mkdir()
    elif change=='other_marker':(root/'oauth-file.log').write_text('EVIDENCE '+str(cases['oauth-auto'])+'\n')
    if change=='none':
        assert a.superseded_oauth_evidence_roots(root)==set(cases.values())
        with pytest.raises(SystemExit):a.oauth_evidence_roots(root)
    else:
        with pytest.raises(SystemExit):
            (a.oauth_evidence_roots if change=='fresh_missing_marker' else a.superseded_oauth_evidence_roots)(root)
