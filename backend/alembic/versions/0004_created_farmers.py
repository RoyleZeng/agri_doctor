"""代建農民帳號：一次性密碼、強制改密碼、連結來源。"""
from alembic import op
import sqlalchemy as sa

revision = 'created_farmers_0004'
down_revision = 'farm_content_0003'
branch_labels = None
depends_on = None


def upgrade():
    # 既有帳號都是自己註冊的：must_change_password 預設 false，不要讓現有農民下次登入被擋。
    op.add_column('farm_accounts', sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('farm_accounts', sa.Column('otp_expires_at', sa.String(40), nullable=True))
    op.add_column('farm_accounts', sa.Column('created_by', sa.String(36), nullable=True))
    # 既有連結都是農民自己給連結碼來的，所以預設 'code'。
    op.add_column('doctor_farmers', sa.Column('source', sa.String(12), nullable=False, server_default='code'))
    op.add_column('doctor_farmers', sa.Column('created_by', sa.String(36), nullable=True))


def downgrade():
    op.drop_column('doctor_farmers', 'created_by')
    op.drop_column('doctor_farmers', 'source')
    op.drop_column('farm_accounts', 'created_by')
    op.drop_column('farm_accounts', 'otp_expires_at')
    op.drop_column('farm_accounts', 'must_change_password')
