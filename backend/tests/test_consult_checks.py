"""用虛構成分與明確日期固定開單邊界，不連資料庫或外網。"""
from datetime import date
import pytest
from app.services.consult_checks import prescription_checks, rotation_summary

TODAY=date(2030,3,1)

def checks(**changes):
    return prescription_checks(today=TODAY,expected_harvest_on=date(2030,3,9),
        **({'phi_days':4,'ingredient_names':['試驗成分甲','synthetic-a'],
            'banned_names':set(),'moa_codes':['FRAC 7'],'history':[]} | changes))


def test_combination_any_banned_ingredient_blocks_exact_names_only():
    result=checks(ingredient_names=['synthetic-a','synthetic-b'],banned_names={'synthetic-b'})
    assert next(c for c in result if c['type']=='banned')['level']=='block'
    result=checks(ingredient_names=['synthetic-ab'],banned_names={'synthetic-a'})
    assert next(c for c in result if c['type']=='banned')['level']=='ok'

@pytest.mark.parametrize('phi,level,shortfall',[(8,'ok',0),(9,'block',1),(None,'warning',None)])
def test_phi_boundary_unknown_is_not_zero(phi,level,shortfall):
    result=next(c for c in checks(phi_days=phi) if c['type']=='phi')
    assert result['level']==level and result['shortfall_days']==shortfall
    assert result['phi_days']==phi


def test_missing_harvest_is_warning_without_guessed_date():
    result=prescription_checks(today=TODAY,expected_harvest_on=None,phi_days=4,
        ingredient_names=['synthetic'],banned_names=set(),moa_codes=[],history=[])
    assert next(c for c in result if c['type']=='phi')['level']=='warning'


def test_rotation_requires_last_two_actual_applications_and_candidate_overlap():
    history=[{'id':'older','applied_on':date(2030,2,20),'moa_codes':['FRAC 7']},
             {'id':'latest','applied_on':date(2030,2,26),'moa_codes':['FRAC 7','IRAC 8']}]
    r=next(c for c in checks(history=history) if c['type']=='rotation')
    assert r['level']=='warning' and r['groups']==['FRAC 7']
    assert r['record_ids']==['latest','older']
    assert next(c for c in checks(history=history,moa_codes=['FRAC 8']) if c['type']=='rotation')['level']=='ok'
    history.append({'id':'interruption','applied_on':date(2030,2,25),'moa_codes':['FRAC 9']})
    assert next(c for c in checks(history=history) if c['type']=='rotation')['level']=='ok'


def test_future_record_does_not_count_and_unknown_code_does_not_mean_safe():
    history=[{'id':'future','applied_on':date(2030,3,8),'moa_codes':['FRAC 7']}]
    r=next(c for c in checks(history=history) if c['type']=='rotation')
    assert r['level']=='warning' and not r['record_ids']
    assert next(c for c in checks(moa_codes=[]) if c['type']=='rotation')['level']=='warning'


# ---- 案件層級輪替摘要（第四輪橫幅用） ----

def record(day, codes, product='試驗藥劑', rid=None):
    return {'id': rid or f'r{day}', 'applied_on': date(2030, 2, day), 'moa_codes': codes,
            'product_text': product}


def summary(**changes):
    return rotation_summary(**({'today': TODAY, 'item_codes': [['FRAC 7']], 'history': []} | changes))


def test_case_rotation_alerts_only_from_the_third_consecutive_use():
    """門檻跟逐項檢查一致：本次是連續第 3 次共用同一機制才喊。"""
    # 連續兩次（一筆歷史＋本次）還不到門檻。
    two = summary(history=[record(20, ['FRAC 7'])])
    assert two['streak'] == 2 and two['level'] != 'crit'
    three = summary(history=[record(10, ['FRAC 7']), record(20, ['FRAC 7'])])
    assert three['streak'] == 3 and three['level'] == 'crit'
    assert three['groups'] == ['FRAC 7']
    assert '連續第 3 次' in three['message']


def test_case_rotation_counts_beyond_three_and_keeps_the_sequence_in_order():
    result = summary(history=[record(d, ['FRAC 7'], f'藥{d}') for d in (5, 10, 15, 20)])
    assert result['streak'] == 5
    # 序列由舊到新，最後一筆是本次處方。
    assert [s['applied_on'] for s in result['sequence']] == [
        '2030-02-05', '2030-02-10', '2030-02-15', '2030-02-20', '2030-03-01']
    assert result['sequence'][-1]['current'] is True
    assert result['sequence'][0]['current'] is False
    assert result['sequence'][-1]['product_text'] == '本次處方'


def test_case_rotation_breaks_on_a_different_mechanism():
    """中間換過機制就不是連續；只算到斷掉的地方為止。"""
    result = summary(history=[record(10, ['FRAC 7']), record(20, ['FRAC 11'])])
    assert result['streak'] == 1
    assert result['level'] != 'crit'


def test_case_rotation_stops_at_a_record_without_codes():
    """缺代碼不等於安全，也不等於連續：只能停在那裡，並說明查不完整。"""
    result = summary(history=[record(10, ['FRAC 7']), record(20, [])])
    assert result['streak'] == 1
    assert result['level'] == 'unknown'
    assert '未載明' in result['message']


def test_case_rotation_narrows_the_shared_mechanism_across_rounds():
    """兩次分別共用不同機制，不算連續共用同一個。"""
    result = summary(item_codes=[['FRAC 7', 'FRAC 3']],
                     history=[record(10, ['FRAC 3']), record(20, ['FRAC 7'])])
    # 最近一次共用 FRAC 7，再往前那次只有 FRAC 3，交集收窄後為空 → 停在 2。
    assert result['streak'] == 2
    assert result['groups'] == ['FRAC 7']
    assert result['level'] != 'crit'


def test_case_rotation_looks_at_every_item_not_just_one():
    """整張處方有任一項延續前次機制就要算——逐項各自看不出來的正是這種。"""
    result = summary(item_codes=[['FRAC 11'], ['FRAC 7']],
                     history=[record(10, ['FRAC 7']), record(20, ['FRAC 7'])])
    assert result['streak'] == 3 and result['level'] == 'crit'
    assert result['groups'] == ['FRAC 7']


def test_case_rotation_excludes_groups_for_the_picker():
    """橫幅的「換一支不同機制」要能直接拿去預篩。"""
    result = summary(history=[record(10, ['FRAC 7']), record(20, ['FRAC 7'])])
    assert result['exclude_groups'] == ['FRAC 7']
    assert summary()['exclude_groups'] == []


def test_case_rotation_ignores_hrac_and_unparsable_codes():
    """輪替只看 FRAC／IRAC；除草劑分類與看不懂的字串不可拿來宣稱連續。"""
    result = summary(item_codes=[['HRAC 9']], history=[record(10, ['HRAC 9']), record(20, ['HRAC 9'])])
    assert result['level'] != 'crit'
    assert result['groups'] == []
