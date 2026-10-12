"""抗藥性輪替：解析 FRAC / IRAC / HRAC 作用機制代碼並分組。

植物醫生開藥最怕的不是選錯藥，而是連續三次都開到同一個作用機制 ——
藥都合法、都有效，但抗藥性就這樣養出來了。PPM 有給代碼，只是散在
表格裡沒人會一欄一欄比，所以這裡把它整理成「同組」關係。

PPM 的代碼欄長這樣：
    FRAC BM02;BM02
    FRAC 24;D3
    IRAC 14
複方會有多個，例如 ['FRAC 1,B1', 'FRAC 3,G1']。
分號前是編號、分號後是分類字母；判斷是否同組時看編號。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SCHEME_RE = re.compile(r"\b(FRAC|IRAC|HRAC)\b", re.I)
_CODE_CLEAN = re.compile(r"[（(].*?[)）]")

SCHEME_LABEL = {
    "FRAC": "殺菌劑",
    "IRAC": "殺蟲劑",
    "HRAC": "除草劑",
}


@dataclass(frozen=True)
class MoaCode:
    scheme: str          # FRAC / IRAC / HRAC
    group: str           # "24"、"BM02"、"1,B1"

    def __str__(self) -> str:
        return f"{self.scheme} {self.group}"

    @property
    def label(self) -> str:
        return f"{SCHEME_LABEL.get(self.scheme, self.scheme)} {self.group} 組"


def parse_moa_codes(raw_codes: list[str] | None) -> list[MoaCode]:
    """把原始代碼字串轉成結構化代碼，順序保留、去重。"""
    out: list[MoaCode] = []
    seen: set[MoaCode] = set()

    for raw in raw_codes or []:
        text = _CODE_CLEAN.sub("", raw or "").strip()
        if not text:
            continue
        m = _SCHEME_RE.search(text)
        if not m:
            continue
        scheme = m.group(1).upper()
        rest = text[m.end():].strip(" :：")
        # 「BM02;BM02」前後常是同一組的兩種寫法，取分號前那段當代表。
        group = rest.split(";")[0].strip()
        if not group:
            continue
        code = MoaCode(scheme=scheme, group=group)
        if code not in seen:
            seen.add(code)
            out.append(code)

    return out


def shares_moa(a: list[MoaCode], b: list[MoaCode]) -> list[MoaCode]:
    """兩支藥共用的作用機制。非空 = 不該連續輪替。"""
    bs = set(b)
    return [code for code in a if code in bs]


@dataclass
class RotationGroup:
    code: MoaCode
    product_ids: list[int] = field(default_factory=list)
    product_names: list[str] = field(default_factory=list)


@dataclass
class RotationAdvice:
    groups: list[RotationGroup] = field(default_factory=list)
    unknown_products: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def distinct_group_count(self) -> int:
        return len(self.groups)


def build_rotation_advice(
    products: list[tuple[int, str, list[str]]],
) -> RotationAdvice:
    """products = [(product_id, 名稱, 原始作用機制代碼list)]。

    回傳以作用機制分組的結果，讓前端可以直接呈現「這組裡挑一支，
    下一次換另一組」。
    """
    groups: dict[MoaCode, RotationGroup] = {}
    unknown: list[str] = []

    for product_id, name, raw_codes in products:
        codes = parse_moa_codes(raw_codes)
        if not codes:
            unknown.append(name)
            continue
        # 複方會同時屬於多組，任一組相同就算共用機制。
        for code in codes:
            group = groups.setdefault(code, RotationGroup(code=code))
            if product_id not in group.product_ids:
                group.product_ids.append(product_id)
                group.product_names.append(name)

    ordered = sorted(
        groups.values(), key=lambda g: (g.code.scheme, len(g.product_ids)), reverse=False
    )
    advice = RotationAdvice(groups=ordered, unknown_products=unknown)

    if not products:
        advice.note = "尚無可用的已收錄用藥資料，無法比較作用機制；請先核對植保系統原始頁面。"
    elif not ordered:
        advice.note = "這次查到的已收錄藥劑未標示作用機制代碼，需查閱原始資料後再評估輪替。"
    elif len(ordered) == 1:
        advice.note = (
            f"這次查到且有代碼的藥劑均涉及 {ordered[0].code.label}，尚無其他已辨識機制可比較；"
            "請搭配非藥劑防治方法，並注意抗藥性風險。"
        )
    else:
        advice.note = (
            f"已辨識 {len(ordered)} 種作用機制。評估輪替時須確認前後藥劑沒有共用任何機制；"
            "同組藥劑不論商品名是否不同，抗藥性風險視為相同。"
        )

    if unknown:
        advice.note += f"（{len(unknown)} 支藥劑未標示作用機制，需個別確認）"

    return advice
