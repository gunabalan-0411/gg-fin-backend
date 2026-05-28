"""add ignore boolean column to edi and iop customer tables

Revision ID: 008_ignore_flag
Revises: 007_nullable_balances
Create Date: 2026-05-28
"""
from typing import Sequence, Union
from alembic import op

revision: str = "008_ignore_flag"
down_revision: Union[str, None] = "007_nullable_balances"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE tbl_edi_customer ADD COLUMN IF NOT EXISTS ignore BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE tbl_iop_customer ADD COLUMN IF NOT EXISTS ignore BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE tbl_edi_customer DROP COLUMN IF EXISTS ignore")
    op.execute("ALTER TABLE tbl_iop_customer DROP COLUMN IF EXISTS ignore")
