"""農業部公開資料獨立保存，不混入三源藥劑合法性事實表。"""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

DOCUMENT = JSON().with_variant(JSONB(), 'postgresql')

class MoaSnapshot(Base):
    __tablename__ = 'moa_snapshots'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(64), index=True)
    endpoint: Mapped[str] = mapped_column(String(120), index=True)
    params: Mapped[dict] = mapped_column(DOCUMENT)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    complete: Mapped[bool] = mapped_column(Boolean)
    row_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(60))
    # deferred 避免列表讀取時把大型原始頁面一併搬進記憶體。
    raw_pages: Mapped[list] = mapped_column(DOCUMENT, deferred=True)
    data: Mapped[dict | list | None] = mapped_column(DOCUMENT, deferred=True)

class MoaDataset(Base):
    __tablename__ = 'moa_datasets'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(120), index=True)
    params: Mapped[dict] = mapped_column(DOCUMENT)
    active_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey('moa_snapshots.id'))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(60))

class MoaRecord(Base):
    __tablename__ = 'moa_records'
    __table_args__ = (Index('ix_moa_records_snapshot_title', 'snapshot_id', 'title'),)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey('moa_snapshots.id'), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(DOCUMENT)
