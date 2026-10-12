"""植物保護資訊系統（PPM）HTML 解析 —— 純函式，不碰網路，可離線測試。

頁面型態
--------
PLC0301.aspx  防治對象列表：作物 / 害物 / 學名 / 中文別名 / 英名，
              每列連到 PLC0101.aspx（連結藏在 data-url，href 是 '#'）
PLC0101.aspx  核心詳細頁：作物資訊 + 害物資訊 + 藥劑表

藥劑表欄位（依表頭文字定位，不寫死索引，因為「全選」與「容許量」欄
會依 EDT / Tolerance 開關增減）：
    藥劑名稱 / 作用機制代碼 / 每公頃施藥量 / 稀釋倍數(倍) /
    施藥方法 / 注意事項 / 容許量(ppm)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

# 表頭文字 -> 內部欄位名。做 startswith 比對，因為表頭帶排序箭頭「▼」。
_HEADER_MAP = {
    "藥劑名稱": "product",
    "作用機制代碼": "moa",
    "每公頃施藥量": "dose",
    "稀釋倍數": "dilution",
    "施藥方法": "method",
    "注意事項": "precautions",
    "容許量": "tolerance",
    "全選": "_checkbox",
}

_WS = re.compile(r"[\s　\xa0]+")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", text.replace("​", "")).strip()


def _node_text(node: Node | None, sep: str = " ") -> str:
    if node is None:
        return ""
    return _clean(node.text(separator=sep, strip=True))


@dataclass
class DrugRow:
    """藥劑表的一列。"""

    product_name: str = ""
    concentration: str | None = None
    formulation: str | None = None
    english_composition: str | None = None
    moa_codes: list[str] = field(default_factory=list)   # ["FRAC 24;D3", "FRAC 34;unknown"]
    moa_description: str | None = None
    dose_per_hectare: str | None = None
    dilution_text: str | None = None
    application_method: str | None = None
    precautions: str | None = None
    tolerance_text: str | None = None
    detail_url: str | None = None                        # PLC0102 藥劑詳細頁
    raw_cells: dict[str, str] = field(default_factory=dict)

    @property
    def dilution_value(self) -> int | None:
        """「1,500」-> 1500。抓不到數字就回 None（例如「稀釋倍數」欄留白）。"""
        if not self.dilution_text:
            return None
        m = re.search(r"[\d,]+", self.dilution_text)
        if not m:
            return None
        try:
            return int(m.group(0).replace(",", ""))
        except ValueError:
            return None


@dataclass
class PestDetail:
    """PLC0101 一頁的完整內容。"""

    crop_name: str = ""
    crop_scientific_name: str | None = None
    crop_description: str | None = None
    fda_category: str | None = None          # 從描述抽「適用之大類容許量為X」
    pest_name: str = ""
    pest_scientific_name: str | None = None
    pest_english_name: str | None = None
    pest_aliases: list[str] = field(default_factory=list)
    symptoms: str | None = None
    transmission: str | None = None
    cultural_control: str | None = None
    drugs: list[DrugRow] = field(default_factory=list)
    source_url: str | None = None


@dataclass
class PestListItem:
    """PLC0301 列表的一列。"""

    target: str = ""            # 防治對象，例如「水稻白葉枯病」
    crop: str = ""
    pest: str = ""
    scientific_name: str | None = None
    alias: str | None = None
    english_name: str | None = None
    detail_url: str | None = None


class PpmSchemaError(RuntimeError):
    """表頭與預期不符 —— 官網改版了，停下來讓人看，不要硬解。"""


# --------------------------------------------------------------------------
# PLC0301 防治對象列表
# --------------------------------------------------------------------------

def parse_pest_list(html: str, base_url: str = "") -> list[PestListItem]:
    tree = HTMLParser(html)
    items: list[PestListItem] = []
    seen: set[str] = set()

    for row in tree.css("tr"):
        # 奇偶列用不同 class（ContectTd / ContectTdX）做斑馬紋，兩種都要收。
        cells = row.css("td.ContectTd, td.ContectTdX")
        # 防治對象 / 作物 / 害物 / 學名 / 中文別名 / 英名 = 6 欄；
        # 部分列（如「稻穀檢疫處理」）尾欄留白，所以只要求前 3 欄。
        if len(cells) < 3:
            continue

        link = cells[0].css_first("a[data-url]")
        if link is None:
            continue
        detail = link.attributes.get("data-url", "")
        if "PLC0101.aspx" not in detail:
            continue

        values = [_node_text(c) for c in cells]
        item = PestListItem(
            target=_node_text(link),
            crop=values[1] if len(values) > 1 else "",
            pest=values[2] if len(values) > 2 else "",
            scientific_name=values[3] or None if len(values) > 3 else None,
            alias=values[4] or None if len(values) > 4 else None,
            english_name=values[5] or None if len(values) > 5 else None,
            detail_url=urljoin(base_url, detail) if base_url else detail,
        )
        key = f"{item.target}|{item.detail_url}"
        if key in seen:      # 桌機版與手機版各印一次同樣的表
            continue
        seen.add(key)
        items.append(item)

    return items


def extract_menu_links(html: str, page: str, base_url: str = "") -> list[str]:
    """抓出頁面上所有指向 `page`（例如 'PLC0301.aspx'）的 data-url。"""
    tree = HTMLParser(html)
    out: list[str] = []
    seen: set[str] = set()
    for node in tree.css("[data-url], a[href]"):
        url = (node.attributes.get("data-url") or node.attributes.get("href", "")).strip("'\" ")
        if page not in url or url in seen:
            continue
        seen.add(url)
        out.append(urljoin(base_url, url) if base_url else url)
    return out


def parse_crop_directory(html: str, base_url: str = "") -> list[PestListItem]:
    """PLC02 directly links every control target with href, not data-url."""
    tree = HTMLParser(html)
    by_url = {}
    for node in tree.css('a[href*="PLC0101.aspx"]'):
        target = _node_text(node)
        url = urljoin(base_url, node.attributes["href"])
        if not target:
            raise PpmSchemaError("作物目錄有未命名的防治對象")
        by_url[url] = PestListItem(target=target, detail_url=url)
    if not by_url:
        raise PpmSchemaError("完整作物目錄未找到防治對象 href，停止發布")
    return list(by_url.values())


# --------------------------------------------------------------------------
# PLC0101 詳細頁
# --------------------------------------------------------------------------

_FDA_CATEGORY_RE = re.compile(r"適用之大類容許量為([^\s。，,]+)")
_LABEL_RE = re.compile(r"^(學\s*名|英\s*名|別\s*名|病\s*徵|傳播途徑)\s*[:：]\s*(.*)$", re.S)


def parse_pest_detail(html: str, source_url: str | None = None) -> PestDetail:
    tree = HTMLParser(html)
    detail = PestDetail(source_url=source_url)

    # 桌機版容器；手機版 (#Mob_show) 是同樣內容的複本，只解析一次。
    scope = tree.css_first("#Com_show") or tree.body
    if scope is None:
        raise PpmSchemaError("PLC0101 頁面沒有 body")

    _parse_title(scope, detail)
    _parse_crop_table(scope, detail)
    _parse_pest_labels(scope, detail)
    _parse_cultural_control(tree, detail)
    if not detail.crop_name or not detail.pest_name:
        raise PpmSchemaError("詳細頁未辨識作物與防治對象，不能判為查無")
    if tree.css_first("#table1") is None and tree.css_first("#drug_show") is None:
        raise PpmSchemaError("詳細頁缺少防治方法區，不能判為查無")
    detail.drugs = parse_drug_table(tree)

    return detail


def _parse_title(scope: Node, detail: PestDetail) -> None:
    """<div class="CropNo_title"><span onClick>水稻</span>    白葉枯病</div>"""
    title = scope.css_first(".CropNo_title")
    if title is None:
        return
    crop_node = title.css_first("span")
    if crop_node is not None:
        detail.crop_name = _node_text(crop_node)
    full = _node_text(title)
    detail.pest_name = _clean(full[len(detail.crop_name):]) if detail.crop_name else full


def _parse_crop_table(scope: Node, detail: PestDetail) -> None:
    """#CropId_detail 的 作物名稱 / 學名 / 描述 三列。"""
    block = scope.css_first("#CropId_detail")
    if block is None:
        return
    for row in block.css("tr"):
        cells = row.css("td")
        if len(cells) < 2:
            continue
        label, value = _node_text(cells[0]), _node_text(cells[1])
        if label == "作物名稱" and value:
            detail.crop_name = detail.crop_name or value
        elif label == "學名":
            detail.crop_scientific_name = value or None
        elif label == "描述":
            detail.crop_description = value or None
            if value and (m := _FDA_CATEGORY_RE.search(value)):
                detail.fda_category = m.group(1)


