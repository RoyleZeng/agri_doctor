"""資料來源：連線設定、感測群組的 adapter 外鍵、無人機任務、GIS 邊界。

* data_sources 是連線設定（HTTP／MQTT／CSV／UAV／GIS 檔案或 WMS／既有氣象預報）；金鑰存加密後的值。
* sensor_groups.source_id 的外鍵在 0013 就留了欄位（那時 data_sources 還沒建），這裡補上。
* uav_missions／field_boundaries 是新表；field_boundaries 一個田區最多一筆（unique）。

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'data_sources',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('farmer_id', sa.String(36), sa.ForeignKey('farm_accounts.id'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('label', sa.String(120), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('last_sync_at', sa.DateTime(timezone=True)),
        sa.Column('last_error', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('sensor_http','sensor_mqtt','sensor_csv','uav_upload','gis_file','gis_wms','weather')",
                           name='ck_data_source_kind'),
        sa.CheckConstraint("status IN ('connected','error','never')", name='ck_data_source_status'),
    )
    op.create_index('ix_data_sources_farmer_id', 'data_sources', ['farmer_id'])

    op.create_table(
        'uav_missions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('field_id', sa.String(36), sa.ForeignKey('farm_fields.id', ondelete='CASCADE'), nullable=False),
        sa.Column('flown_on', sa.Date(), nullable=False),
        sa.Column('product', sa.String(16), nullable=False),
        sa.Column('file_key', sa.String(300), nullable=False),
        sa.Column('crs', sa.String(20), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('is_demo', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("product IN ('ndvi','rgb','multispectral')", name='ck_uav_mission_product'),
        sa.CheckConstraint("status IN ('uploaded','processing','done','failed')", name='ck_uav_mission_status'),
    )
    op.create_index('ix_uav_missions_field_id', 'uav_missions', ['field_id'])

    op.create_table(
        'field_boundaries',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('field_id', sa.String(36), sa.ForeignKey('farm_fields.id', ondelete='CASCADE'), nullable=False),
        sa.Column('geojson', sa.JSON(), nullable=False),
        sa.Column('outside_cells', sa.JSON(), nullable=False),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('field_id', name='uq_field_boundary_field'),
    )

    with op.batch_alter_table('sensor_groups') as batch:
        batch.create_foreign_key('fk_sensor_groups_source', 'data_sources', ['source_id'], ['id'], ondelete='SET NULL')
        batch.create_index('ix_sensor_groups_source_id', ['source_id'])


def downgrade():
    with op.batch_alter_table('sensor_groups') as batch:
        batch.drop_index('ix_sensor_groups_source_id')
        batch.drop_constraint('fk_sensor_groups_source', type_='foreignkey')
    op.drop_table('field_boundaries')
    op.drop_table('uav_missions')
    op.drop_table('data_sources')
