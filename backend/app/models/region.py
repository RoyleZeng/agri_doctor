"""區域作物與病蟲害地圖：彙整結果、主動調查登錄、警戒門檻。

`region_daily_stats` 是彙整出來的結果，不是原始資料——由 `services/region_stats.py` 的純函式從
診斷案件、巡田日誌、規劃、主動調查四個來源算出來，手動「重新彙整」時整批 upsert。彙整永遠只算
`share_region=true` 的農場，而且同一格貢獻的農場數 < 3 時 `count`／`area_ha`／`metric` 一律 None——
這不是「隱藏太小的數字」，是避免用「只有一戶種這個」反推出特定農場種了什麼、有什麼病蟲害。
"""
from datetime import date

from sqlalchemy import CheckConstraint, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.planning import uid

REGION_SOURCES = ("consult", "scout", "plan", "survey")


class RegionDailyStat(Base):
    __tablename__ = "region_daily_stats"
    __table_args__ = (
        CheckConstraint("source IN (" + ",".join(f"'{s}'" for s in REGION_SOURCES) + ")", name="ck_region_stat_source"),
        # 同一鄉鎮、同一天、同一作物／病蟲害、同一來源只留一筆；重新彙整是整批 upsert，不是每次疊加。
        UniqueConstraint("county", "district", "day", "crop_text", "pest_text", "source", name="uq_region_daily_stat"),
        Index("ix_region_stat_lookup", "county", "district", "day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    county: Mapped[str] = mapped_column(String(12))
    district: Mapped[str] = mapped_column(String(20))
    day: Mapped[date] = mapped_column(Date)
    crop_text: Mapped[str] = mapped_column(String(80))
    # 可空：純作物分布（source=plan）不針對病蟲害。
    pest_text: Mapped[str | None] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(12))
    # 貢獻農場數 < 3 時，這三欄一律 None——連「樣本不足但至少有 1 戶」都不透露，避免反推。
    count: Mapped[int | None] = mapped_column(Integer)
    area_ha: Mapped[float | None] = mapped_column(Float)
    # 主動調查：每板隻數的平均；其他來源不填。
    metric: Mapped[float | None] = mapped_column(Float)
    # 貢獻的農場數；survey 沒有農場歸屬，存 0。API 只在 count 不是 None 時才把這欄一起回傳。
    farms: Mapped[int] = mapped_column(Integer, default=0)


class SurveyRecord(Base):
    """植物醫生或機關的主動調查（例如黏板隻數），不是農場自己的紀錄，不受 share_region 與農場數門檻限制。"""

    __tablename__ = "survey_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    district: Mapped[str] = mapped_column(String(20))
    surveyed_on: Mapped[date] = mapped_column(Date)
    crop_text: Mapped[str] = mapped_column(String(80))
    pest_text: Mapped[str] = mapped_column(String(160))
    trap_count: Mapped[float] = mapped_column(Float)
    traps: Mapped[int] = mapped_column(Integer, default=1)
    recorded_by: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"))
    note: Mapped[str] = mapped_column(Text, default="")


class AlertThreshold(Base):
    """警戒門檻。示範值的 basis 一律寫「示範，待高改場提供」，不編造出處；正式門檻由改良場確認後才換。"""

    __tablename__ = "alert_thresholds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    pest_text: Mapped[str] = mapped_column(String(160))
    metric: Mapped[str] = mapped_column(String(20))  # trap_count / reports_30d
    warn_value: Mapped[float] = mapped_column(Float)
    basis: Mapped[str] = mapped_column(String(200))


REGION_TABLES = [RegionDailyStat.__table__, SurveyRecord.__table__, AlertThreshold.__table__]
