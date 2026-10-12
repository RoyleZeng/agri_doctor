"""農藥許可證整理：用真實形狀的 payload 固定邊界，不連資料庫或外網。"""
from datetime import date

import pytest

from app.services.pesticide_permits import (
    match_permit, moa_codes, permit_row, permit_status, roc_date, vendor_names, vendor_summary,
)

TODAY = date(2026, 9, 23)


def payload(**changes):
    # 形狀取自實際同步下來的 PesticideDataQueryType（藥名與廠商換成試驗值）。
    base = {
        "FRAC": "3", "HRAC": "", "IRAC": "", "EnName": "test-a", "Permit": "農藥製",
        "Vendor": "試驗資材股份有限公司", "contents": "25.000 (%)", "formCode": "EC",
        "BrandName": "試驗牌", "ExpireDate": "118-03-31", "ChineseName": "試驗殺菌劑甲",
        "PermitNumber": "12345", "PesticideCode": "F001",
        "RevocationDate": "", "RevocationType": "",
        "ScopeOfUse": "https://example.invalid/detail",
    }
    return base | changes


def test_roc_date_handles_both_separators_and_refuses_to_guess():
    # 同一份資料裡 ExpireDate 用 -、RevocationDate 用 /，兩種都要吃得下。
    assert roc_date("118-03-31") == "2029-03-31"
    assert roc_date("079/05/03") == "1990-05-03"
    assert roc_date("0790503") == "1990-05-03"
    assert roc_date("77-10-12") == "1988-10-12"
    # 看不懂就回 None——不可以猜成今天，那會讓撤銷或到期的判斷整個反過來。
    for bad in ("", None, "未載明", "118-13-45", "2029-03-31 ", "abc"):
        assert roc_date(bad) is None


def test_revoked_reads_the_type_not_the_date():
    """有些撤銷紀錄沒有日期；只看日期會把它算成有效。"""
    row = permit_row(payload(RevocationType="逾期廢止", RevocationDate=""))
    assert row["revoked"] is True
    assert row["revoked_on"] is None
    assert permit_status(row, TODAY) == "revoked"
    assert permit_row(payload())["revoked"] is False


def test_permit_text_is_what_people_actually_say():
    assert permit_row(payload())["permit_text"] == "農藥製字第12345號"
    assert permit_row(payload())["permit_kind"] == "製造"
    assert permit_row(payload(Permit="農藥進"))["permit_kind"] == "輸入"
    # 缺一半就不要硬組出一個看起來像正式字號的字串。
    assert permit_row(payload(Permit=""))["permit_text"] == "12345"
    assert permit_row(payload(PermitNumber=""))["permit_text"] == ""
    # 沒見過的類別不要瞎猜中文。
    assert permit_row(payload(Permit="農藥新"))["permit_kind"] == ""
    # 原體不是成品藥，要標得出來——不然會被當成「這家有賣這支藥」。
    assert permit_row(payload(Permit="農藥原進"))["permit_kind"] == "原體輸入"
    assert permit_row(payload(Permit="農藥原進"))["raw_material"] is True
    assert permit_row(payload())["raw_material"] is False


def test_moa_codes_merge_three_schemes_and_skip_blanks():
    assert moa_codes(payload()) == ["FRAC 3"]
    assert moa_codes(payload(FRAC="11", IRAC="4A")) == ["FRAC 11", "IRAC 4A"]
    assert moa_codes(payload(FRAC="", HRAC="9")) == ["HRAC 9"]
    assert moa_codes(payload(FRAC="", IRAC="", HRAC="")) == []


def test_status_boundaries():
    active = permit_row(payload(ExpireDate="119-03-31"))
    assert permit_status(active, TODAY) == "active"
    # 一年內到期＝expiring；剛好滿一年仍算 expiring（邊界含）。
    assert permit_status(permit_row(payload(ExpireDate="1160101")), date(2026, 1, 1)) == "expiring"
    # 民國 114 = 2025，已經過去。
    assert permit_status(permit_row(payload(ExpireDate="114-01-01")), TODAY) == "expired"
    # 今天到期**還沒**過期——到期日當天仍可使用，提前一天擋掉是拿走農民一天。
    assert permit_status(permit_row(payload(ExpireDate="115-09-23")), TODAY) == "expiring"
    # 過了一天才是 expired。
    assert permit_status(permit_row(payload(ExpireDate="115-09-23")), date(2026, 9, 24)) == "expired"
    # 有效期限看不懂：unknown，不可以當成有效。
    assert permit_status(permit_row(payload(ExpireDate="未載明")), TODAY) == "unknown"


