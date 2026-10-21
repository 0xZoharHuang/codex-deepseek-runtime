"""Re-run the exact historical collector without launching a model or native process."""
from pathlib import Path
import hashlib,json,os,sys
sys.dont_write_bytecode=True
BASE=Path('D:/CodexDeepSeekQA/20261021')
COLLECTOR=BASE/'complex-collector-r2'
os.environ['PATH']=str(BASE/'git/cmd')+os.pathsep+os.environ.get('PATH','')
os.environ['GIT_OPTIONAL_LOCKS']='0'
sys.path[:0]=[str(COLLECTOR),str(COLLECTOR/'src')]
import verify_complex_runtime as collector

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def require(value,message):
    if not value:raise RuntimeError(message)
rows=[]
for name in ['jinja','httpcore']:
    path=BASE/'complex-runtime-windows-r3'/(name+'.json')
    original=json.loads(path.read_text())
    require(original['installation']['source_id']=='94c01b01aa57087becee','Wrong original product identity')
    require(all(sha(COLLECTOR/n)==digest for n,digest in original['collector_sha256'].items()),'Historical collector source changed')
    actual=collector.reconstruct_case(Path(original['fixture_root']),Path(original['entry']))
    require(actual==original,'Original and recomputed complex proof differ')
    require(actual['runtime_mechanisms_passed'] is True and actual['application_task_outcome']['application_task_passed'] is True,'Runtime or business result is not passed')
    rows.append({'case':name,'source_id':original['installation']['source_id'],'original_report':str(path),'original_sha256':sha(path),'collector_directory':str(COLLECTOR),'collector_sha256':original['collector_sha256'],'raw_file_count':len(original['raw_sha256']),'all_original_raw_hashes_reconstructed':True,'runtime_mechanisms_passed':True,'application_task_passed':True,'fresh_model_execution':False,'current_candidate_execution_claimed':False})
print(json.dumps({'historical_reconstruction_verified':True,'release_acceptance_claimed':False,'cases':rows},indent=2))
