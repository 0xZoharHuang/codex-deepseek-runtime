# Standalone Peer evidence must bind both directly executed shared helpers.
from pathlib import Path
import pytest
import assemble_release_acceptance as audit

ROOT=Path(__file__).resolve().parents[1]
HELPERS={'verify_live.py','windows_acceptance_setup.py','qa_workspace.py'}
PRODUCERS=[
    {'verify_browser_runtime.py','install.py','acceptance_assertions.py'},
    {'verify_local_account.py','install.py'},
    {'verify_windows_sandbox.py','install.py'},
    {'verify_protocol_recovery.py','install.py'},
    {'verify_native_mcp_lifetime.py','install.py','src/codex_deepseek/cli.py'},
    {'verify_user_verification_boundary.py','src/codex_deepseek/cli.py'},
    {'verify_real_work.py','acceptance_assertions.py','host_execution_acceptance.py','real_work_imports.py'},
    {'verify_real_work.py','verify_real_work_followup.py','acceptance_assertions.py','host_execution_acceptance.py','real_work_imports.py'},
]


@pytest.mark.parametrize('expected',PRODUCERS)
@pytest.mark.parametrize('missing',[None,'qa_workspace.py','windows_acceptance_setup.py'])
def test_standalone_peer_proof_rejects_missing_execution_helper(expected,missing):
    hashes={name:audit.sha(ROOT/name) for name in expected|HELPERS}
    if missing:
        hashes.pop(missing)
        with pytest.raises(SystemExit,match='inventory'):
            audit.validate_peer_verifiers({'verifier_sha256':hashes},expected,'fixture')
    else:
        assert audit.validate_peer_verifiers({'verifier_sha256':hashes},expected,'fixture')==hashes


@pytest.mark.parametrize('missing',[None,'qa_workspace.py','windows_acceptance_setup.py'])
def test_method_proof_binds_both_shared_helpers(missing):
    names={'verify_remaining_methods.py','verify_remaining_local.py','assemble_release_acceptance.py','process_acceptance.py','install.py'}|HELPERS
    hashes={name:audit.sha(ROOT/name) for name in names}
    if missing:
        hashes.pop(missing)
        with pytest.raises(SystemExit,match='inventory'):
            audit.validate_method_verifiers({'verifier_sha256':hashes})
    else:
        assert audit.validate_method_verifiers({'verifier_sha256':hashes})['five_execution_producers_current']


@pytest.mark.parametrize('changed',['qa_workspace.py','windows_acceptance_setup.py'])
def test_standalone_peer_proof_rejects_changed_helper(changed):
    expected=PRODUCERS[0]
    hashes={name:audit.sha(ROOT/name) for name in expected|HELPERS};hashes[changed]='0'*64
    with pytest.raises(SystemExit,match='changed'):
        audit.validate_peer_verifiers({'verifier_sha256':hashes},expected,'fixture')