def _parse_pest_labels(scope: Node, detail: PestDetail) -> None:
    """#ul1 底下每個 <li> 是「標籤: 內容」。"""
    block = scope.css_first("ul#ul1") or scope.css_first("ul.Harm-show ul ul")
    if block is None:
        return
    for li in block.css("li"):
        text = _node_text(li)
        m = _LABEL_RE.match(text)
        if not m:
            continue
        label = _WS.sub("", m.group(1))
        value = _clean(m.group(2))
        if not value:
            continue
        if label == "學名":
            detail.pest_scientific_name = value
        elif label == "英名":
            detail.pest_english_name = value
        elif label == "別名":
            detail.pest_aliases = [a for a in re.split(r"[、,，/]", value) if a.strip()]
        elif label == "病徵":
            detail.symptoms = value
        elif label == "傳播途徑":
            detail.transmission = value


def _parse_cultural_control(tree: HTMLParser, detail: PestDetail) -> None:
    """#drug_show 開頭的「防治方法：…」是非藥劑防治，到表格為止。"""
    block = tree.css_first("#drug_show")
    if block is None:
        return
    text = block.text(separator="\n", strip=True)
    if "防治方法" not in text:
        return
    body = text.split("防治方法", 1)[1].lstrip("：: \n")
    # 表格內容也在同一個 div 底下，用表頭當終止點。
    for stop in ("全選", "藥劑名稱"):
        if stop in body:
            body = body.split(stop, 1)[0]
    detail.cultural_control = body.strip() or None


