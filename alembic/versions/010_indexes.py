"""add performance indexes

Revision ID: 010_indexes
Revises: 009_investors
Create Date: 2026-05-29
"""
from typing import Union
from alembic import op

revision: str = "010_indexes"
down_revision: Union[str, None] = "009_investors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_edi_customer_balance ON tbl_edi_customer (outstanding_balance)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_edi_customer_name ON tbl_edi_customer (customer_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_iop_customer_name ON tbl_iop_customer (customer_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_edi_txn_date ON tbl_edi_transactions (collection_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_iop_txn_date ON tbl_iop_transactions (collection_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_upi_txn_date ON tbl_upi_transaction (transaction_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_upi_txn_vpa  ON tbl_upi_transaction (sender_vpa)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_edi_customer_balance")
    op.execute("DROP INDEX IF EXISTS ix_edi_customer_name")
    op.execute("DROP INDEX IF EXISTS ix_iop_customer_name")
    op.execute("DROP INDEX IF EXISTS ix_edi_txn_date")
    op.execute("DROP INDEX IF EXISTS ix_iop_txn_date")
    op.execute("DROP INDEX IF EXISTS ix_upi_txn_date")
    op.execute("DROP INDEX IF EXISTS ix_upi_txn_vpa")
