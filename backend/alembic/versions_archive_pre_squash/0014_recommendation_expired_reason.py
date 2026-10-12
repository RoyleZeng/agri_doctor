"""建議撤回時要有理由。

重新分析時若 proposed 建議的前提已不成立（下雨後水分回到門檻以上、14 天內已記施肥），或單純超過
`expires_at`，都要標成 expired 並說明原因——一張叫農民去灌已經濕的田的卡片，比沒有卡片更傷信任。

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('recommendations') as batch:
        batch.add_column(sa.Column('expired_reason', sa.Text(), server_default='', nullable=False))


def downgrade():
    with op.batch_alter_table('recommendations') as batch:
        batch.drop_column('expired_reason')