def _header_fields(table: Node) -> list[str]:
    head = table.css_first("thead tr") or table.css_first("tr")
    if head is None:
        raise PpmSchemaError("藥劑表沒有表頭")

    fields: list[str] = []
    for th in head.css("th"):
        label = _node_text(th)
        matched = next(
            (name for prefix, name in _HEADER_MAP.items() if label.startswith(prefix)),
            None,
        )
        fields.append(matched or f"_unknown:{label}")

    if "product" not in fields:
        raise PpmSchemaError(f"藥劑表表頭找不到「藥劑名稱」，實際為 {fields}")
    return fields


def parse_drug_table(tree: HTMLParser) -> list[DrugRow]:
    # A page can contain cultivar tables and several application tables.
    # Keep treatment context, including low volume, UAV and nursery sections.
    scope = tree.css_first("#drug_show") or tree
    rows: list[DrugRow] = []
    for table in scope.css("table"):
        header = table.css_first("thead tr") or table.css_first("tr")
        labels = [_node_text(h) for h in header.css("th")] if header else []
        if not any(label.startswith("藥劑名稱") for label in labels):
            if any(label.startswith(prefix) for label in labels for prefix in ("稀釋倍數", "每公頃施藥量", "作用機制代碼")):
                raise PpmSchemaError(f"疑似藥劑表的名稱欄改版：{labels}")
            continue
        fields = _header_fields(table)
        section = table.parent
        while section is not None and section.tag != "body" and section.attributes.get("id") != "drug_show":
            if section.attributes.get("style", "").replace(" ", "").startswith("width:100%"):
                break
            section = section.parent
        context = ""
        if section is not None and section.attributes.get("id") != "drug_show":
            prefix = section.html.split('<table', 1)[0]
            context = _clean(HTMLParser(prefix).text(separator=" ", strip=True))
        for tr in table.css("tr"):
            if tr.css_first("th") is not None:
                continue
            cells = tr.css("td")
            if not cells:
                continue
            if len(cells) != len(fields):
                raise PpmSchemaError("藥劑資料列欄數不符，停止發布以免漏列")
            cell_map = {name: cell for name, cell in zip(fields, cells) if not name.startswith("_")}
            if "product" not in cell_map:
                raise PpmSchemaError("藥劑資料列缺名稱欄")
            row = _build_drug_row(cell_map["product"], cell_map)
            if not row.product_name:
                raise PpmSchemaError("藥劑名稱無法解析，不能略過並宣告完整")
            if context:
                row.raw_cells["section_context"] = context
                row.application_method = context + ("\n" + row.application_method if row.application_method else "")
            rows.append(row)
    return rows