def test_vendor_summary_keeps_unknown_separate_from_active():
    rows = [permit_row(p) for p in (
        payload(ExpireDate="119-03-31"),                       # active
        payload(ExpireDate="116-01-01"),                       # expiring
        payload(ExpireDate="114-01-01"),                       # expired
        payload(RevocationType="自請撤銷", FRAC="11"),          # revoked
        payload(ExpireDate="未載明"),                           # unknown
    )]
    summary = vendor_summary(rows, TODAY)
    assert summary["total"] == 5
    assert summary["active"] == 1 and summary["expiring"] == 1
    assert summary["expired"] == 1 and summary["revoked"] == 1
    # 不知道就是不知道，併進 active 等於宣稱它有效。
    assert summary["unknown"] == 1
    # 機制統計不算撤銷的那張：那支已經不能用了。
    assert summary["moa_groups"] == [dict(code="FRAC 3", count=4)]
    assert summary["moa_group_total"] == 1


def test_vendor_summary_caps_the_mechanism_list_but_reports_the_real_total():
    """一家大廠橫跨七十幾個機制，全列出來沒人會讀；取前幾名但總數要照實說。"""
    rows = [permit_row(payload(FRAC=str(i), ExpireDate="119-03-31")) for i in range(1, 21)]
    summary = vendor_summary(rows, TODAY)
    assert len(summary["moa_groups"]) == 8
    assert summary["moa_group_total"] == 20


def test_vendor_names_counts_and_orders_by_permits():
    rows = [permit_row(p) for p in (
        payload(Vendor="甲公司"), payload(Vendor="甲公司"),
        payload(Vendor="乙公司"), payload(Vendor=""),
    )]
    assert vendor_names(rows) == [dict(vendor="甲公司", permits=2), dict(vendor="乙公司", permits=1)]


def test_match_permit_is_exact_only_and_never_matches_by_name():
    rows = [permit_row(p) for p in (
        payload(PermitNumber="11111", PesticideCode="F001", ExpireDate="119-03-31"),
        payload(PermitNumber="22222", PesticideCode="F001", ExpireDate="121-03-31"),
        payload(PermitNumber="33333", PesticideCode="F002", RevocationType="逾期廢止"),
    )]
    # 許可證號優先，而且是精確比對。
    assert match_permit(rows, permit_number="11111")["permit_number"] == "11111"
    # 同一個成分代碼有多張時挑還有效、期限最遠的那張（最可能是現在買得到的）。
    assert match_permit(rows, pesticide_code="F001")["permit_number"] == "22222"
    # 全都撤銷時仍然回一張，畫面才說得出「為什麼不能用」。
    assert match_permit(rows, pesticide_code="F002")["permit_number"] == "33333"
    # 對不到就回 None，不要退而求其次拿藥名去撈——中文沒有詞界，會對到複方。
    assert match_permit(rows, pesticide_code="F999") is None
    assert match_permit(rows, permit_number="99999") is None
    assert match_permit(rows) is None
    assert match_permit([], pesticide_code="F001") is None
    # 給了號碼就只看號碼；號碼對不到不可以偷偷退回用成分代碼比對。
    assert match_permit(rows, pesticide_code="F001", permit_number="99999") is None


# ---- 配方層級對應（藥劑卡那一行） ----

from app.services.pesticide_permits import (  # noqa: E402
    formula_key, formula_summary, normalize_contents, normalize_formulation, normalize_ingredient,
    permit_formula_key,
)


def test_contents_normalise_both_sides_to_a_percentage():
    # 植保：g/L 帶括號百分比，或直接百分比。
    assert normalize_contents("250 g/L (50% w/v)") == "50.000"
    assert normalize_contents("50%") == "50.000"
    assert normalize_contents("42.4%") == "42.400"
    # 農業部：數字在前、百分號在括號裡。
    assert normalize_contents("25.000  (%)") == "25.000"
    assert normalize_contents("0.005  (%)") == "0.005"
    # 同一個濃度兩種寫法要相等，否則這個鍵就沒有意義。
    assert normalize_contents("250 g/L (25% w/v)") == normalize_contents("25.000  (%)")


