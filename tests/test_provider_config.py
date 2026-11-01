"""Explicit home policy: frozen default captures, strict snapshots and idle-only CAS."""
import copy
import dataclasses
import json
import os
from pathlib import Path
import time
from contextlib import contextmanager

import pytest
from codex_deepseek import provider_config as policy, protocol, service
from codex_deepseek.platforms import private_runtime_home, private_directory
from codex_deepseek.processes import matches, terminate
from codex_deepseek.state import Store

ROOT=Path(__file__).resolve().parents[1]
CAPTURES=json.loads((ROOT/'tests/fixtures/provider-default-captures.json').read_bytes())['cases']


def normalized(value):
    if dataclasses.is_dataclass(value):return normalized(dataclasses.asdict(value))
    if isinstance(value,dict):return {str(k):normalized(v) for k,v in sorted(value.items(),key=lambda r:str(r[0]))}
    if isinstance(value,set):return [normalized(v) for v in sorted(value,key=str)]
    if isinstance(value,(list,tuple)):return [normalized(v) for v in value]
    return value


@pytest.mark.parametrize('capture',CAPTURES,ids=lambda c:c['name'])
def test_absent_and_explicit_official_keep_frozen_payload_and_entire_registry(capture):
    for options in [{},{'ordinary_default_provider':'deepseek'}]:
        body=copy.deepcopy(capture['request']);before=copy.deepcopy(body)
        payload,registry=protocol.prepare(body,**options)
        assert body==before
        assert payload==capture['payload']
        assert normalized(vars(registry))==capture['registry']


@pytest.mark.parametrize('capture',CAPTURES,ids=lambda c:c['name'])
def test_morph_only_replaces_ordinary_branch_and_keeps_native_contract(capture):
    body=copy.deepcopy(capture['request']);before=copy.deepcopy(body)
    payload,registry=protocol.prepare(body,ordinary_default_provider='morph/fp8')
    expected=copy.deepcopy(capture['payload'])
    ordinary=expected['provider']['only']==['deepseek']
    if ordinary:expected['provider']['only']=['morph/fp8']
    if ordinary and registry.structured_output_mapping is not None:
        expected['text']['format']={'type':'text'}
        assert registry.structured_output_mapping['upstream_decoder_constraint'] is False
        assert registry.structured_output_mapping['route_format_strategy']=='nonofficial_route_text'
    assert body==before and payload==expected
    assert payload['provider']['allow_fallbacks'] is False
    assert payload['provider']['require_parameters'] is True
    if ordinary:assert 'operator_default/morph/fp8' in registry.route_reason
    else:assert registry.route_reason==capture['registry']['route_reason']
    baseline=copy.deepcopy(capture['registry']);actual=normalized(vars(registry))
    for key in ['route_reason','structured_output_mapping']:baseline.pop(key,None);actual.pop(key,None)
    assert actual==baseline


@pytest.mark.parametrize('value',['cohere','morph','',None,{},True])
def test_unknown_operator_provider_cannot_be_inferred_or_retried(value):
    with pytest.raises(ValueError):protocol.prepare({'model':protocol.MODEL},ordinary_default_provider=value)


@pytest.mark.parametrize('raw',[b'{}',b'[]',b'{"schema":true,"ordinary_default_provider":"deepseek"}',
    b'{"schema":1,"ordinary_default_provider":"cohere"}',
    b'{"schema":1,"ordinary_default_provider":"deepseek","unknown":1}',
    b'{"schema":1,"schema":1,"ordinary_default_provider":"deepseek"}',
    b'{"schema":1,"ordinary_default_provider":NaN}',b'x'*4097])
def test_malformed_private_policy_fails_closed_without_editing_it(tmp_path,raw):
    state=tmp_path/'home/provider-state';state.mkdir(parents=True);p=state/policy.FILENAME;p.write_bytes(raw)
    with pytest.raises(ValueError):policy.load(state.parent)
    assert p.read_bytes()==raw


@pytest.mark.parametrize('kind',['hardlink','symlink'])
def test_linked_policy_is_never_followed(tmp_path,kind):
    state=tmp_path/'home/provider-state';state.mkdir(parents=True);target=tmp_path/'external';target.write_text('{"schema":1,"ordinary_default_provider":"morph/fp8"}')
    p=state/policy.FILENAME
    if kind=='hardlink':os.link(target,p)
    else:p.symlink_to(target)
    with pytest.raises(ValueError):policy.load(state.parent)
    assert target.read_text()=='{"schema":1,"ordinary_default_provider":"morph/fp8"}'


