"""alias / AI-mismatch name mapping tables + triggers that keep them in step with the customer tables

Creates
    tbl_customer_alias, tbl_customer_ai_mismatch       standard mapping tables (no FK to customers)
    fn_ggfin_* functions, trg_ggfin_* triggers          re-point / rename mappings when customers change
    vw_customer_alias, vw_customer_ai_mismatch          mapping rows + live "is customer active"

See Documentation.md in the project root and app/db/mapping_schema.py.

Revision ID: 014_customer_name_mappings
Revises: 013_debt_loan_details
Create Date: 2026-10-13
"""
from alembic import op

from app.db import mapping_schema

revision = "014_customer_name_mappings"
down_revision = "013_debt_loan_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    mapping_schema.create_tables(conn)
    mapping_schema.apply(conn)
    # Backfill: nothing to re-point on a brand-new table, but keeps the migration safe to re-run.
    mapping_schema.resync_all(conn)


def downgrade() -> None:
    conn = op.get_bind()
    mapping_schema.drop_all(conn)
    op.execute("DROP TABLE IF EXISTS tbl_customer_ai_mismatch")
    op.execute("DROP TABLE IF EXISTS tbl_customer_alias")
