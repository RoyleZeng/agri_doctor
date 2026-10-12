"""三源比對的名稱對位測試。

這裡的每一條都對應一個實際踩到的錯誤。名稱對錯位不會讓程式壞掉，
只會安靜地把兩支不同的藥當成同一支 —— 對植物醫生來說是最糟的一種錯，
所以全部釘住。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.matcher import _core_name, _kdais_matches, _product_keys
from app.services.normalize import extract_tolerance_categories, split_composition


@dataclass
class FakeProduct:
    name: str
    concentration_text: str | None = None
    formulation: str | None = None


@dataclass
class FakeKdaisRow:
    product_text: str
    dilution_text: str | None = None
    phi_text: str | None = None


# --------------------------------------------------------------------------
# 藥劑名對位
# --------------------------------------------------------------------------

def test_core_name_normalizes_formulation_variant():
    """植保系統寫「可溼性」，高改場寫「可濕性」——同一個劑型。"""
    assert _core_name("10%克枯爛可溼性粉劑") in _core_name("10%克枯爛可濕性粉劑○S")


def test_core_name_strips_kdais_markers():
    assert "○" not in _core_name("6%撲殺熱粒劑○S")
    assert _core_name(" 33% 克枯三賽唑 可濕性粉劑 ○S ") == "33%克枯三賽唑可濕性粉劑s"


def test_kdais_match_pairs_exact_product():
    rows = [
        FakeKdaisRow("10%克枯爛可濕性粉劑○S", "1,000", "15"),
        FakeKdaisRow("14%嘉賜克枯爛可濕性粉劑○S", "1,500", "14"),
    ]
    product = FakeProduct("克枯爛", "10%", "可溼性粉劑")
    matched = _kdais_matches(rows, product)
    assert [m.product_text for m in matched] == ["10%克枯爛可濕性粉劑○S"]


def test_kdais_match_does_not_confuse_single_with_combination():
    """回歸測試：單劑「克枯爛」曾經命中複方「嘉賜克枯爛」。

    兩者含量不同（10% vs 14%）、成分不同（tecloftalam vs
    tecloftalam+kasugamycin）、稀釋倍數不同（1,000 vs 1,500），
    卻因為單純用藥名做子字串比對而被配成一對，憑空生出
    「稀釋倍數不一致」與「安全採收期不一致」兩個假衝突。
    """
    rows = [FakeKdaisRow("14%嘉賜克枯爛可濕性粉劑○S", "1,500", "14")]
    product = FakeProduct("克枯爛", "10%", "可溼性粉劑")
    assert _kdais_matches(rows, product) == []


def test_kdais_match_requires_same_formulation():
    """劑型不同就是不同產品：高改場只列可濕性粉劑時，水懸劑不該算被推薦。"""
    rows = [FakeKdaisRow("10%克枯爛可濕性粉劑○S", "1,000", "15")]
    assert _kdais_matches(rows, FakeProduct("克枯爛", "10%", "水懸劑")) == []
    assert _kdais_matches(rows, FakeProduct("克枯爛", "10%", "可溼性粉劑"))


def test_product_keys_are_ordered_strict_to_loose():
    keys = _product_keys(FakeProduct("克枯爛", "10%", "可溼性粉劑"))
    assert keys[0] == "10%克枯爛可濕性粉劑"
    # 放寬含量但保留劑型；不可以只用藥名。
    assert keys[1] == "克枯爛可濕性粉劑"
    assert all("克枯爛" != k for k in keys)


def test_product_keys_without_formulation_falls_back_to_concentration():
    keys = _product_keys(FakeProduct("撲殺熱", "6%", None))
    assert keys == ["6%撲殺熱"]


def test_product_keys_empty_for_nameless_product():
    assert _product_keys(FakeProduct("", "10%", "水懸劑")) == []


# --------------------------------------------------------------------------
# 成分與作物類別
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "composition,expected",
    [
        ("tecloftalam + kasugamycin", ["tecloftalam", "kasugamycin"]),
        ("(probenazole + cartap)", ["probenazole", "cartap"]),
        ("Bacillus velezensis BF", ["Bacillus velezensis BF"]),
        (None, []),
        ("", []),
    ],
)
def test_split_composition(composition, expected):
    assert split_composition(composition) == expected


def test_extract_tolerance_categories():
    """植保系統容許量欄的括號註記就是食藥署用的作物類別。

    食藥署同時用「米」與「米類」兩種寫法（嘉賜黴素訂在米類，
    撲殺熱與克枯爛訂在米），只認一個會讓後者被誤標成「查無容許量」。
    """
    text = "嘉賜黴素0.2(旱稻) 克枯爛0.2(米) 嘉賜黴素0.2(米)"
    assert extract_tolerance_categories(text) == {"旱稻", "米"}
    assert extract_tolerance_categories("撲殺熱0.5(米)") == {"米"}
    assert extract_tolerance_categories(None) == set()
    assert extract_tolerance_categories("免訂") == set()
