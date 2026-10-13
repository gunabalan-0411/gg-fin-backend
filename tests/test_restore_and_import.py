"""
Backup restore and bulk import keep the mapping tables healthy.   (T-29, T-33, DB-11, DB-12)

`_do_restore` shells out to the `psql` binary, which a developer machine may not have; the tests replace it with an
equivalent that feeds the SQL to the test database through psycopg2. Everything else in `_do_restore` is the real code.
"""
import subprocess

import pandas as pd
import pytest
from sqlalchemy import text

from app.core.database import engine
from tests.helpers import add_customer, add_mapping, pointer, rows

OLD_DUMP = """
-- what `pg_dump --clean --if-exists` of a database from BEFORE this feature looks like (customer table only)
DROP TABLE IF EXISTS public.tbl_edi_customer;
CREATE TABLE public.tbl_edi_customer (
    customer_id integer NOT NULL PRIMARY KEY, month varchar, loan_start_date date, customer_segment_id integer,
    customer_name varchar, customer_name_ta varchar, customer_address varchar, proof_aadhaar varchar,
    contact_number varchar, loan_amount numeric(12,2), disbursed_amount numeric(12,2), interest numeric(12,2),
    outstanding_balance numeric(12,2), is_closed boolean DEFAULT false NOT NULL, remarks varchar,
    ignore boolean DEFAULT false NOT NULL
);
INSERT INTO public.tbl_edi_customer (customer_id, customer_name, is_closed) VALUES
    (101, 'Restored Person', false), (102, 'Closed One', true), (103, 'Twin', false), (104, 'Twin', false);
"""


@pytest.fixture()
def fake_psql(monkeypatch):
    """Route `psql -f file` / `psql -c sql` to the test database."""
    real_run = subprocess.run

    def run(cmd, *a, **kw):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "psql":
            sql = open(cmd[cmd.index("-f") + 1], encoding="utf-8").read() if "-f" in cmd else cmd[cmd.index("-c") + 1]
            raw = engine.raw_connection()
            try:
                raw.driver_connection.autocommit = True      # the pooled wrapper doesn't forward attribute writes
                with raw.driver_connection.cursor() as cur:
                    cur.execute(sql)
            finally:
                raw.close()
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr("app.api.backup.subprocess.run", run)
    yield
    # A restore replaces the customer table; put the triggers back so later tests start from a healthy database.
    monkeypatch.undo()
    from app.db import mapping_schema
    mapping_schema.ensure(engine)


def _restore(content: str):
    from app.api.backup import _do_restore, _jobs
    _jobs["j1"] = {"progress": 0, "status": "running", "message": ""}
    _do_restore(content.encode(), "old_backup.sql", "j1")
    return _jobs["j1"]


def test_restoring_an_old_backup_reinstalls_triggers_and_repoints_mappings(conn, fake_psql):
    add_customer(conn, "EDI", 5, "Restored Person")                     # state before the restore
    add_mapping(conn, "alias", "EDI", "Restored Person", "rp", cid=5)
    add_mapping(conn, "ai_mismatch", "EDI", "Twin", "tw", cid=None)
    add_mapping(conn, "alias", "EDI", "Not In Backup", "nib", cid=7)    # customer absent from the backup
    add_mapping(conn, "alias", "EDI", "Closed One", "co", cid=None)

    job = _restore(OLD_DUMP)

    assert job["status"] == "done", job
    assert "WARNING" not in job["message"], job
    # pointers re-aimed at the restored ids
    assert pointer(conn, "alias", "EDI", "Restored Person", "rp") == (101, True)
    assert pointer(conn, "ai_mismatch", "EDI", "Twin", "tw") == (103, True)        # first of the two accounts
    assert pointer(conn, "alias", "EDI", "Closed One", "co") == (102, False)       # exists but closed -> inactive
    assert pointer(conn, "alias", "EDI", "Not In Backup", "nib") == (7, False)     # no such customer -> kept, inactive
    # rows were never deleted
    assert len(rows(conn, "alias", "EDI")) == 3 and len(rows(conn, "ai_mismatch", "EDI")) == 1
    # and the triggers are back, so the NEXT customer change is handled too
    n = conn.execute(text("SELECT count(*) FROM pg_trigger WHERE tgrelid = 'tbl_edi_customer'::regclass "
                          "AND NOT tgisinternal")).scalar()
    assert n == 3
    conn.execute(text("UPDATE tbl_edi_customer SET is_closed = true WHERE customer_id = 101"))
    assert pointer(conn, "alias", "EDI", "Restored Person", "rp") == (101, False)


