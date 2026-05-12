"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All tables are created by init_db() (SQLModel.metadata.create_all) before
    # alembic runs. These CREATE TABLE IF NOT EXISTS statements are no-ops when
    # tables already exist, but ensure correctness on fresh installs where
    # init_db() may not have run yet.
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_users (
            id SERIAL PRIMARY KEY,
            username VARCHAR NOT NULL UNIQUE,
            hashed_password VARCHAR NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_edi_group_map (
            customer_segment_id INTEGER PRIMARY KEY,
            customer_segment_name_en VARCHAR,
            customer_segment_name_ta VARCHAR
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_iop_group_map (
            customer_segment_id INTEGER PRIMARY KEY,
            customer_segment_name_en VARCHAR,
            customer_segment_name_ta VARCHAR
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_edi_customer (
            customer_id INTEGER PRIMARY KEY,
            month VARCHAR,
            loan_start_date DATE,
            customer_segment_id INTEGER,
            customer_name VARCHAR,
            customer_address VARCHAR,
            proof_aadhaar VARCHAR,
            contact_number VARCHAR,
            loan_amount NUMERIC(12,2),
            disbursed_amount NUMERIC(12,2),
            interest NUMERIC(12,2),
            outstanding_balance NUMERIC(12,2),
            remarks TEXT
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_iop_customer (
            customer_id INTEGER PRIMARY KEY,
            month VARCHAR,
            loan_start_date DATE,
            customer_segment_id INTEGER,
            customer_name VARCHAR,
            customer_address VARCHAR,
            proof_aadhaar VARCHAR,
            contact_number VARCHAR,
            interest_payment_frequency NUMERIC(6,2),
            loan_amount NUMERIC(12,2),
            disbursed_amount NUMERIC(12,2),
            interest NUMERIC(12,2),
            loan_closure INTEGER,
            remarks TEXT
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_edi_name_map (
            customer_id INTEGER PRIMARY KEY,
            customer_name_en VARCHAR,
            customer_name_ta VARCHAR
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_iop_name_map (
            customer_id INTEGER PRIMARY KEY,
            customer_name_en VARCHAR,
            customer_name_ta VARCHAR
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_edi_transactions (
            transaction_id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            collection_date DATE NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            payment_mode VARCHAR NOT NULL DEFAULT 'CASH',
            payment_status VARCHAR NOT NULL DEFAULT 'PAID'
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_iop_transactions (
            transaction_id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            collection_date DATE NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            payment_mode VARCHAR NOT NULL DEFAULT 'CASH',
            payment_status VARCHAR NOT NULL DEFAULT 'PAID'
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tbl_expense (
            id SERIAL PRIMARY KEY,
            amount NUMERIC(12,2) NOT NULL,
            date DATE NOT NULL,
            notes TEXT
        )
    """)


def downgrade() -> None:
    op.drop_table("tbl_expense")
    op.drop_table("tbl_iop_transactions")
    op.drop_table("tbl_edi_transactions")
    op.drop_table("tbl_iop_name_map")
    op.drop_table("tbl_edi_name_map")
    op.drop_table("tbl_iop_customer")
    op.drop_table("tbl_edi_customer")
    op.drop_table("tbl_iop_group_map")
    op.drop_table("tbl_edi_group_map")
    op.drop_table("tbl_users")
