"""Private farm workspaces; intentionally separate from public crop/pesticide entities."""
from datetime import date
from uuid import uuid4

from sqlalchemy import Boolean, Date, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def uid() -> str:
    return str(uuid4())


class FarmAccount(Base):
    __tablename__ = "farm_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(12))
    password_hash: Mapped[str] = mapped_column(String(256))
    farm_name: Mapped[str] = mapped_column(String(120), default="我的農場")
    location: Mapped[str] = mapped_column(String(120), default="")
    weather_county: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # 精準模式分頁開關；有示範資料時不看這個也會顯示。
    precision_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # 同意把這個農場的資料匿名彙整進區域統計；預設不同意，彙整時只算勾選過的農場。
    share_region: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # 代建帳號：首次登入必須改密碼。植物醫生知道那組一次性密碼，不改掉就等於他一直有農民的帳號。
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # 一次性密碼的有效期限（ISO 字串，UTC）。過期就只能重發，不會自動延長。
    otp_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 誰建立的；自己註冊的是 None。用來在農民管理標「你建立的」。
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class FarmSession(Base):
    __tablename__ = "farm_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"), index=True)
    expires_at: Mapped[int] = mapped_column(Integer)


class FarmInvite(Base):
    __tablename__ = "farm_invites"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"), index=True)
    expires_at: Mapped[int] = mapped_column(Integer)


class DoctorFarmer(Base):
    __tablename__ = "doctor_farmers"
    doctor_id: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"), primary_key=True)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"), primary_key=True)
    # 這個連結怎麼來的：`code`＝農民給了連結碼，`created`＝植物醫生代建帳號。
    # 兩者的權限一樣，差別在畫面要說得出來；農民本人隨時可以解除，建立不等於永久授權。
    source: Mapped[str] = mapped_column(String(12), default="code", server_default="code")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class FarmPlan(Base):
    __tablename__ = "farm_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"), index=True)
    crop_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    crop_name: Mapped[str] = mapped_column(String(80))
    field_name: Mapped[str] = mapped_column(String(100))
    variety: Mapped[str] = mapped_column(String(80), default="")
    area: Mapped[str] = mapped_column(String(60), default="")
    planted_on: Mapped[date] = mapped_column(Date)
    end_on: Mapped[date] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    template_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sources: Mapped[list] = mapped_column(JSON, default=list)


class FarmEvent(Base):
    __tablename__ = "farm_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    plan_id: Mapped[str] = mapped_column(ForeignKey("farm_plans.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(120))
    start_on: Mapped[date] = mapped_column(Date)
    end_on: Mapped[date] = mapped_column(Date)
    detail: Mapped[str] = mapped_column(Text, default="")
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    media: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(80), default="系統參考範本")
    basis: Mapped[str] = mapped_column(String(40), default="template_estimate")
    # 田區主檔連結（精準模式）；規劃仍以 farm_plans.field_name 為準，這欄在田區管理上線後才會填。
    field_id: Mapped[str | None] = mapped_column(
        ForeignKey("farm_fields.id", ondelete="SET NULL", use_alter=True, name="fk_farm_events_field"), index=True)


class FarmNote(Base):
    __tablename__ = "farm_notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    farmer_id: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("farm_accounts.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)


PLANNING_TABLES = [FarmAccount.__table__, FarmSession.__table__, FarmInvite.__table__, DoctorFarmer.__table__, FarmPlan.__table__, FarmEvent.__table__, FarmNote.__table__]
