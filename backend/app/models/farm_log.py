"""農事日誌：記的是「實際做了什麼」，跟規劃（打算做什麼）分開存。

這份紀錄之後要交給產銷履歷驗證與補助查核，所以：
* 田區、作物、資材、病蟲害名稱一律存輸入當下的快照，改規劃或上游資料更新都不回寫。
* 外鍵（規劃、事項、處方、病蟲害）只是「從哪裡來」的連結；上游刪了就清成空，日誌本身不跟著消失。
"""
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.planning import uid

LOG_KINDS = ("spray", "fertilize", "irrigate", "fieldwork", "harvest", "tillage", "scout", "other")
PHI_SOURCES = ("prescription", "registration", "none")


class FarmWorkLog(Base, TimestampMixin):
    __tablename__ = "farm_work_logs"
    __table_args__ = (
        CheckConstraint("kind IN (" + ",".join(f"'{k}'" for k in LOG_KINDS) + ")", name="ck_work_log_kind"),
        CheckConstraint("phi_source IN (" + ",".join(f"'{s}'" for s in PHI_SOURCES) + ")", name="ck_work_log_phi_source"),
        Index("ix_work_log_farmer_done", "farmer_id", "done_on"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"))
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("farm_plans.id", ondelete="SET NULL"), index=True)
    # 一個規劃事項最多對應一筆日誌；唯一索引也是同一事項被重複送出時的最後防線。
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("farm_events.id", ondelete="SET NULL"), index=True, unique=True
    )
    prescription_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("prescription_items.id", ondelete="SET NULL"), index=True
    )
    # 由精準模式建議「已執行」而來；建議被示範產生器重建時只清連結，日誌留著。
    recommendation_id: Mapped[str | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL", use_alter=True, name="fk_farm_work_logs_recommendation"),
        index=True,
    )
    # 田區主檔連結；field_text 仍是輸入當下的快照。安全採收期核對兩邊都有 field_id 才用它，否則退回名稱。
    field_id: Mapped[str | None] = mapped_column(ForeignKey("farm_fields.id", ondelete="SET NULL"), index=True)
    # 手動記施藥時從藥劑登記挑選，作用機制與安全採收期才有可靠來源（不用藥名反查）。
    # 正規化會刪除舊登記列，所以外鍵只當連結、會被清空；當下取到的值完整存在快照裡。
    registration_id: Mapped[int | None] = mapped_column(ForeignKey("registrations.id", ondelete="SET NULL"), index=True)
    registration_snapshot: Mapped[dict | None] = mapped_column(JSON)

    done_on: Mapped[date] = mapped_column(Date)
    done_time: Mapped[str | None] = mapped_column(String(5))
    kind: Mapped[str] = mapped_column(String(16))
    field_text: Mapped[str] = mapped_column(String(160))
    crop_text: Mapped[str] = mapped_column(String(80), default="")
    # 自動產生的標題最長是「標籤 · 藥名 稀釋倍數 倍」，欄位留寬，不截斷。
    title: Mapped[str] = mapped_column(String(300))

    material_name: Mapped[str] = mapped_column(String(200), default="")
    material_supplier: Mapped[str] = mapped_column(String(120), default="")
    material_lot: Mapped[str] = mapped_column(String(80), default="")
    dilution: Mapped[str] = mapped_column(String(40), default="")
    # 用量、產量存字串：保留農民原本的寫法（「0.3~0.5」「約 2 桶」），不為了算數字而改寫。
    amount_value: Mapped[str] = mapped_column(String(40), default="")
    amount_unit: Mapped[str] = mapped_column(String(20), default="")
    area_text: Mapped[str] = mapped_column(String(60), default="")
    yield_value: Mapped[str] = mapped_column(String(40), default="")
    yield_unit: Mapped[str] = mapped_column(String(20), default="")
    yield_destination: Mapped[str] = mapped_column(String(120), default="")
    labor_text: Mapped[str] = mapped_column(String(120), default="")

    # 巡田觀察到的病蟲害；區域病蟲害地圖彙整要用（規格第三部分），名稱存快照。
    pest_id: Mapped[int | None] = mapped_column(ForeignKey("pests.id", ondelete="SET NULL"))
    pest_text: Mapped[str] = mapped_column(String(160), default="")

    note: Mapped[str] = mapped_column(Text, default="")
    # 安全採收期快照，來源與作用機制同一條路（處方或挑選的登記）。處方項目日後被刪，
    # 已交出去的核對結果也不能變成「無法判斷」。none＝沒有依據，phi_days 為 None，不填 0。
    phi_days: Mapped[int | None] = mapped_column(Integer)
    phi_source: Mapped[str] = mapped_column(String(16), default="none", server_default="none")
    # 產銷履歷缺哪些欄位，存檔當下算好並記規則版本；規則改版不回頭改舊紀錄的判定。
    compliance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"))


class FarmLogPhoto(Base):
    """照片走受權限保護的端點（實作第 2 步），不放 static/：誰能看農場才看得到照片。"""

    __tablename__ = "farm_log_photos"
    __table_args__ = (CheckConstraint("taken_at_source IN ('exif','upload')", name="ck_log_photo_taken_at_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    log_id: Mapped[str] = mapped_column(ForeignKey("farm_work_logs.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(300))
    thumb_key: Mapped[str] = mapped_column(String(300))
    bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # EXIF 拍攝時間；沒有 EXIF 時用上傳時間，補助查核要能證明何時拍的。
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 拍攝時間從哪來。exif＝相機記錄，才算得上「何時拍的」的證據；upload＝EXIF 沒有可信時間，退回上傳時間。
    # 匯出附錄據此印「上傳時間（無拍攝資訊）」，退回的時間不能看起來跟真的拍攝時間一樣，否則等於替證據灌水。
    taken_at_source: Mapped[str] = mapped_column(String(8), default="upload")
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    caption: Mapped[str] = mapped_column(String(200), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # 刪照片只軟刪，實體檔交給清理排程；紀錄要交給政府，不能手滑就少了證據。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


FARM_LOG_TABLES = [FarmWorkLog.__table__, FarmLogPhoto.__table__]
