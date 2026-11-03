"""Personal Finance: expense accounts (funds, bank matching), 'paid from' on personal expenses, rollover start month on budgets

Revision ID: 023_personal_accounts
Revises: 022_personal_budget
Create Date: 2026-11-03
"""
from alembic import op

revision = "023_personal_accounts"
down_revision = "022_personal_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_personal_account (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name VARCHAR NOT NULL,
            opening_balance NUMERIC(14, 2) NOT NULL DEFAULT 0,
            opening_date DATE NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_account_user_id ON tbl_personal_account (user_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_personal_account_user_name ON tbl_personal_account (user_id, lower(name))")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_personal_account_entry (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            "date" DATE NOT NULL,
            amount NUMERIC(14, 2) NOT NULL,
            kind VARCHAR NOT NULL DEFAULT 'fund',
            notes VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_account_entry_user_id ON tbl_personal_account_entry (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_account_entry_account_id ON tbl_personal_account_entry (account_id)")
    op.execute('CREATE INDEX IF NOT EXISTS ix_tbl_personal_account_entry_date ON tbl_personal_account_entry ("date")')
    op.execute("ALTER TABLE tbl_personal_expense ADD COLUMN IF NOT EXISTS account_id INTEGER")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_expense_account_id ON tbl_personal_expense (account_id)")
    # budgets already set start rolling forward from the month of this migration
    op.execute("ALTER TABLE tbl_personal_budget ADD COLUMN IF NOT EXISTS start_month DATE NOT NULL DEFAULT date_trunc('month', now())::date")


def downgrade() -> None:
    op.execute("ALTER TABLE tbl_personal_budget DROP COLUMN IF EXISTS start_month")
    op.execute("DROP INDEX IF EXISTS ix_tbl_personal_expense_account_id")
    op.execute("ALTER TABLE tbl_personal_expense DROP COLUMN IF EXISTS account_id")
    op.execute("DROP TABLE IF EXISTS tbl_personal_account_entry")
    op.execute("DROP TABLE IF EXISTS tbl_personal_account")