_FORMULATION_HINT = re.compile(r"劑$|粉$|水$")


def _build_drug_row(product_cell: Node, cell_map: dict[str, Node]) -> DrugRow:
    row = DrugRow()
    row.raw_cells = {k: _node_text(v) for k, v in cell_map.items()}

    _parse_product_cell(product_cell, row)

    if (moa := cell_map.get("moa")) is not None:
        _parse_moa_cell(moa, row)

    row.dose_per_hectare = _node_text(cell_map.get("dose")) or None
    row.dilution_text = _node_text(cell_map.get("dilution")) or None
    row.application_method = _node_text(cell_map.get("method")) or None
    row.precautions = _node_text(cell_map.get("precautions")) or None
    row.tolerance_text = _node_text(cell_map.get("tolerance")) or None
    return row


def _parse_product_cell(cell: Node, row: DrugRow) -> None:
    """「10% <a>撲殺培丹</a></br>粒劑</br>(probenazole + cartap)」

    <a> 裡是藥劑名；它前面的是含量，後面依序是劑型與英文成分組合。
    """
    link = cell.css_first("a")
    if link is not None:
        row.product_name = _node_text(link)
        row.detail_url = link.attributes.get("data-url")

    parts = [_clean(p) for p in cell.text(separator="\n", strip=True).split("\n")]
    parts = [p for p in parts if p]

    if row.product_name:
        try:
            idx = parts.index(row.product_name)
        except ValueError:
            idx = next(
                (i for i, p in enumerate(parts) if row.product_name in p), len(parts)
            )
        before, after = parts[:idx], parts[idx + 1:]
    else:
        # 沒有連結的列：整格當名稱，靠括號切出英文成分。
        before, after = [], parts[1:]
        row.product_name = parts[0] if parts else ""

    if before:
        row.concentration = " ".join(before)

    for part in after:
        if part.startswith("(") or part.startswith("（"):
            row.english_composition = part.strip("()（）")
        elif row.formulation is None:
            row.formulation = part


def _parse_moa_cell(cell: Node, row: DrugRow) -> None:
    """複方會有多個 tooltip，例如 FRAC 24;D3 「+」 FRAC 34;unknown。"""
    codes: list[str] = []
    descriptions: list[str] = []

    for tip in cell.css("div.tooltip"):
        inner = tip.css_first("span.tooltiptext")
        description = _node_text(inner)
        if inner is not None:
            inner.decompose()
        code = _node_text(tip)
        if code:
            codes.append(code)
        if description:
            descriptions.append(description)

    if not codes:
        text = _node_text(cell)
        if text:
            codes = [c.strip() for c in text.split("+") if c.strip()]

    row.moa_codes = codes
    row.moa_description = " | ".join(descriptions) or None


_MOA_CODE_RE = re.compile(r"\b(FRAC|IRAC|HRAC)\s+([^\s;]+)", re.I)


def split_moa_code(code_text: str) -> tuple[str | None, str | None]:
    """「FRAC 24;D3」-> ('FRAC', '24;D3')。認不出來就回 (None, 原文)。"""
    m = _MOA_CODE_RE.search(code_text or "")
    if not m:
        return None, (code_text or None)
    scheme = m.group(1).upper()
    rest = code_text[m.start(2):].strip()
    return scheme, rest or None
