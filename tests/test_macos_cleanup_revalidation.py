"""Exact reviewed metadata cleanup receipts cannot replace arbitrary native probes."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
import assemble_release_acceptance as acceptance
from test_oauth_revalidation import oauth_run
from test_offline_reference import save

@pytest.fixture
def cleanup_receipt(tmp_path,monkeypatch):
    root=tmp_path/'run';root.mkdir();home=root/'primary/home';home.mkdir(parents=True)
    driver=tmp_path/acceptance.MACOS_KEYRING_DRIVER
    identity={'device':1,'inode':2,'uid':3,'mode':'0700','marker_sha256':'marker'}
    account='secrets|'+acceptance.hashlib.sha256(str(home).encode()).hexdigest()[:16]
    monkeypatch.setattr(acceptance,'sha',lambda p:'security-sha' if str(p)=='/usr/bin/security' else 'artifact-sha')
    proof={'schema_version':1,'driver_kind':'macos-security-metadata-only','driver_path':str(driver),
        'driver_sha256':acceptance.MACOS_KEYRING_DRIVER_SHA256,'security_path':'/usr/bin/security','security_sha256':'security-sha',
        'runtime_root':str(root),'producer_gate':'oauth-secrets','artifact_path':str(root/'artifact.json'),'artifact_sha256':'artifact-sha',
        'home':str(home),'home_identity':identity,'native_active_before':False,'native_active_after':False,'service':'codex',
        'password_requested':False,'present_after':False,'passed':True,'account':account,
        'source_archive_sha256':'3f9d7e83c9f152a438633813daefacee944d29165101c305e8ac08dc00a0e361',
        'upstream_commit':acceptance.BASELINE_COMMIT,'native_source_member':'codex-rs/secrets/src/lib.rs',
        'native_source_sha256':'7e243492527e390d2e7210031ed2afd4a6d97c9866a5b7f1d1f6cc51f9ecd837',
        'started':1.,'finished':2.,'present_before':True,'deleted':True,'delete_exit':0,
        'metadata_before':{'present':True,'exit':0,'account':account,'service':'codex','keychain':str(Path.home()/'Library/Keychains/login.keychain-db'),'class_genp':True},
        'metadata_after':{'present':False,'exit':44}}
    return proof,{},root,home,driver,identity

@pytest.mark.parametrize('change',['none','absent','password','foreign_home','foreign_identity','foreign_artifact','wrong_source','driver','security',
    'account','other_selector','foreign_chain','password_data','wrong_after','after_present','no_delete','delete_failed','boolean_exit',
    'absent_claims_delete','active','zero_time','reverse_time','infinite_time','boolean_time','failed','error'])
def test_metadata_receipt_exact_identity_and_deletion_logic(cleanup_receipt,change):
    proof,*args=cleanup_receipt
    if change in {'absent','absent_claims_delete'}:
        proof.update(present_before=False,deleted=False,delete_exit=None,metadata_before={'present':False,'exit':44})
        if change=='absent_claims_delete':proof['deleted']=True
    elif change=='password':proof['password_requested']=True
    elif change=='foreign_home':proof['home']='other-home'
    elif change=='foreign_identity':proof['home_identity']={**proof['home_identity'],'inode':99}
    elif change=='foreign_artifact':proof['artifact_sha256']='other'
    elif change=='wrong_source':proof['native_source_sha256']='other'
    elif change=='driver':proof['driver_sha256']='other'
    elif change=='security':proof['security_sha256']='other'
    elif change=='account':proof['account']='other'
    elif change=='other_selector':proof['metadata_before']['account']='other'
    elif change=='foreign_chain':proof['metadata_before']['keychain']=str(Path.home()/'unrelated/keychain')
    elif change=='password_data':proof['metadata_before']['data']='secret'
    elif change=='wrong_after':proof['metadata_after']['exit']=0
    elif change=='after_present':proof['metadata_after']['present']=True
    elif change=='no_delete':proof['deleted']=False
    elif change=='delete_failed':proof['delete_exit']=1
    elif change=='boolean_exit':proof['delete_exit']=False
    elif change=='active':proof['native_active_after']=True
    elif change=='zero_time':proof['started']=0
    elif change=='reverse_time':proof['finished']=0.5
    elif change=='infinite_time':proof['finished']=float('inf')
    elif change=='boolean_time':proof['started']=True
    elif change=='failed':proof['passed']=False
    elif change=='error':proof['error_type']='RuntimeError'
    if change in {'none','absent'}:
        result=acceptance.validate_macos_cleanup_receipt(proof,*args)
        assert result['deleted'] is (change=='none')
    else:
        with pytest.raises(SystemExit):acceptance.validate_macos_cleanup_receipt(proof,*args)

@pytest.mark.parametrize('target',['x86_64-pc-windows-msvc','x86_64-unknown-linux-gnu','aarch64-apple-darwin'])
def test_probe_change_is_never_a_general_native_probe_allowance(tmp_path,target):
    artifact={'target':target,'installation':{'source_id':'other'}}
    with pytest.raises(SystemExit,match='fixed macOS product'):
        acceptance.validate_macos_keyring_revalidation(artifact,artifact,tmp_path)

def test_all_four_passing_gates_required_before_probe_revalidation(oauth_run,monkeypatch):
    source,root,old,before,new,after=oauth_run
    new['native_probes']['keyring']['sha256']='changed'
    called=[]
    monkeypatch.setattr(acceptance,'validate_macos_keyring_revalidation',lambda *args:called.append(args))
    # The independently frozen probe bytes and exact four-gate result remain mandatory.
    save(root/'artifact.json',new);save(root/'verdict.json',after);save(root/'results.json',after['gates'])
    with pytest.raises(SystemExit):acceptance.validate_standard_gates(old,before,root,revalidation_scope='oauth')
    assert called==[]

def test_exact_skip_map_keeps_windows_scope_and_rejects_unlisted_skip():
    # Complete platform totals are checked once by test_offline_skip_catalogs.
    # This control protects the Windows-only scope of the cleanup exceptions.
    windows=acceptance.expected_offline_skips('x86_64-pc-windows-msvc')
    assert not any(name.startswith(('test_windows_sandbox_admission::','test_windows_asyncio::')) for name in windows)
    assert 'test_windows_sandbox_admission::invented' not in acceptance.expected_offline_skips('aarch64-apple-darwin')
    added={'test_runtime_home_owner::test_windows_native_runtime_home_rejects_unproven_BA_owner[foreign-ace]', 'test_runtime_home_owner::test_windows_exact_private_BA_home_and_cache_migrate_without_replacing_identity', 'test_runtime_home_owner::test_windows_system_owned_profile_is_rejected_without_owner_or_acl_changes', 'test_runtime_home_owner::test_windows_native_runtime_home_rejects_unproven_BA_owner[unprotected]'}
    for target in ['x86_64-unknown-linux-gnu','aarch64-apple-darwin']:
        catalog=acceptance.expected_offline_skips(target)
        assert added<=set(catalog)
        assert all(catalog[name]=='Windows protected legacy-BA native profile DACL contract' for name in added)
    assert not(added&set(windows))
