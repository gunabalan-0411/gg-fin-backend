"""Add EMI/loan metadata columns to tbl_debt and seed initial loan records.

Revision ID: 013_debt_loan_details
Revises: 012_fix_closure_backfill
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa
from datetime import date as d

revision = "013_debt_loan_details"
down_revision = "012_fix_closure_backfill"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Loan seed data — current balances calculated as of 2026-07-09
# Formula: B = PMT × [1 - (1+r)^(-n_remaining)] / r
# Principal_paid = loan_amount - current_balance
# ---------------------------------------------------------------------------
_LOANS = [
    dict(
        date=d(2024, 9, 7),
        lender_name="HDFC PL",
        borrower_name="Gu",
        amount=1180000,
        emi_amount=25568,
        interest_rate_pa=10.9,
        tenure_months=60,
        end_date=d(2029, 9, 7),
        paid_by="Business",
        notes=None,
        principal_paid=361427,   # 1180000 - 818573
    ),
    dict(
        date=d(2023, 2, 1),
        lender_name="HDFC Home L",
        borrower_name="Gu",
        amount=3200000,
        emi_amount=29170,
        interest_rate_pa=8.0,
        tenure_months=240,
        end_date=d(2041, 7, 5),
        paid_by="Gu",
        notes=None,
        principal_paid=145200,   # 3200000 - 3054800
    ),
    dict(
        date=d(2023, 1, 7),
        lender_name="ICICI PL",
        borrower_name="Kam",
        amount=1000000,
        emi_amount=21489,
        interest_rate_pa=10.5,
        tenure_months=60,
        end_date=d(2028, 1, 7),
        paid_by="Business",
        notes=None,
        principal_paid=643429,   # 1000000 - 356571
    ),
    dict(
        date=d(2025, 2, 1),
        lender_name="RE Bike Loan",
        borrower_name="Kam",
        amount=219704,
        emi_amount=10995,
        interest_rate_pa=10.1,
        tenure_months=24,
        end_date=d(2027, 2, 5),
        paid_by="Business",
        notes=None,
        principal_paid=145275,   # 219704 - 74429
    ),
    dict(
        date=d(2022, 1, 7),
        lender_name="ICICI PL",
        borrower_name="Suji",
        amount=450000,
        emi_amount=10000,
        interest_rate_pa=13.0,
        tenure_months=60,
        end_date=d(2027, 9, 5),
        paid_by="Business",
        notes=None,
        principal_paid=320760,   # 450000 - 129240
    ),
    dict(
        date=d(2023, 1, 7),
        lender_name="HDFC PL",
        borrower_name="Suji",
        amount=500000,
        emi_amount=11000,
        interest_rate_pa=13.0,
        tenure_months=60,
        end_date=d(2028, 9, 5),
        paid_by="Business",
        notes=None,
        principal_paid=251917,   # 500000 - 248083
    ),
]


def upgrade():
    op.add_column("tbl_debt", sa.Column("borrower_name", sa.String(), nullable=True))
    op.add_column("tbl_debt", sa.Column("emi_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("tbl_debt", sa.Column("interest_rate_pa", sa.Numeric(5, 2), nullable=True))
    op.add_column("tbl_debt", sa.Column("tenure_months", sa.Integer(), nullable=True))
    op.add_column("tbl_debt", sa.Column("end_date", sa.Date(), nullable=True))
    op.add_column("tbl_debt", sa.Column("paid_by", sa.String(), nullable=True))

    conn = op.get_bind()

    # Only seed if the table is currently empty
    existing = conn.execute(sa.text("SELECT COUNT(*) FROM tbl_debt")).scalar()
    if existing > 0:
        return

    # Balance is computed dynamically via amortization formula in the API,
    # so no repayment entries are seeded here.
    for loan in _LOANS:
        conn.execute(
            sa.text("""
                INSERT INTO tbl_debt
                    (date, lender_name, borrower_name, amount, emi_amount,
                     interest_rate_pa, tenure_months, end_date, paid_by, notes)
                VALUES
                    (:date, :lender_name, :borrower_name, :amount, :emi_amount,
                     :interest_rate_pa, :tenure_months, :end_date, :paid_by, :notes)
            """),
            {k: v for k, v in loan.items() if k != "principal_paid"},
        )


def downgrade():
    op.drop_column("tbl_debt", "paid_by")
    op.drop_column("tbl_debt", "end_date")
    op.drop_column("tbl_debt", "tenure_months")
    op.drop_column("tbl_debt", "interest_rate_pa")
    op.drop_column("tbl_debt", "emi_amount")
    op.drop_column("tbl_debt", "borrower_name")
