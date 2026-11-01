"""Personal Finance: monthly budget per top-level expense category (each row belongs to one user)

Revision ID: 022_personal_budget
Revises: 021_debt_balance_anchor
Create Date: 2026-11-02
"""
from alembic import op

revision = "022_personal_budget"
down_revision = "021_debt_balance_anchor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_personal_budget (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            amount NUMERIC(14, 2) NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_budget_user_id ON tbl_personal_budget (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_budget_category_id ON tbl_personal_budget (category_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_personal_budget_user_category ON tbl_personal_budget (user_id, category_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tbl_personal_budget")
