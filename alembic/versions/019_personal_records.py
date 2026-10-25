"""Personal Finance: savings accounts, friendly lends, gifts received (each row belongs to one user)

Revision ID: 019_personal_records
Revises: 018_personal_expenses
Create Date: 2026-10-25
"""
from alembic import op

revision = "019_personal_records"
down_revision = "018_personal_expenses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_personal_saving (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount NUMERIC(14, 2) NOT NULL,
            bank VARCHAR NOT NULL,
            account_name VARCHAR NOT NULL,
            balance_date DATE NOT NULL,
            notes VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_saving_user_id ON tbl_personal_saving (user_id)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_personal_lend (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            to_whom VARCHAR NOT NULL,
            amount NUMERIC(14, 2) NOT NULL,
            follow_up_date DATE,
            is_settled BOOLEAN NOT NULL DEFAULT FALSE,
            notes VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_lend_user_id ON tbl_personal_lend (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_lend_follow_up_date ON tbl_personal_lend (follow_up_date)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_personal_gift (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            gift_type VARCHAR NOT NULL,
            amount NUMERIC(14, 2),
            gold_grams NUMERIC(10, 3),
            item_name VARCHAR,
            occasion VARCHAR,
            gift_from VARCHAR,
            received_date DATE,
            notes VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_gift_user_id ON tbl_personal_gift (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_gift_received_date ON tbl_personal_gift (received_date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tbl_personal_gift")
    op.execute("DROP TABLE IF EXISTS tbl_personal_lend")
    op.execute("DROP TABLE IF EXISTS tbl_personal_saving")
