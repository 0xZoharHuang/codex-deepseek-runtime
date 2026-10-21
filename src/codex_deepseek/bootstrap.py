"""Start private helpers from this exact installed package, ignoring import injection."""
from pathlib import Path
import sys
import os


def module_command(module, *arguments):
    root = str(Path(__file__).resolve().parent.parent)
    program = ('import runpy,sys;sys.path.insert(0,' + repr(root) + ');'
               'sys.argv=[sys.argv[1],*sys.argv[2:]];'
               'runpy.run_module(sys.argv[0],run_name="__main__")')
    executable=sys._base_executable if os.name=='nt' else sys.executable
    return [executable, '-I', '-X', 'utf8', '-c', program, module, *map(str, arguments)]


def module_environment(environment=None):
    environment=dict(os.environ if environment is None else environment)
    if os.name=='nt':
        # Use CPython's own venv redirector protocol while launching its real
        # executable directly, so Popen.pid is the Python process owning IPC.
        environment['__PYVENV_LAUNCHER__']=sys.executable
    return environment
