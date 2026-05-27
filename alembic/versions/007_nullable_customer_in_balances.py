"""nullable customer_id and customer_name in unclaimed/defaulted balance tables

Revision ID: 007_nullable_customer_in_balances
Revises: 006_missing_tables
Create Date: 2026-05-27
"""
from typing import Sequence, Union
from alembic import op

revision: str = "007_nullable_customer_in_balances"
down_revision: Union[str, None] = "006_missing_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE tbl_unclaimed_balance ALTER COLUMN customer_id DROP NOT NULL")
    op.execute("ALTER TABLE tbl_unclaimed_balance ALTER COLUMN customer_name DROP NOT NULL")
    op.execute("ALTER TABLE tbl_defaulted_balance ALTER COLUMN customer_id DROP NOT NULL")
    op.execute("ALTER TABLE tbl_defaulted_balance ALTER COLUMN customer_name DROP NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE tbl_unclaimed_balance SET customer_id = 0 WHERE customer_id IS NULL")
    op.execute("UPDATE tbl_unclaimed_balance SET customer_name = '' WHERE customer_name IS NULL")
    op.execute("ALTER TABLE tbl_unclaimed_balance ALTER COLUMN customer_id SET NOT NULL")
    op.execute("ALTER TABLE tbl_unclaimed_balance ALTER COLUMN customer_name SET NOT NULL")
    op.execute("UPDATE tbl_defaulted_balance SET customer_id = 0 WHERE customer_id IS NULL")
    op.execute("UPDATE tbl_defaulted_balance SET customer_name = '' WHERE customer_name IS NULL")
    op.execute("ALTER TABLE tbl_defaulted_balance ALTER COLUMN customer_id SET NOT NULL")
    op.execute("ALTER TABLE tbl_defaulted_balance ALTER COLUMN customer_name SET NOT NULL")
