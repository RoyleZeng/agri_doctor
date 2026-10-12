"""施藥日誌可連結藥劑登記，並保存選取當下的登記快照（作用機制、安全採收期）。

手動記的施藥最常沒經過植物醫生，偏偏最需要輪替檢查；沒有作用機制，檢查只能回「無法判斷」。
從藥劑登記挑選才有可靠的機制來源，不用藥名反查（中文子字串比對會把單劑配成複方）。
正規化會刪除舊登記列，所以外鍵設 SET NULL，選取時的值完整留在快照裡。

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('farm_work_logs') as batch:
        batch.add_column(sa.Column('registration_id', sa.Integer()))
        batch.add_column(sa.Column('registration_snapshot', sa.JSON()))
        batch.create_foreign_key('fk_farm_work_logs_registration', 'registrations',
                                 ['registration_id'], ['id'], ondelete='SET NULL')
        batch.create_index('ix_farm_work_logs_registration_id', ['registration_id'])


def downgrade():
    with op.batch_alter_table('farm_work_logs') as batch:
        batch.drop_index('ix_farm_work_logs_registration_id')
        batch.drop_constraint('fk_farm_work_logs_registration', type_='foreignkey')
        batch.drop_column('registration_snapshot')
        batch.drop_column('registration_id')
