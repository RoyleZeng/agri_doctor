"""診療資料獨立保留快照，來源更新不應改寫已儲存的處方。"""
from datetime import date, datetime
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin
from app.models.planning import uid


class ConsultCase(Base, TimestampMixin):
    __tablename__='consult_cases'
    __table_args__=(CheckConstraint("status IN ('draft','saved','issued')",name='ck_consult_status'),
        Index('ix_consult_doctor_created','doctor_id','created_at'))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    doctor_id: Mapped[str]=mapped_column(ForeignKey('farm_accounts.id'))
    farmer_id: Mapped[str | None]=mapped_column(ForeignKey('farm_accounts.id',ondelete='SET NULL'),index=True)
    farmer_text: Mapped[str]=mapped_column(String(120))
    case_no: Mapped[str]=mapped_column(String(32),unique=True)
    plan_id: Mapped[str | None]=mapped_column(ForeignKey('farm_plans.id',ondelete='SET NULL'),index=True)
    crop_id: Mapped[int | None]=mapped_column(ForeignKey('crops.id',ondelete='SET NULL'))
    crop_text: Mapped[str]=mapped_column(String(80))
    variety: Mapped[str]=mapped_column(String(80),default='')
    field_text: Mapped[str]=mapped_column(String(160),default='')
    area_text: Mapped[str]=mapped_column(String(60),default='')
    expected_harvest_on: Mapped[date | None]=mapped_column(Date)
    examined_by: Mapped[list]=mapped_column(JSON,default=list)
    symptoms: Mapped[str]=mapped_column(Text,default='')
    diagnosis_pest_id: Mapped[int | None]=mapped_column(ForeignKey('pests.id',ondelete='SET NULL'))
    diagnosis_text: Mapped[str]=mapped_column(String(160),default='')
    diagnosis_basis: Mapped[list]=mapped_column(JSON,default=list)
    cultural_control: Mapped[str]=mapped_column(Text,default='')
    status: Mapped[str]=mapped_column(String(16),default='draft')
    issued_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    version: Mapped[int]=mapped_column(Integer,default=1)


class PrescriptionItem(Base):
    __tablename__='prescription_items'
    __table_args__=(CheckConstraint('phi_days IS NULL OR phi_days >= 0',name='ck_prescription_phi'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    case_id: Mapped[str]=mapped_column(ForeignKey('consult_cases.id',ondelete='CASCADE'),index=True)
    # 正規化會刪除舊登記列；只清外鍵，不刪處方，來源編號另存快照。
    registration_id: Mapped[int | None]=mapped_column(ForeignKey('registrations.id',ondelete='SET NULL'),index=True)
    product_text: Mapped[str]=mapped_column(String(200))
    ingredient_text: Mapped[str]=mapped_column(String(200))
    moa_text: Mapped[str]=mapped_column(String(60))
    dilution: Mapped[str | None]=mapped_column(String(40))
    dose: Mapped[str | None]=mapped_column(String(40))
    frequency: Mapped[str]=mapped_column(String(80),default='')
    phi_days: Mapped[int | None]=mapped_column(Integer)
    advice: Mapped[str]=mapped_column(Text,default='')
    source_values: Mapped[dict]=mapped_column(JSON,default=dict)
    checks: Mapped[list]=mapped_column(JSON,default=list)
    sort_order: Mapped[int]=mapped_column(Integer,default=0)


class ApplicationRecord(Base, TimestampMixin):
    __tablename__='application_records'
    __table_args__=(Index('ix_application_farmer_field_date','farmer_id','field_text','applied_on'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    farmer_id: Mapped[str]=mapped_column(ForeignKey('farm_accounts.id'))
    plan_id: Mapped[str | None]=mapped_column(ForeignKey('farm_plans.id',ondelete='SET NULL'))
    case_id: Mapped[str | None]=mapped_column(ForeignKey('consult_cases.id',ondelete='SET NULL'))
    # plan 可為空且同一田區可跨期種植，需獨立保存田區才能限縮輪替歷史。
    field_text: Mapped[str]=mapped_column(String(160))
    product_text: Mapped[str]=mapped_column(String(200))
    moa_codes: Mapped[list]=mapped_column(JSON,default=list)
    applied_on: Mapped[date]=mapped_column(Date)
    note: Mapped[str]=mapped_column(Text,default='')
    # 由施藥日誌衍生時指回日誌；日誌在可刪期限內撤回，這筆也要消失，輪替檢查不能讀到撤回的施藥。
    work_log_id: Mapped[str | None]=mapped_column(ForeignKey('farm_work_logs.id',ondelete='CASCADE'),index=True,unique=True)


CONSULT_TABLES=[ConsultCase.__table__,PrescriptionItem.__table__,ApplicationRecord.__table__]
