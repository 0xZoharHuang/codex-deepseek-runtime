from pathlib import Path
import base64,hashlib,json,sys
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE));import server_guest_python as guest
DEST=BASE/'windows-r3-sandbox-final-evidence';DEST.mkdir(exist_ok=True)
code=r'''from pathlib import Path
import base64,hashlib,json,sqlite3,sys,platform,time
finished=Path('C:/CodexDeepSeekQA/r3/sandbox-job/job-result.json');deadline=time.monotonic()+240
while not finished.exists():
 assert time.monotonic()<deadline,'Original guest gate has not reached terminal state'
 time.sleep(1)
assert json.loads(finished.read_text())['exit']==0,'Preserve original failed guest gate'
evidence=Path('C:/CodexDeepSeekQA/r3/sandbox-job/evidence');roots=[p for p in evidence.iterdir() if p.is_dir()];assert len(roots)==1;root=roots[0]
sha=lambda b:hashlib.sha256(b).hexdigest()
artifact=json.loads((root/'artifact.json').read_text(encoding='utf-8'));verdict=json.loads((root/'verdict.json').read_text(encoding='utf-8'))
files={}
for name in ['artifact.json','verdict.json','results.json']:
 p=root/name;b=p.read_bytes();files[name]={'source':str(p),'sha256':sha(b),'base64':base64.b64encode(b).decode()}
for mode in ['unelevated','elevated']:
 case=root/mode
 paths=[case/'outside.txt',case/'work/allowed.txt',case/'home/config.toml',case/'client-requests.jsonl']
 paths.extend(case.glob('events-*.json'));paths.extend(case.glob('launch-*.json'))
 for p in paths:
  b=p.read_bytes();files[p.relative_to(root).as_posix()]={'source':str(p),'sha256':sha(b),'base64':base64.b64encode(b).decode()}
for name in ['job-start.json','job-result.json','install.log','job.log']:
 p=root.parent.parent/name;b=p.read_bytes();files['job/'+name]={'source':str(p),'sha256':sha(b),'base64':base64.b64encode(b).decode()}
counts={}
for mode in ['unelevated','elevated']:
 database=root/mode/'home/provider-state/adapter.sqlite'
 with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:counts[mode]=db.execute('SELECT count(*) FROM receipts').fetchone()[0]
entry=Path(artifact['entry']);release=entry.parent.parent.parent;actual=json.loads((release/'installation.json').read_text(encoding='utf-8'));native=json.loads((release/'native-runtime.json').read_text(encoding='utf-8'))
source=Path(r'C:\CodexDeepSeekQA\r3\final-qa-fd3217ee050e32c7')
checks={'installed_metadata_equals_artifact':actual==artifact['installation'],'installed_native_manifest_equals_artifact':native==artifact['native_manifest'],'entry_sha256':sha(entry.read_bytes()),'verifier_sha256':{n:sha((source/n).read_bytes()) for n in artifact['verifier_sha256']},'model_receipt_counts':counts,'outside_bytes_preserved':{m:(root/m/'outside.txt').read_bytes()==b'PRESERVE' for m in counts},'inside_bytes':{m:(root/m/'work/allowed.txt').read_bytes()==b'SANDBOX_ALLOWED' for m in counts},'guest_hostname':platform.node(),'platform':platform.platform(),'python':sys.version,'original_root':str(root),'failure_file_exists':(root/'failure.json').exists()}
checks['original_files_unchanged_during_read']=all(sha(Path(row['source']).read_bytes())==row['sha256'] for row in files.values())
print(json.dumps({'files':files,'checks':checks},ensure_ascii=False))'''
r=guest.run(code,timeout=600);bundle=json.loads(r.stdout)
raw=DEST/bundle['checks']['original_root'].replace('\\','/').split('/')[-1];raw.mkdir(exist_ok=True);inventory={}
for name,row in bundle['files'].items():
 p=raw/name;p.parent.mkdir(parents=True,exist_ok=True);data=base64.b64decode(row['base64']);assert hashlib.sha256(data).hexdigest()==row['sha256']
 if p.exists():assert p.read_bytes()==data,'Refusing to overwrite different preserved raw evidence'
 else:p.write_bytes(data)
 inventory[name]={'original_guest_path':row['source'],'original_sha256':row['sha256'],'local_relative_path':p.relative_to(DEST).as_posix(),'local_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':len(data)}
receipt={'original_guest_root':bundle['checks']['original_root'],'remote_read_script_sha256':hashlib.sha256(code.encode()).hexdigest(),'transport_helper_sha256':hashlib.sha256((BASE/'server_guest_python.py').read_bytes()).hexdigest(),'read_only_remote':True,'remote_exit_code':r.returncode,'files':inventory,'checks':bundle['checks']}
(DEST/'transfer-and-current-readback.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'files':len(inventory),'bytes':sum(x['bytes'] for x in inventory.values()),'checks':bundle['checks'],'output':str(DEST)},ensure_ascii=False))
