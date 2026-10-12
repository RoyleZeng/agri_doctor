"""範本草稿與歷次發布分開保存，已建規劃仍讀自己的快照。"""
from sqlalchemy import Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.models.planning import uid


class FarmContent(Base):
    __tablename__ = 'farm_content'
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    draft: Mapped[dict] = mapped_column(JSON)
    published: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    published_version: Mapped[str | None] = mapped_column(String(80), nullable=True)


class FarmContentRelease(Base):
    __tablename__ = 'farm_content_releases'
    __table_args__ = (UniqueConstraint('crop_key', 'revision'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    crop_key: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)
    published_by: Mapped[str] = mapped_column(String(36))
    published_at: Mapped[str] = mapped_column(String(40))
