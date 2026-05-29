"""add tbl_investor

Revision ID: 009_investors
Revises: 008_ignore_flag
Create Date: 2026-05-29
"""
from typing import Union
from alembic import op

revision: str = "009_investors"
down_revision: Union[str, None] = "008_ignore_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_investor (
            id SERIAL PRIMARY KEY,
            date DATE NOT NULL,
            investor_name TEXT NOT NULL,
            amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
            return_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
            notes TEXT
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tbl_investor")
