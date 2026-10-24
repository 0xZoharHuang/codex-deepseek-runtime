"""Audit complete pinned sources, target overlays and the exact native package."""
import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tomllib
import build_native as build

SOURCE = Path(__file__).resolve().parent
PROTECTED = ['codex-rs/core/src/session/', 'codex-rs/core/src/agent/', 'codex-rs/core/src/tasks/',
             'codex-rs/core/src/tools/handlers/', 'codex-rs/app-server/src/thread_',
             'codex-rs/app-server/src/codex_message_processor', 'codex-rs/linux-sandbox/',
             'codex-rs/windows-sandbox-rs/', 'codex-rs/macos-sandbox/']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def inventory_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    require(bool(name) and not path.is_absolute() and '..' not in path.parts
            and '\\' not in name and ':' not in name, 'Unsafe native inventory path: ' + name)
    return name


def upstream_inventory(archive):
    require(build.sha(archive) == build.SOURCE_ASSET[1], 'Upstream source archive checksum mismatch')
    files, links = {}, {}
    prefix = 'codex-' + build.COMMIT + '/'
    with tarfile.open(archive) as bundle:
        for member in bundle:
            if member.name.rstrip('/') == prefix.rstrip('/'):
                continue
            require(member.name.startswith(prefix), 'Unexpected upstream archive root')
            name = safe_name(member.name[len(prefix):].rstrip('/'))
            if member.isdir():
                continue
            require(name not in files and name not in links, 'Duplicate upstream path: ' + name)
            if member.issym():
                links[name] = member.linkname
            else:
                require(member.isfile() or member.islnk(), 'Unsupported upstream member: ' + name)
                files[name] = hashlib.sha256(bundle.extractfile(member).read()).hexdigest()
    return files, links


def source_contract(target, archive):
    before, links = upstream_inventory(archive)
    recipe = build.runtime_recipe()
    require(target in recipe['required_base_inputs'], 'Unsupported source-audit target')
    inputs = copy.deepcopy(recipe['required_base_inputs'][target])
    common = load(SOURCE / 'native/source-changes.json')
    require(common['base_commit'] == build.COMMIT, 'Common native baseline mismatch')
    current, declared = dict(before), {}
    for row in common['files']:
        name = safe_name(row['path'])
        require(current.get(name) == row['base_sha256'], 'Common input hash mismatch: ' + name)
        current[name] = declared[name] = row['fork_sha256']
    if target.endswith('windows-msvc'):
        windows = load(SOURCE / 'native/windows-source-changes.json')
        require(windows['base_patch_sha256'] == inputs[0]['sha256'], 'Windows base patch mismatch')
        for row in windows['files']:
            name = safe_name(row['path'])
            require(current.get(name) == row['common_sha256'], 'Windows input hash mismatch: ' + name)
            current[name] = declared[name] = row['fork_sha256']
    for item in recipe['overlays']:
        if target not in item['changes_by_target']:
            continue
        inputs.append({'file': item['file'], 'sha256': item['sha256']})
        for row in item['changes_by_target'][target]:
            name = safe_name(row['path'])
            require(current.get(name) == row['before_sha256'], 'Overlay chain mismatch: ' + name)
            current[name] = declared[name] = row['after_sha256']
    for item in inputs:
        require(build.sha(SOURCE / 'native' / safe_name(item['file'])) == item['sha256'],
                'Native patch checksum mismatch: ' + item['file'])
    protected = {name: digest for name, digest in before.items()
                 if any(name.startswith(prefix) for prefix in PROTECTED)}
    require(protected, 'Upstream protected source inventory is empty')
    unchanged = {name: digest for name, digest in protected.items() if name not in declared}
    declared_protected = {name: digest for name, digest in declared.items()
                          if any(name.startswith(prefix) for prefix in PROTECTED)}
    return {'target': target, 'before': before, 'links': links, 'final': current, 'declared': declared,
            'protected': protected, 'unchanged_protected': unchanged,
            'declared_protected': declared_protected, 'recipe': recipe, 'patch_inputs': inputs,
            'recipe_id': build.native_recipe_identity(target, recipe)}


def source_inventory(tree, links):
    result = {}
    for path in Path(tree).rglob('*'):
        name = path.relative_to(tree).as_posix()
        if any(part in {'.git', '__pycache__'} for part in path.relative_to(tree).parts):
            continue
        if path.is_symlink():
            require(name in links and path.readlink().as_posix() == links[name],
                    'Unexpected source symlink: ' + name)
        if path.is_file():
            result[name] = build.sha(path)
    return result


