"""Windows entrypoints made with distlib's public script-copying API.

These are ordinary Python console launchers. They select the installed package
and profile, then enter the same CLI in that interpreter; no shell parses paths
or arguments and no additional runtime or process relay is involved.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from .platforms import file_lock, private_directory, replace_file

DISTLIB_VERSION = '0.4.4'


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def launcher_recipe(*, home=None, interpreter=None, package_root=None):
    recipe = {
        'format': 1,
        'distlib': DISTLIB_VERSION,
        'interpreter': str(Path(interpreter or sys.executable).absolute()),
        'package_root': str(Path(package_root or Path(__file__).parent.parent).resolve()),
        'home': str(Path(home).resolve()) if home is not None else None,
    }
    recipe['script_sha256'] = hashlib.sha256(_script(recipe).encode('utf-8')).hexdigest()
    return recipe


def _script(recipe):
    script = '#!python -I -X utf8\nimport os, runpy, sys\n'
    if recipe['home'] is not None:
        script += 'os.environ["CODEX_DEEPSEEK_HOME"] = ' + repr(recipe['home']) + '\n'
    script += 'sys.path.insert(0, ' + repr(recipe['package_root']) + ')\n'
    script += 'runpy.run_module("codex_deepseek.cli", run_name="__main__")\n'
    return script


def make_launchers(directory, recipe, names=('codex', 'codex-deepseek')):
    """Generate only in an empty staging directory, never replace a live exe."""
    import distlib
    from distlib.scripts import ScriptMaker
    if os.name != 'nt':
        raise RuntimeError('Windows launchers require Windows')
    if distlib.__version__ != DISTLIB_VERSION:
        raise RuntimeError('The pinned distlib version is required for Windows launchers')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise RuntimeError('Launcher output directory must be empty')
    # Temp directories created while elevated must also admit the same user unelevated.
    private_directory(directory)
    with tempfile.TemporaryDirectory(prefix='launcher-source-', dir=directory.parent) as source:
        maker = ScriptMaker(source, str(directory))
        maker.executable = recipe['interpreter']
        maker.variants = {''}
        for name in names:
            (Path(source)/(name+'.py')).write_text(_script(recipe), encoding='utf-8')
            outputs = maker.make(name+'.py')
            if outputs != [str((directory/(name+'.exe')).absolute())]:
                raise RuntimeError('Unexpected Windows launcher output')
    return {name+'.exe': _sha(directory/(name+'.exe')) for name in names}


def _valid(directory, recipe):
    try:
        record = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        return (not directory.is_symlink() and record['recipe'] == recipe
                and set(record['files']) == {'codex.exe', 'codex-deepseek.exe'}
                and all(not (directory/name).is_symlink() and _sha(directory/name) == digest
                        for name, digest in record['files'].items()))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def profile_launchers(home, *, interpreter=None, package_root=None):
    recipe = launcher_recipe(home=home, interpreter=interpreter, package_root=package_root)
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:24]
    root = Path(home)/'provider-state/cli-path'
    private_directory(root)
    with (root/'launchers.lock').open('a') as lock, file_lock(lock):
        target = root/digest
        candidates = [target, *sorted(root.glob(digest+'-repair-*'), reverse=True)]
        for candidate in candidates:
            if _valid(candidate, recipe):
                return candidate
        if target.exists():
            target = target.with_name(digest+'-repair-'+uuid.uuid4().hex[:10])
        staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=root))
        try:
            files = make_launchers(staging, recipe)
            (staging/'manifest.json').write_text(json.dumps({'recipe': recipe, 'files': files}, indent=2), encoding='utf-8')
            replace_file(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return target


def make_installed_entry(destination):
    """Replace pip's initial entry only while assembling a new private release."""
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix='.entry-', dir=destination.parent) as staging:
        make_launchers(staging, launcher_recipe(), names=('codex-deepseek',))
        replace_file(Path(staging)/'codex-deepseek.exe', destination)
