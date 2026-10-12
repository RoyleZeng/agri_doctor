"""精準模式資料表；日誌補安全採收期快照與田區連結。

* farm_fields 是田區主檔；farm_work_logs 與 farm_events 加 field_id（nullable，SET NULL），field_text 仍是快照。
* farm_work_logs.phi_days / phi_source：安全採收期快照。既有紀錄從處方項目或登記快照回填——
  處方項目日後被刪，已匯出的核對結果不能變成「無法判斷」。
* farm_work_logs.recommendation_id 在 0010 先留欄位，這裡補外鍵。
* 每張新表都有 is_demo：示範資料與真實資料共用同一套表。
* sensor_groups.source_id 指向第 7 步才建的 data_sources，外鍵留到那一步。

Revision ID: 0013
Revises: 0012
"""
import json

from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

READING_ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade():
    op.create_table(
        'farm_fields',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('farmer_id', sa.String(36), sa.ForeignKey('farm_accounts.id'), nullable=False),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('area_text', sa.String(60), nullable=False),
        sa.Column('grid_cols', sa.Integer(), nullable=False),
        sa.Column('grid_rows', sa.Integer(), nullable=False),
        sa.Column('is_demo', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_farm_fields_farmer_id', 'farm_fields', ['farmer_id'])

    op.create_table(
        'farm_zones',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('field_id', sa.String(36), sa.ForeignKey('farm_fields.id', ondelete='CASCADE'), nullable=False),
        sa.Column('code', sa.String(8), nullable=False),
        sa.Column('label', sa.String(40), nullable=False),
        sa.Column('cells', sa.JSON(), nullable=False),
        sa.Column('area_ha', sa.Float()),
        sa.Column('is_demo', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('field_id', 'code', name='uq_farm_zone_code'),
    )
    op.create_index('ix_farm_zones_field_id', 'farm_zones', ['field_id'])

    op.create_table(
        'sensor_groups',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('field_id', sa.String(36), sa.ForeignKey('farm_fields.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(80), nullable=False),
        sa.Column('source_id', sa.String(36)),
        sa.Column('metrics', sa.JSON(), nullable=False),
        sa.Column('is_demo', sa.Boolean(), nullable=False),
    )
    op.create_index('ix_sensor_groups_field_id', 'sensor_groups', ['field_id'])

    op.create_table(
        'sensor_stations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('field_id', sa.String(36), sa.ForeignKey('farm_fields.id', ondelete='CASCADE'), nullable=False),
        sa.Column('group_id', sa.String(36), sa.ForeignKey('sensor_groups.id', ondelete='SET NULL')),
        sa.Column('zone_id', sa.String(36), sa.ForeignKey('farm_zones.id', ondelete='SET NULL')),
        sa.Column('code', sa.String(20), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('grid_col', sa.Integer()),
        sa.Column('grid_row', sa.Integer()),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('is_demo', sa.Boolean(), nullable=False),
    )
    for column in ('field_id', 'group_id', 'zone_id'):
        op.create_index(f'ix_sensor_stations_{column}', 'sensor_stations', [column])

    op.create_table(
        'sensor_readings',
        sa.Column('id', READING_ID, primary_key=True, autoincrement=True),
        sa.Column('station_id', sa.String(36), sa.ForeignKey('sensor_stations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('metric', sa.String(24), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(12), nullable=False),
        sa.Column('is_demo', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('station_id', 'metric', 'ts', name='uq_sensor_reading'),
    )
    op.create_index('ix_sensor_readings_station_id', 'sensor_readings', ['station_id'])

    op.create_table(
        'field_layers',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('field_id', sa.String(36), sa.ForeignKey('farm_fields.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('captured_on', sa.Date(), nullable=False),
        sa.Column('source_text', sa.String(120), nullable=False),
        sa.Column('cell_values', sa.JSON(), nullable=False),
        sa.Column('zone_values', sa.JSON(), nullable=False),
        sa.Column('is_demo', sa.Boolean(), nullable=False),
        sa.CheckConstraint("kind IN ('ndvi','soil_moisture','ph','yield_prev')", name='ck_field_layer_kind'),
    )
    op.create_index('ix_field_layer_field_kind_date', 'field_layers', ['field_id', 'kind', 'captured_on'])

    op.create_table(
        'recommendations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('field_id', sa.String(36), sa.ForeignKey('farm_fields.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=False),
        sa.Column('five_r', sa.JSON(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('proposed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_id', sa.String(36), sa.ForeignKey('farm_events.id', ondelete='SET NULL')),
        sa.Column('work_log_id', sa.String(36), sa.ForeignKey('farm_work_logs.id', ondelete='SET NULL')),
        sa.Column('rule_version', sa.String(40), nullable=False),
        sa.Column('decided_by', sa.String(36), sa.ForeignKey('farm_accounts.id')),
        sa.Column('decided_at', sa.DateTime(timezone=True)),
        sa.Column('status_note', sa.Text(), nullable=False),
        sa.Column('is_demo', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('proposed','accepted','done','dismissed','expired')", name='ck_recommendation_status'),
        sa.CheckConstraint("kind IN ('irrigate','vrt_fertilizer')", name='ck_recommendation_kind'),
    )
    for column in ('field_id', 'event_id', 'work_log_id'):
        op.create_index(f'ix_recommendations_{column}', 'recommendations', [column])
    op.create_index('uq_recommendation_proposed', 'recommendations', ['field_id', 'kind'], unique=True,
                    postgresql_where=sa.text("status = 'proposed'"), sqlite_where=sa.text("status = 'proposed'"))

    with op.batch_alter_table('farm_accounts') as batch:
        batch.add_column(sa.Column('precision_enabled', sa.Boolean(), server_default=sa.false(), nullable=False))

    with op.batch_alter_table('farm_events') as batch:
        batch.add_column(sa.Column('field_id', sa.String(36)))
        batch.create_foreign_key('fk_farm_events_field', 'farm_fields', ['field_id'], ['id'], ondelete='SET NULL')
        batch.create_index('ix_farm_events_field_id', ['field_id'])

    with op.batch_alter_table('farm_work_logs') as batch:
        batch.add_column(sa.Column('field_id', sa.String(36)))
        batch.add_column(sa.Column('phi_days', sa.Integer()))
        batch.add_column(sa.Column('phi_source', sa.String(16), server_default='none', nullable=False))
        batch.create_check_constraint('ck_work_log_phi_source', "phi_source IN ('prescription','registration','none')")
        batch.create_foreign_key('fk_farm_work_logs_field', 'farm_fields', ['field_id'], ['id'], ondelete='SET NULL')
        batch.create_foreign_key('fk_farm_work_logs_recommendation', 'recommendations', ['recommendation_id'], ['id'],
                                 ondelete='SET NULL')
        batch.create_index('ix_farm_work_logs_field_id', ['field_id'])
        batch.create_index('ix_farm_work_logs_recommendation_id', ['recommendation_id'])

    if op.get_context().as_sql:
        # 離線產生 SQL 時讀不到既有資料，回填只能在線上執行 upgrade 時做。
        op.execute("-- 0013: farm_work_logs.phi_days / phi_source 的回填需在線上執行 alembic upgrade")
    else:
        backfill_phi(op.get_bind())


def backfill_phi(conn):
    """既有施藥紀錄補快照：處方優先（與作用機制同一順序），其次登記快照；兩者都沒有就維持 none。"""
    logs = conn.execute(sa.text(
        "SELECT id, prescription_item_id, registration_snapshot FROM farm_work_logs WHERE kind = 'spray'")).all()
    for log_id, item_id, snapshot in logs:
        if item_id:
            row = conn.execute(sa.text("SELECT phi_days FROM prescription_items WHERE id = :id"), {"id": item_id}).first()
            if row is not None:
                conn.execute(sa.text("UPDATE farm_work_logs SET phi_days = :days, phi_source = 'prescription' WHERE id = :id"),
                             {"days": row[0], "id": log_id})
                continue
        if snapshot:
            data = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
            conn.execute(sa.text("UPDATE farm_work_logs SET phi_days = :days, phi_source = 'registration' WHERE id = :id"),
                         {"days": data.get("phi_days"), "id": log_id})


def downgrade():
    with op.batch_alter_table('farm_work_logs') as batch:
        batch.drop_index('ix_farm_work_logs_recommendation_id')
        batch.drop_index('ix_farm_work_logs_field_id')
        batch.drop_constraint('fk_farm_work_logs_recommendation', type_='foreignkey')
        batch.drop_constraint('fk_farm_work_logs_field', type_='foreignkey')
        batch.drop_constraint('ck_work_log_phi_source', type_='check')
        batch.drop_column('phi_source')
        batch.drop_column('phi_days')
        batch.drop_column('field_id')
    with op.batch_alter_table('farm_events') as batch:
        batch.drop_index('ix_farm_events_field_id')
        batch.drop_constraint('fk_farm_events_field', type_='foreignkey')
        batch.drop_column('field_id')
    with op.batch_alter_table('farm_accounts') as batch:
        batch.drop_column('precision_enabled')
    for table in ('recommendations', 'field_layers', 'sensor_readings', 'sensor_stations', 'sensor_groups',
                  'farm_zones', 'farm_fields'):
        op.drop_table(table)
