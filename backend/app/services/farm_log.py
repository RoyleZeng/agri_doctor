"""農事日誌的純函式：產銷履歷欄位完整度、預設標題、可修改期限。不碰資料庫，離線可測。"""
import re
from datetime import datetime, timedelta, timezone

KIND_LABELS = {
    "spray": "施藥",
    "fertilize": "施肥",
    "irrigate": "灌溉",
    "fieldwork": "田間作業",
    "harvest": "採收",
    "tillage": "整地",
    "scout": "巡田",
    "other": "其他",
}

# 紀錄要交給產銷履歷與補助查核：24 小時內可以更正打錯的內容，之後只能補備註。
EDIT_WINDOW = timedelta(hours=24)

# 《產銷履歷農產品驗證基準》第 5、6 條要求的紀錄項目，對應到日誌欄位。
# 作業時間（done_on）與場區（field_text）在輸入驗證就必填，不列在這裡。
# 規則要改就換版本號：已存檔的判定保留當時的版本，不回頭重算舊紀錄。
COMPLIANCE_RULE_VERSION = "tap-basis-art5-6/v1"
_MATERIAL_FIELDS = ("material_name", "material_supplier", "material_lot", "amount_value", "amount_unit", "area_text")
COMPLIANCE_FIELDS = {
    "spray": _MATERIAL_FIELDS,
    "fertilize": _MATERIAL_FIELDS,
    "harvest": ("crop_text", "area_text", "yield_value", "yield_unit"),
}


def compliance(kind: str, values: dict) -> dict:
    required = COMPLIANCE_FIELDS.get(kind, ())
    missing = [name for name in required if not str(values.get(name) or "").strip()]
    return {"rule_version": COMPLIANCE_RULE_VERSION, "required": list(required), "missing": missing}


def default_title(kind: str, *, material_name: str = "", dilution: str = "", fallback: str = "") -> str:
    """沒填標題時的顯示文字，例如「施藥 · 試驗藥劑 1,000 倍」「田間作業 · 規劃事項名稱」。"""
    detail = material_name.strip()
    ratio = dilution.strip()
    if detail and ratio:
        detail += f" {ratio}" if ratio.endswith("倍") else f" {ratio} 倍"
    detail = detail or fallback.strip()
    label = KIND_LABELS[kind]
    return f"{label} · {detail}" if detail else label


_PLAIN_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")


def plain_number(text: str) -> float | None:
    # 產量存的是農民原本的寫法；只有純數字（可含千分位逗號）才加總，「約 2 籃」這種不猜，列為無法加總。
    value = (text or "").replace(",", "").strip()
    return float(value) if _PLAIN_NUMBER.match(value) else None


def as_utc(value: datetime) -> datetime:
    # SQLite 讀回的時間不帶時區；寫入時一律是 UTC，所以補上 UTC 而不是當地時間。
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def within_edit_window(created_at: datetime, now: datetime) -> bool:
    return as_utc(now) - as_utc(created_at) <= EDIT_WINDOW
