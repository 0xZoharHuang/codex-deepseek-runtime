"""Release and CI skip declarations must describe the exact same platform cases."""
import json
from pathlib import Path
import pytest
import assemble_release_acceptance as acceptance

@pytest.mark.parametrize('target,platform,count',[
    # Stable historical node IDs preserve immutable acceptance lineage.
    pytest.param('x86_64-unknown-linux-gnu','linux',55,id='x86_64-unknown-linux-gnu-linux-40'),
    pytest.param('aarch64-apple-darwin','darwin',56,id='aarch64-apple-darwin-darwin-41'),
    pytest.param('x86_64-pc-windows-msvc','win32',4,id='x86_64-pc-windows-msvc-win32-4'),
])
def test_release_and_ci_offline_skip_catalogs_have_exact_case_reason_parity(target,platform,count):
    catalog=json.loads((Path(__file__).resolve().parents[1]/'ci/offline-platform-skips.json').read_text(encoding='utf-8'))
    assert set(catalog)=={'common','linux','darwin','win32'}
    rows=(catalog['common'] if platform!='win32' else [])+catalog[platform]
    exact={}
    full=set()
    for row in rows:
        assert set(row)=={'classname','name','message'}
        assert all(isinstance(row[key],str) and row[key] for key in row)
        assert row['classname'].startswith('tests.')
        identity=(row['classname'],row['name'])
        assert identity not in full,'Duplicate declared skip identity'
        full.add(identity)
        key=row['classname'].split('.')[-1]+'::'+row['name']
        assert key not in exact,'Classname normalization cannot collapse distinct cases'
        exact[key]=row['message']
    assert len(exact)==count
    assert acceptance.expected_offline_skips(target)==exact
