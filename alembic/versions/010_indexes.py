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
    # EDI customer — dashboard queries filter on outstanding_balance frequently
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_edi_customer_balance ON tbl_edi_customer (outstanding_balance)")
    # EDI customer — name sort (newly added sortable column in UI)
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_edi_customer_name ON tbl_edi_customer (customer_name)")
    # IOP customer — name sort
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_iop_customer_name ON tbl_iop_customer (customer_name)")
    # Transaction dates — used in every dashboard/activity query
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_edi_txn_date ON tbl_edi_transactions (collection_date)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_iop_txn_date ON tbl_iop_transactions (collection_date)")
    # UPI — date and VPA are filtered/searched on every UPI page load
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_upi_txn_date ON tbl_upi_transaction (transaction_date)")
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_upi_txn_vpa  ON tbl_upi_transaction (sender_vpa)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_edi_customer_balance")
    op.execute("DROP INDEX IF EXISTS ix_edi_customer_name")
    op.execute("DROP INDEX IF EXISTS ix_iop_customer_name")
    op.execute("DROP INDEX IF EXISTS ix_edi_txn_date")
    op.execute("DROP INDEX IF EXISTS ix_iop_txn_date")
    op.execute("DROP INDEX IF EXISTS ix_upi_txn_date")
    op.execute("DROP INDEX IF EXISTS ix_upi_txn_vpa")