def test_in_place_change_during_bounded_read_is_rejected(tmp_path,monkeypatch):
    home=tmp_path/'home';state=home/'provider-state';state.mkdir(parents=True);p=state/policy.FILENAME;p.write_text('{"schema":1,"ordinary_default_provider":"deepseek"}')
    original=policy._open_private_file
    class ChangedRead:
        def __init__(self,stream):self.stream=stream
        def fileno(self):return self.stream.fileno()
        def read(self,n):
            raw=self.stream.read(n)
            with p.open('ab') as out:out.write(b' ')
            return raw
    @contextmanager
    def opened(*a,**k):
        with original(*a,**k) as stream:yield ChangedRead(stream)
    monkeypatch.setattr(policy,'_open_private_file',opened)
    with pytest.raises(ValueError,match='changed'):policy.load(home)


def test_idle_CAS_is_exact_and_other_home_remains_official(tmp_path,monkeypatch):
    home=tmp_path/'one';other=tmp_path/'two'
    assert policy.load(home)['policy']==policy.default()
    initial=policy.set_provider(home,'morph/fp8',expected_revision='absent')
    assert initial['configured_provider']=='morph/fp8'
    p=home/'provider-state'/policy.FILENAME;original=p.read_bytes()
    with pytest.raises(ValueError,match='CAS'):policy.set_provider(home,'deepseek',expected_revision='absent')
    assert p.read_bytes()==original
    changed=policy.set_provider(home,'deepseek',expected_revision=initial['configured_revision'])
    assert changed['configured_provider']=='deepseek'
    assert policy.load(other)['policy']==policy.default() and not other.exists()


def test_active_native_owner_rejects_writer_without_killing_or_changing_config(tmp_path,monkeypatch):
    home=tmp_path/'home';policy.set_provider(home,'deepseek');p=home/'provider-state'/policy.FILENAME;before=p.read_bytes();store=Store(home/'provider-state/adapter.sqlite');Store.retain_service(store.path,store.owner)
    monkeypatch.setattr(service,'terminate',lambda *a,**k:pytest.fail('writer must never terminate an owner'))
    try:
        with pytest.raises(RuntimeError,match='active native'):policy.set_provider(home,'morph/fp8')
        assert p.read_bytes()==before and service.native_active(home)
    finally:store.close()


def test_unknown_held_service_lease_rejects_even_without_native_receipt(tmp_path):
    from codex_deepseek.platforms import file_lock
    home=tmp_path/'home';policy.set_provider(home,'deepseek');state=home/'provider-state';before=(state/policy.FILENAME).read_bytes()
    with (state/'gateway.lease').open('a') as stream,file_lock(stream):
        with pytest.raises(RuntimeError,match='still admitted'):policy.set_provider(home,'morph/fp8')
    assert (state/policy.FILENAME).read_bytes()==before


def test_ensure_reads_policy_and_computes_identity_inside_startup_lock(tmp_path,monkeypatch):
    from types import SimpleNamespace
    home=tmp_path/'home';inside=[];original=service.file_lock
    @contextmanager
    def lock(stream,**kwargs):
        with original(stream,**kwargs):
            inside.append(True)
            try:yield
            finally:inside.pop()
    original_load=policy.load
    def load(value):assert inside;return original_load(value)
    def code(*args):assert inside;assert args[3]==policy.default();return 'a'*64
    monkeypatch.setattr(service,'file_lock',lock);monkeypatch.setattr(policy,'load',load);monkeypatch.setattr(service,'code_id',code)
    doc=policy.default();value={'pid':431,'birth':'fixture','code_id':'a'*64,'provider_config':doc,'provider_config_sha256':policy.digest(doc),'ordinary_default_provider':'deepseek'}
    monkeypatch.setattr(service,'read_descriptor',lambda h:value);monkeypatch.setattr(service,'matches',lambda *a:True);monkeypatch.setattr(service,'healthy',lambda v:True)
    assert service.ensure(home,tmp_path/'unused-catalog')==value


def test_code_identity_uses_admitted_snapshot_not_a_later_file_read(tmp_path):
    home=tmp_path/'home';policy.set_provider(home,'morph/fp8');catalog=tmp_path/'catalog';catalog.write_text('{}');snapshot=policy.load(home)['policy'];first=service.code_id(catalog,home=home,provider_snapshot=snapshot)
    policy.set_provider(home,'deepseek')
    assert service.code_id(catalog,home=home,provider_snapshot=snapshot)==first
    assert service.code_id(catalog,home=home)!=first


