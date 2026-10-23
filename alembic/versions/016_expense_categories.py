"""Expense categories (two levels) and "for whom": tbl_expense_category, tbl_expense_person, tbl_expense.category_id / person_id

Revision ID: 016_expense_categories
Revises: 015_note_pages
Create Date: 2026-10-23

Old expenses keep category_id / person_id NULL ("ungrouped"); nothing is guessed or changed here. The default categories and
people are created by the application the first time they are read (see app/services/expense_group_service.py).
"""
from alembic import op

revision = "016_expense_categories"
down_revision = "015_note_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_expense_category (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL,
            parent_id INTEGER
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_expense_category_parent_id ON tbl_expense_category (parent_id)")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_expense_category_name
        ON tbl_expense_category (COALESCE(parent_id, 0), lower(name))
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_expense_person (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_expense_person_name ON tbl_expense_person (lower(name))")
    op.execute("ALTER TABLE tbl_expense ADD COLUMN IF NOT EXISTS category_id INTEGER")
    op.execute("ALTER TABLE tbl_expense ADD COLUMN IF NOT EXISTS person_id INTEGER")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_expense_category_id ON tbl_expense (category_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_expense_person_id ON tbl_expense (person_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE tbl_expense DROP COLUMN IF EXISTS person_id")
    op.execute("ALTER TABLE tbl_expense DROP COLUMN IF EXISTS category_id")
    op.execute("DROP TABLE IF EXISTS tbl_expense_person")
    op.execute("DROP TABLE IF EXISTS tbl_expense_category")
