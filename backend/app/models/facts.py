"""三源各自的事實表，加上比對後產生的衝突。"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import Confidence, IssueType, Severity


class Registration(Base, TimestampMixin):
    """★ PPM 核准用藥：這是合法性底線，任何推薦都必須在這張表裡。"""

    __tablename__ = "registrations"
    __table_args__ = (
        # 同一支藥、同一稀釋倍數可能有多種施藥時機（本田 / 育苗箱），
        # 施藥量不同就是不同的用法，唯一鍵必須帶上它。
        UniqueConstraint("crop_id", "pest_id", "product_id", "usage_hash", name="uq_registration_usage"),
        Index("ix_registration_crop_pest", "crop_id", "pest_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    crop_id: Mapped[int] = mapped_column(ForeignKey("crops.id", ondelete="CASCADE"))
    pest_id: Mapped[int] = mapped_column(ForeignKey("pests.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="SET NULL")
    )

    dose_per_hectare: Mapped[str | None] = mapped_column(String(120))   # "1.67公升"
    dilution_text: Mapped[str | None] = mapped_column(String(80))       # "1,500"
    dilution_value: Mapped[int | None] = mapped_column(Integer)         # 1500，供排序
    usage_hash: Mapped[str] = mapped_column(String(64))
    application_method: Mapped[str | None] = mapped_column(Text)        # 施藥方法
    precautions: Mapped[str | None] = mapped_column(Text)               # 注意事項原文

    # 從 precautions 抽出來的結構化欄位（services/phi_extract.py）
    phi_days: Mapped[int | None] = mapped_column(Integer)               # 安全採收期
    mix_warnings: Mapped[list | None] = mapped_column(JSONB)            # 混用藥害警告

    ppm_mrl_text: Mapped[str | None] = mapped_column(Text)  # PPM 頁上的容許量原文
    source_url: Mapped[str | None] = mapped_column(Text)

    crop = relationship("Crop")
    pest = relationship("Pest")
    product = relationship("Product")


class MrlLimit(Base, TimestampMixin):
    """★ 食藥署農藥殘留容許量。以 (成分, 作物類別) 為鍵。"""

    __tablename__ = "mrl_limits"
    __table_args__ = (
        UniqueConstraint("ingredient_id", "crop_category", "snapshot_id",
                         name="uq_mrl_limit"),
        Index("ix_mrl_lookup", "ingredient_id", "crop_category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("active_ingredients.id", ondelete="SET NULL")
    )
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="SET NULL")
    )

    international_name: Mapped[str | None] = mapped_column(String(200), index=True)
    common_name: Mapped[str | None] = mapped_column(String(200), index=True)
    crop_category: Mapped[str] = mapped_column(String(120), index=True)  # 「柿」「米類」
    limit_ppm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    limit_text: Mapped[str | None] = mapped_column(String(60))           # 原文，含「不得檢出」
    note: Mapped[str | None] = mapped_column(Text)                       # 備註（生長調節劑…）
    effective_date: Mapped[date | None] = mapped_column(Date)

    ingredient = relationship("ActiveIngredient")


class KdaisRecommendation(Base, TimestampMixin):
    """★ 高改場摘要表。PDF 解析而來，未經人工校對者不併入推薦。"""

    __tablename__ = "kdais_recommendations"
    __table_args__ = (Index("ix_kdais_crop_pest", "crop_id", "pest_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    crop_id: Mapped[int | None] = mapped_column(ForeignKey("crops.id", ondelete="SET NULL"))
    pest_id: Mapped[int | None] = mapped_column(ForeignKey("pests.id", ondelete="SET NULL"))
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL")
    )
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="SET NULL")
    )

    crop_text: Mapped[str | None] = mapped_column(String(120))    # PDF 標題抓到的作物名
    pest_text: Mapped[str | None] = mapped_column(String(200))
    product_text: Mapped[str | None] = mapped_column(String(300))
    moa_text: Mapped[str | None] = mapped_column(String(200))      # 作用機制代碼
    dilution_text: Mapped[str | None] = mapped_column(String(80))
    dose_text: Mapped[str | None] = mapped_column(String(120))     # 每公頃施藥量
    phi_text: Mapped[str | None] = mapped_column(String(120))      # 安全採收期(天)
    tolerance_text: Mapped[str | None] = mapped_column(String(200))  # 安全容許量(PPM)
    method_text: Mapped[str | None] = mapped_column(Text)
    remark: Mapped[str | None] = mapped_column(Text)
    raw_row: Mapped[dict | None] = mapped_column(JSONB)           # PDF 原始整列

    document_title: Mapped[str | None] = mapped_column(String(300))
    pdf_url: Mapped[str | None] = mapped_column(Text)
    pdf_page: Mapped[int | None] = mapped_column(Integer)
    revision_date: Mapped[str | None] = mapped_column(String(20))  # 民國「114.12.22」

    confidence: Mapped[Confidence] = mapped_column(
        Enum(Confidence, native_enum=False), default=Confidence.LOW, index=True
    )
    verified_by: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    crop = relationship("Crop")
    pest = relationship("Pest")
    product = relationship("Product")


class KdaisDocument(Base, TimestampMixin):
    """高改場 PDF 清單。用 revision_date 做增量下載判斷。"""

    __tablename__ = "kdais_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), unique=True)
    crop_text: Mapped[str | None] = mapped_column(String(120), index=True)
    revision_date: Mapped[str | None] = mapped_column(String(20))
    pdf_url: Mapped[str] = mapped_column(Text)
    file_size: Mapped[str | None] = mapped_column(String(20))
    local_path: Mapped[str | None] = mapped_column(Text)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parse_note: Mapped[str | None] = mapped_column(Text)


class MatchIssue(Base, TimestampMixin):
    """★ 三源不一致 —— 這張表就是這個產品的價值所在。

    衝突一律顯示，不靜默忽略、不自動修正。
    """

    __tablename__ = "match_issues"
    __table_args__ = (
        Index("ix_issue_type_severity", "issue_type", "severity"),
        Index("ix_issue_crop_pest", "crop_id", "pest_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_type: Mapped[IssueType] = mapped_column(Enum(IssueType, native_enum=False))
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, native_enum=False), default=Severity.WARNING
    )

    crop_id: Mapped[int | None] = mapped_column(ForeignKey("crops.id", ondelete="CASCADE"))
    pest_id: Mapped[int | None] = mapped_column(ForeignKey("pests.id", ondelete="CASCADE"))
    ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("active_ingredients.id", ondelete="CASCADE")
    )
    registration_id: Mapped[int | None] = mapped_column(
        ForeignKey("registrations.id", ondelete="CASCADE")
    )

    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)   # 三源原文，供前端展開
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_note: Mapped[str | None] = mapped_column(Text)

    crop = relationship("Crop")
    pest = relationship("Pest")
    ingredient = relationship("ActiveIngredient")
