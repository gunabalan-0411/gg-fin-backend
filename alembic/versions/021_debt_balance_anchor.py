"""Lender-reported balance on tbl_debt (balance_anchor + balance_anchor_date), seeded from the bank sheet of 2025-10-24

Revision ID: 021_debt_balance_anchor
Revises: 020_personal_borrow_invest
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa

revision = "021_debt_balance_anchor"
down_revision = "020_personal_borrow_invest"
branch_labels = None
depends_on = None

ANCHOR_DATE = "2025-10-24"

# (loan start date, loan amount, balance the bank reported on ANCHOR_DATE). Matched on date + amount so a
# renamed lender / borrower still lands on the right row; a row is only touched when exactly one loan matches.
_BANK_BALANCES = [
    ("2024-09-07", 1180000, 975455),   # HDFC PL
    ("2023-02-01", 3200000, 3041416),  # HDFC Home L
    ("2023-01-07", 1000000, 514822),   # ICICI PL
    ("2025-02-01", 219704, 155114),    # RE Bike Loan
    ("2022-01-07", 450000, 202000),    # ICICI PL
    ("2023-01-07", 500000, 324000),    # HDFC PL
]


def upgrade() -> None:
    op.execute("ALTER TABLE tbl_debt ADD COLUMN IF NOT EXISTS balance_anchor NUMERIC(12, 2)")
    op.execute("ALTER TABLE tbl_debt ADD COLUMN IF NOT EXISTS balance_anchor_date DATE")
    conn = op.get_bind()
    for start, amount, balance in _BANK_BALANCES:
        matches = conn.execute(
            sa.text("SELECT id FROM tbl_debt WHERE date = :d AND amount = :a AND balance_anchor IS NULL"),
            {"d": start, "a": amount},
        ).fetchall()
        if len(matches) == 1:
            conn.execute(
                sa.text("UPDATE tbl_debt SET balance_anchor = :b, balance_anchor_date = :ad WHERE id = :id"),
                {"b": balance, "ad": ANCHOR_DATE, "id": matches[0][0]},
            )


def downgrade() -> None:
    op.execute("ALTER TABLE tbl_debt DROP COLUMN IF EXISTS balance_anchor_date")
    op.execute("ALTER TABLE tbl_debt DROP COLUMN IF EXISTS balance_anchor")