def checked_source_inventory(tree, contract):
    actual = source_inventory(tree, contract['links'])
    expected, auxiliary = dict(contract['final']), {}
    # Windows materializes the pinned upstream LICENSE symlink. Both forms must
    # retain the exact bytes of its archive target.
    for name, target in contract['links'].items():
        resolved = (PurePosixPath(name).parent / target).as_posix()
        require(resolved in contract['before'], 'Unknown upstream symlink target')
        # The Windows source staging never consumes the Linux-only bubblewrap
        # license alias and may omit that archive symlink. No regular source is omitted.
        optional_windows_alias = (contract['target'].endswith('windows-msvc')
            and name == 'codex-rs/vendor/bubblewrap/LICENSE' and target == 'COPYING')
        if name in actual or not optional_windows_alias:
            expected[name] = contract['before'][resolved]
    probes = {'codex-rs/http-client/tests/deepseek_policy_revocation.rs': 'policy-revocation-probe.rs',
              'codex-rs/secrets/tests/deepseek_fixture_secret_key.rs': 'secret-key-probe.rs'}
    for name, source in probes.items():
        if name in actual:
            auxiliary[name] = expected[name] = build.sha(SOURCE / 'native' / source)
    require(actual == expected, 'Complete native source inventory differs: ' + repr(sorted(
        name for name in actual.keys() | expected.keys() if actual.get(name) != expected.get(name))))
    require(all(actual.get(name) == digest for name, digest in contract['unchanged_protected'].items()),
            'Undeclared protected runtime source changed')
    return actual, auxiliary


def package_identity(package, native, contract, combined_sha, archived_recipe=None):
    package = Path(package).resolve()
    require(native.get('status') == 'ready' and native.get('upstream_commit') == build.COMMIT
            and native.get('upstream_version') == build.VERSION
            and native.get('native_recipe_id') == contract['recipe_id']
            and native.get('build_toolchain_pin') == contract['recipe']['build_toolchain']
            and native.get('patch_sha256') == combined_sha, 'Native manifest source/recipe mismatch')
    recorded = native.get('files')
    require(isinstance(recorded, dict) and recorded, 'Native manifest has no file inventory')
    actual = {}
    for path in package.rglob('*'):
        require(not path.is_symlink(), 'Native package cannot contain symlinks')
        if path.is_file():
            actual[path.relative_to(package).as_posix()] = build.sha(path)
    for name in recorded:
        safe_name(name)
    require(actual == recorded, 'Native package differs from manifest inventory')
    suffix = '.exe' if native['target'].endswith('windows-msvc') else ''
    binaries = {'bin/' + name + suffix for name in ['codex', 'codex-http-transport', 'codex-code-mode-host']}
    binaries.add('codex-path/rg' + suffix)
    if suffix:
        binaries.update('bin/' + name + suffix for name in ['codex-windows-sandbox-setup', 'codex-command-runner', 'codex-windows-managed-deny-probe', 'codex-windows-sandbox-service'])
    if 'linux' in native['target']:
        binaries.add('codex-resources/bwrap')
    require(binaries <= actual.keys(), 'Native package is missing required target binaries')
    metadata = load(package / 'codex-package.json')
    receipt = load(package / 'build-receipt.json')
    recipe_proof = None
    if archived_recipe is not None:
        # Reuse the exact closure already used for fixed public Unix archives.
        from ci import qualify_native
        require(qualify_native.ROOT.resolve() == SOURCE.resolve(),
                'Archived recipe helper belongs to another source checkout')
        recipe_proof = qualify_native.recipe_scope_identity(
            receipt, contract['recipe'], native['target'], Path(archived_recipe))
    require(metadata.get('target') == native['target'] and metadata.get('upstreamCommit') == build.COMMIT
            and metadata.get('version') == build.VERSION and metadata.get('recipeId') == contract['recipe_id']
            and metadata.get('patchSha256') == combined_sha
            and metadata.get('patchInputs') == contract['patch_inputs'], 'Native metadata source identity mismatch')
    require(receipt.get('upstream_commit') == build.COMMIT
            and receipt.get('native_recipe_id') == contract['recipe_id']
            and receipt.get('patch_inputs') == contract['patch_inputs']
            and receipt.get('source_files') == contract['declared']
            and receipt.get('patch_sha256') == receipt.get('combined_patch_sha256') == combined_sha
            and receipt.get('build_toolchain_pin') == contract['recipe']['build_toolchain']
            and receipt.get('upstream_toolchain_pin') == contract['recipe']['upstream_toolchain']
            and str(receipt.get('rustc', '')).startswith('rustc ' + contract['recipe']['build_toolchain'] + ' ')
            and (recipe_proof is not None or receipt.get('overlay_recipe_sha256') == build.sha(SOURCE / 'native/runtime-overlays.json'))
            and receipt.get('archives') == {name: {'url': url, 'sha256': digest}
                for name, (url, digest) in build.build_assets(native['target']).items()}
            and receipt.get('files') == {name: digest for name, digest in actual.items() if name != 'build-receipt.json'},
            'Native build receipt does not bind the audited source and package')
    proof = {'package': str(package), 'native_manifest': native, 'files_sha256': actual,
             'binary_sha256': {name: actual[name] for name in sorted(binaries)}}
    if recipe_proof is not None:
        proof['recipe_validation'] = recipe_proof
    return proof


