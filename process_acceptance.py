"""Portable fixed programs for native process/terminal acceptance, never model tools."""
import os
import subprocess
import psutil
from codex_deepseek.processes import identity
import sys
from pathlib import Path


def python_command(program,*arguments):
    executable=sys._base_executable if os.name=='nt' else sys.executable
    return [executable,'-I','-u','-c',program,*map(str,arguments)]


def shell_command(arguments):
    if os.name!='nt':
        import shlex
        return shlex.join(arguments)
    # Native Windows shellCommand selects PowerShell. Literal arguments must
    # not interpolate $, %, quotes or workspace paths as expressions.
    return '& '+' '.join("'"+str(value).replace("'","''")+"'" for value in arguments)


def evidence_root(name):
    return Path(os.environ.get('CODEX_DEEPSEEK_TEST_EVIDENCE_ROOT',str(Path(__file__).resolve().parent.parent/'delivery/evidence')))/name


def fixture_identity(marker,fixture):
    """Map a sandbox PID to its real kernel owner without trusting a bare PID."""
    if sys.platform!='linux':
        pid=marker['pid'];birth=identity(pid)
        assert birth and str(fixture) in psutil.Process(pid).cmdline()
        return pid,birth
    expected='linux:'+marker['boot_id']+':'+marker['start_ticks']
    candidates=[]
    for process in psutil.process_iter(['pid','cmdline']):
        if str(fixture) not in (process.info['cmdline'] or []):continue
        try:
            status=(Path('/proc')/str(process.pid)/'status').read_text(encoding='utf-8')
            nspid=next(line.split()[1:] for line in status.splitlines() if line.startswith('NSpid:'))
            if int(nspid[-1])==marker['pid'] and identity(process.pid)==expected:candidates.append(process.pid)
        except (OSError,ValueError,StopIteration):continue
    assert len(candidates)==1,'The sandbox process did not map to one host PID/start-time identity'
    return candidates[0],expected


def write_process_tree_fixture(fixture,pidfile):
    """Both parent and child identify themselves inside the native PID namespace."""
    program='''import json,os,sys,subprocess,time
from pathlib import Path
if "--child" in sys.argv:
    time.sleep(120)
    raise SystemExit(0)
child=subprocess.Popen([sys.executable,"-I","-u",__file__,"--child"])
def marker(pid):
    value={"pid":pid}
    if sys.platform=="linux":
        stat=(Path("/proc")/str(pid)/"stat").read_text()
        value["start_ticks"]=stat[stat.rfind(")")+2:].split()[19]
        value["boot_id"]=Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return value
ready=Path(PIDFILE)
temporary=ready.with_suffix(".tmp")
temporary.write_text(json.dumps([marker(os.getpid()),marker(child.pid)]),encoding="utf-8")
temporary.replace(ready)
child.wait()
'''
    fixture.write_text(program.replace('PIDFILE',repr(str(pidfile))),encoding='utf-8')

