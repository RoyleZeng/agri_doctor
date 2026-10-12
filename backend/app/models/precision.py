"""精準模式：田區、分區、感測、圖層與建議。

示範資料與未來的真實資料共用同一套表，只差 `is_demo`：前端看到任何 demo 列就顯示「示範資料」橫幅，
不做第二套示範頁面；示範產生器也只刪建 `is_demo=true` 的列，不會碰到真實資料。
建議只是「建議」：規則引擎產生 proposed，農民接受才變待辦，做完才進日誌——日誌才是事實。
"""
from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, JSON,
                        String, Text, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.planning import uid

RECOMMENDATION_STATUSES = ("proposed", "accepted", "done", "dismissed", "expired")
RECOMMENDATION_KINDS = ("irrigate", "vrt_fertilizer")
LAYER_KINDS = ("ndvi", "soil_moisture", "ph", "yield_prev")
# 感測群組的「接法」＝要用哪個 adapter；現在只有 sensor_csv 真的有人讀，其餘先能設定、之後接真實協定。
DATA_SOURCE_KINDS = ("sensor_http", "sensor_mqtt", "sensor_csv", "uav_upload", "gis_file", "gis_wms", "weather")
DATA_SOURCE_STATUSES = ("connected", "error", "never")
UAV_PRODUCTS = ("ndvi", "rgb", "multispectral")
UAV_STATUSES = ("uploaded", "processing", "done", "failed")
BOUNDARY_SOURCES = ("hand_grid", "upload", "cadastral_wms")
# 讀數量大（每站每指標每小時一筆），用整數主鍵；SQLite 只有 INTEGER PRIMARY KEY 會自動遞增。
READING_ID = BigInteger().with_variant(Integer, "sqlite")


