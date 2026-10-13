"""
Database layer: tables, live view, re-pointing and renaming triggers.
Covers R-20 … R-29 (see Todo.md, tests T-19 … T-33).
"""
import pytest
from sqlalchemy import text

from app.db import mapping_schema
from tests.helpers import (add_customer, add_mapping, delete_customer, pointer, rename, rows, set_closed)

FT = ["EDI", "IOP"]


# ── T-19 tables ─────────────────────────────────────────────────────────────────────────────────────────────
def _columns(conn, table):
    r = conn.execute(text("SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                          "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"), {"t": table})
    return {row[0]: (row[1], row[2], row[3]) for row in r}


@pytest.mark.parametrize("table,word", [("tbl_customer_alias", "alias_name"), ("tbl_customer_ai_mismatch", "mismatch_name")])
def test_tables_have_required_columns_and_no_foreign_keys(conn, table, word):
    cols = _columns(conn, table)
    for c in ("id", "finance_type", "customer_name", word, "active_customer_id", "use_count", "last_used_at",
              "created_at", "updated_at"):
        assert c in cols, c
    assert "is_customer_active" not in cols          # R-21: never stored in the table
    fks = conn.execute(text("SELECT count(*) FROM information_schema.table_constraints "
                            "WHERE table_name = :t AND constraint_type = 'FOREIGN KEY'"), {"t": table}).scalar()
    assert fks == 0                                   # R-20: not scoped to a customer id


@pytest.mark.parametrize("kind,view,word", [("alias", "vw_customer_alias", "alias_name"),
                                            ("ai_mismatch", "vw_customer_ai_mismatch", "mismatch_name")])
def test_views_expose_is_customer_active_column(conn, kind, view, word):
    cols = _columns(conn, view)
    assert "is_customer_active" in cols and word in cols and "active_customer_id" in cols


def _shape(c, schema, table):
    """columns (type, nullable, default), constraint names+kinds and index names of one table."""
    cols = {r[0]: (r[1], r[2], r[3]) for r in c.exec_driver_sql(
        "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = %(s)s AND table_name = %(t)s", {"s": schema, "t": table})}
    cons = sorted((r[0], r[1]) for r in c.exec_driver_sql(
        "SELECT constraint_name, constraint_type FROM information_schema.table_constraints "
        "WHERE table_schema = %(s)s AND table_name = %(t)s AND constraint_type IN ('UNIQUE','CHECK','PRIMARY KEY','FOREIGN KEY') "
        "AND constraint_name NOT LIKE '%%not_null%%' AND constraint_name NOT LIKE '%%_not_null'", {"s": schema, "t": table}))
    cons = [(n if kind != "PRIMARY KEY" else "pk", kind) for n, kind in cons]
    idx = sorted(r[0] for r in c.exec_driver_sql(
        "SELECT indexname FROM pg_indexes WHERE schemaname = %(s)s AND tablename = %(t)s AND indexname NOT LIKE '%%pkey'",
        {"s": schema, "t": table}))
    return cols, cons, idx


def test_sqlmodel_and_raw_sql_table_definitions_match(conn):
    """The Alembic/raw-SQL definition and the SQLModel model (used by create_all on dev databases) are the same table:
    columns, types, nullability, DEFAULTS, constraint names and index names."""
    from app.core.database import engine
    with engine.begin() as c:
        c.exec_driver_sql("DROP SCHEMA IF EXISTS parity CASCADE")
        c.exec_driver_sql("CREATE SCHEMA parity")
        c.exec_driver_sql("SET LOCAL search_path TO parity")
        mapping_schema.create_tables(c)
        raw = {t: _shape(c, "parity", t) for t in ("tbl_customer_alias", "tbl_customer_ai_mismatch")}
        c.exec_driver_sql("DROP SCHEMA parity CASCADE")
    with engine.connect() as c:
        for t, shape in raw.items():
            assert _shape(c, "public", t) == shape, t


# ── T-20 live view ──────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ft", FT)
def test_is_customer_active_is_read_live_from_the_customer_table(conn, ft):
    add_customer(conn, ft, 5, "Selvam")
    add_mapping(conn, "alias", ft, "Selvam", "Selva", cid=5)
    assert pointer(conn, "alias", ft, "Selvam", "Selva") == (5, True)
    set_closed(conn, ft, 5, True)
    assert pointer(conn, "alias", ft, "Selvam", "Selva") == (5, False)     # closed -> inactive
    set_closed(conn, ft, 5, False)
    assert pointer(conn, "alias", ft, "Selvam", "Selva") == (5, True)      # re-opened -> active again


@pytest.mark.parametrize("ft", FT)
def test_is_customer_active_guards_against_a_stale_id_that_now_belongs_to_someone_else(conn, ft):
    add_customer(conn, ft, 5, "Somebody Else")
    add_mapping(conn, "ai_mismatch", ft, "Selvam", "Selvm", cid=5)         # id 5 exists but is another customer
    assert pointer(conn, "ai_mismatch", ft, "Selvam", "Selvm") == (5, False)


def test_finance_type_keeps_edi_and_iop_apart(conn):
    add_customer(conn, "EDI", 7, "Same Name")
    add_customer(conn, "IOP", 7, "Same Name", closed=True)
    add_mapping(conn, "alias", "EDI", "Same Name", "x", cid=7)
    add_mapping(conn, "alias", "IOP", "Same Name", "x", cid=7)
    assert pointer(conn, "alias", "EDI", "Same Name", "x") == (7, True)
    assert pointer(conn, "alias", "IOP", "Same Name", "x") == (7, False)


# ── T-21 unique ─────────────────────────────────────────────────────────────────────────────────────────────
def test_unique_finance_type_customer_name_word(conn):
    add_mapping(conn, "alias", "EDI", "A", "w")
    add_mapping(conn, "alias", "IOP", "A", "w")                            # other finance type: fine
    add_mapping(conn, "alias", "EDI", "B", "w")                            # other customer: fine
    with pytest.raises(Exception):
        add_mapping(conn, "alias", "EDI", "A", "w")
    with pytest.raises(Exception):
        add_mapping(conn, "ai_mismatch", "EDI", "A", "w")
        add_mapping(conn, "ai_mismatch", "EDI", "A", "w")


def test_finance_type_check_constraint(conn):
    with pytest.raises(Exception):
        add_mapping(conn, "alias", "XYZ", "A", "w")


# ── T-22 / T-30 rows survive, old id kept ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ft", FT)
def test_closing_or_deleting_a_customer_never_deletes_mapping_rows(conn, ft):
    add_customer(conn, ft, 1, "Kumar")
    add_mapping(conn, "alias", ft, "Kumar", "K", cid=1)
    add_mapping(conn, "ai_mismatch", ft, "Kumar", "Kumr", cid=1)
    set_closed(conn, ft, 1, True)
    delete_customer(conn, ft, 1)
    assert len(rows(conn, "alias", ft)) == 1 and len(rows(conn, "ai_mismatch", ft)) == 1
    # R-26: no customer with that name any more -> the old id is kept, row simply reports inactive
    assert pointer(conn, "alias", ft, "Kumar", "K") == (1, False)


# ── T-23 / T-24 ────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ft", FT)
def test_two_same_name_accounts_first_open_one_wins(conn, ft):
    add_customer(conn, ft, 10, "Raja", closed=True)                        # older account, closed
    add_mapping(conn, "alias", ft, "Raja", "R", cid=10)
    add_customer(conn, ft, 20, "Raja")                                     # new open account, same name -> re-pointed
    assert pointer(conn, "alias", ft, "Raja", "R") == (20, True)
    add_customer(conn, ft, 30, "Raja")                                     # a third, also open: lowest open id stays
    assert pointer(conn, "alias", ft, "Raja", "R") == (20, True)
    set_closed(conn, ft, 20, True)                                         # 20 closes -> next open one (30)
    assert pointer(conn, "alias", ft, "Raja", "R") == (30, True)


@pytest.mark.parametrize("ft", FT)
def test_reopening_an_older_account_with_lower_id_takes_over(conn, ft):
    add_customer(conn, ft, 10, "Raja", closed=True)
    add_customer(conn, ft, 20, "Raja")
    add_mapping(conn, "alias", ft, "Raja", "R", cid=20)
    set_closed(conn, ft, 10, False)                                        # first come, first served
    assert pointer(conn, "alias", ft, "Raja", "R") == (10, True)


# ── T-25 / T-26 / T-24: re-point on every kind of change ───────────────────────────────────────────────────
@pytest.mark.parametrize("ft", FT)
def test_new_account_with_same_name_after_close_re_activates_the_mappings(conn, ft):
    add_customer(conn, ft, 1, "Priya")
    add_mapping(conn, "ai_mismatch", ft, "Priya", "Priyaa", cid=1)
    set_closed(conn, ft, 1, True)
    assert pointer(conn, "ai_mismatch", ft, "Priya", "Priyaa") == (1, False)
    add_customer(conn, ft, 2, "Priya")                                     # "new account opened with the same name"
    assert pointer(conn, "ai_mismatch", ft, "Priya", "Priyaa") == (2, True)


@pytest.mark.parametrize("ft", FT)
def test_delete_then_recreate_with_new_id(conn, ft):
    add_customer(conn, ft, 3, "Anand")
    add_mapping(conn, "alias", ft, "Anand", "An", cid=3)
    delete_customer(conn, ft, 3)
    assert pointer(conn, "alias", ft, "Anand", "An") == (3, False)
    add_customer(conn, ft, 9, "Anand")
    assert pointer(conn, "alias", ft, "Anand", "An") == (9, True)


@pytest.mark.parametrize("ft", FT)
def test_deleting_one_of_two_same_name_accounts_repoints_to_the_other(conn, ft):
    add_customer(conn, ft, 1, "Devi")
    add_customer(conn, ft, 2, "Devi")
    add_mapping(conn, "alias", ft, "Devi", "D", cid=1)
    delete_customer(conn, ft, 1)
    assert pointer(conn, "alias", ft, "Devi", "D") == (2, True)


# ── T-31 rename ─────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ft", FT)
def test_rename_follows_the_customer_and_keeps_the_words(conn, ft):
    add_customer(conn, ft, 4, "Muthu")
    add_mapping(conn, "alias", ft, "Muthu", "Muthu K", cid=4)
    add_mapping(conn, "ai_mismatch", ft, "Muthu", "Mutu", cid=4)
    rename(conn, ft, 4, "Muthu Kumar")
    assert rows(conn, "alias", ft) == [("Muthu Kumar", "Muthu K", 4)]
    assert rows(conn, "ai_mismatch", ft) == [("Muthu Kumar", "Mutu", 4)]
    assert pointer(conn, "alias", ft, "Muthu Kumar", "Muthu K") == (4, True)


def test_rename_only_touches_the_same_finance_type_and_id(conn):
    add_customer(conn, "EDI", 4, "Muthu")
    add_customer(conn, "IOP", 4, "Muthu")                                   # same id, same name, other type
    add_customer(conn, "EDI", 8, "Muthu")                                   # same name, other id
    add_mapping(conn, "alias", "EDI", "Muthu", "m", cid=4)
    add_mapping(conn, "alias", "IOP", "Muthu", "m", cid=4)
    rename(conn, "EDI", 8, "Someone")                                       # not the pointed-to customer
    assert rows(conn, "alias") == [("Muthu", "m", 4), ("Muthu", "m", 4)]
    rename(conn, "EDI", 4, "Muthu Renamed")                                 # only the EDI row follows
    assert sorted(rows(conn, "alias")) == [("Muthu", "m", 4), ("Muthu Renamed", "m", 4)]
    assert rows(conn, "alias", "IOP") == [("Muthu", "m", 4)]


def test_rename_collision_merges_identical_words_instead_of_failing(conn):
    add_customer(conn, "EDI", 1, "Old Name")
    add_customer(conn, "EDI", 2, "Target")
    add_mapping(conn, "alias", "EDI", "Old Name", "shared", cid=1)
    add_mapping(conn, "alias", "EDI", "Old Name", "only-old", cid=1)
    add_mapping(conn, "alias", "EDI", "Target", "shared", cid=2)
    rename(conn, "EDI", 1, "Target")                                        # must not raise a unique violation
    got = sorted((n, w) for n, w, _ in rows(conn, "alias", "EDI"))
    assert got == [("Target", "only-old"), ("Target", "shared")]


def test_rename_does_not_hijack_rows_with_a_stale_id(conn):
    """A row pointing at id 5 under a different name must not follow whoever now sits at id 5."""
    add_customer(conn, "EDI", 5, "Current Owner")
    add_mapping(conn, "alias", "EDI", "Ghost", "g", cid=5)
    rename(conn, "EDI", 5, "Brand New")
    assert rows(conn, "alias", "EDI") == [("Ghost", "g", 5)]


# ── Rows that don't change (performance guard) ─────────────────────────────────────────────────────────────
def test_balance_only_update_does_not_fire_the_trigger(conn):
    add_customer(conn, "EDI", 1, "Zed")
    add_mapping(conn, "alias", "EDI", "Zed", "z", cid=99)                   # deliberately wrong pointer
    conn.execute(text("UPDATE tbl_edi_customer SET outstanding_balance = 5 WHERE customer_id = 1"))
    assert pointer(conn, "alias", "EDI", "Zed", "z")[0] == 99               # untouched: trigger did not run


# ── T-27 delete_and_resequence through the real repositories ───────────────────────────────────────────────
@pytest.mark.parametrize("ft", FT)
def test_delete_and_resequence_keeps_pointers_correct(session, conn, ft):
    from app.repositories.customer_repo import EdiCustomerRepo, IopCustomerRepo
    repo = (EdiCustomerRepo if ft == "EDI" else IopCustomerRepo)(session)
    for i, n in enumerate(["Alpha", "Bravo", "Charlie", "Delta", "Echo"], start=1):
        add_customer(conn, ft, i, n)
    add_customer(conn, ft, 6, "Delta")                                      # second account of Delta
    add_mapping(conn, "alias", ft, "Alpha", "a", cid=1)
    add_mapping(conn, "alias", ft, "Charlie", "c", cid=3)
    add_mapping(conn, "alias", ft, "Delta", "d", cid=4)
    add_mapping(conn, "alias", ft, "Echo", "e", cid=5)
    add_mapping(conn, "alias", ft, "Bravo", "b", cid=2)

    assert repo.delete_and_resequence(2) is True                            # Bravo deleted; ids 3.. shift down by 1

    assert pointer(conn, "alias", ft, "Alpha", "a") == (1, True)
    assert pointer(conn, "alias", ft, "Charlie", "c") == (2, True)          # was 3
    assert pointer(conn, "alias", ft, "Delta", "d") == (3, True)            # was 4 (first Delta account)
    assert pointer(conn, "alias", ft, "Echo", "e") == (4, True)             # was 5
    # deleted customer: id kept (2) but 2 now belongs to Charlie -> name guard says inactive
    assert pointer(conn, "alias", ft, "Bravo", "b") == (2, False)


@pytest.mark.parametrize("ft", FT)
def test_resequence_with_a_closed_customer_still_tracks_the_shifted_id(session, conn, ft):
    from app.repositories.customer_repo import EdiCustomerRepo, IopCustomerRepo
    repo = (EdiCustomerRepo if ft == "EDI" else IopCustomerRepo)(session)
    add_customer(conn, ft, 1, "One")
    add_customer(conn, ft, 2, "Two", closed=True)
    add_customer(conn, ft, 3, "Three", closed=True)
    add_mapping(conn, "ai_mismatch", ft, "Three", "t", cid=3)
    repo.delete_and_resequence(1)
    assert pointer(conn, "ai_mismatch", ft, "Three", "t") == (2, False)    # follows the shifted id, still inactive
    set_closed(conn, ft, 2, True)


# ── T-29 import / restore style writes ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ft", FT)
def test_bulk_inserts_like_the_excel_import_repoint(conn, ft):
    add_mapping(conn, "alias", ft, "Imported", "imp", cid=None)
    add_customer(conn, ft, 50, "Imported")
    assert pointer(conn, "alias", ft, "Imported", "imp") == (50, True)
    # executemany (pandas.to_sql style): several rows in one call
    conn.execute(text("INSERT INTO tbl_edi_customer (customer_id, customer_name, is_closed, ignore) "
                      "VALUES (:i, :n, false, false)"),
                 [{"i": 60, "n": "Bulk A"}, {"i": 61, "n": "Bulk B"}])
    add_mapping(conn, "alias", "EDI", "Bulk B", "bb", cid=None)
    conn.execute(text("UPDATE tbl_customer_alias SET active_customer_id = NULL"))
    assert mapping_schema.resync_all(conn) >= 1
    assert pointer(conn, "alias", "EDI", "Bulk B", "bb") == (61, True)


# ── T-32 same transaction ──────────────────────────────────────────────────────────────────────────────────
def test_rolling_back_a_customer_change_also_rolls_back_the_mapping_change(session, conn):
    add_customer(conn, "EDI", 1, "Tx Test")
    add_mapping(conn, "alias", "EDI", "Tx Test", "t", cid=1)
    session.execute(text("UPDATE tbl_edi_customer SET customer_name = 'Renamed In Tx' WHERE customer_id = 1"))
    seen_in_tx = session.execute(text("SELECT customer_name FROM tbl_customer_alias")).scalar()
    assert seen_in_tx == "Renamed In Tx"                                    # visible inside the transaction…
    session.rollback()
    assert rows(conn, "alias", "EDI") == [("Tx Test", "t", 1)]              # …and gone after rollback


# ── T-33 resync + restore healing ──────────────────────────────────────────────────────────────────────────
def test_resync_repairs_a_deliberately_corrupted_pointer(conn):
    add_customer(conn, "EDI", 12, "Lakshmi")
    add_mapping(conn, "alias", "EDI", "Lakshmi", "L", cid=12)
    conn.execute(text("UPDATE tbl_customer_alias SET active_customer_id = 999"))
    assert mapping_schema.resync_all(conn) == 1
    assert pointer(conn, "alias", "EDI", "Lakshmi", "L") == (12, True)
    assert mapping_schema.resync_all(conn) == 0                             # idempotent


def test_restoring_an_older_dump_recreates_customer_tables_without_triggers_and_ensure_heals_it(conn):
    """pg_dump --clean does DROP TABLE + CREATE TABLE: the customer tables lose our triggers (and views must not block it)."""
    from app.core.database import engine
    add_customer(conn, "EDI", 1, "Old Customer")
    add_mapping(conn, "alias", "EDI", "Old Customer", "oc", cid=1)

    # what an old dump does — would fail with "other objects depend on it" if a view referenced the table
    conn.exec_driver_sql("DROP TABLE IF EXISTS tbl_edi_customer")
    conn.exec_driver_sql("""CREATE TABLE tbl_edi_customer (
        customer_id integer PRIMARY KEY, month varchar, loan_start_date date, customer_segment_id integer,
        customer_name varchar, customer_name_ta varchar, customer_address varchar, proof_aadhaar varchar,
        contact_number varchar, loan_amount numeric(12,2), disbursed_amount numeric(12,2), interest numeric(12,2),
        outstanding_balance numeric(12,2), is_closed boolean NOT NULL DEFAULT false, remarks varchar,
        ignore boolean NOT NULL DEFAULT false)""")
    conn.exec_driver_sql("INSERT INTO tbl_edi_customer (customer_id, customer_name) VALUES (77, 'Old Customer')")
    # no trigger -> pointer is stale
    assert pointer(conn, "alias", "EDI", "Old Customer", "oc") == (1, False)
    n_triggers = conn.execute(text("SELECT count(*) FROM pg_trigger WHERE tgrelid = 'tbl_edi_customer'::regclass "
                                   "AND NOT tgisinternal")).scalar()
    assert n_triggers == 0

    assert mapping_schema.ensure(engine) == 1                               # triggers re-created + pointers fixed
    assert pointer(conn, "alias", "EDI", "Old Customer", "oc") == (77, True)
    add_customer(conn, "EDI", 78, "Old Customer", closed=False, name_map=False)   # trigger works again
    n_triggers = conn.execute(text("SELECT count(*) FROM pg_trigger WHERE tgrelid = 'tbl_edi_customer'::regclass "
                                   "AND NOT tgisinternal")).scalar()
    assert n_triggers == 3


def test_apply_is_idempotent(conn):
    mapping_schema.apply(conn)
    mapping_schema.apply(conn)
    for t in ("tbl_edi_customer", "tbl_iop_customer"):
        n = conn.execute(text(f"SELECT count(*) FROM pg_trigger WHERE tgrelid = '{t}'::regclass AND NOT tgisinternal")).scalar()
        assert n == 3


def test_customer_with_null_name_is_ignored(conn):
    add_customer(conn, "EDI", 1, None)
    rename(conn, "EDI", 1, "Now Named")
    add_mapping(conn, "alias", "EDI", "Now Named", "x")
    set_closed(conn, "EDI", 1, True)
    set_closed(conn, "EDI", 1, False)
    assert pointer(conn, "alias", "EDI", "Now Named", "x") == (1, True)


def test_missing_trigram_extension_is_never_fatal(conn):
    """If the role may not create extensions, apply()/migration must still succeed (matcher falls back)."""
    class NoExtensions:
        def begin_nested(self):
            raise RuntimeError("permission denied to create extension")
    assert mapping_schema.ensure_trigram(NoExtensions()) is False

    class Boom:
        """A real connection whose CREATE EXTENSION fails inside the savepoint: the outer transaction must survive."""
    from app.core.database import engine
    with engine.begin() as c:
        original = c.exec_driver_sql

        def failing(sql, *a, **k):
            if "pg_trgm" in sql:
                raise RuntimeError("permission denied")
            return original(sql, *a, **k)
        c.exec_driver_sql = failing
        mapping_schema.apply(c)                       # must not raise
        c.exec_driver_sql = original
        assert c.exec_driver_sql("SELECT 1").scalar() == 1