def test_real_shared_home_worker_restart_preserves_snapshot_and_other_home_default(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','NONFUNCTIONAL-POLICY-TEST');monkeypatch.setenv('CODEX_DEEPSEEK_SERVICE_IDLE_SECONDS','1')
    home=tmp_path/'one';other=tmp_path/'two';policy.set_provider(home,'morph/fp8');catalog=ROOT/'catalog.json';store=Store(home/'provider-state/adapter.sqlite');value=None;other_value=None
    try:
        value=service.ensure(home,catalog,owner=store.owner)
        assert value['ordinary_default_provider']=='morph/fp8' and value['provider_config_sha256']==policy.digest({'schema':1,'ordinary_default_provider':'morph/fp8'})
        again=service.ensure(home,catalog,owner=store.owner);assert (again['pid'],again['port'],again['code_id'])==(value['pid'],value['port'],value['code_id'])
        with pytest.raises(RuntimeError):policy.set_provider(home,'deepseek')
        # Simulate an uncoordinated outside-file change. Restart must keep the
        # admitted snapshot, never reload this different preference per worker.
        (home/'provider-state'/policy.FILENAME).write_text(json.dumps(policy.default()))
        original=service.read_descriptor(home);terminate(original['worker_pid'],original['worker_birth'])
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            current=service.read_descriptor(home)
            if current.get('worker_pid')!=original['worker_pid'] and service.healthy(current):break
            time.sleep(.1)
        assert current['worker_pid']!=original['worker_pid'] and current['ordinary_default_provider']=='morph/fp8' and current['provider_config_sha256']==value['provider_config_sha256']
        with pytest.raises(RuntimeError,match='changed'):
            service.ensure(home,catalog,owner=store.owner)
        assert matches(value['pid'],value['birth'])
        other_value=service.ensure(other,catalog);assert other_value['ordinary_default_provider']=='deepseek' and other_value['provider_config_sha256']==policy.digest(policy.default())
    finally:
        store.close()
        if value:terminate(value['pid'],value['birth'],timeout=8)
        if other_value:terminate(other_value['pid'],other_value['birth'],timeout=8)


@pytest.mark.parametrize('bad',['hash','provider','unknown'])
def test_worker_rejects_tampered_snapshot_metadata(bad):
    doc=policy.default();value={'provider_config':doc,'provider_config_sha256':policy.digest(doc),'ordinary_default_provider':'deepseek'}
    if bad=='hash':value['provider_config_sha256']='0'*64
    elif bad=='provider':value['ordinary_default_provider']='morph/fp8'
    else:value['provider_config']={**doc,'hidden':True}
    with pytest.raises((ValueError,RuntimeError)):service._admitted_provider(value)


def test_CAS_observes_change_after_temporary_staging_without_overwriting_original(tmp_path,monkeypatch):
    home=tmp_path/'home';policy.set_provider(home,'deepseek');p=home/'provider-state'/policy.FILENAME;original=policy.load;calls=[0];foreign=b'{"schema":1,"ordinary_default_provider":"deepseek"}  '
    def loaded(value):
        calls[0]+=1
        # observation, locked initial read, then final pre-publication read
        if calls[0]==3:p.write_bytes(foreign)
        return original(value)
    monkeypatch.setattr(policy,'load',loaded)
    with pytest.raises(ValueError,match='CAS'):policy.set_provider(home,'morph/fp8')
    assert p.read_bytes()==foreign
    assert not list(p.parent.glob('.provider-*'))


async def test_supported_provider_CLI_is_offline_before_native_IPC_or_credentials(tmp_path,monkeypatch,capsys):
    from codex_deepseek import cli
    monkeypatch.setattr(cli,'data_root',lambda:tmp_path/'install-root');monkeypatch.delenv('CODEX_DEEPSEEK_HOME',raising=False)
    def forbidden(*a,**k):pytest.fail('provider management must not launch native, read credentials or start home IPC')
    monkeypatch.setattr(cli,'native',forbidden);monkeypatch.setattr(cli,'credential',forbidden);monkeypatch.setattr(cli,'check_home_ipc',forbidden)
    assert await cli.run(['runtime','provider','set','morph/fp8','--if-match','absent'])==0
    changed=json.loads(capsys.readouterr().out);assert changed['configured_provider']=='morph/fp8' and not changed['service_running']
    assert await cli.run(['runtime','provider','status'])==0
    status=json.loads(capsys.readouterr().out);assert status['configured_revision']==changed['configured_revision'] and status['admitted_provider'] is None


def test_status_never_invents_official_identity_for_legacy_live_metadata(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'read_descriptor',lambda h:{'pid':1,'birth':'legacy'});monkeypatch.setattr('codex_deepseek.processes.matches',lambda *a:True)
    status=policy.status(tmp_path/'home');assert status['service_running'] and status['admitted_provider'] is None and status['admitted_config_sha256'] is None


@pytest.mark.parametrize('kind',['symlink','hardlink'])
def test_writer_never_touches_external_service_lease_alias(tmp_path,kind):
    home=tmp_path/'home';policy.set_provider(home,'deepseek');state=home/'provider-state';lease=state/'gateway.lease';lease.unlink();outside=tmp_path/'unrelated-lease';outside.write_bytes(b'never modify this')
    if kind=='symlink':lease.symlink_to(outside)
    else:os.link(outside,lease)
    before=(state/policy.FILENAME).read_bytes()
    with pytest.raises((ValueError,RuntimeError)):policy.set_provider(home,'morph/fp8')
    assert outside.read_bytes()==b'never modify this' and (state/policy.FILENAME).read_bytes()==before
