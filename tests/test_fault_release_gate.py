from copy import deepcopy
import pytest
from assemble_release_acceptance import NATIVE_FAULT_KINDS,validate_fault_rows


def complete_rows():
    return [{'kind':kind,'variant':variant,'passed':True}
            for kind in NATIVE_FAULT_KINDS for variant in ['official','candidate']]


def test_all_fourteen_exact_actual_cases_are_required():
    rows=complete_rows()
    assert validate_fault_rows(rows)==rows


@pytest.mark.parametrize('bad',['missing','duplicate','foreign-kind','foreign-variant','failed','truthy'])
def test_missing_duplicate_or_failed_fault_cases_block_release(bad):
    rows=complete_rows()
    if bad=='missing':rows.pop()
    elif bad=='duplicate':rows[-1]=deepcopy(rows[0])
    elif bad=='foreign-kind':rows[0]['kind']='other'
    elif bad=='foreign-variant':rows[0]['variant']='reference-like'
    elif bad=='failed':rows[0]['passed']=False
    else:rows[0]['passed']=1
    with pytest.raises(SystemExit,match='fourteen unique'):
        validate_fault_rows(rows)
