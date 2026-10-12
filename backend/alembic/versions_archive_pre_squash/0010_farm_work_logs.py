"""農事日誌：實際作業紀錄與照片，並讓施藥紀錄追溯到來源日誌。

application_records 用 batch 模式加欄：正式庫（PostgreSQL）直接 ALTER；
離線測試用的 SQLite 不支援加外鍵的 ALTER，會整表重建並保留既有資料。

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

# 寫死而不是匯入 app 常數：migration 要描述「當時」的結構，之後改常數不能改寫舊版本。
KINDS = "('spray','fertilize','irrigate','fieldwork','harvest','tillage','scout','other')"


def text_column(name, length):
    return sa.Column(name, sa.String(length), nullable=False)


def upgrade():
    op.create_table('farm_work_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('farmer_id', sa.String(36), sa.ForeignKey('farm_accounts.id'), nullable=False),
        sa.Column('plan_id', sa.String(36), sa.ForeignKey('farm_plans.id', ondelete='SET NULL')),
        sa.Column('event_id', sa.String(36), sa.ForeignKey('farm_events.id', ondelete='SET NULL')),
        sa.Column('prescription_item_id', sa.String(36), sa.ForeignKey('prescription_items.id', ondelete='SET NULL')),
        sa.Column('recommendation_id', sa.String(36)),
        sa.Column('done_on', sa.Date(), nullable=False),
        sa.Column('done_time', sa.String(5)),
        text_column('kind', 16),
        text_column('field_text', 160),
        text_column('crop_text', 80),
        text_column('title', 300),
        text_column('material_name', 200),
        text_column('material_supplier', 120),
        text_column('material_lot', 80),
        text_column('dilution', 40),
        text_column('amount_value', 40),
        text_column('amount_unit', 20),
        text_column('area_text', 60),
        text_column('yield_value', 40),
        text_column('yield_unit', 20),
        text_column('yield_destination', 120),
        text_column('labor_text', 120),
        sa.Column('pest_id', sa.Integer(), sa.ForeignKey('pests.id', ondelete='SET NULL')),
        text_column('pest_text', 160),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('compliance', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('farm_accounts.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f'kind IN {KINDS}', name='ck_work_log_kind'))
    op.create_index('ix_work_log_farmer_done', 'farm_work_logs', ['farmer_id', 'done_on'])
    op.create_index('ix_farm_work_logs_plan_id', 'farm_work_logs', ['plan_id'])
    op.create_index('ix_farm_work_logs_event_id', 'farm_work_logs', ['event_id'], unique=True)
    op.create_index('ix_farm_work_logs_prescription_item_id', 'farm_work_logs', ['prescription_item_id'])

    op.create_table('farm_log_photos',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('log_id', sa.String(36), sa.ForeignKey('farm_work_logs.id', ondelete='CASCADE'), nullable=False),
        text_column('storage_key', 300),
        text_column('thumb_key', 300),
        sa.Column('bytes', sa.Integer(), nullable=False),
        sa.Column('width', sa.Integer()),
        sa.Column('height', sa.Integer()),
        sa.Column('taken_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lat', sa.Float()),
        sa.Column('lng', sa.Float()),
        text_column('caption', 200),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_farm_log_photos_log_id', 'farm_log_photos', ['log_id'])

    with op.batch_alter_table('application_records') as batch:
        batch.add_column(sa.Column('work_log_id', sa.String(36)))
        batch.create_foreign_key('fk_application_records_work_log', 'farm_work_logs',
                                 ['work_log_id'], ['id'], ondelete='CASCADE')
        batch.create_index('ix_application_records_work_log_id', ['work_log_id'], unique=True)


def downgrade():
    with op.batch_alter_table('application_records') as batch:
        batch.drop_index('ix_application_records_work_log_id')
        batch.drop_constraint('fk_application_records_work_log', type_='foreignkey')
        batch.drop_column('work_log_id')
    op.drop_table('farm_log_photos')
    op.drop_table('farm_work_logs')
