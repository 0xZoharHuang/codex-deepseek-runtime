# Actual startup policy files: elevation-stable ownership and atomic replacement.
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def policy_run(tmp_path, monkeypatch):
    import codex_deepseek.cli as cli
    root=tmp_path/'data-root';home=root/'codex-home'
    monkeypatch.setattr(cli,'data_root',lambda:root)
    monkeypatch.setenv('CODEX_DEEPSEEK_HOME',str(home))
    monkeypatch.setattr(cli,'native',lambda:root/'native/bin/codex')
    # Only exercise the actual startup filesystem path, never network or a native process.
    class Service:
        def __init__(self,*args,**kwargs):pass
        async def __aenter__(self):return SimpleNamespace(port=32123,token='NONFUNCTIONAL_FIXTURE')
        async def __aexit__(self,*args):pass
    monkeypatch.setattr(cli,'ProviderService',Service)
    monkeypatch.setattr(cli,'WINDOWS',True)
    async def run():return await cli.run(['runtime','status'])
    return SimpleNamespace(run=run,home=home,policy=home/'runtime-policy/config.toml',cli=cli)


async def test_windows_identical_policy_replaces_old_file_identity_without_changing_other_link(policy_run,tmp_path,assert_private):
    p=policy_run;assert await p.run()==0
    original=p.policy.read_bytes();inode=p.policy.stat().st_ino
    retained=tmp_path/'old-policy-object';os.link(p.policy,retained)
    assert await p.run()==0
    assert p.policy.read_bytes()==retained.read_bytes()==original
    assert p.policy.stat().st_ino!=inode and retained.stat().st_ino==inode
    assert not list(p.policy.parent.glob('.config-*'))
    assert_private(p.policy.parent);assert_private(p.policy)


async def test_windows_policy_replacement_does_not_read_unreadable_old_file(policy_run,monkeypatch):
    p=policy_run;assert await p.run()==0
    original=p.policy.read_bytes();read=Path.read_text
    def deny_old(path,*args,**kwargs):
        if path==p.policy:raise PermissionError('old High-only file must be replaced atomically')
        return read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',deny_old)
    assert await p.run()==0 and p.policy.read_bytes()==original


async def test_failed_policy_replacement_preserves_old_file_and_removes_owned_temporary(policy_run,monkeypatch):
    p=policy_run;assert await p.run()==0
    original=p.policy.read_bytes();inode=p.policy.stat().st_ino
    def locked(source,destination):
        assert destination==p.policy and source.parent==p.policy.parent
        raise PermissionError('fixture holds destination without delete sharing')
    monkeypatch.setattr(p.cli,'replace_file',locked)
    with pytest.raises(PermissionError,match='without delete sharing'):await p.run()
    assert p.policy.read_bytes()==original and p.policy.stat().st_ino==inode
    assert not list(p.policy.parent.glob('.config-*'))
