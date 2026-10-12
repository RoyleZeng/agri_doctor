"""食藥署農藥殘留容許量 HTML 解析 —— 純函式，可離線測試。

頁面
----
PesticideList.aspx?nodeID=520&tk=<分頁籤>&p=<頁碼>

分頁籤 tk
    1  農藥殘留容許量            8,274 筆 / 828 頁（每頁 10 筆）
    7  外源性農藥殘留容許量
    2  得免訂定容許量之農藥
    3  公告禁用農藥              ← 推薦用藥前的硬性安全閘門

分頁是單純的 GET `&p=N`，不需要 __VIEWSTATE postback。
（站方的「匯出Excel」是 postback，實測會被擋掉並回「找不到網路資源」，
  所以走逐頁 GET，慢但穩。）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from selectolax.parser import HTMLParser

TAB_MRL = 1
TAB_EXOGENOUS = 7
TAB_EXEMPT = 2
TAB_BANNED = 3

_WS = re.compile(r"[\s　\xa0]+")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", text).strip()


class FdaSchemaError(RuntimeError):
    """表頭與預期不符 —— 停下來讓人看，不要硬解。"""


@dataclass
class MrlRow:
    seq: str = ""
    international_name: str = ""   # 國際普通名稱，例如 "Zoxamide"
    common_name: str = ""          # 普通名稱（中文），例如 "座賽胺"
    crop_category: str = ""        # 作物類別，例如 "米類" / "柿"
    limit_text: str = ""           # 原文，可能是 "0.01" 或 "不得檢出"
    note: str | None = None        # 備註，例如 "殺菌劑"

    @property
    def limit_ppm(self) -> Decimal | None:
        """數值化容許量。「不得檢出」等文字回 None，由前端顯示原文。"""
        m = re.search(r"\d+(?:\.\d+)?", self.limit_text or "")
        if not m:
            return None
        try:
            return Decimal(m.group(0))
        except InvalidOperation:
            return None


@dataclass
class BannedRow:
    seq: str = ""
    name: str = ""
    english_name: str = ""
    banned_manufacture_date: str | None = None   # 禁止製造、輸入日期（國字紀年原文）
    banned_sale_date: str | None = None          # 禁止銷售、使用日期


@dataclass
class ExemptRow:
    seq: str = ""
    name: str = ""
    english_name: str = ""


@dataclass
class ListPage:
    total_rows: int | None = None
    total_pages: int | None = None
    mrl_rows: list[MrlRow] = field(default_factory=list)
    banned_rows: list[BannedRow] = field(default_factory=list)
    exempt_rows: list[ExemptRow] = field(default_factory=list)


_TOTAL_ROWS_RE = re.compile(r"共有\s*<span[^>]*>(\d[\d,]*)</span>")
_TOTAL_PAGES_RE = re.compile(r"共有\s*(\d+)\s*頁")

# 表頭文字 -> MrlRow 欄位
_MRL_HEADERS = {
    "項次": "seq",
    "國際普通名稱": "international_name",
    "普通名稱": "common_name",
    "作物類別": "crop_category",
    "容許量": "limit_text",
    "備註": "note",
}
_BANNED_HEADERS = {
    "項次": "seq",
    "農藥名稱": "name",
    "英文名稱": "english_name",
    "禁止製造": "banned_manufacture_date",
    "禁止銷售": "banned_sale_date",
}


def parse_totals(html: str) -> tuple[int | None, int | None]:
    rows = pages = None
    if m := _TOTAL_ROWS_RE.search(html):
        rows = int(m.group(1).replace(",", ""))
    if m := _TOTAL_PAGES_RE.search(html):
        pages = int(m.group(1))
    return rows, pages


def _header_fields(table, mapping: dict[str, str]) -> list[str | None]:
    ths = table.css("th")
    if not ths:
        raise FdaSchemaError("清單表格沒有表頭")
    fields: list[str | None] = []
    for th in ths:
        label = _clean(th.text(strip=True))
        fields.append(
            next((f for prefix, f in mapping.items() if label.startswith(prefix)), None)
        )
    return fields


def _iter_rows(table, fields: list[str | None]):
    for tr in table.css("tr"):
        if tr.css_first("th") is not None:
            continue
        cells = tr.css("td")
        if len(cells) < 2:
            continue
        yield {
            name: _clean(cell.text(strip=True))
            for name, cell in zip(fields, cells)
            if name
        }


def parse_list_page(html: str, tab: int = TAB_MRL) -> ListPage:
    tree = HTMLParser(html)
    table = tree.css_first("table.rwd-table")
    page = ListPage()
    page.total_rows, page.total_pages = parse_totals(html)
    if table is None:
        return page

    if tab in (TAB_MRL, TAB_EXOGENOUS):
        fields = _header_fields(table, _MRL_HEADERS)
        if "crop_category" not in fields or "limit_text" not in fields:
            raise FdaSchemaError(
                f"容許量表頭與預期不符：{[th.text(strip=True) for th in table.css('th')]}"
            )
        page.mrl_rows = [
            MrlRow(**{**{"note": None}, **row})
            for row in _iter_rows(table, fields)
            if row.get("international_name") or row.get("common_name")
        ]
    elif tab == TAB_BANNED:
        fields = _header_fields(table, _BANNED_HEADERS)
        page.banned_rows = [
            BannedRow(**row) for row in _iter_rows(table, fields) if row.get("name")
        ]
    elif tab == TAB_EXEMPT:
        fields = _header_fields(table, _BANNED_HEADERS)
        page.exempt_rows = [
            ExemptRow(
                seq=row.get("seq", ""),
                name=row.get("name", ""),
                english_name=row.get("english_name", ""),
            )
            for row in _iter_rows(table, fields)
            if row.get("name")
        ]

    return page


def page_url(base_url: str, *, tab: int = TAB_MRL, page: int = 1) -> str:
    """base_url 已含 ?nodeID=520，這裡只補 tk / p。"""
    sep = "&" if "?" in base_url else "?"
    parts = [f"tk={tab}"]
    if page > 1:
        parts.append(f"p={page}")
    return f"{base_url}{sep}{'&'.join(parts)}"
