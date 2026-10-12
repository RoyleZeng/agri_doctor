"""高雄區農業改良場「作物安全用藥資訊」解析。

清單頁
------
https://www.kdais.gov.tw/ws.php?id=7665 （兩頁，第二頁 &page=2）
每筆長這樣：

    <span class="headline">蓮霧病蟲害防治用藥摘要表 (114.12.22修訂)
      <a href="/redirect_files.php?id=65501&file_name=..."
         title="蓮霧病蟲害防治用藥摘要表 (114.12.22修訂).pdf (0.43MB)">PDF</a>
    </span>

標題裡就有作物名與修訂日期，可以直接拿來做增量下載判斷，
不必每次都把 35 份 PDF 重抓一遍。

PDF
---
各作物版面不一致，所以 extract_tables 之後用表頭文字模糊比對欄位，
對不上的列標成低信心，交給人工校對介面處理，不直接進推薦。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

_WS = re.compile(r"[\s　\xa0]+")

# 「蓮霧病蟲害防治用藥摘要表 (114.12.22修訂)」
_TITLE_RE = re.compile(
    r"^(?P<crop>.+?)"
    r"(?:病蟲害|病害|蟲害|草害)?"
    r"(?:防治)?用藥摘要表"
    r"\s*(?:[（(](?P<rev>[\d.]+)\s*修訂[）)])?",
)
_SIZE_RE = re.compile(r"\(([\d.]+\s*[KMG]?B)\)", re.I)


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", text).strip()


@dataclass
class KdaisDoc:
    title: str = ""
    crop_text: str | None = None
    revision_date: str | None = None    # 民國紀年原文「114.12.22」
    pdf_url: str = ""
    file_size: str | None = None

    def is_newer_than(self, other_revision: str | None) -> bool:
        """比對修訂日期決定要不要重抓。抓不到日期就一律重抓，寧可多做。"""
        if not self.revision_date or not other_revision:
            return True
        return _rev_key(self.revision_date) > _rev_key(other_revision)


def _rev_key(rev: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", rev)) or (0,)


def parse_document_list(html: str, base_url: str = "") -> list[KdaisDoc]:
    tree = HTMLParser(html)
    docs: list[KdaisDoc] = []
    seen: set[str] = set()

    for headline in tree.css("span.headline"):
        link = headline.css_first("a[href*='redirect_files.php']")
        if link is None:
            continue

        href = link.attributes.get("href", "")
        # headline 的文字含 PDF 這個連結文字，去掉才是純標題。
        title = _clean(headline.text(strip=True))
        if title.endswith("PDF"):
            title = _clean(title[:-3])
        if not title or href in seen:
            continue
        seen.add(href)

        doc = KdaisDoc(
            title=title,
            pdf_url=urljoin(base_url, href) if base_url else href,
        )
        if m := _TITLE_RE.match(title):
            doc.crop_text = _clean(m.group("crop")) or None
            doc.revision_date = m.group("rev")
        if m := _SIZE_RE.search(link.attributes.get("title", "")):
            doc.file_size = m.group(1)

        docs.append(doc)

    return docs


def find_next_page(html: str, base_url: str = "") -> str | None:
    tree = HTMLParser(html)
    for a in tree.css("a[href*='page=']"):
        if _clean(a.text(strip=True)) in ("下一頁", ">", "»"):
            href = a.attributes.get("href", "")
            return urljoin(base_url, href) if base_url else href
    return None


# --------------------------------------------------------------------------
# PDF 表格
# --------------------------------------------------------------------------

# 摘要表的欄位在各作物間相當一致，但用字略有差異（「防治病蟲害」／
# 「防治蟲害」／「防治病害」），所以做子字串比對。
# 順序有意義：先比對比較具體的欄名，避免「作用機制代碼」被「備註」那組吃掉。
_COLUMN_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("作用機制代碼", "作用機制"), "moa"),
    (("安全採收期", "採收期", "安全期"), "phi"),
    (("安全容許量", "容許量"), "tolerance"),
    (("每公頃施藥量", "每公頃", "施藥量", "使用量"), "dose"),
    (("稀釋倍數", "稀釋"), "dilution"),
    (("防治病蟲害", "防治蟲害", "防治病害", "病蟲害", "防治對象", "病害名稱"), "pest"),
    (("藥劑名稱", "推薦藥劑", "推薦用藥", "藥劑"), "product"),
    (("施藥方法", "使用方法", "施用方法"), "method"),
    (("備註", "備 註", "注意事項"), "remark"),
]

REQUIRED_COLUMNS = ("product", "pest")

# 表頭比對時把所有空白拿掉：PDF 常見「備 註」這種撐版的寫法。
_ALL_WS = re.compile(r"\s+")
# 中日韓文字之間的排版空白，例如「枯 草 桿 菌」-> 「枯草桿菌」。
_CJK_GAP = re.compile(r"(?<=[一-鿿])\s+(?=[一-鿿])")


def _nfkc(text: str) -> str:
    """統一 Unicode 表示法。

    這些 PDF 會混用 CJK 相容字，例如「容許量」的「量」實際是 U+F97E 而不是
    U+91CF，肉眼完全看不出來，但字串比對會直接失敗（實測「安全容許量(PPM)」
    這一欄就是這樣被漏掉的）。NFKC 會把相容字收斂回正規寫法，
    順便把全形英數轉成半形。
    """
    return unicodedata.normalize("NFKC", text)


def _header_label(cell: str | None) -> str:
    return _ALL_WS.sub("", _nfkc(cell or ""))


def normalize_cell(cell: str | None) -> str:
    """把 PDF 的排版空白收乾淨，但保留中英文之間必要的空格。"""
    text = _clean(_nfkc(cell or ""))
    prev = None
    while prev != text:            # 「枯 草 桿 菌」要跑兩輪才收完
        prev = text
        text = _CJK_GAP.sub("", text)
    return text


@dataclass
class KdaisRow:
    pest_text: str | None = None
    product_text: str | None = None
    moa_text: str | None = None
    dilution_text: str | None = None
    phi_text: str | None = None
    dose_text: str | None = None
    tolerance_text: str | None = None
    method_text: str | None = None
    remark: str | None = None
    raw_row: dict = field(default_factory=dict)
    page: int | None = None
    confident: bool = False


def match_columns(header: list[str | None]) -> dict[int, str]:
    """表頭欄位索引 -> 內部欄位名。認不出來的欄不收。"""
    mapping: dict[int, str] = {}
    for idx, cell in enumerate(header):
        label = _header_label(cell)
        if not label:
            continue
        for hints, name in _COLUMN_HINTS:
            if name in mapping.values():
                continue
            if any(_header_label(h) in label for h in hints):
                mapping[idx] = name
                break
    return mapping


def looks_like_header(row: list[str | None]) -> bool:
    mapping = match_columns(row)
    return len(mapping) >= 4 and all(c in mapping.values() for c in REQUIRED_COLUMNS)


class TableReader:
    """跨頁讀一份摘要表。

    續頁沒有表頭 —— 這是最容易出事的地方：如果每張表都硬要求表頭，
    多數頁面會被無聲丟掉，看起來像「這個作物只有第一頁有藥」。
    所以記住上一次看到的表頭對照，續頁只要欄數一致就沿用；
    欄數對不上就整張跳過並記一筆 warning，不硬猜。
    """

    def __init__(self) -> None:
        self.mapping: dict[int, str] | None = None
        self.width: int | None = None
        self.warnings: list[str] = []
        self._last_pest: str | None = None

    def read_table(self, table: list[list[str | None]], page_no: int) -> list[KdaisRow]:
        if not table:
            return []

        body = table
        header_idx = next(
            (i for i, row in enumerate(table[:3]) if looks_like_header(row)), None
        )
        if header_idx is not None:
            self.mapping = match_columns(table[header_idx])
            self.width = len(table[header_idx])
            body = table[header_idx + 1:]
            self._last_pest = None
        elif self.mapping is None:
            self.warnings.append(f"p{page_no}: 找不到表頭，整張跳過")
            return []

        assert self.mapping is not None
        rows: list[KdaisRow] = []

        for raw in body:
            # 續頁欄數可能因為 pdfplumber 多切出空欄而變多，允許多出來的尾欄，
            # 但少於表頭欄數就不敢對位。
            if self.width is not None and len(raw) < self.width:
                self.warnings.append(
                    f"p{page_no}: 欄數 {len(raw)} < 表頭 {self.width}，跳過該列"
                )
                continue
            if looks_like_header(raw):      # 續頁重印表頭
                continue

            values = {
                name: normalize_cell(raw[i])
                for i, name in self.mapping.items()
                if i < len(raw)
            }
            product = values.get("product", "")

            if not product:
                # 上一列的文字換行溢出到這一列，補回去而不是丟掉。
                extra = " ".join(v for v in values.values() if v)
                if rows and extra:
                    rows[-1].remark = " ".join(filter(None, [rows[-1].remark, extra]))
                continue

            # 摘要表用垂直合併儲存格表示「同一個病蟲害的多支藥」，
            # 續列的病蟲害欄是空的 —— 往下沿用上一個值。
            pest = values.get("pest") or self._last_pest
            if values.get("pest"):
                self._last_pest = values["pest"]

            row = KdaisRow(
                pest_text=pest or None,
                product_text=product,
                moa_text=values.get("moa") or None,
                dilution_text=values.get("dilution") or None,
                phi_text=values.get("phi") or None,
                dose_text=values.get("dose") or None,
                tolerance_text=values.get("tolerance") or None,
                method_text=values.get("method") or None,
                remark=values.get("remark") or None,
                raw_row={str(i): normalize_cell(c) for i, c in enumerate(raw)},
                page=page_no,
            )
            # 藥劑名 + 病蟲害 + 稀釋倍數 三者齊全才算高信心；
            # 其餘一律送人工校對，不直接進推薦。
            row.confident = bool(
                row.product_text and row.pest_text and row.dilution_text
            )
            rows.append(row)

        return rows


def parse_pdf_tables(
    tables_by_page: list[tuple[int, list[list[list[str | None]]]]],
) -> tuple[list[KdaisRow], list[str]]:
    """給定 [(頁碼, 該頁的 tables)]，回傳 (資料列, 警告)。不依賴 pdfplumber，好測試。"""
    reader = TableReader()
    rows: list[KdaisRow] = []
    for page_no, tables in tables_by_page:
        for table in tables:
            rows.extend(reader.read_table(table, page_no))
    return rows, reader.warnings


def parse_pdf(pdf_path: str) -> tuple[list[KdaisRow], list[str]]:
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        tables_by_page = [
            (i, page.extract_tables()) for i, page in enumerate(pdf.pages, start=1)
        ]
    return parse_pdf_tables(tables_by_page)
