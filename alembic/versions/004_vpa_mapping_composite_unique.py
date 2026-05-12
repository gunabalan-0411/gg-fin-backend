"""vpa_mapping_composite_unique

Revision ID: 004_vpa_composite
Revises: 003_upi_notes
Create Date: 2026-03-14

"""
from typing import Sequence, Union
from alembic import op

revision: str = "004_vpa_composite"
down_revision: Union[str, None] = "003_upi_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old unique index if exists, recreate as non-unique + composite unique constraint
    op.execute("DROP INDEX IF EXISTS ix_tbl_upi_vpa_mappings_upi_vpa")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_upi_vpa_mappings_upi_vpa ON tbl_upi_vpa_mappings (upi_vpa)")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_vpa_type'
            ) THEN
                ALTER TABLE tbl_upi_vpa_mappings ADD CONSTRAINT uq_vpa_type UNIQUE (upi_vpa, customer_type);
            END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE tbl_upi_vpa_mappings DROP CONSTRAINT IF EXISTS uq_vpa_type")
    op.execute("DROP INDEX IF EXISTS ix_tbl_upi_vpa_mappings_upi_vpa")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_tbl_upi_vpa_mappings_upi_vpa ON tbl_upi_vpa_mappings (upi_vpa)")
