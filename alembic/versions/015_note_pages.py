"""Note Pad: tbl_note_page (a few Markdown pages)

Revision ID: 015_note_pages
Revises: 014_customer_name_mappings
Create Date: 2026-10-20
"""
from alembic import op

revision = "015_note_pages"
down_revision = "014_customer_name_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_note_page (
            id SERIAL PRIMARY KEY,
            title VARCHAR(120) NOT NULL DEFAULT 'Untitled',
            content TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tbl_note_page")
