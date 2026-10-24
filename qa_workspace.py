# Explicit QA-only physical workspaces. Product runtime never imports this module.
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import uuid

ENV = 'CODEX_DEEPSEEK_TEST_WORKSPACE_LAYOUT'
WINDOWS = os.name == 'nt'
RECORD = '.qa-workspace.json'


def evidence_files(root, pattern="*"):
    """Walk a physical QA evidence tree without entering runtime-created links."""
    root=Path(root).absolute();_plain(root);physical=root.resolve(strict=True)
    def fail(error):raise error
    for directory,dirs,files in os.walk(root,followlinks=False,onerror=fail):
        parent=Path(directory);_plain(parent)
        if parent.resolve(strict=True)!=physical/parent.relative_to(root):
            raise ValueError('QA evidence directory escaped its physical root')
        dirs[:]=[name for name in dirs if not _link(parent/name)]
        for name in files:
            path=parent/name
            if not _link(path) and path.is_file() and path.match(pattern):
                yield path


def _identity(path):
    value = Path(path).lstat()
    return [value.st_dev, value.st_ino]


def _link(path):
    value = Path(path).lstat()
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, 'st_file_attributes', 0) & 0x400)


def _plain(path):
    path = Path(path)
    if _link(path) or not path.is_dir():
        raise ValueError('QA workspace directory must be physical: '+str(path))
    return path


def _ancestry(path):
    for item in [Path(path), *Path(path).parents]:
        _plain(item)


def _config(root, create=True):
    raw = os.environ.get(ENV)
    if not WINDOWS or not raw:
        return None
    value = json.loads(raw)
    if set(value) != {'run_id', 'workspace_root', 'case_roots'}:
        raise ValueError('Explicit QA workspace run/root/case roots required')
    if not isinstance(value['run_id'], str) or not value['run_id']:
        raise ValueError('Invalid QA workspace run identity')
    base = Path(value['workspace_root'])
    cases = value['case_roots']
    if not base.is_absolute() or not isinstance(cases, list) or not cases:
        raise ValueError('Absolute QA workspace and case roots required')
    if any(not isinstance(p, str) or not Path(p).is_absolute() for p in cases):
        raise ValueError('Absolute QA case roots required')
    if not any(root == Path(p).resolve(strict=True) or root.is_relative_to(Path(p).resolve(strict=True)) for p in cases):
        raise ValueError('Fixture is outside explicit QA case roots')
    if base == root or base.is_relative_to(root):
        raise ValueError('Physical workspace must be outside the private fixture')
    # cmd's percent expansion also applies inside quotes; refuse that QA layout.
    if any(c in str(root)+str(base) for c in '\"%\r\n'):
        raise ValueError('Unsupported shell metacharacter in QA layout path')
    existing = base
    while not os.path.lexists(existing):existing = existing.parent
    _ancestry(existing)
    base = base.resolve(strict=False)
    if base == root or base.is_relative_to(root):
        raise ValueError('Physical workspace must be outside the private fixture')
    if create:base.mkdir(parents=True, exist_ok=True)
    _ancestry(base)
    value['workspace_root'] = str(base.resolve(strict=True))
    return value


