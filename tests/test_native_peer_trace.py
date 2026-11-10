"""Current C codec/source binding; neither model comprehension nor task success."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_deepseek import gateway
from codex_deepseek.protocol import MODEL, Registry, prepare
from codex_deepseek.request_metadata import native_request_metadata
from verify_nested_agents import (_canonical_json_sha256, _strict_json_loads, _peer_semantics,
                                   delivered_peer_messages, load_native_peer_evidence)

OWNER = '00000000-0000-0000-0000-000000000001'
OTHER = '00000000-0000-0000-0000-000000000002'
TURN = '00000000-0000-0000-0000-000000000003'
RECEIPT = '00000000-0000-0000-0000-000000000004'
HEADER = 'Message Type: MESSAGE\nTask name: /root/auditor\nSender: /root\nPayload:\n'
TEXT = HEADER + 'producer uses integer cents SYNTHETIC_MARKER'


def fixture(*, peer=None, items=None, owner=OWNER):
    peer = peer if peer is not None else {'type':'agent_message','id':'amsg_synthetic_original',
        'author':'/root','recipient':'/root/auditor','content':[{'type':'input_text','text':TEXT}]}
    body = {'model':MODEL,'input':copy.deepcopy([peer] if items is None else items),
            'client_metadata':{'x-codex-turn-metadata':json.dumps({'thread_id':owner,'turn_id':TURN,'request_kind':'turn'})}}
    payload, registry = prepare(body)
    evidence = gateway.native_peer_trace_evidence(RECEIPT, body, payload, registry, native_request_metadata(body, {}))
    return peer, body, payload, registry, {RECEIPT:{'evidence':evidence,'prepared_request':payload}}


def delivered(peer, payload, records, *, native=None):
    return delivered_peer_messages([payload], '/root/auditor', '/root', 'MESSAGE',
        ['producer','integer','SYNTHETIC_MARKER'],
        native_messages=([{'thread_id':OWNER,'item':peer}] if native is None else native),
        owner=OWNER, native_peer_evidence=records)


def resign(evidence):
    evidence['record_sha256'] = _canonical_json_sha256({k:v for k,v in evidence.items() if k != 'record_sha256'})


def refresh(records):
    paired=records[RECEIPT];evidence=paired['evidence'];payload=paired['prepared_request']
    evidence['prepared_request_sha256']=_canonical_json_sha256(payload)
    evidence['prepared_input_sha256']=_canonical_json_sha256(payload['input'])
    for binding in evidence['bindings']:
        index=binding['prepared_input_index']
        if type(index) is int and 0 <= index < len(payload['input']):
            binding['prepared_item_sha256']=_canonical_json_sha256(payload['input'][index])
    resign(evidence)


def test_true_peer_source_is_bound_after_interrupted_prefix_insertion():
    peer,_,_,_,_=fixture()
    reasoning={'type':'reasoning','id':'rs_synthetic','summary':[{'type':'summary_text','text':'synthetic retained summary'}]}
    peer,body,payload,registry,records=fixture(peer=peer,items=[reasoning,peer])
    original=copy.deepcopy((body,payload))
    proof=delivered(peer,payload,records)
    assert len(proof)==1 and proof[0]['native_input_index']==1 and proof[0]['prepared_input_index']==1
    assert proof[0]['native_item_id']==peer['id'] and records[RECEIPT]['evidence']['status']=='qualified'
    assert (body,payload)==original
    # Source turn may differ: this is a legitimate later request carrying history.
    source=copy.deepcopy(peer);source['internal_chat_message_metadata_passthrough']={'turn_id':OTHER}
    assert delivered(peer,payload,records,native=[{'thread_id':OWNER,'item':source}])==proof


def test_no_sidecar_no_source_and_wrong_owner_are_never_deliveries():
    peer,_,payload,_,records=fixture()
    assert delivered(peer,payload,{})==[]
    assert delivered(peer,payload,records,native=[])==[]
    assert delivered(peer,payload,records,native=[{'thread_id':OTHER,'item':peer}])==[]
    peer,_,payload,_,records=fixture(owner=OTHER)
    assert delivered(peer,payload,records)==[]


def test_same_owner_historical_pretty_JSON_echo_is_not_a_source():
    peer,_,payload,_,_=fixture();echo=copy.deepcopy(payload['input'][0])
    assert _peer_semantics(echo)==_peer_semantics(peer,native_source=True)
    peer,_,payload,registry,records=fixture(peer=peer,items=[echo,{'type':'message','role':'user','content':'continue'}])
    assert registry.peer_history_bindings==[] and records[RECEIPT]['evidence']['status']=='no_native_peers'
    assert delivered(peer,payload,records)==[]


def test_C_source_still_requires_matching_native_item_ID():
    peer,_,payload,_,records=fixture()
    assert delivered(peer,payload,records)
    unrecorded=copy.deepcopy(peer);unrecorded['id']='amsg_synthetic_unrecorded'
    assert delivered(peer,payload,records,native=[{'thread_id':OWNER,'item':unrecorded}])==[]


@pytest.mark.parametrize('representation',['assistant_three_fields','B_user_quoted','raw_AgentMessage','legacy_serializer'])
def test_old_or_raw_prepared_carriers_are_not_current_transport_even_with_source(representation):
    peer,_,payload,_,records=fixture()
    three={k:peer[k] for k in ('author','recipient','content')}
    if representation=='assistant_three_fields':
        item={'type':'message','role':'assistant','phase':'commentary',
              'content':[{'type':'output_text','text':json.dumps(three)}]}
    elif representation=='B_user_quoted':
        item={'type':'message','role':'user','content':[{'type':'input_text','text':json.dumps(three)}]}
    elif representation=='raw_AgentMessage':item=copy.deepcopy(peer)
    else:
        # This serializer is not called by the production native model path.
        legacy={'author':peer['author'],'recipient':peer['recipient'],'other_recipients':[],
                'content':TEXT,'trigger_turn':False}
        item={'type':'message','role':'assistant','phase':'commentary',
              'content':[{'type':'output_text','text':json.dumps(legacy)}]}
    payload['input'][0]=item;refresh(records)
    assert _peer_semantics(item) is None and delivered(peer,payload,records)==[]


@pytest.mark.parametrize('change', ['source_id','receipt','request_hash','input_hash','item_hash','wire_index','native_index','owner'])
def test_sidecar_tampering_rejects_even_with_fresh_record_checksum(change):
    peer,_,payload,_,records=fixture();e=records[RECEIPT]['evidence'];b=e['bindings'][0]
    if change=='source_id':b['native_item_id']='amsg_synthetic_unrecorded'
    elif change=='receipt':e['receipt_id']=OTHER
    elif change=='request_hash':e['prepared_request_sha256']='0'*64
    elif change=='input_hash':e['prepared_input_sha256']='0'*64
    elif change=='item_hash':b['prepared_item_sha256']='0'*64
    elif change=='wire_index':b['prepared_input_index']=len(payload['input'])
    elif change=='native_index':b['native_input_index']=1000000
    elif change=='owner':e['native_request']['thread_id']=OTHER
    resign(e)
    assert delivered(peer,payload,records)==[]


def test_strict_JSON_blocks_conflicting_duplicate_keys_despite_source_and_hashes():
    peer,_,payload,_,records=fixture()
    encoded=payload['input'][0]['content'][0]['text']
    payload['input'][0]['content'][0]['text']='{"author":"/root/attacker",'+encoded[1:]
    refresh(records)
    assert delivered(peer,payload,records)==[]
    with pytest.raises(ValueError,match='duplicate'):_strict_json_loads('{"a":1,"a":2}')
    with pytest.raises(ValueError,match='nonfinite'):_strict_json_loads('{"a":1e999}')


@pytest.mark.parametrize('role',['system','developer','assistant'])
def test_higher_or_foreign_message_role_is_not_peer_evidence(role):
    peer,_,payload,_,records=fixture();payload['input'][0]['role']=role;refresh(records)
    assert delivered(peer,payload,records)==[]


def test_C_metadata_and_native_parts_are_decoded_without_quoting_or_added_policy():
    peer,body,payload,registry,records=fixture()
    item=payload['input'][0]
    metadata={'type':'agent_message','author':peer['author'],'recipient':peer['recipient']}
    assert item=={'type':'message','role':'user','content':[
        {'type':'input_text','text':json.dumps(metadata,ensure_ascii=False,separators=(',',':'))},
        *peer['content']]}
    assert _peer_semantics(item)==_peer_semantics(peer,native_source=True)
    assert item['content'][1:]==peer['content'] and delivered(peer,payload,records)
    assert all(x['text'].encode('utf8')==y['text'].encode('utf8')
               for x,y in zip(item['content'][1:],peer['content']))
    assert delivered(peer,payload,{})==[]


@pytest.mark.parametrize('extra', [{'phase':None},{'phase':'commentary'},{'id':'msg_not_current_codec'}])
def test_C_transport_does_not_accept_phase_or_other_outer_fields(extra):
    peer,_,payload,_,records=fixture();payload['input'][0].update(extra);refresh(records)
    assert delivered(peer,payload,records)==[]


@pytest.mark.parametrize('field', ['trigger_turn','other_recipients','content','unknown'])
def test_C_metadata_frame_rejects_extra_or_guessed_fields(field):
    peer,_,payload,_,records=fixture();frame=payload['input'][0]['content'][0]
    metadata=json.loads(frame['text']);metadata[field]='synthetic unsupported field'
    frame['text']=json.dumps(metadata);refresh(records)
    assert delivered(peer,payload,records)==[]


def test_content_parts_order_cannot_be_changed_by_a_sidecar():
    peer,_,_,_,_=fixture();peer['content'].append({'type':'input_text','text':'SYNTHETIC second part λ'})
    peer,_,payload,_,records=fixture(peer=peer);assert delivered(peer,payload,records)
    item=payload['input'][0];item['content'][1:]=list(reversed(item['content'][1:]));refresh(records)
    assert delivered(peer,payload,records)==[]


@pytest.mark.parametrize('change',['text','drop_part','append_part','merge_parts'])
def test_C_native_parts_cannot_be_changed_or_repartitioned(change):
    peer,_,_,_,_=fixture();peer['content'].append({'type':'input_text','text':'second λ\nline'})
    peer,_,payload,_,records=fixture(peer=peer);parts=payload['input'][0]['content']
    if change=='text':parts[1]['text']+=' changed'
    elif change=='drop_part':parts.pop()
    elif change=='append_part':parts.append({'type':'input_text','text':'new unprovided input'})
    else:parts[1:] = [{'type':'input_text','text':'\n'.join(x['text'] for x in parts[1:])}]
    refresh(records)
    assert delivered(peer,payload,records)==[]


@pytest.mark.parametrize('failure',['missing_id','duplicate_id','unknown_metadata','missing_hook'])
def test_unqualified_trace_never_changes_or_rejects_prepared_payload(failure):
    peer,body,payload,registry,_=fixture()
    if failure=='missing_id':body['input'][0].pop('id')
    elif failure=='duplicate_id':body['input'].append(copy.deepcopy(body['input'][0]))
    elif failure=='unknown_metadata':body.pop('client_metadata')
    elif failure=='missing_hook':registry=SimpleNamespace()
    before=copy.deepcopy((body,payload))
    evidence=gateway.native_peer_trace_evidence(RECEIPT,body,payload,registry,native_request_metadata(body,{}))
    assert evidence['status']=='unqualified' and evidence['bindings']==[] and (body,payload)==before
    assert delivered(peer,payload,{RECEIPT:{'evidence':evidence,'prepared_request':payload}})==[]


def test_sidecar_is_bounded_and_does_not_copy_peer_plaintext():
    peer,body,payload,registry,_=fixture()
    evidence=gateway.native_peer_trace_evidence(RECEIPT,body,payload,registry,native_request_metadata(body,{}))
    raw=json.dumps(evidence).encode();assert len(raw)<=65536 and b'SYNTHETIC_MARKER' not in raw and b'producer uses' not in raw
    body['input']=[{**peer,'id':'amsg_'+str(i)} for i in range(129)]
    evidence=gateway.native_peer_trace_evidence(RECEIPT,body,payload,registry,native_request_metadata(body,{}))
    assert evidence['status']=='unqualified' and evidence['reason']=='peer_evidence_limit_exceeded'
    assert len(json.dumps(evidence,ensure_ascii=False,indent=2).encode())<=65536


def test_reader_receipt_join_and_legacy_trace_rejection(tmp_path):
    peer,body,payload,registry,records=fixture()
    gateway.private_write(tmp_path/(RECEIPT+'-request.json'),json.dumps(payload))
    assert load_native_peer_evidence(tmp_path)=={}
    gateway.write_native_peer_trace_evidence(tmp_path,RECEIPT,body,payload,registry,native_request_metadata(body,{}))
    loaded=load_native_peer_evidence(tmp_path);assert delivered(peer,payload,loaded)
    path=tmp_path/(RECEIPT+'-native-peer-evidence.json')
    path.write_text('{"schema_version":1,"schema_version":1}',encoding='utf8')
    assert 'error' in load_native_peer_evidence(tmp_path)[RECEIPT]
    assert delivered(peer,payload,load_native_peer_evidence(tmp_path))==[]
    path.write_bytes(b'x'*65537)
    assert 'error' in load_native_peer_evidence(tmp_path)[RECEIPT]


def test_sidecar_write_failure_is_best_effort_and_never_exposes_exception_content(tmp_path,monkeypatch,caplog):
    peer,body,payload,registry,_=fixture();before=copy.deepcopy(payload)
    def fail(*args):raise OSError('SYNTHETIC_SECRET_MUST_NOT_LOG')
    monkeypatch.setattr(gateway,'private_write',fail)
    assert gateway.write_native_peer_trace_evidence(tmp_path,RECEIPT,body,payload,registry,native_request_metadata(body,{})) is None
    assert payload==before and 'Native peer trace incomplete' in caplog.text and 'SYNTHETIC_SECRET_MUST_NOT_LOG' not in caplog.text
    assert not (tmp_path/(RECEIPT+'-native-peer-evidence.json')).exists()


# These two transport tests use only an ephemeral loopback fixture; no model/API key.
from test_gateway import fake_upstream
import aiohttp
from aiohttp import web


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['sidecar_write_error','unqualified_missing_id'])
async def test_peer_evidence_failure_cannot_change_forwarding_or_HTTP_error(fake_upstream,tmp_path,monkeypatch,caplog,mode):
    peer,body,_,_,_=fixture()
    if mode=='unqualified_missing_id':body['input'][0].pop('id')
    body['stream']=True
    expected,_=prepare(body)
    seen=[]
    async def reject(request):
        received=await request.json();seen.append(received)
        assert received==expected
        return web.json_response({'error':{'message':'synthetic throttle'}},status=429,headers={'Retry-After':'7'})
    fake_upstream.append(reject)
    trace=tmp_path/'trace'
    if mode=='sidecar_write_error':
        original=gateway.private_write
        def write(path,data):
            if path.name.endswith('-native-peer-evidence.json'):raise OSError('SYNTHETIC_SECRET_MUST_NOT_LOG')
            return original(path,data)
        monkeypatch.setattr(gateway,'private_write',write)
    store=gateway.Store(tmp_path/'state.sqlite')
    try:
        async with gateway.Gateway('SECRET','LOCAL',store,{},trace_source=lambda:str(trace)) as service, aiohttp.ClientSession() as client:
            async with client.post(f'http://127.0.0.1:{service.port}/v1/responses',json=body,headers={'Authorization':'Bearer LOCAL'}) as response:
                assert response.status==429 and response.headers['Retry-After']=='7'
                assert (await response.json())['error']['message']=='synthetic throttle'
        assert len(seen)==1 and store.db.execute('SELECT status FROM receipts').fetchone()[0]=='upstream_error'
        prepared=json.loads(next(trace.glob('*-request.json')).read_bytes())
        assert prepared==expected
        records=load_native_peer_evidence(trace)
        assert delivered(peer,prepared,records)==[]
        if mode=='sidecar_write_error':
            assert not list(trace.glob('*-native-peer-evidence.json'))
            assert 'Native peer trace incomplete' in caplog.text and 'SYNTHETIC_SECRET_MUST_NOT_LOG' not in caplog.text
        else:
            assert len(records)==1 and next(iter(records.values()))['evidence']['status']=='unqualified'
    finally:store.close()


def test_sidecar_limit_matches_the_actual_indented_file_encoding():
    peer,_,_,_,_=fixture()
    peers=[{**peer,'id':'amsg_'+str(index),'author':'/root/'+'a'*96} for index in range(128)]
    _,body,payload,registry,_=fixture(items=peers)
    evidence=gateway.native_peer_trace_evidence(RECEIPT,body,payload,registry,native_request_metadata(body,{}))
    assert len(json.dumps(evidence,ensure_ascii=False,allow_nan=False,indent=2).encode())<=65536
