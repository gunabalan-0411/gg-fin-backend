"""Personal Finance: friendly borrows (money the person owes) and investments (each row belongs to one user)

Revision ID: 020_personal_borrow_invest
Revises: 019_personal_records
Create Date: 2026-10-29
"""
from alembic import op

revision = "020_personal_borrow_invest"
down_revision = "019_personal_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_personal_borrow (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            from_whom VARCHAR NOT NULL,
            amount NUMERIC(14, 2) NOT NULL,
            pay_back_date DATE,
            is_settled BOOLEAN NOT NULL DEFAULT FALSE,
            notes VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_borrow_user_id ON tbl_personal_borrow (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_borrow_pay_back_date ON tbl_personal_borrow (pay_back_date)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_personal_investment (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name VARCHAR NOT NULL,
            inv_type VARCHAR NOT NULL,
            amount NUMERIC(14, 2) NOT NULL,
            current_value NUMERIC(14, 2),
            invested_date DATE,
            notes VARCHAR
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_investment_user_id ON tbl_personal_investment (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_personal_investment_invested_date ON tbl_personal_investment (invested_date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tbl_personal_investment")
    op.execute("DROP TABLE IF EXISTS tbl_personal_borrow")
