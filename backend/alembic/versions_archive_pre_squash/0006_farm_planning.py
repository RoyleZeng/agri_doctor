"""Add private farm accounts, doctor links, plans, events and shared notes."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("farm_accounts", sa.Column("id", sa.String(36), primary_key=True), sa.Column("username", sa.String(120), nullable=False, unique=True), sa.Column("name", sa.String(80), nullable=False), sa.Column("role", sa.String(12), nullable=False), sa.Column("password_hash", sa.String(256), nullable=False), sa.Column("farm_name", sa.String(120), nullable=False), sa.Column("location", sa.String(120), nullable=False))
    op.create_table("farm_sessions", sa.Column("token_hash", sa.String(64), primary_key=True), sa.Column("account_id", sa.String(36), sa.ForeignKey("farm_accounts.id"), nullable=False), sa.Column("expires_at", sa.Integer(), nullable=False))
    op.create_index("ix_farm_sessions_account_id", "farm_sessions", ["account_id"])
    op.create_table("farm_invites", sa.Column("code_hash", sa.String(64), primary_key=True), sa.Column("farmer_id", sa.String(36), sa.ForeignKey("farm_accounts.id"), nullable=False), sa.Column("expires_at", sa.Integer(), nullable=False))
    op.create_index("ix_farm_invites_farmer_id", "farm_invites", ["farmer_id"])
    op.create_table("doctor_farmers", sa.Column("doctor_id", sa.String(36), sa.ForeignKey("farm_accounts.id"), primary_key=True), sa.Column("farmer_id", sa.String(36), sa.ForeignKey("farm_accounts.id"), primary_key=True))
    op.create_table("farm_plans", sa.Column("id", sa.String(36), primary_key=True), sa.Column("farmer_id", sa.String(36), sa.ForeignKey("farm_accounts.id"), nullable=False), sa.Column("crop_key", sa.String(40)), sa.Column("crop_name", sa.String(80), nullable=False), sa.Column("field_name", sa.String(100), nullable=False), sa.Column("variety", sa.String(80), nullable=False), sa.Column("area", sa.String(60), nullable=False), sa.Column("planted_on", sa.Date(), nullable=False), sa.Column("end_on", sa.Date(), nullable=False), sa.Column("notes", sa.Text(), nullable=False), sa.Column("archived", sa.Boolean(), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("template_version", sa.String(80)), sa.Column("sources", sa.JSON(), nullable=False))
    op.create_index("ix_farm_plans_farmer_id", "farm_plans", ["farmer_id"])
    op.create_table("farm_events", sa.Column("id", sa.String(36), primary_key=True), sa.Column("plan_id", sa.String(36), sa.ForeignKey("farm_plans.id"), nullable=False), sa.Column("kind", sa.String(20), nullable=False), sa.Column("title", sa.String(120), nullable=False), sa.Column("start_on", sa.Date(), nullable=False), sa.Column("end_on", sa.Date(), nullable=False), sa.Column("detail", sa.Text(), nullable=False), sa.Column("source_ids", sa.JSON(), nullable=False), sa.Column("completed", sa.Boolean(), nullable=False), sa.Column("note", sa.Text(), nullable=False), sa.Column("updated_by", sa.String(80), nullable=False), sa.Column("basis", sa.String(40), nullable=False))
    op.create_index("ix_farm_events_plan_id", "farm_events", ["plan_id"])
    op.create_table("farm_notes", sa.Column("id", sa.String(36), primary_key=True), sa.Column("farmer_id", sa.String(36), sa.ForeignKey("farm_accounts.id"), nullable=False), sa.Column("author_id", sa.String(36), sa.ForeignKey("farm_accounts.id"), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("created_at", sa.Integer(), nullable=False))
    op.create_index("ix_farm_notes_farmer_id", "farm_notes", ["farmer_id"])


def downgrade():
    for table in ["farm_notes", "farm_events", "farm_plans", "doctor_farmers", "farm_invites", "farm_sessions", "farm_accounts"]:
        op.drop_table(table)
