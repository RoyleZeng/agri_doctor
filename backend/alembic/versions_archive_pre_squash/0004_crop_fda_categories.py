"""作物對應多個食藥署作物類別

實測發現食藥署同時使用「米」與「米類」兩種寫法，而且不同成分用的不一樣：

    Kasugamycin  嘉賜黴素  米類  0.2
    Probenazole  撲殺熱    米    0.5
    Tecloftalam  克枯爛    米    0.2

原本一個作物只存一個 fda_category（從植保系統描述抽的「米類」），
導致撲殺熱與克枯爛被誤標成「查無容許量」。

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crop_fda_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("crop_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_note", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["crop_id"], ["crops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("crop_id", "category", name="uq_crop_fda_category"),
    )
    op.create_index("ix_crop_fda_category", "crop_fda_categories", ["crop_id", "priority"])
    op.create_index(
        "ix_crop_fda_categories_category", "crop_fda_categories", ["category"]
    )


def downgrade() -> None:
    op.drop_index("ix_crop_fda_categories_category", table_name="crop_fda_categories")
    op.drop_index("ix_crop_fda_category", table_name="crop_fda_categories")
    op.drop_table("crop_fda_categories")
