"""Pure validation of the native retry assertion; the native suite remains exact."""

import json

import pytest

from test_native_payload_retry import assert_same_compaction_retry


@pytest.mark.parametrize('mutation', [
    'none', 'extra-client-field', 'extra-turn-field', 'changed-turn', 'input-order',
    'removed-workspace', 'changed-workspace', 'unexpected-root', 'unexpected-git-field',
])
def test_compaction_retry_only_allows_late_native_workspace_observation(tmp_path, mutation):
    (tmp_path / '.git').mkdir()
    key = 'x-codex-turn-metadata'
    original = {
        'input': [{'id': 'msg_a'}, {'id': 'msg_b'}],
        'client_metadata': {'thread_id': 'thread', key: json.dumps({'turn_id': 'turn'})},
    }
    enriched = json.loads(json.dumps(original))
    turn = {'turn_id': 'turn', 'workspaces': {str(tmp_path): {'has_changes': True}}}
    if mutation == 'extra-client-field':
        enriched['client_metadata']['unknown'] = True
    elif mutation == 'extra-turn-field':
        turn['unknown'] = True
    elif mutation == 'changed-turn':
        turn['turn_id'] = 'different-turn'
    elif mutation == 'input-order':
        enriched['input'].reverse()
    elif mutation in {'removed-workspace', 'changed-workspace'}:
        original['client_metadata'][key] = json.dumps(turn)
        if mutation == 'removed-workspace':
            turn.pop('workspaces')
        else:
            turn['workspaces'][str(tmp_path)]['has_changes'] = False
    elif mutation == 'unexpected-root':
        turn['workspaces'] = {str(tmp_path.parent): {'has_changes': True}}
    elif mutation == 'unexpected-git-field':
        turn['workspaces'][str(tmp_path)]['unknown'] = True
    enriched['client_metadata'][key] = json.dumps(turn)
    if mutation == 'none':
        assert_same_compaction_retry(original, original, tmp_path)
        assert_same_compaction_retry(original, enriched, tmp_path)
    else:
        with pytest.raises(AssertionError):
            assert_same_compaction_retry(original, enriched, tmp_path)
