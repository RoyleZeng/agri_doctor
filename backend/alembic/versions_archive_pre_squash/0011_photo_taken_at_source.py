"""照片記錄拍攝時間的來源：exif（相機記錄）或 upload（沒有可信 EXIF，退回上傳時間）。

補助查核問的是「何時拍的」，只有 EXIF 時間算得上證據；匯出附錄要據此把退回的時間
印成「上傳時間（無拍攝資訊）」，不能讓它看起來跟真的拍攝時間一樣。

另開一版而不改 0010：每一步的 migration 各自能升能降，出錯時可以只退這一步。
既有列補 upload：事後無法證明當時讀到的是 EXIF，寧可低估證據強度也不要高估。

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('farm_log_photos') as batch:
        batch.add_column(sa.Column('taken_at_source', sa.String(8), nullable=False, server_default='upload'))
        batch.create_check_constraint('ck_log_photo_taken_at_source', "taken_at_source IN ('exif','upload')")


def downgrade():
    with op.batch_alter_table('farm_log_photos') as batch:
        batch.drop_constraint('ck_log_photo_taken_at_source', type_='check')
        batch.drop_column('taken_at_source')
