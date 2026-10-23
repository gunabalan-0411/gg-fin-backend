"""Personal Finance: tbl_personal_expense (one user's own expenses, same categories as office expenses)

Revision ID: 018_personal_expenses
Revises: 017_iop_principal_amount
Create Date: 2026-10-23
"""
from alembic import op

revision = "018_personal_expenses"
down_revision = "017_iop_principal_amount"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_personal_expense (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount NUMERIC(12, 2) NOT NULL,
            date DATE NOT NULL,
            notes VARCHAR,
            category_id INTEGER,
            person_id INTEGER
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_expense_user_id ON tbl_personal_expense (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_expense_date ON tbl_personal_expense (date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_expense_category_id ON tbl_personal_expense (category_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_expense_person_id ON tbl_personal_expense (person_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tbl_personal_expense")
