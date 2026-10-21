from copy import deepcopy
from prepare_real_work import WINDOWS_EXPIRY_FAILURES,valid_httpcore_baseline


def test_historical_windows_baseline_only_admits_known_expiry_failures():
    clean={'exit':0,'collected':213,'failures':[],'errors':[],'skipped':[]}
    assert valid_httpcore_baseline(clean,False)
    windows={**clean,'exit':1,'failures':list(WINDOWS_EXPIRY_FAILURES)}
    assert valid_httpcore_baseline(windows,True)
    assert not valid_httpcore_baseline(windows,False)
    for key,value in [('collected',0),('collected',212),('exit',0),
                      ('failures',[('tests._async.test_connection_pool','other_bug')]),
                      ('errors',[('collection','error')]),('skipped',[('test','hidden')])]:
        bad=deepcopy(windows);bad[key]=value
        assert not valid_httpcore_baseline(bad,True)


def test_known_expiry_failure_cannot_hide_duplicate_results_or_wrong_exit():
    known=next(iter(WINDOWS_EXPIRY_FAILURES))
    assert not valid_httpcore_baseline({'exit':1,'collected':213,'failures':[known,known],'errors':[],'skipped':[]},True)
    assert not valid_httpcore_baseline({'exit':1,'collected':213,'failures':[],'errors':[],'skipped':[]},True)


def test_heldout_baseline_requires_real_fix_difference_beyond_windows_clock():
    row={'stage':'heldout','exit':1,'collected':213,'failures':list(WINDOWS_EXPIRY_FAILURES),'errors':[],'skipped':[]}
    assert not valid_httpcore_baseline(row,True)
    row['failures'].append(('tests._async.test_connection_pool','test_connection_pool_closed_connections[asyncio]'))
    assert valid_httpcore_baseline(row,True)
    assert not valid_httpcore_baseline({**row,'stage':'oracle'},True)
