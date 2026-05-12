"""upi_txn_type_and_vpa_mappings

Revision ID: 002_upi_vpa
Revises: fcb1bdb0c670
Create Date: 2026-03-14

"""
from typing import Sequence, Union
from alembic import op

revision: str = "002_upi_vpa"
down_revision: Union[str, None] = "fcb1bdb0c670"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create tbl_upi_transactions if not exists (may have been created by init_db)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_upi_transactions (
            id SERIAL PRIMARY KEY,
            upi_ref_no VARCHAR NOT NULL UNIQUE,
            amount NUMERIC(12,2) NOT NULL,
            transaction_type VARCHAR NOT NULL DEFAULT 'credit',
            sender_vpa VARCHAR,
            sender_name VARCHAR,
            notes VARCHAR,
            transaction_date DATE NOT NULL,
            source VARCHAR NOT NULL DEFAULT 'gmail',
            mapped_customer_id INTEGER,
            mapped_customer_type VARCHAR,
            mapped_customer_name VARCHAR
        )
    """)
    # Add transaction_type if somehow missing
    op.execute("""
        ALTER TABLE tbl_upi_transactions
        ADD COLUMN IF NOT EXISTS transaction_type VARCHAR NOT NULL DEFAULT 'credit'
    """)
    # Create vpa mappings table
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_upi_vpa_mappings (
            id SERIAL PRIMARY KEY,
            upi_vpa VARCHAR NOT NULL,
            customer_id INTEGER NOT NULL,
            customer_type VARCHAR NOT NULL,
            customer_name VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_upi_vpa_mappings_upi_vpa ON tbl_upi_vpa_mappings (upi_vpa)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tbl_upi_vpa_mappings")
    op.execute("ALTER TABLE tbl_upi_transactions DROP COLUMN IF EXISTS transaction_type")
