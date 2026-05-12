"""add_customer_name_ta

Revision ID: fcb1bdb0c670
Revises: 001
Create Date: 2026-03-09 14:38:48.553129

"""
from typing import Sequence, Union
from alembic import op

revision: str = 'fcb1bdb0c670'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE tbl_edi_customer ADD COLUMN IF NOT EXISTS customer_name_ta VARCHAR")
    op.execute("ALTER TABLE tbl_iop_customer ADD COLUMN IF NOT EXISTS customer_name_ta VARCHAR")
    # Drop old unique constraint if present, ensure named unique index exists
    op.execute("ALTER TABLE tbl_users DROP CONSTRAINT IF EXISTS tbl_users_username_key")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_tbl_users_username ON tbl_users (username)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tbl_users_username")
    op.execute("ALTER TABLE tbl_edi_customer DROP COLUMN IF EXISTS customer_name_ta")
    op.execute("ALTER TABLE tbl_iop_customer DROP COLUMN IF EXISTS customer_name_ta")