class FarmField(Base, TimestampMixin):
    """田區主檔。日誌與規劃事項用 field_id 連回來；field_text 仍是輸入當下的快照。"""

    __tablename__ = "farm_fields"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    area_text: Mapped[str] = mapped_column(String(60), default="")
    # 示範用格子座標系；真實邊界匯入（第 7 步）後，格子由邊界外接矩形依此切分。
    grid_cols: Mapped[int] = mapped_column(Integer, default=6)
    grid_rows: Mapped[int] = mapped_column(Integer, default=4)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class FarmZone(Base):
    __tablename__ = "farm_zones"
    __table_args__ = (UniqueConstraint("field_id", "code", name="uq_farm_zone_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    field_id: Mapped[str] = mapped_column(ForeignKey("farm_fields.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(8))
    label: Mapped[str] = mapped_column(String(40), default="")
    cells: Mapped[list] = mapped_column(JSON, default=list)
    # 抽不出面積就是 None，不填 0：施肥總量會跟著變成「無法計算」，而不是少算。
    area_ha: Mapped[float | None] = mapped_column(Float)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class SensorGroup(Base):
    __tablename__ = "sensor_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    field_id: Mapped[str] = mapped_column(ForeignKey("farm_fields.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    # 決定用哪個 adapter；上游來源被刪只清連結，群組與站點本身留著（歷史讀數不能因此消失）。
    source_id: Mapped[str | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"), index=True)
    metrics: Mapped[list] = mapped_column(JSON, default=list)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class SensorStation(Base):
    __tablename__ = "sensor_stations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    field_id: Mapped[str] = mapped_column(ForeignKey("farm_fields.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("sensor_groups.id", ondelete="SET NULL"), index=True)
    zone_id: Mapped[str | None] = mapped_column(ForeignKey("farm_zones.id", ondelete="SET NULL"), index=True)
    code: Mapped[str] = mapped_column(String(20), default="")
    kind: Mapped[str] = mapped_column(String(16))
    grid_col: Mapped[int | None] = mapped_column(Integer)
    grid_row: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="demo")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = (
        # 同一站同一指標同一時間只能一筆：重送或重跑匯入不會把平均值灌水。
        UniqueConstraint("station_id", "metric", "ts", name="uq_sensor_reading"),
    )

    id: Mapped[int] = mapped_column(READING_ID, primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(ForeignKey("sensor_stations.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metric: Mapped[str] = mapped_column(String(24))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(12), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class FieldLayer(Base):
    __tablename__ = "field_layers"
    __table_args__ = (
        CheckConstraint("kind IN (" + ",".join(f"'{k}'" for k in LAYER_KINDS) + ")", name="ck_field_layer_kind"),
        Index("ix_field_layer_field_kind_date", "field_id", "kind", "captured_on"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    field_id: Mapped[str] = mapped_column(ForeignKey("farm_fields.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    captured_on: Mapped[date] = mapped_column(Date)
    source_text: Mapped[str] = mapped_column(String(120), default="")
    # 只存指標值不存影像：{"col,row": value} 與 {zone_code: value}。
    cell_values: Mapped[dict] = mapped_column(JSON, default=dict)
    zone_values: Mapped[dict] = mapped_column(JSON, default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Recommendation(Base, TimestampMixin):
    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint("status IN (" + ",".join(f"'{s}'" for s in RECOMMENDATION_STATUSES) + ")",
                        name="ck_recommendation_status"),
        CheckConstraint("kind IN (" + ",".join(f"'{k}'" for k in RECOMMENDATION_KINDS) + ")",
                        name="ck_recommendation_kind"),
        # 同一田區同一種建議在 proposed 只留一筆；規則引擎重跑是更新它。部分唯一索引是併發重跑時的最後防線。
        Index("uq_recommendation_proposed", "field_id", "kind", unique=True,
              postgresql_where=text("status = 'proposed'"), sqlite_where=text("status = 'proposed'")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    field_id: Mapped[str] = mapped_column(ForeignKey("farm_fields.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(12), default="proposed")
    title: Mapped[str] = mapped_column(String(200))
    rationale: Mapped[str] = mapped_column(Text, default="")
    five_r: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[str | None] = mapped_column(ForeignKey("farm_events.id", ondelete="SET NULL"), index=True)
    work_log_id: Mapped[str | None] = mapped_column(ForeignKey("farm_work_logs.id", ondelete="SET NULL"), index=True)
    rule_version: Mapped[str] = mapped_column(String(40))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("farm_accounts.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 略過的原因；「為什麼沒照建議做」之後調規則用得到。
    status_note: Mapped[str] = mapped_column(Text, default="")
    # 只有 expired 才有值：前提已不成立（例如下雨後水分回升、14 天內已施肥）或單純超時，都要說明原因，
    # 不能讓卡片默默消失——一張叫農民去灌已經濕的田的卡片，比沒有卡片更傷信任。
    expired_reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class DataSource(Base, TimestampMixin):
    """一個連線設定：感測器（HTTP／MQTT／CSV）、無人機上傳、GIS 檔案或 WMS、既有的氣象預報。

    `config` 可能含金鑰／密碼，寫入前由 `app/precision/crypto.py` 加密，API 一律回遮罩後的值——
    設定畫面本來就不該把明文印出來給任何看得到畫面的人。
    """

    __tablename__ = "data_sources"
    __table_args__ = (
        CheckConstraint("kind IN (" + ",".join(f"'{k}'" for k in DATA_SOURCE_KINDS) + ")", name="ck_data_source_kind"),
        CheckConstraint("status IN (" + ",".join(f"'{s}'" for s in DATA_SOURCE_STATUSES) + ")",
                        name="ck_data_source_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="never")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")


class UavMission(Base, TimestampMixin):
    """一次無人機飛行的成果。示範階段只吃已經算好的分區數值 CSV；GeoTIFF／影像服務留到之後。"""

    __tablename__ = "uav_missions"
    __table_args__ = (
        CheckConstraint("product IN (" + ",".join(f"'{p}'" for p in UAV_PRODUCTS) + ")", name="ck_uav_mission_product"),
        CheckConstraint("status IN (" + ",".join(f"'{s}'" for s in UAV_STATUSES) + ")", name="ck_uav_mission_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    field_id: Mapped[str] = mapped_column(ForeignKey("farm_fields.id", ondelete="CASCADE"), index=True)
    flown_on: Mapped[date] = mapped_column(Date)
    product: Mapped[str] = mapped_column(String(16))
    # 原始上傳檔的相對路徑（跟 farm_log_photos.storage_key 同一個做法），根目錄由 media_root 決定。
    file_key: Mapped[str] = mapped_column(String(300), default="")
    crs: Mapped[str] = mapped_column(String(20), default="EPSG:4326")
    status: Mapped[str] = mapped_column(String(12), default="uploaded")
    note: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class FieldBoundary(Base):
    """田區的 GIS 邊界，一律存 EPSG:4326。有真實邊界前，分區圖用示範格子，格子沒有內外之分。"""

    __tablename__ = "field_boundaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    # unique 本身就會建索引；field_id 只會拿來單獨查（一個田區最多一筆），不需要再疊一個一般索引。
    field_id: Mapped[str] = mapped_column(ForeignKey("farm_fields.id", ondelete="CASCADE"), unique=True)
    geojson: Mapped[dict] = mapped_column(JSON)
    # 依邊界外接矩形切出 grid_cols×grid_rows 格後，中心點落在邊界外的格子；分區圖據此不著色。
    outside_cells: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(20))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


PRECISION_TABLES = [FarmField.__table__, FarmZone.__table__, SensorGroup.__table__, SensorStation.__table__,
                    SensorReading.__table__, FieldLayer.__table__, Recommendation.__table__]
DATA_SOURCE_TABLES = [DataSource.__table__, UavMission.__table__, FieldBoundary.__table__]
