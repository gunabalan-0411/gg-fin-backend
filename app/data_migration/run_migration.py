"""
Run this script once to seed the database from Excel files.

Usage (from backend/ directory):
    python -m app.data_migration.run_migration

Excel files are expected at:
    ../data_migration/GG Finance.xlsx
    ../data_migration/KG Finance.xlsx
"""
import os
import sys

# Add backend root to path when running directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from sqlmodel import Session

from app.core.database import engine
from app.models import (
    EdiCustomer, IopCustomer,
    EdiTransaction, IopTransaction,
    Expense,
    EdiNameMap, EdiGroupMap,
    IopNameMap, IopGroupMap,
)
from app.data_migration.excel_processor import ExcelCollectionProcessor
from app.data_migration.seeder import bulk_insert

# In Docker, data_migration/ is mounted at /app/data_migration_files
# Locally, it's three levels up from this file
_docker_dir = "/app/data_migration_files"
_local_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data_migration")
BASE_DIR = _docker_dir if os.path.isdir(_docker_dir) else _local_dir

GG_FILE = os.path.join(BASE_DIR, "GG Finance.xlsx")
KG_FILE = os.path.join(BASE_DIR, "KG Finance.xlsx")


def run():
    print("Starting data migration...")

    # ── EDI Transactions ──────────────────────────────────────────────
    print("\n[1/9] EDI Transactions")
    proc = ExcelCollectionProcessor(GG_FILE, "collection_det", "edi")
    df_edi_txn = proc.transform_transactions()

    # ── IOP Transactions ──────────────────────────────────────────────
    print("\n[2/9] IOP Transactions")
    proc = ExcelCollectionProcessor(KG_FILE, "collection_det", "iop")
    df_iop_txn = proc.transform_transactions()

    # ── EDI Customer Details ──────────────────────────────────────────
    print("\n[3/9] EDI Customer Details")
    proc = ExcelCollectionProcessor(GG_FILE, "customer_det", "edi")
    df_edi_cust = proc.transform_edi_customer_details()

    # ── IOP Customer Details ──────────────────────────────────────────
    print("\n[4/9] IOP Customer Details")
    proc = ExcelCollectionProcessor(KG_FILE, "customer_det", "iop")
    df_iop_cust = proc.transform_iop_customer_details()

    # ── Mapping tables ────────────────────────────────────────────────
    print("\n[5/9] EDI Name Map")
    proc = ExcelCollectionProcessor(GG_FILE, "name_map", "edi")
    df_edi_name = proc.transform_mapping_tbls(["customer_id", "customer_name_en", "customer_name_ta"])

    print("\n[6/9] EDI Group Map")
    proc = ExcelCollectionProcessor(GG_FILE, "grp_map", "edi")
    df_edi_grp = proc.transform_mapping_tbls(["customer_segment_id", "customer_segment_name_en", "customer_segment_name_ta"])

    print("\n[7/9] IOP Name Map")
    proc = ExcelCollectionProcessor(KG_FILE, "name_map", "iop")
    df_iop_name = proc.transform_mapping_tbls(["customer_id", "customer_name_en", "customer_name_ta"])

    print("\n[8/9] IOP Group Map")
    proc = ExcelCollectionProcessor(KG_FILE, "grp_map", "iop")
    df_iop_grp = proc.transform_mapping_tbls(["customer_segment_id", "customer_segment_name_en", "customer_segment_name_ta"])

    # ── Expenses ──────────────────────────────────────────────────────
    print("\n[9/9] Expenses")
    proc = ExcelCollectionProcessor(KG_FILE, "House expense", "all")
    df_expense = proc.transform_expense_tbl()

    # ── Insert into DB ────────────────────────────────────────────────
    print("\nInserting into PostgreSQL...")
    with Session(engine) as session:
        bulk_insert(session, EdiNameMap, df_edi_name)
        bulk_insert(session, EdiGroupMap, df_edi_grp)
        bulk_insert(session, IopNameMap, df_iop_name)
        bulk_insert(session, IopGroupMap, df_iop_grp)
        bulk_insert(session, EdiCustomer, df_edi_cust)
        bulk_insert(session, IopCustomer, df_iop_cust)
        bulk_insert(session, EdiTransaction, df_edi_txn)
        bulk_insert(session, IopTransaction, df_iop_txn)
        bulk_insert(session, Expense, df_expense)

    print("\nMigration complete!")


if __name__ == "__main__":
    run()