def _inventory(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        _plain(parent)
        for name in dirs:
            _plain(parent/name)
        for name in files:
            path = parent/name
            info = path.lstat()
            if _link(path) or not path.is_file() or info.st_nlink != 1:
                raise ValueError('Foreign linked workspace input: '+str(path))
            with path.open('rb') as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def _save(root, record):
    temporary = root/(RECORD+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root/RECORD)
    finally:
        temporary.unlink(missing_ok=True)


def _junction(alias, physical):
    command = str(Path(os.environ['SystemRoot'])/'System32/cmd.exe')
    line = '"'+command+'" /d /v:off /c mklink /J "'+str(alias)+'" "'+str(physical)+'"'
    result = subprocess.run(line, capture_output=True)
    if result.returncode:
        raise RuntimeError('Owned QA workspace junction creation failed: '+str(result.returncode))


def _validate(root, config, record):
    if record.get('state') != 'ready' or record.get('config') != config:
        raise ValueError('Prior QA workspace attempt is not reusable')
    base = Path(config['workspace_root'])
    work_name = record.get('work_name', 'work')
    if work_name not in {'work', 'work with spaces'}:raise ValueError('Unknown QA workspace alias')
    alias = root/work_name
    physical = Path(record['physical'])
    if (record.get('case') != str(root) or record.get('alias') != str(alias)
            or physical.parent != base or not physical.name.startswith(work_name+'-')
            or _identity(root) != record.get('case_identity')
            or _identity(base) != record.get('base_identity')):
        raise ValueError('QA workspace binding differs')
    _plain(physical)
    if (not _link(alias) or _identity(alias) != record.get('alias_identity')
            or _identity(physical) != record.get('physical_identity')
            or alias.resolve(strict=True) != physical):
        raise ValueError('Foreign QA workspace alias or target')
    return physical


def prepare_workspace(root, work_name="work"):
    root = Path(root).absolute()
    if work_name not in {'work', 'work with spaces'}:raise ValueError('Unknown QA workspace alias')
    if not WINDOWS or not os.environ.get(ENV):
        return root/work_name
    _ancestry(root)
    root = root.resolve(strict=True)
    config = _config(root)
    record_path = root/RECORD
    if os.path.lexists(record_path):
        if _link(record_path) or record_path.stat().st_nlink != 1:
            raise ValueError('Foreign QA workspace record')
        return _validate(root, config, json.loads(record_path.read_text(encoding='utf-8')))
    alias = _plain(root/work_name)
    backup = root/'.qa-work-original'
    if os.path.lexists(backup):
        raise ValueError('Preserving pre-existing QA workspace backup')
    original = _inventory(alias)
    base = Path(config['workspace_root'])
    physical = base/(work_name+'-'+uuid.uuid4().hex)
    record = {'state':'pending', 'config':config, 'case':str(root), 'alias':str(alias), 'work_name':work_name,
              'physical':str(physical), 'case_identity':_identity(root),
              'base_identity':_identity(base), 'original_identity':_identity(alias)}
    # Reserve the case before any move. Failed or unknown attempts never retry.
    with record_path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        physical.mkdir()
        record['physical_identity'] = _identity(physical)
        _save(root, record)
        # New files inherit the ordinary destination ACL; no private source ACLs.
        shutil.copytree(alias, physical, dirs_exist_ok=True, copy_function=shutil.copyfile)
        if _inventory(alias) != original or _inventory(physical) != original:
            raise ValueError('QA workspace changed while copying')
        alias.rename(backup)
        _junction(alias, physical)
        if not _link(alias) or alias.resolve(strict=True) != physical:
            raise ValueError('Owned QA junction did not bind its physical workspace')
        record.update(state='ready', alias_identity=_identity(alias),
                      initial_files_sha256=original, backup=str(backup))
        _save(root, record)
        return _validate(root, config, record)
    except BaseException as error:
        if not os.path.lexists(alias) and backup.exists() and _identity(backup)==record['original_identity']:
            backup.rename(alias)
        record.update(state='failed', error_type=type(error).__name__, error=str(error))
        _save(root, record)
        raise


def workspace_path(root, value=None):
    root = Path(root).absolute()
    alias = root/'work'
    if WINDOWS and os.environ.get(ENV) and (root/RECORD).exists():
        prepare_workspace(root)
        alias = root/json.loads((root/RECORD).read_text(encoding='utf-8')).get('work_name','work')
    selected = str(alias) if value is None else value
    if not WINDOWS or not os.environ.get(ENV) or not isinstance(selected, str):
        return Path(selected) if value is None else selected
    # Only the controller's own fixture prefix is mapped. Other paths stay exact.
    if not Path(selected).is_absolute():
        return selected
    prefix = os.path.normcase(os.path.normpath(str(alias)))
    candidate = os.path.normcase(os.path.normpath(selected))
    try:
        if os.path.commonpath([prefix, candidate]) != prefix:
            return selected
    except ValueError:
        return selected
    relative = os.path.relpath(os.path.normpath(selected), os.path.normpath(str(alias)))
    return str(prepare_workspace(root)/relative)


def workspace_request(root, message):
    params = message.get('params')
    if not isinstance(params, dict) or not ('cwd' in params or 'cwds' in params):
        return message
    mapped = dict(params)
    if 'cwd' in mapped:
        mapped['cwd'] = workspace_path(root, mapped['cwd'])
    if isinstance(mapped.get('cwds'), list):
        mapped['cwds'] = [workspace_path(root, value) for value in mapped['cwds']]
    return {**message, 'params':mapped}


def cleanup_workspace(root):
    root = Path(root).absolute()
    _ancestry(root)
    root = root.resolve(strict=True)
    config = _config(root, create=False)
    if config is None:
        raise ValueError('Explicit QA workspace layout required for cleanup')
    record_path = root/RECORD
    if _link(record_path) or record_path.stat().st_nlink != 1:
        raise ValueError('Foreign QA workspace record')
    record = json.loads(record_path.read_text(encoding='utf-8'))
    physical = _validate(root, config, record)
    alias = Path(record['alias'])
    alias.unlink() if alias.is_symlink() else alias.rmdir()
    shutil.rmtree(physical)
    record['state'] = 'cleaned'
    _save(root, record)
