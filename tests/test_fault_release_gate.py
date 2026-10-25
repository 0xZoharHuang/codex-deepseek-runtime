from copy import deepcopy
import pytest
from assemble_release_acceptance import NATIVE_FAULT_KINDS,validate_fault_rows


def complete_rows():
    return [{'kind':kind,'variant':variant,'passed':True}
            for kind in NATIVE_FAULT_KINDS for variant in ['official','candidate']]


def test_all_fourteen_exact_actual_cases_are_required():
    rows=complete_rows()
    assert validate_fault_rows(rows)==rows


@pytest.mark.parametrize('bad',['missing','duplicate','foreign-kind','foreign-variant','failed','truthy'])
def test_missing_duplicate_or_failed_fault_cases_block_release(bad):
    rows=complete_rows()
    if bad=='missing':rows.pop()
    elif bad=='duplicate':rows[-1]=deepcopy(rows[0])
    elif bad=='foreign-kind':rows[0]['kind']='other'
    elif bad=='foreign-variant':rows[0]['variant']='reference-like'
    elif bad=='failed':rows[0]['passed']=False
    else:rows[0]['passed']=1
    with pytest.raises(SystemExit,match='fourteen unique'):
        validate_fault_rows(rows)



def fault_proof_fixture(tmp_path,monkeypatch):
    """Synthetic complete receipts exercise the consumer without starting a runtime."""
    import json,sqlite3
    from contextlib import closing
    import assemble_release_acceptance as acceptance
    from verify_fault_boundaries import VERIFIERS
    assert 'ci/qualify_native.py' in VERIFIERS
    source=tmp_path/'source';source.mkdir();monkeypatch.setattr(acceptance,'SOURCE',source)
    for name in VERIFIERS:
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic '+name)
    root=tmp_path/'proof';root.mkdir()
    def save(path,value):path.write_text(json.dumps(value),encoding='utf-8')
    entry=tmp_path/'release/venv/Scripts/codex-deepseek.exe';entry.parent.mkdir(parents=True);entry.write_bytes(b'synthetic entry')
    installation={'console_script_sha256':acceptance.sha(entry)}
    save(entry.parent.parent.parent/'installation.json',installation)
    native={'files':{'bin/codex.exe':'candidate'}};official='official'
    direct={}
    for kind in ['auth401','auth403']:
        path=root/(kind+'-direct.json')
        save(path,{'passed':True,'binary_sha256':official,'request_count':1})
        direct[kind]={'path':str(path),'sha256':acceptance.sha(path),'requests':1}
    artifact={'entry':str(entry),'entry_sha256':installation['console_script_sha256'],
              'installation_sha256':acceptance.sha(entry.parent.parent.parent/'installation.json'),
              'candidate_sha256':'candidate','official_sha256':official,'model_calls':0,'real_credentials':0,
              'verifier_sha256':{name:acceptance.sha(source/name) for name in VERIFIERS},
              'auth_native_default_basis':{'raw':direct}}
    rows=[];comparisons=[]
    for kind in sorted(NATIVE_FAULT_KINDS):
        effects=[{'marker':'synthetic'}] if kind in {'effect-after','effect-worker'} else []
        receipts=[{'id':'synthetic','status':'completed','upstream_status':200,'response_id':'synthetic','detail':None}]
        semantic={'requests':1,'effects':len(effects),'receipts':[['completed',200]],'auth_attempts':None,'cold_mcp_count':0}
        comparisons.append({'kind':kind,'official':semantic,'candidate':semantic,'passed':True})
        for variant in ['official','candidate']:
            case=root/(kind+'-'+variant);(case/'home/provider-state').mkdir(parents=True)
            proof={'kind':kind,'passed':True,'binary_sha256':official if variant=='official' else 'candidate',
                   'cleanup_errors':[],'remaining_before_forced_cleanup':[],'unknown_live':[],'observation_errors':[],
                   'effects':effects,'requests':1,'receipts':receipts}
            if effects:proof['cold_evidence']={'mcp_events':[],'ledger':effects,'call':[{'call_id':'synthetic'}]}
            save(case/'verdict.json',proof);save(case/'upstream-requests.json',[{}])
            if effects:(case/'effects.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in effects),encoding='utf-8')
            with closing(sqlite3.connect(case/'home/provider-state/adapter.sqlite')) as db, db:
                db.execute('CREATE TABLE receipts(id,status,upstream_status,response_id,detail,started)')
                db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)',(*receipts[0].values(),0))
            rows.append({'kind':kind,'variant':variant,'passed':True,'evidence':str(case/'verdict.json')})
    save(root/'artifact.json',artifact);save(root/'results.json',rows);save(root/'comparisons.json',comparisons)
    save(root/'complete.json',{'passed':True,'all_scenarios':True,'selected_scenarios':sorted(NATIVE_FAULT_KINDS),
        'artifact':acceptance.sha(root/'artifact.json'),'model_calls':0,'real_credentials':0,'comparisons':comparisons})
    checks=['entry','installation','candidate','reference','native_manifest','valid_release','direct_reference_verdicts']
    save(root/'final-artifact-check.json',dict.fromkeys(checks,True))
    return acceptance,root,entry,installation,native,official


@pytest.mark.parametrize('field',['unknown_live','observation_errors'])
@pytest.mark.parametrize('change',['clean','missing','nonempty','false'])
def test_native_fault_consumer_requires_explicit_clean_observation_fields(tmp_path,monkeypatch,field,change):
    import json
    acceptance,root,entry,installation,native,official=fault_proof_fixture(tmp_path,monkeypatch)
    path=root/'auth401-official/verdict.json';proof=json.loads(path.read_text())
    if change=='missing':proof.pop(field)
    elif change=='nonempty':proof[field]=[{'pid':123,'reason':'synthetic unresolved observation'}]
    elif change=='false':proof[field]=False
    path.write_text(json.dumps(proof),encoding='utf-8')
    # passed=True and cleanup_errors=[] cannot conceal missing or unresolved fields.
    if change=='clean':assert acceptance.validate_native_faults(root,entry,installation,native,official)['actual_cases']==14
    else:
        with pytest.raises(SystemExit,match='Native fault case failed'):
            acceptance.validate_native_faults(root,entry,installation,native,official)


@pytest.mark.parametrize('change',['current','missing_helper','changed_helper','extra_helper'])
def test_native_fault_consumer_keeps_exact_current_helper_provenance(tmp_path,monkeypatch,change):
    import json
    acceptance,root,entry,installation,native,official=fault_proof_fixture(tmp_path,monkeypatch)
    path=root/'artifact.json';artifact=json.loads(path.read_text())
    if change=='missing_helper':artifact['verifier_sha256'].pop('ci/qualify_native.py')
    elif change=='changed_helper':artifact['verifier_sha256']['ci/qualify_native.py']='old helper bytes'
    elif change=='extra_helper':artifact['verifier_sha256']['unreviewed.py']='extra'
    path.write_text(json.dumps(artifact),encoding='utf-8')
    complete_path=root/'complete.json';complete=json.loads(complete_path.read_text());complete['artifact']=acceptance.sha(path)
    complete_path.write_text(json.dumps(complete),encoding='utf-8')
    if change=='current':assert acceptance.validate_native_faults(root,entry,installation,native,official)['actual_cases']==14
    else:
        with pytest.raises(SystemExit,match='Native fault verifier/helper identity changed'):
            acceptance.validate_native_faults(root,entry,installation,native,official)
