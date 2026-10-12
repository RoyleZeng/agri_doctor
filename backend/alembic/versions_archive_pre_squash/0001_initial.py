"""initial schema

第一版直接由 SQLAlchemy metadata 建表（含 pg_trgm 的 GIN 索引），
之後的異動再用 alembic revision --autogenerate 產生正式 diff。

Revision ID: 0001
Revises:
"""
from alembic import op

from app.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 中文作物別名、藥劑俗名要做模糊比對，先開 pg_trgm。
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
