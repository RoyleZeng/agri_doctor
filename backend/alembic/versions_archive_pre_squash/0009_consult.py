"""保留診療、處方快照與依田區區隔的實際施藥紀錄。"""
from alembic import op
import sqlalchemy as sa

revision='0009'
down_revision='0008'
branch_labels=None
depends_on=None


def timestamps():
    return [sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
            sa.Column('updated_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False)]


def upgrade():
    op.create_table('consult_cases',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('doctor_id',sa.String(36),sa.ForeignKey('farm_accounts.id'),nullable=False),
        sa.Column('farmer_id',sa.String(36),sa.ForeignKey('farm_accounts.id',ondelete='SET NULL')),
        sa.Column('farmer_text',sa.String(120),nullable=False),
        sa.Column('case_no',sa.String(32),nullable=False,unique=True),
        sa.Column('plan_id',sa.String(36),sa.ForeignKey('farm_plans.id',ondelete='SET NULL')),
        sa.Column('crop_id',sa.Integer(),sa.ForeignKey('crops.id',ondelete='SET NULL')),
        sa.Column('crop_text',sa.String(80),nullable=False),
        sa.Column('variety',sa.String(80),nullable=False),
        sa.Column('field_text',sa.String(160),nullable=False),
        sa.Column('area_text',sa.String(60),nullable=False),
        sa.Column('expected_harvest_on',sa.Date()),
        sa.Column('examined_by',sa.JSON(),nullable=False),
        sa.Column('symptoms',sa.Text(),nullable=False),
        sa.Column('diagnosis_pest_id',sa.Integer(),sa.ForeignKey('pests.id',ondelete='SET NULL')),
        sa.Column('diagnosis_text',sa.String(160),nullable=False),
        sa.Column('diagnosis_basis',sa.JSON(),nullable=False),
        sa.Column('cultural_control',sa.Text(),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('issued_at',sa.DateTime(timezone=True)),
        sa.Column('version',sa.Integer(),nullable=False),
        *timestamps(),sa.CheckConstraint("status IN ('draft','saved','issued')",name='ck_consult_status'))
    op.create_index('ix_consult_doctor_created','consult_cases',['doctor_id','created_at'])
    op.create_index('ix_consult_cases_farmer_id','consult_cases',['farmer_id'])
    op.create_index('ix_consult_cases_plan_id','consult_cases',['plan_id'])
    op.create_table('prescription_items',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('case_id',sa.String(36),sa.ForeignKey('consult_cases.id',ondelete='CASCADE'),nullable=False),
        sa.Column('registration_id',sa.Integer(),sa.ForeignKey('registrations.id',ondelete='SET NULL')),
        sa.Column('product_text',sa.String(200),nullable=False),
        sa.Column('ingredient_text',sa.String(200),nullable=False),
        sa.Column('moa_text',sa.String(60),nullable=False),
        sa.Column('dilution',sa.String(40)),sa.Column('dose',sa.String(40)),
        sa.Column('frequency',sa.String(80),nullable=False),sa.Column('phi_days',sa.Integer()),
        sa.Column('advice',sa.Text(),nullable=False),sa.Column('source_values',sa.JSON(),nullable=False),
        sa.Column('checks',sa.JSON(),nullable=False),sa.Column('sort_order',sa.Integer(),nullable=False),
        sa.CheckConstraint('phi_days IS NULL OR phi_days >= 0',name='ck_prescription_phi'))
    op.create_index('ix_prescription_items_case_id','prescription_items',['case_id'])
    op.create_index('ix_prescription_items_registration_id','prescription_items',['registration_id'])
    op.create_table('application_records',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('farmer_id',sa.String(36),sa.ForeignKey('farm_accounts.id'),nullable=False),
        sa.Column('plan_id',sa.String(36),sa.ForeignKey('farm_plans.id',ondelete='SET NULL')),
        sa.Column('case_id',sa.String(36),sa.ForeignKey('consult_cases.id',ondelete='SET NULL')),
        sa.Column('field_text',sa.String(160),nullable=False),
        sa.Column('product_text',sa.String(200),nullable=False),
        sa.Column('moa_codes',sa.JSON(),nullable=False),sa.Column('applied_on',sa.Date(),nullable=False),
        sa.Column('note',sa.Text(),nullable=False),*timestamps())
    op.create_index('ix_application_farmer_field_date','application_records',['farmer_id','field_text','applied_on'])


def downgrade():
    for name in ('application_records','prescription_items','consult_cases'):
        op.drop_table(name)