@pytest.mark.parametrize("value", ["", None, "1X10^9 CFU/g", "≧5*10^9 cfu/g", "未載明"])
def test_contents_without_a_percentage_refuse_to_guess(value):
    """活菌數換不成百分比，硬換就是編一個數字出來。"""
    assert normalize_contents(value) is None


def test_formulation_maps_chinese_and_code_to_the_same_key():
    assert normalize_formulation("水基乳劑") == "EW"
    assert normalize_formulation("EW") == "EW"
    assert normalize_formulation("ew") == "EW"
    # 可溼／可濕兩種寫法都要吃得下（植保用「溼」）。
    assert normalize_formulation("可溼性粉劑") == normalize_formulation("可濕性粉劑") == "WP"
    # 表裡沒有就是沒有，不要猜。
    assert normalize_formulation("沒看過的劑型") is None
    assert normalize_formulation("") is None
    assert normalize_formulation(None) is None


def test_ingredient_order_does_not_matter_for_mixtures():
    assert normalize_ingredient("azoxystrobin + difenoconazole") == "azoxystrobin+difenoconazole"
    assert normalize_ingredient("difenoconazole+azoxystrobin") == "azoxystrobin+difenoconazole"
    assert normalize_ingredient("Tebuconazole ") == "tebuconazole"
    assert normalize_ingredient("") is None
    assert normalize_ingredient("   ") is None


def test_formula_key_needs_all_three_parts():
    """三項任一項失敗就整個不對應——寧可不顯示，也不要用半個鍵撈出別的配方。"""
    assert formula_key(ingredient="tebuconazole", contents="25%", formulation="EW") == "tebuconazole|25.000|EW"
    assert formula_key(ingredient="", contents="25%", formulation="EW") is None
    assert formula_key(ingredient="tebuconazole", contents="1X10^9 CFU/g", formulation="EW") is None
    assert formula_key(ingredient="tebuconazole", contents="25%", formulation="沒看過的劑型") is None


def test_formula_summary_counts_permits_vendors_and_revoked_without_picking_one():
    rows = [permit_row(p) for p in (
        payload(EnName="tebuconazole", contents="25.000  (%)", formCode="EW", Vendor="甲公司"),
        payload(EnName="tebuconazole", contents="25.000  (%)", formCode="EW", Vendor="乙公司"),
        payload(EnName="tebuconazole", contents="25.000  (%)", formCode="EW", Vendor="乙公司",
                RevocationType="逾期廢止"),
        # 含量不同就是不同配方，不可以算進來。
        payload(EnName="tebuconazole", contents="43.000  (%)", formCode="EW", Vendor="丙公司"),
        # 劑型不同也一樣。
        payload(EnName="tebuconazole", contents="25.000  (%)", formCode="WP", Vendor="丁公司"),
    )]
    key = formula_key(ingredient="tebuconazole", contents="250 g/L (25% w/v)", formulation="水基乳劑")
    summary = formula_summary(rows, key)
    assert summary["permits"] == 3
    assert summary["vendors"] == 2
    assert summary["revoked"] == 1
    assert summary["all_revoked"] is False
    # 摘要裡不可以出現「挑中的那一家」或「挑中的到期日」——那會變成替人指定品牌。
    assert "vendor" not in summary and "expire_on" not in summary


def test_formula_summary_flags_a_formula_with_no_live_registration():
    rows = [permit_row(payload(EnName="old-one", contents="10.000  (%)", formCode="EC",
                               RevocationType="逾期廢止"))]
    key = formula_key(ingredient="old-one", contents="10%", formulation="乳劑")
    summary = formula_summary(rows, key)
    assert summary["permits"] == 1 and summary["all_revoked"] is True
    # 完全沒有對應時不要說成「全部撤銷」。
    empty = formula_summary(rows, formula_key(ingredient="other", contents="10%", formulation="乳劑"))
    assert empty["permits"] == 0 and empty["all_revoked"] is False


def test_permit_rows_without_a_usable_key_are_simply_not_matched():
    """許可證自己的欄位缺了也不對應，不會變成一個「空鍵」把所有缺欄位的湊在一起。"""
    rows = [permit_row(payload(EnName="", contents="25.000  (%)", formCode="EW")),
            permit_row(payload(EnName="a", contents="", formCode="EW")),
            permit_row(payload(EnName="a", contents="25.000  (%)", formCode=""))]
    assert [permit_formula_key(r) for r in rows] == [None, None, None]
    assert formula_summary(rows, "a|25.000|EW")["permits"] == 0
