"""missing_tables — gmail, debt, unclaimed, defaulted

Revision ID: 006_missing_tables
Revises: 005_drive_settings
Create Date: 2026-03-20
"""
from typing import Sequence, Union
from alembic import op

revision: str = "006_missing_tables"
down_revision: Union[str, None] = "005_drive_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_gmail_settings (
            id INTEGER PRIMARY KEY,
            email VARCHAR,
            access_token VARCHAR,
            refresh_token VARCHAR,
            token_expiry TIMESTAMP WITHOUT TIME ZONE
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_debt (
            id SERIAL PRIMARY KEY,
            date DATE NOT NULL,
            lender_name VARCHAR NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            notes VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_debt_date ON tbl_debt (date)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_debt_repayment (
            id SERIAL PRIMARY KEY,
            debt_id INTEGER NOT NULL REFERENCES tbl_debt(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            notes VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_debt_repayment_debt_id ON tbl_debt_repayment (debt_id)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_unclaimed_balance (
            id SERIAL PRIMARY KEY,
            date DATE NOT NULL,
            product VARCHAR NOT NULL,
            customer_id INTEGER NOT NULL,
            customer_name VARCHAR NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            notes VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_unclaimed_balance_date ON tbl_unclaimed_balance (date)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_defaulted_balance (
            id SERIAL PRIMARY KEY,
            date DATE NOT NULL,
            product VARCHAR NOT NULL,
            customer_id INTEGER NOT NULL,
            customer_name VARCHAR NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            notes VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_defaulted_balance_date ON tbl_defaulted_balance (date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tbl_defaulted_balance")
    op.execute("DROP TABLE IF EXISTS tbl_unclaimed_balance")
    op.execute("DROP TABLE IF EXISTS tbl_debt_repayment")
    op.execute("DROP TABLE IF EXISTS tbl_debt")
    op.execute("DROP TABLE IF EXISTS tbl_gmail_settings")
