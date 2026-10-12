"""離線 parser 測試 —— 全部跑存檔的 fixture，不碰網路。

這些測試的用意不只是「現在能解析」，更是「官網改版時先紅」：
政府網站改版沒有公告，parser 靜默失效是這個系統最大的風險，
所以每個來源都釘住幾筆具體資料，改了就會被抓到。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.crawlers.fda_parser import TAB_BANNED, TAB_EXEMPT, TAB_MRL, page_url, parse_list_page
from app.crawlers.kdais_parser import parse_document_list, find_next_page, parse_pdf
from app.crawlers.ppm_parser import parse_pest_detail, parse_pest_list
from app.services.phi_extract import extract_mix_warnings, extract_phi_days
from app.services.resistance import build_rotation_advice, parse_moa_codes

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# 植物保護資訊系統
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rice_blight():
    return parse_pest_detail(
        read("ppm_plc0101_rice_blight.html"),
        "https://otserv2.acri.gov.tw/PPM/PLC0101.aspx",
    )


def test_ppm_detail_crop_and_pest(rice_blight):
    assert rice_blight.crop_name == "水稻"
    assert rice_blight.crop_scientific_name == "Oryza sativa"
    # 這句是作物 -> 食藥署作物類別的種子資料來源，壞掉會讓 MRL 全查不到。
    assert rice_blight.fda_category == "米類"

    assert rice_blight.pest_name == "白葉枯病"
    assert rice_blight.pest_english_name == "Bacterial leaf blight"
    assert "乾葉尾" in rice_blight.pest_aliases
    assert "Xanthomonas campestris" in rice_blight.pest_scientific_name


def test_ppm_detail_profile_text(rice_blight):
    assert "黃色條斑" in rice_blight.symptoms
    assert "颱風" in rice_blight.transmission
    assert "秈稻" in rice_blight.cultural_control


def test_ppm_drug_table_biological(rice_blight):
    row = next(r for r in rice_blight.drugs if r.product_name == "貝萊斯芽孢桿菌BF")
    assert row.dilution_text == "600"
    assert row.dilution_value == 600
    assert row.dose_per_hectare == "1.67公升"
    assert row.formulation == "水懸劑"
    assert row.english_composition == "Bacillus velezensis BF"
    assert row.moa_codes == ["FRAC BM02;BM02"]


def test_ppm_drug_table_combination_product(rice_blight):
    """複方藥要能拆出兩個成分與兩組作用機制代碼。"""
    row = next(r for r in rice_blight.drugs if r.product_name == "嘉賜克枯爛")
    assert row.concentration == "14%"
    assert row.formulation == "可溼性粉劑"
    assert row.english_composition == "tecloftalam + kasugamycin"
    assert row.dilution_text == "1,500"
    assert row.dilution_value == 1500
    assert row.moa_codes == ["FRAC 24;D3", "FRAC 34;unknown"]
    assert "採收前14天停止施藥" in row.precautions
    # PPM 頁面自己也印了容許量，之後要拿來跟食藥署對照。
    assert "嘉賜黴素0.2" in row.tolerance_text


def test_ppm_drug_table_row_count(rice_blight):
    assert len(rice_blight.drugs) == 9


def test_ppm_pest_list_covers_striped_rows():
    """奇偶列用不同 class 做斑馬紋，只認一種會漏掉一半的資料。"""
    items = parse_pest_list(
        read("ppm_plc0301_rice.html"), "https://otserv2.acri.gov.tw/PPM/"
    )
    assert len(items) == 55
    names = {i.target for i in items}
    for expected in ("水稻白葉枯病", "水稻胡麻葉枯病", "水稻稻熱病", "稻穀檢疫處理"):
        assert expected in names

    blight = next(i for i in items if i.target == "水稻白葉枯病")
    assert blight.crop == "水稻"
    assert blight.pest == "白葉枯病"
    assert blight.alias == "乾葉尾"
    assert "PLC0101.aspx?ASParam=" in blight.detail_url


# --------------------------------------------------------------------------
# 食藥署
# --------------------------------------------------------------------------

def test_fda_mrl_page():
    page = parse_list_page(read("fda_pesticidelist_p1.html"), TAB_MRL)
    assert page.total_rows == 8274
    assert page.total_pages == 828
    assert len(page.mrl_rows) == 10

    first = page.mrl_rows[0]
    assert first.seq == "1"
    assert first.international_name == "1-methylcyclopropene"
    assert first.crop_category == "柿"
    assert first.limit_text == "0.01"
    assert float(first.limit_ppm) == 0.01
    assert first.note == "生長調節劑"


def test_fda_banned_page():
    """禁用農藥是推薦前的硬性閘門，欄位不能解錯。"""
    page = parse_list_page(read("fda_banned_tk3.html"), TAB_BANNED)
    assert page.total_rows == 65
    row = page.banned_rows[0]
    assert row.name == "一品松"
    assert row.english_name == "EPN"
    assert "八十五年十二月九日" in row.banned_manufacture_date


def test_fda_exempt_page():
    page = parse_list_page(read("fda_exempt_tk2.html"), TAB_EXEMPT)
    assert page.total_rows == 44
    assert page.exempt_rows[0].name == "DL-甲硫胺酸"


def test_fda_page_url():
    base = "https://consumer.fda.gov.tw/Law/PesticideList.aspx?nodeID=520"
    assert page_url(base, tab=1, page=1).endswith("&tk=1")
    assert page_url(base, tab=3, page=7).endswith("&tk=3&p=7")


# --------------------------------------------------------------------------
# 高改場
# --------------------------------------------------------------------------

def test_kdais_document_list():
    docs = parse_document_list(read("kdais_list_p1.html"), "https://www.kdais.gov.tw/")
    assert len(docs) == 20

    lotus = docs[0]
    assert lotus.crop_text == "蓮霧"
    assert lotus.revision_date == "114.12.22"
    assert lotus.file_size == "0.43MB"
    assert "redirect_files.php?id=65501" in lotus.pdf_url

    assert find_next_page(
        read("kdais_list_p1.html"), "https://www.kdais.gov.tw/"
    ).endswith("id=7665&page=2")


def test_kdais_revision_comparison():
    docs = parse_document_list(read("kdais_list_p1.html"), "https://www.kdais.gov.tw/")
    lotus = docs[0]
    assert lotus.is_newer_than("114.12.21") is True
    assert lotus.is_newer_than("114.12.22") is False
    # 抓不到日期時寧可重抓，不要漏更新。
    assert lotus.is_newer_than(None) is True


@pytest.mark.parametrize(
    "crop,min_rows",
    [("芒果", 150), ("蓮霧", 90), ("水稻", 100)],
)
def test_kdais_pdf_parses_all_pages(crop, min_rows):
    """續頁沒有表頭。

    早期版本每張表都硬要求表頭，結果只解析到第一頁 ——
    看起來像「這個作物只有幾支藥」，是最難察覺的一種錯。
    """
    pdf = FIXTURES / f"kdais_{crop}.pdf"
    if not pdf.exists():
        pytest.skip(f"缺少 fixture {pdf.name}")

    rows, warnings = parse_pdf(str(pdf))
    assert len(rows) >= min_rows
    assert not warnings
    assert len({r.page for r in rows}) > 1, "應該要跨多頁"


def test_kdais_pdf_tolerance_column_survives_compatibility_chars():
    """這些 PDF 把「容許量」的「量」寫成 CJK 相容字 U+F97E。

    肉眼一模一樣，但沒做 NFKC 正規化就整欄讀不到，
    安全容許量會全部變成空的。
    """
    pdf = FIXTURES / "kdais_芒果.pdf"
    if not pdf.exists():
        pytest.skip("缺少芒果 fixture")

    rows, _ = parse_pdf(str(pdf))
    with_tolerance = [r for r in rows if r.tolerance_text]
    assert len(with_tolerance) > len(rows) * 0.9

    first = rows[0]
    assert first.pest_text == "炭疽病"
    assert "枯草桿菌" in first.product_text     # 排版空白要收乾淨
    assert first.dilution_text == "500"
    assert first.moa_text == "FRAC BM02"


# --------------------------------------------------------------------------
# 抽取與輪替
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("1.採收前14天停止施藥。", 14),
        ("2.採收前10天停止用藥。", 10),
        ("1.採收前35天停止施藥。2.本藥劑混合…可能發生藥害。", 35),
        ("全株均勻噴施。", None),
        ("施藥時稻田內應保持水深3～5公分，維持4～5天。", None),
        (None, None),
    ],
)
def test_extract_phi_days(text, expected):
    """抽不到就回 None —— 寧可讓前端顯示「未載明」，也不要猜一個數字害人。"""
    assert extract_phi_days(text) == expected


def test_extract_mix_warnings():
    text = (
        "1.採收前14天停止施藥。"
        "2.不可與強酸及強鹼性藥劑混合。"
        "3.本藥劑混合25%賓克隆可濕性粉劑等農藥可能發生藥害。"
    )
    warnings = extract_mix_warnings(text)
    assert len(warnings) == 2
    assert warnings[0] == "不可與強酸及強鹼性藥劑混合"
    assert "可能發生藥害" in warnings[1]
    assert extract_mix_warnings("採收前7天停止施藥。") == []


def test_parse_moa_codes():
    codes = parse_moa_codes(["FRAC 24;D3", "FRAC 34;unknown", "IRAC 14"])
    assert [str(c) for c in codes] == ["FRAC 24", "FRAC 34", "IRAC 14"]
    assert codes[0].label == "殺菌劑 24 組"
    assert codes[2].label == "殺蟲劑 14 組"
    assert parse_moa_codes([]) == []
    assert parse_moa_codes(["不明"]) == []


def test_rotation_advice_groups_by_mechanism():
    advice = build_rotation_advice([
        (1, "嘉賜克枯爛", ["FRAC 24;D3", "FRAC 34;unknown"]),
        (2, "克枯三賽唑", ["FRAC 16.1;I1", "FRAC 34;unknown"]),
        (3, "撲殺培丹", ["IRAC 14"]),
        (4, "無標示藥劑", []),
    ])
    groups = {str(g.code): g.product_names for g in advice.groups}
    # FRAC 34 兩支藥共用 -> 不該連續輪替
    assert set(groups["FRAC 34"]) == {"嘉賜克枯爛", "克枯三賽唑"}
    assert groups["IRAC 14"] == ["撲殺培丹"]
    assert advice.unknown_products == ["無標示藥劑"]
    assert "輪替" in advice.note


def test_rotation_advice_single_group_warns():
    advice = build_rotation_advice([
        (1, "A劑", ["FRAC 24;D3"]),
        (2, "B劑", ["FRAC 24;D3"]),
    ])
    assert advice.distinct_group_count == 1
    assert "尚無其他已辨識機制" in advice.note
    assert set(advice.groups[0].product_ids) == {1, 2}
