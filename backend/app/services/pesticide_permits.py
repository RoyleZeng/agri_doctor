"""農藥許可證：把農業部開放資料的原始欄位整理成畫面用得上的形狀。

純函式，測試跑存下來的 payload，不連資料庫也不連外網。

為什麼要有這一層：上游的日期是民國年、而且同一份資料裡兩個日期欄位用不同分隔符
（`ExpireDate` 是 `077-10-12`、`RevocationDate` 是 `079/05/03`）；
撤銷與否要看 `RevocationType` 有沒有值，不是看有沒有 `RevocationDate`。
這些判斷錯了，畫面就會把撤銷的藥說成有效的——所以規則集中在這裡並且測得到。
"""
from datetime import date
from functools import lru_cache
from pathlib import Path
import json
import re

# 民國年：三位數年＋月日，分隔符可能是 - 或 /，也可能完全沒有。
_ROC = re.compile(r"^(\d{2,3})[-/.]?(\d{2})[-/.]?(\d{2})$")

# 實際資料裡只出現這四種（查過 `select distinct Permit`）。
# 「原」開頭的是原體（未調配的有效成分），農民買不到、也不會拿去噴——
# 標出來才不會讓人以為那是一支可以用的成品藥。
PERMIT_KINDS = {"農藥製": "製造", "農藥進": "輸入", "農藥原製": "原體製造", "農藥原進": "原體輸入"}
RAW_MATERIAL_PERMITS = ("農藥原製", "農藥原進")
#: 有效期限在這個天數內就標成「即將到期」。
EXPIRING_DAYS = 365
#: 廠商卡上最多列幾個作用機制。
TOP_MOA_GROUPS = 8


def roc_date(value: str | None) -> str | None:
    """民國年字串轉西元 ISO 日期。看不懂就回 `None`，不要猜也不要填今天。"""
    text = (value or "").strip()
    if not text:
        return None
    match = _ROC.match(text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year + 1911, month, day).isoformat()
    except ValueError:
        return None  # 上游出現 077-13-45 這種值時照樣回 None，不要拋例外把整頁弄掉


def moa_codes(payload: dict) -> list[str]:
    """FRAC／IRAC／HRAC 三欄合成一份代碼清單，格式與 `resistance.parse_moa_codes` 吃得下的一致。"""
    out = []
    for scheme in ("FRAC", "IRAC", "HRAC"):
        group = (payload.get(scheme) or "").strip()
        if group:
            out.append(f"{scheme} {group}")
    return out


def permit_row(payload: dict) -> dict:
    """一列許可證。欄位缺就是缺，不補預設值。"""
    revocation_type = (payload.get("RevocationType") or "").strip()
    permit = (payload.get("Permit") or "").strip()
    number = (payload.get("PermitNumber") or "").strip()
    return {
        "permit": permit,
        "permit_number": number,
        # 「農藥製字第 12345 號」是植物醫生與農民實際講出口的形式；兩段分開存，顯示時再組。
        "permit_text": f"{permit}字第{number}號" if permit and number else (number or ""),
        "permit_kind": PERMIT_KINDS.get(permit, ""),
        # 原體不是成品藥：畫面要標出來，不然會被當成「這家有賣這支藥」。
        "raw_material": permit in RAW_MATERIAL_PERMITS,
        "vendor": (payload.get("Vendor") or "").strip(),
        "chinese_name": (payload.get("ChineseName") or "").strip(),
        "en_name": (payload.get("EnName") or "").strip(),
        "brand_name": (payload.get("BrandName") or "").strip(),
        "contents": (payload.get("contents") or "").strip(),
        "form_code": (payload.get("formCode") or "").strip(),
        "pesticide_code": (payload.get("PesticideCode") or "").strip(),
        "expire_on": roc_date(payload.get("ExpireDate")),
        # 撤銷看的是 RevocationType 有沒有值：有些撤銷紀錄沒有日期，只看日期會漏掉。
        "revoked": bool(revocation_type),
        "revocation_type": revocation_type,
        "revoked_on": roc_date(payload.get("RevocationDate")),
        "moa_codes": moa_codes(payload),
        "detail_url": (payload.get("ScopeOfUse") or "").strip(),
    }


def permit_status(row: dict, today: date) -> str:
    """`revoked` / `expired` / `expiring` / `active` / `unknown`（有效期限看不懂時）。"""
    if row["revoked"]:
        return "revoked"
    if not row["expire_on"]:
        return "unknown"
    expire = date.fromisoformat(row["expire_on"])
    if expire < today:
        return "expired"
    return "expiring" if (expire - today).days <= EXPIRING_DAYS else "active"


def vendor_summary(rows: list[dict], today: date) -> dict:
    """廠商卡上的數字。

    `unknown`（有效期限看不懂）單獨算一類，不併進 active——
    併進去等於宣稱它有效，而我們其實不知道。
    """
    counts = {"active": 0, "expiring": 0, "expired": 0, "revoked": 0, "unknown": 0}
    groups: dict[str, int] = {}
    for row in rows:
        counts[permit_status(row, today)] += 1
        if not row["revoked"]:
            for code in row["moa_codes"]:
                groups[code] = groups.get(code, 0) + 1
    ranked = sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "total": len(rows),
        **counts,
        # 一家大廠可能橫跨七十幾個機制；全部列出來沒有人會讀，取前幾名並附上總數。
        "moa_groups": [dict(code=code, count=n) for code, n in ranked[:TOP_MOA_GROUPS]],
        "moa_group_total": len(ranked),
    }


