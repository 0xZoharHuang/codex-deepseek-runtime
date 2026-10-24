"""Archived Unix source receipts and cursor-separated hook labels retain strict guards."""
import base64
import copy
import json
import pytest
import build_native as build
import verify_native_source as audit
from ci import qualify_native
from test_qa_release_contracts import source_case
from test_hook_trust_acceptance import fixture as hook_case
from verify_hook_trust import CASE_NAMES, REVIEW_LABELS, case_contract, review_screen


def archived_case(c, monkeypatch):
    original = copy.deepcopy(c.contract['recipe'])
    original['required_base_inputs']['x86_64-pc-windows-msvc'] = []
    archived = c.source / 'original-recipe.json'
    archived.write_text(json.dumps(original), encoding='utf-8')
    current = copy.deepcopy(original)
    current['overlays'].append({
        'file': 'windows-only.patch', 'sha256': 'a' * 64,
        'changes_by_target': {'x86_64-pc-windows-msvc': []}})
    (c.source / 'native/runtime-overlays.json').write_text(json.dumps(current), encoding='utf-8')
    monkeypatch.setattr(build, 'runtime_recipe', lambda: copy.deepcopy(current))
    monkeypatch.setattr(qualify_native, 'ROOT', c.source)
    helper = c.source / 'ci/qualify_native.py'
    helper.parent.mkdir()
    helper.write_bytes(b'fixed-helper-source')
    receipt_path = c.package / 'build-receipt.json'
    receipt = audit.load(receipt_path)
    receipt['overlay_recipe_sha256'] = build.sha(archived)
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    c.native['files']['build-receipt.json'] = build.sha(receipt_path)
    return archived, receipt_path.read_bytes()


def audited(c, archived):
    return audit.audit(c.archive, c.tree, 'x86_64-unknown-linux-gnu',
                       c.package, c.native, c.output, archived)


def test_archived_recipe_is_explicit_and_preserves_original_receipt(source_case, monkeypatch):
    c = source_case
    archived, before = archived_case(c, monkeypatch)
    with pytest.raises(ValueError, match='Native build receipt'):
        audited(c, None)
    c.output = c.output.with_name('explicit-audit')
    proof = audited(c, archived)
    assert c.package.joinpath('build-receipt.json').read_bytes() == before
    assert proof['native_package']['recipe_validation']['mode'] == 'original_archive_target_equivalence'
    assert proof['verifier_sha256']['ci/qualify_native.py'] == build.sha(c.source/'ci/qualify_native.py')
    assert audit.validate_audit(proof, c.native)['patch_replay_passed']


@pytest.mark.parametrize('change', ['archive_bytes', 'target_changes', 'helper_hash', 'missing_mode'])
def test_archived_mode_rejects_wrong_bytes_target_or_unbound_helper(source_case, monkeypatch, change):
    c = source_case
    archived, _ = archived_case(c, monkeypatch)
    if change == 'archive_bytes':
        archived.write_bytes(archived.read_bytes() + b' ')
        with pytest.raises(ValueError, match='Archived recipe bytes'):
            audited(c, archived)
    elif change == 'target_changes':
        current = build.runtime_recipe()
        current['build_toolchain'] = 'another-toolchain'
        monkeypatch.setattr(build, 'runtime_recipe', lambda: copy.deepcopy(current))
        c.native['build_toolchain_pin'] = current['build_toolchain']
        with pytest.raises(ValueError, match='outside the Windows-only scope'):
            qualify_native.recipe_scope_identity(audit.load(c.package/'build-receipt.json'), current,
                                                  'x86_64-unknown-linux-gnu', archived)
        with pytest.raises(ValueError, match='Native manifest source/recipe mismatch'):
            audited(c, archived)
    else:
        proof = audited(c, archived)
        if change == 'helper_hash':
            proof['verifier_sha256']['ci/qualify_native.py'] = '0' * 64
        else:
            proof['native_package']['recipe_validation'].pop('mode')
        with pytest.raises(ValueError):
            audit.validate_audit(proof, c.native)


def test_archived_mode_cannot_relax_windows(source_case, monkeypatch):
    c = source_case
    archived, _ = archived_case(c, monkeypatch)
    receipt = audit.load(c.package/'build-receipt.json')
    with pytest.raises(ValueError, match='limited to the qualified Unix targets'):
        qualify_native.recipe_scope_identity(receipt, build.runtime_recipe(),
                                              'x86_64-pc-windows-msvc', archived)


def test_cursor_positioned_hook_words_keep_exact_all_labels_and_input_order():
    transcript = b'\r\n'.join(b'\x1b[3C'.join(label.encode().split()) for label in REVIEW_LABELS)
    assert review_screen(transcript)
    data = hook_case(CASE_NAMES[0])
    data['transcript'] = transcript
    for row in data['inputs']:
        row['after_output_bytes'] = len(transcript)
    assert case_contract(**data)
    data['inputs'][0]['after_output_bytes'] = 0
    with pytest.raises(AssertionError, match='preceded'):
        case_contract(**data)
    for label in REVIEW_LABELS:
        assert not review_screen(transcript.replace(b'\x1b[3C'.join(label.encode().split()), b''))