def test_restore_still_succeeds_but_warns_if_the_mapping_repair_fails(conn, fake_psql, monkeypatch):
    def boom(_engine):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr("app.db.mapping_schema.ensure", boom)
    job = _restore(OLD_DUMP)
    assert job["status"] == "done"
    assert "WARNING" in job["message"] and "simulated failure" in job["message"]   # never silent


def test_every_restore_entry_point_goes_through__do_restore():
    """Manual import, Drive import, first-run restore and start-up auto-restore all call the same function."""
    import inspect
    from app.api import backup, setup
    from app.services import drive_service
    assert "_do_restore" in inspect.getsource(backup.import_backup)
    assert "_do_restore" in inspect.getsource(setup)
    assert "_do_restore" in inspect.getsource(drive_service.start_import_job)
    assert "_do_restore" in inspect.getsource(drive_service.auto_restore_on_startup)


def test_excel_style_bulk_insert_then_ensure_repoints(session, conn):
    """The Excel migration loads customers with seeder.bulk_insert (no triggers fire for bulk_save_objects? they do — it is plain INSERT)."""
    from app.data_migration.seeder import bulk_insert
    from app.db import mapping_schema
    from app.models import EdiCustomer

    add_mapping(conn, "alias", "EDI", "Bulk Person", "bp", cid=None)
    df = pd.DataFrame([{"customer_id": 1, "customer_name": "Bulk Person", "is_closed": False},
                       {"customer_id": 2, "customer_name": "Other", "is_closed": False}])
    bulk_insert(session, EdiCustomer, df)
    assert pointer(conn, "alias", "EDI", "Bulk Person", "bp") == (1, True)        # trigger handled the bulk insert
    conn.execute(text("UPDATE tbl_customer_alias SET active_customer_id = NULL"))
    assert mapping_schema.ensure(engine) == 1                                     # what run_migration does at the end
    assert pointer(conn, "alias", "EDI", "Bulk Person", "bp") == (1, True)


def test_init_db_installs_everything_on_a_database_missing_the_triggers(conn):
    """App start-up self-heals (e.g. database restored out-of-band)."""
    from app.core.database import init_db
    for t in ("tbl_edi_customer", "tbl_iop_customer"):
        for e in ("ins", "del", "upd"):
            conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_ggfin_{'edi' if 'edi' in t else 'iop'}_cust_{e} ON {t}")
    conn.exec_driver_sql("DROP VIEW IF EXISTS vw_customer_alias")
    init_db()
    for t in ("tbl_edi_customer", "tbl_iop_customer"):
        n = conn.execute(text(f"SELECT count(*) FROM pg_trigger WHERE tgrelid = '{t}'::regclass AND NOT tgisinternal")).scalar()
        assert n == 3
    assert conn.execute(text("SELECT count(*) FROM vw_customer_alias")).scalar() == 0


def test_run_migration_script_repoints_at_the_end(conn, monkeypatch):
    """Run the real `run_migration.run()` with the Excel reader stubbed out: it must finish by repairing mappings."""
    from app.data_migration import run_migration

    class StubProcessor:
        def __init__(self, *a, **k):
            pass

        def transform_transactions(self):
            return pd.DataFrame()

        def transform_edi_customer_details(self):
            return pd.DataFrame([{"customer_id": 1, "customer_name": "Excel Person", "is_closed": False}])

        def transform_iop_customer_details(self):
            return pd.DataFrame([{"customer_id": 1, "customer_name": "Excel Person", "is_closed": False}])

        def transform_mapping_tbls(self, cols):
            return pd.DataFrame(columns=cols)

        def transform_expense_tbl(self):
            return pd.DataFrame()

    monkeypatch.setattr(run_migration, "ExcelCollectionProcessor", StubProcessor)
    add_mapping(conn, "alias", "EDI", "Excel Person", "ep", cid=999)       # stale pointer from before the import
    add_mapping(conn, "ai_mismatch", "IOP", "Excel Person", "epp", cid=999)
    run_migration.run()
    assert pointer(conn, "alias", "EDI", "Excel Person", "ep") == (1, True)
    assert pointer(conn, "ai_mismatch", "IOP", "Excel Person", "epp") == (1, True)
