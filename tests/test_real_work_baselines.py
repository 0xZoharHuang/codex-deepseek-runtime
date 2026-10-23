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


def test_pinned_upstream_xfails_are_explicit_and_not_ordinary_skips(tmp_path):
    from prepare_real_work import baseline_observation,HTTPCORE_EXISTING_XFAILS
    import xml.etree.ElementTree as ET
    root=ET.Element('testsuite')
    for case in sorted(HTTPCORE_EXISTING_XFAILS):
        row=ET.SubElement(root,'testcase',classname=case[0],name=case[1]);ET.SubElement(row,'skipped',type='pytest.xfail',message='upstream')
    ordinary=ET.SubElement(root,'testcase',classname='tests.other',name='test_skipped');ET.SubElement(ordinary,'skipped',type='pytest.skip',message='not supported')
    path=tmp_path/'results.xml';ET.ElementTree(root).write(path,encoding='utf-8')
    observation=baseline_observation(path,0)
    assert set(observation['expected_failures'])==HTTPCORE_EXISTING_XFAILS
    assert observation['skipped']==[('tests.other','test_skipped')]


def test_only_exact_existing_xfail_ids_are_admitted_and_original_failures_still_count():
    from prepare_real_work import HTTPCORE_EXISTING_XFAILS
    clean={'exit':0,'collected':213,'failures':[],'errors':[],'skipped':[],'expected_failures':list(HTTPCORE_EXISTING_XFAILS)}
    assert valid_httpcore_baseline(clean,False)
    assert valid_httpcore_baseline({**clean,'failures':list(WINDOWS_EXPIRY_FAILURES),'exit':1},True)
    known=next(iter(HTTPCORE_EXISTING_XFAILS))
    for values in [[('tests.test_cancellations','new_xfail')],[known,known]]:
        assert not valid_httpcore_baseline({**clean,'expected_failures':values},True)
    assert not valid_httpcore_baseline({**clean,'skipped':[known]},True)
    assert not valid_httpcore_baseline({**clean,'failures':[known],'exit':1},True)
