"""IOP split collections: tbl_iop_transactions.principal_amount

Revision ID: 017_iop_principal_amount
Revises: 016_expense_categories
Create Date: 2026-10-23

A collection can be part interest, part principal (10,000 = 500 interest + 9,500 principal). The transaction keeps the interest in
`amount`; the principal is remembered here so that re-submitting, editing or deleting the transaction adds / takes back only the
difference on the customer's principal_paid. Existing rows get 0.
"""
from alembic import op

revision = "017_iop_principal_amount"
down_revision = "016_expense_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tbl_iop_transactions ADD COLUMN IF NOT EXISTS principal_amount NUMERIC(12, 2) NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE tbl_iop_transactions DROP COLUMN IF EXISTS principal_amount")