def audit(archive, tree, target, package, native, output, archived_recipe=None):
    archive, tree, output = Path(archive).resolve(), Path(tree).resolve(), Path(output).resolve()
    require(native.get('target') == target, 'Native manifest target mismatch')
    contract = source_contract(target, archive)
    actual, auxiliary = checked_source_inventory(tree, contract)
    output.mkdir(parents=True, exist_ok=False)
    replay = output / 'replay'
    replay.mkdir()
    needed = set(contract['declared']) | {'codex-rs/rust-toolchain.toml', 'codex-rs/Cargo.lock'}
    with tarfile.open(archive) as bundle:
        for member in bundle:
            name = member.name.removeprefix('codex-' + build.COMMIT + '/')
            if name in needed and (member.isfile() or member.islnk()):
                path = replay / safe_name(name); path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bundle.extractfile(member).read())
    before_lock = tomllib.loads((replay / 'codex-rs/Cargo.lock').read_text(encoding='utf-8'))
    subprocess.run(['git', 'init', '-q', str(replay)], check=True)
    inputs = copy.deepcopy(contract['recipe']['required_base_inputs'][target])
    for item in inputs:
        result = subprocess.run(['git', '-c', 'core.longpaths=true', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', 'apply', str(SOURCE / 'native' / item['file'])], cwd=replay, capture_output=True, text=True, encoding='utf-8')
        (output / (item['file'] + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
        result.check_returncode()
    build.apply_runtime_overlays(replay, target, inputs, contract['recipe'])
    require(inputs == contract['patch_inputs'] and all(build.sha(replay / name) == digest for name, digest in contract['declared'].items()), 'Native patch replay differs')
    after_lock = tomllib.loads((replay / 'codex-rs/Cargo.lock').read_text(encoding='utf-8'))
    external = lambda value: sorted(json.dumps(row, sort_keys=True) for row in value['package'] if row.get('source'))
    require(external(before_lock) == external(after_lock), 'External native dependency versions changed')
    combined = hashlib.sha256(build.combined_patch(tree, archive, contract['declared']).encode()).hexdigest()
    package_proof = package_identity(package, native, contract, combined, archived_recipe)
    require(checked_source_inventory(tree, contract)[0] == actual, 'Native source changed during audit')
    proof = {'schema_version': 2, 'passed': True, 'target': target, 'upstream_commit': build.COMMIT,
             'upstream_archive_sha256': build.SOURCE_ASSET[1], 'upstream_archive': str(archive),
             'source_tree': str(tree), 'source_files': len(contract['before']),
             'upstream_inventory_sha256': inventory_digest(contract['before']),
             'source_inventory_sha256': inventory_digest(actual),
             'changed_native_files': sorted(contract['declared']), 'declared_source_sha256': contract['declared'],
             'protected_inventory_sha256': inventory_digest(contract['protected']),
             'unchanged_protected_runtime_files': len(contract['unchanged_protected']),
             'unchanged_protected_source_sha256': contract['unchanged_protected'],
             'declared_protected_source_sha256': contract['declared_protected'],
             'auxiliary_test_and_license_files': auxiliary, 'patch_inputs': contract['patch_inputs'],
             'patches': {item['file']: item['sha256'] for item in contract['patch_inputs']},
             'native_recipe_id': contract['recipe_id'], 'combined_patch_sha256': combined,
             'native_package': package_proof, 'external_cargo_packages_unchanged': True,
             'full_source_hashes_verified': True, 'patch_replay_passed': True,
             'verifier_sha256': {name: build.sha(SOURCE / name) for name in ['verify_native_source.py', 'build_native.py']}}
    if archived_recipe is not None:
        proof['verifier_sha256']['ci/qualify_native.py'] = build.sha(SOURCE / 'ci/qualify_native.py')
    (output / 'verdict.json').write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
    return proof


def validate_audit(proof, native):
    require(proof.get('schema_version') == 2 and proof.get('passed') is True
            and proof.get('target') == native.get('target') and proof.get('upstream_commit') == build.COMMIT
            and proof.get('upstream_archive_sha256') == build.SOURCE_ASSET[1], 'Source audit target/upstream mismatch')
    recipe_proof = proof.get('native_package', {}).get('recipe_validation')
    archived_recipe = None
    if recipe_proof is not None:
        require(isinstance(recipe_proof, dict)
                and recipe_proof.get('mode') == 'original_archive_target_equivalence'
                and isinstance(recipe_proof.get('archived_recipe'), str)
                and recipe_proof['archived_recipe'], 'Invalid explicit archived recipe proof')
        archived_recipe = Path(recipe_proof['archived_recipe'])
    contract = source_contract(native['target'], proof['upstream_archive'])
    actual, auxiliary = checked_source_inventory(proof['source_tree'], contract)
    expected = {'source_files': len(contract['before']),
                'upstream_inventory_sha256': inventory_digest(contract['before']),
                'source_inventory_sha256': inventory_digest(actual),
                'changed_native_files': sorted(contract['declared']), 'declared_source_sha256': contract['declared'],
                'protected_inventory_sha256': inventory_digest(contract['protected']),
                'unchanged_protected_runtime_files': len(contract['unchanged_protected']),
                'unchanged_protected_source_sha256': contract['unchanged_protected'],
                'declared_protected_source_sha256': contract['declared_protected'],
                'auxiliary_test_and_license_files': auxiliary, 'patch_inputs': contract['patch_inputs'],
                'patches': {item['file']: item['sha256'] for item in contract['patch_inputs']},
                'native_recipe_id': contract['recipe_id'], 'external_cargo_packages_unchanged': True,
                'full_source_hashes_verified': True, 'patch_replay_passed': True,
                'verifier_sha256': {name: build.sha(SOURCE / name) for name in ['verify_native_source.py', 'build_native.py']}}
    if archived_recipe is not None:
        expected['verifier_sha256']['ci/qualify_native.py'] = build.sha(SOURCE / 'ci/qualify_native.py')
    require(all(proof.get(key) == value for key, value in expected.items()), 'Source audit inventory or verifier proof differs')
    combined = hashlib.sha256(build.combined_patch(proof['source_tree'], proof['upstream_archive'], contract['declared']).encode()).hexdigest()
    require(proof.get('combined_patch_sha256') == combined, 'Audited source diff identity changed')
    require(proof.get('native_package') == package_identity(proof['native_package']['package'], native, contract, combined, archived_recipe), 'Audited native package identity changed')
    return {'upstream_source_files': len(contract['before']),
            'unchanged_protected_runtime_files': len(contract['unchanged_protected']),
            'declared_protected_source_sha256': contract['declared_protected'],
            'external_cargo_packages_unchanged': True, 'patch_replay_passed': True,
            'patch_sha256': combined, 'native_recipe_id': contract['recipe_id'],
            'declared_changed_files': sorted(contract['declared']),
            'native_binary_sha256': proof['native_package']['binary_sha256']}


def main():
    parser = argparse.ArgumentParser()
    for name in ['archive', 'source-tree', 'native-package', 'native-manifest', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--archived-recipe', type=Path)
    args = parser.parse_args()
    result = audit(args.archive, args.source_tree, args.target, args.native_package, load(args.native_manifest), args.output, args.archived_recipe)
    print(json.dumps({'passed': result['passed'], 'target': args.target, 'native_recipe_id': result['native_recipe_id'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
