"""drive_settings

Revision ID: 005_drive_settings
Revises: 004_vpa_composite
Create Date: 2026-03-14

"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "005_drive_settings"
down_revision: Union[str, None] = "004_vpa_composite"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_drive_settings (
            id INTEGER PRIMARY KEY,
            email VARCHAR,
            access_token VARCHAR,
            refresh_token VARCHAR,
            token_expiry TIMESTAMP WITH TIME ZONE
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tbl_drive_settings")
