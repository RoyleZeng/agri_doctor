"""可發布的作物內容與事項媒體快照。"""
from alembic import op
import sqlalchemy as sa
revision = 'farm_content_0003'
down_revision = 'moa_local_0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('farm_events', sa.Column('media', sa.JSON(), nullable=False, server_default='[]'))
    op.create_table('farm_content', sa.Column('key', sa.String(40), primary_key=True),
                    sa.Column('revision', sa.Integer(), nullable=False), sa.Column('draft', sa.JSON(), nullable=False),
                    sa.Column('published', sa.JSON()), sa.Column('published_version', sa.String(80)))
    op.create_table('farm_content_releases', sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('crop_key', sa.String(40), nullable=False), sa.Column('revision', sa.Integer(), nullable=False),
                    sa.Column('content', sa.JSON(), nullable=False), sa.Column('published_by', sa.String(36), nullable=False),
                    sa.Column('published_at', sa.String(40), nullable=False), sa.UniqueConstraint('crop_key', 'revision'))


def downgrade():
    op.drop_table('farm_content_releases')
    op.drop_table('farm_content')
    op.drop_column('farm_events', 'media')
