"""registrations 唯一鍵加入每公頃施藥量

實際資料裡同一支藥、同一稀釋倍數會出現多列，因為施藥時機不同，例如：

    水稻 稻蝨類 0.5% 可尼丁 粒劑  本田：40-60公斤/公頃
    水稻 稻蝨類 0.5% 可尼丁 粒劑  育苗箱：50公克/公頃

這是兩種真實不同的用法，不能被唯一鍵擋掉。全庫掃描共 31 組這種情形。
（完全重複的列另外在 normalize_ppm 以程式去重。）

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_registration", "registrations", type_="unique")
    op.create_unique_constraint(
        "uq_registration",
        "registrations",
        ["crop_id", "pest_id", "product_id", "dilution_text", "dose_per_hectare"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_registration", "registrations", type_="unique")
    op.create_unique_constraint(
        "uq_registration",
        "registrations",
        ["crop_id", "pest_id", "product_id", "dilution_text"],
    )
