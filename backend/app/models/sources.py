"""每次爬取的快照與原始列。

原則：爬蟲失敗絕不覆蓋舊資料。每跑一次寫一個新 snapshot，
原始列先落 source_records（JSONB 保留全文），正規化通過後才把
snapshot 標成 active，供比對使用。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import SnapshotStatus, SourceName


class SourceSnapshot(Base, TimestampMixin):
    __tablename__ = "source_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[SourceName] = mapped_column(
        Enum(SourceName, native_enum=False), index=True
    )
    status: Mapped[SnapshotStatus] = mapped_column(
        Enum(SnapshotStatus, native_enum=False), default=SnapshotStatus.RUNNING, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_dir: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSONB)

    records: Mapped[list["SourceRecord"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class SourceRecord(Base):
    """一列原始資料。canonical id 為 null 代表還沒對上，會進 match_issues。"""

    __tablename__ = "source_records"
    __table_args__ = (
        Index("ix_source_records_snapshot_kind", "snapshot_id", "record_kind"),
        Index("ix_source_records_payload", "payload", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[SourceName] = mapped_column(Enum(SourceName, native_enum=False))
    # ppm_registration / ppm_profile / fda_mrl / kdais_row
    record_kind: Mapped[str] = mapped_column(String(40), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    row_hash: Mapped[str] = mapped_column(String(64), index=True)

    crop_id: Mapped[int | None] = mapped_column(ForeignKey("crops.id", ondelete="SET NULL"))
    pest_id: Mapped[int | None] = mapped_column(ForeignKey("pests.id", ondelete="SET NULL"))
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL")
    )

    snapshot: Mapped[SourceSnapshot] = relationship(back_populates="records")
