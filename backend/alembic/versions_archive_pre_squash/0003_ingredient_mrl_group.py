"""成分可歸屬到「以群組訂容許量」的上層成分

食藥署有些農藥不逐一訂容許量，而是以化學群組訂。實測發現鋅錳乃浦
(mancozeb)、免得爛(maneb) 等在容許量表裡根本查不到，因為它們歸在
「Dithiocarbamates 二硫代胺基甲酸鹽類」底下。

沒有這一層，這些藥會全部被標成「查無容許量」—— 是假警報，
而假警報多了，真的警報就沒人看了。

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "active_ingredients",
        sa.Column("mrl_group_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ingredient_mrl_group",
        "active_ingredients", "active_ingredients",
        ["mrl_group_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_ingredient_mrl_group", "active_ingredients", type_="foreignkey")
    op.drop_column("active_ingredients", "mrl_group_id")
