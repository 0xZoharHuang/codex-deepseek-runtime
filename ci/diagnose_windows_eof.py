"""Strict diagnostic only; never full release admission."""
import sys,pathlib,os,json,hashlib
REPO=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from ci.qualify_installed_release import isolated_environment,write
from ci.qualify_native import run_observed
ROOT=pathlib.Path(os.environ["RUNNER_TEMP"])/"cds-eof-owned"
EVIDENCE=pathlib.Path(os.environ["RUNNER_TEMP"])/"cds-eof-evidence"
if(sys.platform!="win32"or os.environ.get("RUNNER_ENVIRONMENT")!="github-hosted"or os.environ.get("GITHUB_REPOSITORY")!="0xZoharHuang/codex-deepseek-runtime"or os.environ.get("GITHUB_REF")!="refs/heads/codex/windows-eof-diagnostic-r583"):
 raise RuntimeError("Wrong diagnostic host/ref")
if ROOT.exists()or EVIDENCE.exists():raise RuntimeError("Fresh roots required")
EVIDENCE.mkdir()
env=isolated_environment(ROOT/"home")
record={"scope":"strict EOF diagnostic only; not release admission","model_requests":0,"stages":[]}
def run(name,argv,environment,timeout):
 with(EVIDENCE/(name+".log")).open("wb")as log:
  result=run_observed(argv,cwd=REPO,env=environment,log=log,timeout=timeout)
 record["stages"].append({"name":name,**result})
 write(EVIDENCE/"diagnostic.json",record)
 if not result["passed"]:raise RuntimeError(name+" strict gate failed")
try:
 run("install",[sys.executable,"-I","-B",str(REPO/"install.py"),"--root",str(ROOT),"--no-activate"],env,900)
 releases=[p for p in(ROOT/"releases").iterdir()if(p/"installation.json").is_file()]
 if len(releases)!=1:raise RuntimeError("Unexpected installation")
 release=releases[0];installation=json.loads((release/"installation.json").read_bytes())
 record["source_id"]=installation["source_id"]
 record["native_archive_sha256"]=installation["native_archive_sha256"]
 record["source_modules"]={p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in(REPO/"src/codex_deepseek").glob("*.py")}
 if len(record["source_modules"])!=32:raise RuntimeError("Unexpected source modules")
 entry=release/"venv/Scripts/codex-deepseek.exe"
 run("version",[str(entry),"--version"],env,45)
 binding={"home":str(ROOT/"home"),"entry":str(entry),"cwd":str(REPO),"output":str(EVIDENCE)}
 write(EVIDENCE/"binding.json",binding)
 worker_env={**env,**{k:os.environ[k]for k in["GITHUB_ACTIONS","RUNNER_ENVIRONMENT","GITHUB_REPOSITORY"]}}
 run("ipc",[str(release/"venv/Scripts/python.exe"),"-I","-B",str(REPO/"ci/qualify_installed_release.py"),"--ipc-worker",str(EVIDENCE/"binding.json")],worker_env,300)
 record["strict_EOF_passed"]=True
finally:write(EVIDENCE/"diagnostic.json",record)
