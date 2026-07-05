"""add is_closed and principal_paid; drop loan_closure

Revision ID: 011_balance_and_closure
Revises: 010_indexes
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa

revision = "011_balance_and_closure"
down_revision = "010_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── EDI: add is_closed ────────────────────────────────────────────────────
    op.add_column(
        "tbl_edi_customer",
        sa.Column("is_closed", sa.Boolean(), server_default="false", nullable=False),
    )
    # Backfill EDI outstanding_balance from actual paid transactions
    op.execute("""
        UPDATE tbl_edi_customer c
        SET outstanding_balance = (
            COALESCE(c.loan_amount, 0) -
            COALESCE((
                SELECT SUM(t.amount)
                FROM tbl_edi_transactions t
                WHERE t.customer_id = c.customer_id
                  AND t.payment_status = 'PAID'
            ), 0)
        )
    """)
    # Set is_closed for EDI
    op.execute("""
        UPDATE tbl_edi_customer
        SET is_closed = (outstanding_balance IS NOT NULL AND outstanding_balance <= 0)
    """)

    # ── IOP: add new columns ──────────────────────────────────────────────────
    op.add_column(
        "tbl_iop_customer",
        sa.Column("principal_paid", sa.Numeric(12, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "tbl_iop_customer",
        sa.Column("outstanding_balance", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "tbl_iop_customer",
        sa.Column("is_closed", sa.Boolean(), server_default="false", nullable=False),
    )
    # Backfill IOP is_closed from loan_closure (0 = closed), outstanding_balance from loan_amount
    op.execute("""
        UPDATE tbl_iop_customer
        SET outstanding_balance = COALESCE(loan_amount, 0),
            is_closed = (loan_closure IS NOT NULL AND loan_closure = 0)
    """)
    # Drop loan_closure
    op.drop_column("tbl_iop_customer", "loan_closure")


def downgrade() -> None:
    op.add_column(
        "tbl_iop_customer",
        sa.Column("loan_closure", sa.Integer(), nullable=True),
    )
    op.execute("UPDATE tbl_iop_customer SET loan_closure = CASE WHEN is_closed THEN 0 ELSE 1 END")
    op.drop_column("tbl_iop_customer", "is_closed")
    op.drop_column("tbl_iop_customer", "outstanding_balance")
    op.drop_column("tbl_iop_customer", "principal_paid")
    op.drop_column("tbl_edi_customer", "is_closed")
