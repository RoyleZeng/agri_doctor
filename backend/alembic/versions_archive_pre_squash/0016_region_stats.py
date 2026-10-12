"""區域作物與病蟲害地圖：彙整結果表、主動調查登錄、警戒門檻，農場加「同意匿名彙整」開關。

示範警戒值只給 2、3 筆，basis 老實寫「示範，待高改場提供」——這個專案的立場是抽不出來、
確認不了就明講，不編造出處給看起來像官方數字的東西。

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

DEMO_THRESHOLDS = [
    ('小黃薊馬', 'trap_count', 20.0, '示範，待高改場提供'),
    ('東方果實蠅', 'trap_count', 15.0, '示範，待高改場提供'),
    ('炭疽病', 'reports_30d', 5.0, '示範，待高改場提供'),
]


def upgrade():
    op.create_table(
        'region_daily_stats',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('county', sa.String(12), nullable=False),
        sa.Column('district', sa.String(20), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('crop_text', sa.String(80), nullable=False),
        sa.Column('pest_text', sa.String(160)),
        sa.Column('source', sa.String(12), nullable=False),
        sa.Column('count', sa.Integer()),
        sa.Column('area_ha', sa.Float()),
        sa.Column('metric', sa.Float()),
        sa.Column('farms', sa.Integer(), nullable=False),
        sa.CheckConstraint("source IN ('consult','scout','plan','survey')", name='ck_region_stat_source'),
        sa.UniqueConstraint('county', 'district', 'day', 'crop_text', 'pest_text', 'source', name='uq_region_daily_stat'),
    )
    op.create_index('ix_region_stat_lookup', 'region_daily_stats', ['county', 'district', 'day'])

    op.create_table(
        'survey_records',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('district', sa.String(20), nullable=False),
        sa.Column('surveyed_on', sa.Date(), nullable=False),
        sa.Column('crop_text', sa.String(80), nullable=False),
        sa.Column('pest_text', sa.String(160), nullable=False),
        sa.Column('trap_count', sa.Float(), nullable=False),
        sa.Column('traps', sa.Integer(), nullable=False),
        sa.Column('recorded_by', sa.String(36), sa.ForeignKey('farm_accounts.id'), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
    )

    op.create_table(
        'alert_thresholds',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('pest_text', sa.String(160), nullable=False),
        sa.Column('metric', sa.String(20), nullable=False),
        sa.Column('warn_value', sa.Float(), nullable=False),
        sa.Column('basis', sa.String(200), nullable=False),
    )

    with op.batch_alter_table('farm_accounts') as batch:
        batch.add_column(sa.Column('share_region', sa.Boolean(), server_default=sa.false(), nullable=False))

    if not op.get_context().as_sql:
        from uuid import uuid4
        conn = op.get_bind()
        for pest_text, metric, warn_value, basis in DEMO_THRESHOLDS:
            conn.execute(sa.text(
                "INSERT INTO alert_thresholds (id, pest_text, metric, warn_value, basis) "
                "VALUES (:id, :pest_text, :metric, :warn_value, :basis)"
            ), dict(id=str(uuid4()), pest_text=pest_text, metric=metric, warn_value=warn_value, basis=basis))


def downgrade():
    with op.batch_alter_table('farm_accounts') as batch:
        batch.drop_column('share_region')
    op.drop_table('alert_thresholds')
    op.drop_table('survey_records')
    op.drop_index('ix_region_stat_lookup', table_name='region_daily_stats')
    op.drop_table('region_daily_stats')
