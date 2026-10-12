"""農業開放資料本地快照；固定 DDL，不依賴即時 model。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'moa_local_0002'
down_revision = '0001'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('moa_snapshots',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('dataset_key',sa.String(64),nullable=False),
        sa.Column('endpoint',sa.String(120),nullable=False),sa.Column('params',postgresql.JSONB(),nullable=False),
        sa.Column('fetched_at',sa.DateTime(timezone=True),nullable=False),sa.Column('refresh_after',sa.DateTime(timezone=True),nullable=False),
        sa.Column('complete',sa.Boolean(),nullable=False),sa.Column('row_count',sa.Integer(),nullable=False),
        sa.Column('content_hash',sa.String(64)),sa.Column('error',sa.String(60)),
        sa.Column('raw_pages',postgresql.JSONB(),nullable=False),sa.Column('data',postgresql.JSONB()))
    op.create_index('ix_moa_snapshots_dataset_key','moa_snapshots',['dataset_key'])
    op.create_index('ix_moa_snapshots_endpoint','moa_snapshots',['endpoint'])
    op.create_table('moa_datasets',sa.Column('key',sa.String(64),primary_key=True),
        sa.Column('endpoint',sa.String(120),nullable=False),sa.Column('params',postgresql.JSONB(),nullable=False),
        sa.Column('active_snapshot_id',sa.String(36),sa.ForeignKey('moa_snapshots.id')),
        sa.Column('last_attempt_at',sa.DateTime(timezone=True)),sa.Column('last_error',sa.String(60)))
    op.create_index('ix_moa_datasets_endpoint','moa_datasets',['endpoint'])
    op.create_table('moa_records',sa.Column('snapshot_id',sa.String(36),sa.ForeignKey('moa_snapshots.id'),primary_key=True),
        sa.Column('ordinal',sa.Integer(),primary_key=True),sa.Column('title',sa.Text(),nullable=False),
        sa.Column('search_text',sa.Text(),nullable=False),sa.Column('payload',postgresql.JSONB(),nullable=False))
    op.create_index('ix_moa_records_snapshot_title','moa_records',['snapshot_id','title'])

def downgrade():
    op.drop_table('moa_records');op.drop_table('moa_datasets');op.drop_table('moa_snapshots')
