"""Start private helpers from this exact installed package, ignoring import injection."""
from pathlib import Path
import sys


def module_command(module, *arguments):
    root = str(Path(__file__).resolve().parent.parent)
    program = ('import runpy,sys;sys.path.insert(0,' + repr(root) + ');'
               'sys.argv=[sys.argv[1],*sys.argv[2:]];'
               'runpy.run_module(sys.argv[0],run_name="__main__")')
    return [sys.executable, '-I', '-c', program, module, *map(str, arguments)]
