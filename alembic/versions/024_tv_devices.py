"""Home TV: paired devices and pairing codes

Revision ID: 024_tv_devices
Revises: 023_personal_accounts
Create Date: 2026-11-10
"""
from alembic import op

revision = "024_tv_devices"
down_revision = "023_personal_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_tv_device (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name VARCHAR NOT NULL,
            token_hash VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_tbl_tv_device_token_hash ON tbl_tv_device (token_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_tv_device_user_id ON tbl_tv_device (user_id)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_tv_pairing (
            id SERIAL PRIMARY KEY,
            code VARCHAR NOT NULL,
            poll_hash VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            approved_user_id INTEGER,
            device_name VARCHAR,
            approved_at TIMESTAMPTZ,
            claimed_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_tbl_tv_pairing_poll_hash ON tbl_tv_pairing (poll_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tbl_tv_pairing_code ON tbl_tv_pairing (code)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tbl_tv_pairing")
    op.execute("DROP TABLE IF EXISTS tbl_tv_device")
