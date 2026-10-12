"""Coverage receipts and immutable source-change notifications."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PpmCoverage(Base):
    __tablename__ = "ppm_coverage"
    __table_args__ = (UniqueConstraint("snapshot_id", "target_key", name="uq_ppm_coverage_target"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    target_key: Mapped[str] = mapped_column(String(64))
    crop_name: Mapped[str] = mapped_column(String(200))
    pest_name: Mapped[str] = mapped_column(String(200))
    target_name: Mapped[str] = mapped_column(String(400))
    source_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    drug_count: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    html_sha256: Mapped[str | None] = mapped_column(String(64))


class SourceChange(Base):
    __tablename__ = "source_changes"
    __table_args__ = (UniqueConstraint("snapshot_id", "change_key", name="uq_source_change"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    previous_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshots.id"))
    change_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    source_url: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_date: Mapped[str | None] = mapped_column(String(40))


class UpdateJob(Base):
    __tablename__ = "update_jobs"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schedule_label: Mapped[str] = mapped_column(String(200))