def vendor_names(rows: list[dict]) -> list[dict]:
    """廠商 typeahead 用的清單：名稱＋張數，張數多的在前。"""
    counts: dict[str, int] = {}
    for row in rows:
        name = row["vendor"]
        if name:
            counts[name] = counts.get(name, 0) + 1
    return [dict(vendor=name, permits=n) for name, n in
            sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def match_permit(rows: list[dict], *, pesticide_code: str = "", permit_number: str = "") -> dict | None:
    """藥劑卡要顯示的那一張許可證。

    **只做精確比對**（`PesticideCode` 或 `PermitNumber`），不拿藥名去對——
    中文沒有詞界，「克枯爛」會命中複方「嘉賜克枯爛」，對錯了就是把別家的許可證
    掛到這支藥上（AGENTS.md 的既有原則）。對不到就回 `None`，畫面不顯示這一行。

    同一個成分代碼可能有多張（不同廠商、不同劑型）：挑還有效、期限最遠的那張，
    因為那是「現在買得到」最可能的一張；全都撤銷就回最近撤銷的，讓畫面能說明為什麼不能用。
    """
    code = (pesticide_code or "").strip()
    number = (permit_number or "").strip()
    if not code and not number:
        return None
    hits = [r for r in rows
            if (number and r["permit_number"] == number) or (code and not number and r["pesticide_code"] == code)]
    if not hits:
        return None
    live = [r for r in hits if not r["revoked"]]
    if live:
        return max(live, key=lambda r: (r["expire_on"] or "", r["permit_number"]))
    return max(hits, key=lambda r: (r["revoked_on"] or "", r["permit_number"]))


# ---- 配方層級對應（藥劑卡的「此配方 N 張許可證」用） ----
#
# 為什麼是**配方層級**而不是挑一張許可證：同一個配方（成分＋含量＋劑型）在市面上
# 常常同時有好幾家廠商的登記。挑其中一家印在處方卡上，等於替植物醫生指定了品牌，
# 那既不準也不是這個平台該做的事。所以只回「有幾張、幾家、幾張已撤銷」，
# 要看是哪幾張就去農藥廠商查詢。
#
# 對應鍵用 AGENTS.md 既有的藥劑識別三元組：英文成分名＋含量＋劑型。
# **三項任一項正規化失敗就整個不對應**——寧可不顯示，也不要用半個鍵去撈出別的配方。

_FORM_PATH = Path(__file__).resolve().parents[1] / "data" / "formulation_codes.json"
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_SPLIT_INGREDIENTS = re.compile(r"\s*[+＋/]\s*")


@lru_cache(maxsize=1)
def formulation_table() -> dict[str, str]:
    """中文劑型／代碼 → 代碼。表在 `app/data/formulation_codes.json`，含來源出處。"""
    raw = json.loads(_FORM_PATH.read_text(encoding="utf-8"))
    table: dict[str, str] = {}
    for code, names in raw["codes"].items():
        table[code.upper()] = code.upper()
        for name in names:
            table[name] = code.upper()
    return table


def normalize_formulation(value: str | None) -> str | None:
    """劑型 → 兩字母代碼。查不到就回 `None`，不要猜。"""
    text = "".join((value or "").split())
    if not text:
        return None
    return formulation_table().get(text) or formulation_table().get(text.upper())


def normalize_contents(value: str | None) -> str | None:
    """含量 → 百分比字串（小數點後三位）。

    兩邊寫法不同：植保是 `250 g/L (50% w/v)` 或 `50%`，農業部是 `25.000  (%)`。
    共同點是都帶得出一個百分比，所以一律化成百分比再比。
    **沒有百分比就回 `None`**——像 `1X10^9 CFU/g` 這種活菌數沒辦法換算成百分比，
    硬換就是編一個數字出來。
    """
    text = (value or "").strip()
    if not text:
        return None
    match = _PERCENT.search(text)
    if not match:
        # `25.000  (%)` 這種括號在數字後面的寫法，百分號與數字之間隔著括號。
        bare = re.match(r"^\s*(\d+(?:\.\d+)?)\s*\(\s*%\s*\)\s*$", text)
        if not bare:
            return None
        match = bare
    try:
        return f"{float(match.group(1)):.3f}"
    except ValueError:
        return None


def normalize_ingredient(value: str | None) -> str | None:
    """英文成分名 → 正規化後的字串。複方排序後再接起來，`A + B` 與 `B + A` 是同一個配方。"""
    text = (value or "").strip().lower()
    if not text:
        return None
    parts = [re.sub(r"[^a-z0-9]+", "", part) for part in _SPLIT_INGREDIENTS.split(text)]
    parts = [p for p in parts if p]
    return "+".join(sorted(parts)) if parts else None


def formula_key(*, ingredient: str | None, contents: str | None, formulation: str | None) -> str | None:
    """三元組的對應鍵。任一項正規化失敗就回 `None`，整行不顯示。"""
    parts = (normalize_ingredient(ingredient), normalize_contents(contents), normalize_formulation(formulation))
    return "|".join(parts) if all(parts) else None


def permit_formula_key(row: dict) -> str | None:
    return formula_key(ingredient=row["en_name"], contents=row["contents"], formulation=row["form_code"])


def formula_summary(rows: list[dict], key: str) -> dict:
    """這個配方在許可證裡的樣子：幾張、幾家廠商、幾張已撤銷。

    **不回單一廠商或單一到期日**：同配方多家廠商是常態，挑一家等於替人指定品牌。
    """
    hits = [r for r in rows if permit_formula_key(r) == key]
    vendors = sorted({r["vendor"] for r in hits if r["vendor"]})
    return {
        "key": key,
        "permits": len(hits),
        "vendors": len(vendors),
        "revoked": sum(1 for r in hits if r["revoked"]),
        # 全部撤銷是「這個配方現在沒有有效登記」，畫面要講得出這件事。
        "all_revoked": bool(hits) and all(r["revoked"] for r in hits),
    }
