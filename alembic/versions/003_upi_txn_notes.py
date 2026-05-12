"""upi_txn_notes

Revision ID: 003_upi_notes
Revises: 002_upi_vpa
Create Date: 2026-03-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003_upi_notes"
down_revision: Union[str, None] = "002_upi_vpa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE tbl_upi_transactions ADD COLUMN IF NOT EXISTS notes VARCHAR")


def downgrade() -> None:
    op.execute("ALTER TABLE tbl_upi_transactions DROP COLUMN IF EXISTS notes")
