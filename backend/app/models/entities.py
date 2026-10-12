"""Canonical 實體：作物、病蟲害、成分、藥劑產品。

三個來源各自用不同的名稱寫同一件事（例如「蓮霧」vs「洋蒲桃」、
「嘉賜黴素」vs「kasugamycin」），所以每個實體都配一張 alias 表，
用 pg_trgm 做模糊比對。
"""

from datetime import date

from sqlalchemy import (
    Date, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import PestKind, SourceName


class Crop(Base, TimestampMixin):
    __tablename__ = "crops"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    scientific_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    # 食藥署容許量的「作物類別」（米類、包葉菜類…）。PPM 詳細頁的
    # 「適用之大類容許量為X」可直接抽出來當種子資料。
    fda_category: Mapped[str | None] = mapped_column(String(60), index=True)

    aliases: Mapped[list["CropAlias"]] = relationship(
        back_populates="crop", cascade="all, delete-orphan"
    )
    fda_categories: Mapped[list["CropFdaCategory"]] = relationship(
        back_populates="crop", cascade="all, delete-orphan",
        order_by="CropFdaCategory.priority",
    )


class CropAlias(Base):
    __tablename__ = "crop_aliases"
    __table_args__ = (
        UniqueConstraint("crop_id", "alias", name="uq_crop_alias"),
        Index("ix_crop_aliases_alias_trgm", "alias",
              postgresql_using="gin", postgresql_ops={"alias": "gin_trgm_ops"}),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    crop_id: Mapped[int] = mapped_column(ForeignKey("crops.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(120), index=True)
    source: Mapped[SourceName | None] = mapped_column(Enum(SourceName, native_enum=False))

    crop: Mapped[Crop] = relationship(back_populates="aliases")


class CropFdaCategory(Base):
    """作物 -> 食藥署「作物類別」的候選對照（一個作物可能對到多個）。

    食藥署同時用「米」與「米類」兩種寫法，而且不同成分用的不一樣：
    嘉賜黴素訂在「米類」，撲殺熱與克枯爛卻訂在「米」。只認一個類別會讓
    後兩者被誤標成「查無容許量」—— 這是假警報，而假警報一多，
    真正該注意的警告就沒人看了。

    來源優先序（priority 小的先查）：
        0  作物本名
        1  植保系統容許量欄的括號註記，例如「克枯爛0.2(米)」-> 米
        2  植保系統作物描述的「適用之大類容許量為米類」-> 米類
        3  人工種子資料
    """

    __tablename__ = "crop_fda_categories"
    __table_args__ = (
        UniqueConstraint("crop_id", "category", name="uq_crop_fda_category"),
        Index("ix_crop_fda_category", "crop_id", "priority"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    crop_id: Mapped[int] = mapped_column(ForeignKey("crops.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(120), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    source_note: Mapped[str | None] = mapped_column(String(120))

    crop: Mapped[Crop] = relationship(back_populates="fda_categories")


class Pest(Base, TimestampMixin):
    __tablename__ = "pests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    scientific_name: Mapped[str | None] = mapped_column(Text)
    english_name: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[PestKind] = mapped_column(
        Enum(PestKind, native_enum=False), default=PestKind.OTHER
    )

    aliases: Mapped[list["PestAlias"]] = relationship(
        back_populates="pest", cascade="all, delete-orphan"
    )


class PestAlias(Base):
    __tablename__ = "pest_aliases"
    __table_args__ = (
        UniqueConstraint("pest_id", "alias", name="uq_pest_alias"),
        Index("ix_pest_aliases_alias_trgm", "alias",
              postgresql_using="gin", postgresql_ops={"alias": "gin_trgm_ops"}),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pest_id: Mapped[int] = mapped_column(ForeignKey("pests.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(160), index=True)
    source: Mapped[SourceName | None] = mapped_column(Enum(SourceName, native_enum=False))

    pest: Mapped[Pest] = relationship(back_populates="aliases")


class PestProfile(Base, TimestampMixin):
    """病徵、傳播途徑、非藥劑防治方法 —— 來自 PPM PLC0101 頁上半部。

    同一個病害在不同作物上的描述可能不同，所以以 (crop, pest) 為鍵。
    """

    __tablename__ = "pest_profiles"
    __table_args__ = (UniqueConstraint("crop_id", "pest_id", name="uq_profile_crop_pest"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    crop_id: Mapped[int] = mapped_column(ForeignKey("crops.id", ondelete="CASCADE"))
    pest_id: Mapped[int] = mapped_column(ForeignKey("pests.id", ondelete="CASCADE"))
    symptoms: Mapped[str | None] = mapped_column(Text)          # 病徵
    transmission: Mapped[str | None] = mapped_column(Text)      # 傳播途徑
    cultural_control: Mapped[str | None] = mapped_column(Text)  # 防治方法（非藥劑）

    crop: Mapped[Crop] = relationship()
    pest: Mapped[Pest] = relationship()


class ActiveIngredient(Base, TimestampMixin):
    """有效成分。中文名來自 PPM，英文普通名是接上食藥署 MRL 的鑰匙。"""

    __tablename__ = "active_ingredients"
    __table_args__ = (
        Index("ix_ingredient_name_trgm", "name",
              postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        Index("ix_ingredient_en_trgm", "english_name",
              postgresql_using="gin", postgresql_ops={"english_name": "gin_trgm_ops"}),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    english_name: Mapped[str | None] = mapped_column(String(200), index=True)
    # 食藥署有些農藥不逐一訂容許量，而是以化學群組訂，例如鋅錳乃浦(mancozeb)、
    # 免得爛(maneb) 都歸在「Dithiocarbamates 二硫代胺基甲酸鹽類」底下。
    # 查不到本身的容許量時，往這個群組再查一次。
    mrl_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("active_ingredients.id", ondelete="SET NULL")
    )

    frac_code: Mapped[str | None] = mapped_column(String(40))
    irac_code: Mapped[str | None] = mapped_column(String(40))
    hrac_code: Mapped[str | None] = mapped_column(String(40))
    moa_description: Mapped[str | None] = mapped_column(Text)

    mrl_group: Mapped["ActiveIngredient | None"] = relationship(
        remote_side="ActiveIngredient.id"
    )


class IngredientNameMap(Base, TimestampMixin):
    """PPM 中文成分名 ↔ 食藥署普通名／國際普通名的人工對照表。

    自動比對對不上時進 match_issues，不靜默忽略；人工在後台補一列到這裡。
    """

    __tablename__ = "ingredient_name_map"
    __table_args__ = (UniqueConstraint("source", "source_name", name="uq_name_map"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[SourceName] = mapped_column(Enum(SourceName, native_enum=False))
    source_name: Mapped[str] = mapped_column(String(200), index=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("active_ingredients.id", ondelete="CASCADE")
    )
    note: Mapped[str | None] = mapped_column(Text)

    ingredient: Mapped[ActiveIngredient] = relationship()


class Product(Base, TimestampMixin):
    """藥劑產品：含量% + 中文商品名 + 劑型，例如「10% 撲殺培丹 粒劑」。"""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("name", "formulation", "concentration_text",
                         name="uq_product_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    concentration_text: Mapped[str | None] = mapped_column(String(80))  # "10%", "1×10^9 cfu/mL"
    formulation: Mapped[str | None] = mapped_column(String(80))         # 粒劑、可溼性粉劑…
    english_composition: Mapped[str | None] = mapped_column(Text)       # "(probenazole + cartap)"
    moa_text: Mapped[str | None] = mapped_column(Text)                  # 原始作用機制欄位全文

    ingredients: Mapped[list["ProductIngredient"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductIngredient(Base):
    __tablename__ = "product_ingredients"
    __table_args__ = (
        UniqueConstraint("product_id", "ingredient_id", name="uq_product_ingredient"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("active_ingredients.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped[Product] = relationship(back_populates="ingredients")
    ingredient: Mapped[ActiveIngredient] = relationship()
