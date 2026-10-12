"""Add crawler coverage ledger and source change feed."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ppm_coverage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("source_snapshots.id"), nullable=False),
        sa.Column("target_key", sa.String(64), nullable=False),
        sa.Column("crop_name", sa.String(200), nullable=False),
        sa.Column("pest_name", sa.String(200), nullable=False),
        sa.Column("target_name", sa.String(400), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("source_records.id")),
        sa.Column("drug_count", sa.Integer()),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("html_sha256", sa.String(64)),
        sa.UniqueConstraint("snapshot_id", "target_key", name="uq_ppm_coverage_target"))
    op.create_index("ix_ppm_coverage_snapshot_id", "ppm_coverage", ["snapshot_id"])
    op.create_table("source_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("source_snapshots.id"), nullable=False),
        sa.Column("previous_snapshot_id", sa.Integer(), sa.ForeignKey("source_snapshots.id")),
        sa.Column("change_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("before", sa.JSON()), sa.Column("after", sa.JSON()),
        sa.Column("source_url", sa.Text()),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_date", sa.String(40)),
        sa.UniqueConstraint("snapshot_id", "change_key", name="uq_source_change"))
    for field in ("source", "snapshot_id", "kind", "detected_at"):
        op.create_index(f"ix_source_changes_{field}", "source_changes", [field])
    op.create_table("update_jobs", sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("schedule_label", sa.String(200), nullable=False))


def downgrade():
    for table in ("update_jobs", "source_changes", "ppm_coverage"):
        op.drop_table(table)
