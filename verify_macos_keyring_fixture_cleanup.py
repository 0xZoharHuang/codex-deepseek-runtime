#!/usr/bin/env python3
"""Metadata-only cleanup of a frozen macOS OAuth acceptance fixture.

This QA executable accepts the native libtest probe argv. It never requests a
password, changes a default keychain, or searches for an ancestor artifact.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid

SOURCE_ID = '2d43ed50050d0a687f55'
TARGET = 'aarch64-apple-darwin'
NATIVE_SHA = 'aef450933d716b0c9451d5a487eee59eb346c188c894f8fee1b8155c836052b0'
BASELINE_SHA = '95845b7f973fc9401888a5429ef6a09a94ac388bf12c80ad7b301a2e0c6f3a43'
MARKER = b'NONFUNCTIONAL_TEST_CREDENTIALS_ONLY'
UPSTREAM = 'fe136d4e70ef2b5bbed40a2d22b3aabf267796d3'
ACCOUNT_SOURCE = 'codex-rs/secrets/src/lib.rs'
ACCOUNT_SOURCE_SHA = '7e243492527e390d2e7210031ed2afd4a6d97c9866a5b7f1d1f6cc51f9ecd837'
SOURCE_ARCHIVE_SHA = '3f9d7e83c9f152a438633813daefacee944d29165101c305e8ac08dc00a0e361'
SECURITY = Path('/usr/bin/security')
RECEIPT = 'macos-keyring-cleanup.json'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular(path):
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid():
        raise RuntimeError('Expected a single owned regular file')
    return st


def fixture_identity(home):
    st = home.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o700):
        raise RuntimeError('Fixture home is not private and owned')
    marker = home / '.credential-acceptance-fixture'
    regular(marker)
    if marker.read_bytes() != MARKER:
        raise RuntimeError('Fixture marker mismatch')
    return {'device': st.st_dev, 'inode': st.st_ino, 'uid': st.st_uid,
            'mode': '0700', 'marker_sha256': sha(marker)}


def fixture_scope(home):
    # These are the only two producer layouts in run_acceptance. No ancestor search.
    tail = home.parts[-5:]
    if (len(tail) == 5 and tail[:2] == ('evidence', 'independent-audit')
            and tail[3] in {'primary', 'other'} and tail[4] == 'home'
            and str(uuid.UUID(tail[2])) == tail[2]):
        return home.parents[4], 'oauth-secrets', 5
    tail = home.parts[-3:]
    if (len(tail) == 3 and tail[0] == 'auth-stores'
            and tail[2] in {'keyring-secrets', 'keyring-secrets-other'}
            and str(uuid.UUID(tail[1])) == tail[1]):
        return home.parents[2], 'auth-stores', 3
    raise RuntimeError('Unexpected fixture path structure')


def validate(home, driver):
    if sys.platform != 'darwin' or not home.is_absolute() or home.resolve(strict=True) != home:
        raise RuntimeError('Only a canonical local macOS fixture is accepted')
    run, gate, depth = fixture_scope(home)
    for path in [home, *home.parents[:depth]]:
        st = path.lstat()
        if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) & 0o022):
            raise RuntimeError('Fixture path is linked or not exclusively owned')
    artifact_path = run / 'artifact.json'
    regular(artifact_path)
    artifact = json.loads(artifact_path.read_text(encoding='utf-8'))
    native = artifact['native_manifest']
    installation = artifact['installation']
    digest = sha(driver)
    if (artifact['evidence_roots'] != [str(run)] or gate not in artifact['selected_gates']
            or artifact['target'] != TARGET
            or artifact['baseline_matrix_sha256'] != BASELINE_SHA
            or artifact['qa_default_sandbox_mode'] != 'workspace-write'
            or installation['source_id'] != SOURCE_ID
            or installation['native_archive_sha256'] != NATIVE_SHA
            or native['target'] != TARGET or native['archive']['sha256'] != NATIVE_SHA
            or native['upstream_commit'] != UPSTREAM
            or artifact['native_probes']['keyring'] != {'path': str(driver), 'sha256': digest}
            or artifact['verifier_source_sha256'].get(driver.name) != digest):
        raise RuntimeError('Frozen producer identity mismatch')
    source = Path(installation['source'])
    release = Path(artifact['release'])
    if (source.resolve(strict=True) != source or driver != source / driver.name
            or json.loads((release / 'installation.json').read_text()) != installation
            or json.loads((release / 'native-runtime.json').read_text()) != native
            or sha(Path(artifact['entry'])) != artifact['entry_sha256']):
        raise RuntimeError('Installed artifact identity changed')
    # Account naming is frozen by the archive/member hashes documented above.
    # Ensure this candidate does not patch the native account or keyring layer.
    changes_path = source / 'native/source-changes.json'
    if sha(changes_path) != installation['source_sha256']['native/source-changes.json']:
        raise RuntimeError('Native source change inventory changed')
    changes = changes_path.read_text(encoding='utf-8')
    if any(name in changes for name in ['codex-rs/secrets/', 'codex-rs/keyring-store/']):
        raise RuntimeError('Native account implementation was modified')
    for name in ['src/codex_deepseek/service.py', 'src/codex_deepseek/processes.py']:
        if sha(source / name) != installation['adapter_source_sha256'][name]:
            raise RuntimeError('Native ownership checker source changed')
    sys.path.insert(0, str(source / 'src'))
    from codex_deepseek.service import native_active
    identity = fixture_identity(home)
    if native_active(home):
        raise RuntimeError('Native fixture owner remains live')
    return run, artifact_path, identity, native_active


def query(account, keychain=None):
    argv = [str(SECURITY), 'find-generic-password', '-s', 'codex', '-a', account]
    if keychain is not None:
        argv.append(keychain)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    if result.returncode == 44:
        return {'present': False, 'exit': 44}
    if result.returncode:
        raise RuntimeError('Metadata lookup failed')
    # Retain only selector metadata; never raw stdout/stderr or a data field.
    acct = re.search(r'^\s*"acct"<blob>="([^"]*)"\s*$', result.stdout, re.M)
    service = re.search(r'^\s*"svce"<blob>="([^"]*)"\s*$', result.stdout, re.M)
    chain = re.search(r'^keychain: "([^"]+)"\s*$', result.stdout, re.M)
    if (not acct or acct[1] != account or not service or service[1] != 'codex'
            or not chain or 'class: "genp"' not in result.stdout):
        raise RuntimeError('Metadata selector mismatch')
    chain_path = Path(chain[1])
    if (not chain_path.is_absolute()
            or not chain_path.is_relative_to(Path.home() / 'Library/Keychains')):
        raise RuntimeError('Unexpected keychain metadata path')
    return {'present': True, 'exit': 0, 'account': acct[1], 'service': service[1],
            'keychain': str(chain_path), 'class_genp': True}


def cleanup(home, driver):
    run, artifact_path, identity, native_active = validate(home, driver)
    receipt = home / RECEIPT
    if receipt.exists() or receipt.is_symlink():
        raise RuntimeError('Preserve the prior cleanup receipt')
    # Native CodexAuth namespace: canonical UTF-8 path -> SHA256 -> first 16 hex.
    account = 'secrets|' + hashlib.sha256(str(home).encode('utf-8')).hexdigest()[:16]
    proof = {'schema_version': 1, 'driver_kind': 'macos-security-metadata-only',
             'driver_path': str(driver), 'driver_sha256': sha(driver),
             'security_path': str(SECURITY), 'security_sha256': sha(SECURITY),
             'runtime_root': str(run), 'producer_gate': fixture_scope(home)[1],
             'artifact_path': str(artifact_path),
             'artifact_sha256': sha(artifact_path), 'home': str(home),
             'home_identity': identity, 'native_active_before': False,
             'account': account, 'service': 'codex', 'password_requested': False,
             'source_archive_sha256': SOURCE_ARCHIVE_SHA, 'upstream_commit': UPSTREAM,
             'native_source_member': ACCOUNT_SOURCE, 'native_source_sha256': ACCOUNT_SOURCE_SHA,
             'started': time.time(), 'passed': False}
    try:
        before = query(account)
        proof['metadata_before'] = before
        if fixture_identity(home) != identity or native_active(home):
            raise RuntimeError('Fixture ownership changed before deletion')
        proof.update(present_before=before['present'], deleted=False, delete_exit=None)
        if before['present']:
            result = subprocess.run([str(SECURITY), 'delete-generic-password', '-s', 'codex',
                                     '-a', account, before['keychain']],
                                    capture_output=True, text=True, timeout=10)
            proof['delete_exit'] = result.returncode
            if result.returncode:
                raise RuntimeError('Exact owned fixture deletion failed')
            proof['deleted'] = True
        after = query(account)
        proof['metadata_after'] = after
        if after['present'] or fixture_identity(home) != identity or native_active(home):
            raise RuntimeError('Fixture deletion or ownership verification failed')
        if sha(driver) != proof['driver_sha256'] or sha(artifact_path) != proof['artifact_sha256']:
            raise RuntimeError('Cleanup producer changed')
        proof.update(present_after=False, native_active_after=False, passed=True)
    except Exception as error:
        proof['error_type'] = type(error).__name__
        raise
    finally:
        proof['finished'] = time.time()
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(proof, stream, indent=2)
            stream.write('\n')
    return proof


def main():
    if sys.argv[1:] != ['--ignored', '--exact', 'native_fixture_secret_key', '--nocapture']:
        return 2
    try:
        home = Path(os.environ['CODEX_DEEPSEEK_KEYRING_FIXTURE_HOME'])
        proof = cleanup(home, Path(__file__).resolve(strict=True))
    except Exception:
        # Do not print exception text, frames, security output, or credential data.
        return 1
    print('KEYRING_FIXTURE_RESULT ' + json.dumps({k: proof[k] for k in
          ['present_before', 'deleted', 'present_after', 'account', 'driver_kind']}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
