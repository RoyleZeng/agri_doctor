"""kdais_recommendations 存文件標題

版本要顯示得完整需要標題＋修訂日期（「水稻病害防治用藥摘要表 114.12.22修訂」）。
同一個作物可能有多份摘要表（水稻分成病害與蟲害兩份），只有修訂日期分不出是哪一份。

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kdais_recommendations",
        sa.Column("document_title", sa.String(length=300), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kdais_recommendations", "document_title")
