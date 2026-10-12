"""Preserve distinct application methods and store chosen weather county."""
import hashlib
import json
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("farm_accounts", sa.Column("weather_county", sa.String(12)))
    op.add_column("registrations", sa.Column("usage_hash", sa.String(64)))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id,dilution_text,dose_per_hectare,application_method,precautions FROM registrations")).mappings().all()
    for r in rows:
        value = [r[k] for k in ('dilution_text','dose_per_hectare','application_method','precautions')]
        digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        conn.execute(sa.text("UPDATE registrations SET usage_hash=:hash WHERE id=:id"), {'hash':digest,'id':r['id']})
    op.drop_constraint("uq_registration", "registrations", type_="unique")
    op.alter_column("registrations", "usage_hash", nullable=False)
    op.create_unique_constraint("uq_registration_usage", "registrations", ["crop_id","pest_id","product_id","usage_hash"])


def downgrade():
    # Old key cannot represent multiple distinct application methods. Fail safely
    # on duplicates rather than deleting any of the acquired evidence.
    op.drop_constraint("uq_registration_usage", "registrations", type_="unique")
    op.create_unique_constraint("uq_registration", "registrations", ["crop_id","pest_id","product_id","dilution_text","dose_per_hectare"])
    op.drop_column("registrations", "usage_hash")
    op.drop_column("farm_accounts", "weather_county")
