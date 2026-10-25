"""
Delete-and-resequence: every table that stores a customer id follows, so nothing ends up pointing at a different customer. RS-01 ... RS-08.
"""
import pytest
from sqlalchemy import text

from tests.helpers import add_customer, add_mapping, pointer

FT = ["EDI", "IOP"]


def repo_for(session, ft):
    from app.repositories.customer_repo import EdiCustomerRepo, IopCustomerRepo
    return (EdiCustomerRepo if ft == "EDI" else IopCustomerRepo)(session)


def txn(conn, ft, cid, amount, day="2026-10-01"):
    t = "tbl_edi_transactions" if ft == "EDI" else "tbl_iop_transactions"
    extra_cols, extra_vals = (", principal_amount", ", 0") if ft == "IOP" else ("", "")
    conn.execute(text(f"INSERT INTO {t} (customer_id, collection_date, amount, payment_mode, payment_status{extra_cols}) VALUES (:c, :d, :a, 'CASH', 'PAID'{extra_vals})"),
                 {"c": cid, "d": day, "a": amount})


def txns(conn, ft):
    t = "tbl_edi_transactions" if ft == "EDI" else "tbl_iop_transactions"
    return sorted((int(c), float(a)) for c, a in conn.execute(text(f"SELECT customer_id, amount FROM {t}")).all())


def setup(conn, ft):
    for i, n in enumerate(["Alpha", "Bravo", "Charlie", "Delta"], start=1):
        add_customer(conn, ft, i, n)
    for cid, amt in ((1, 10), (2, 20), (3, 30), (4, 40)):
        txn(conn, ft, cid, amt)


@pytest.mark.parametrize("ft", FT)
def test_transactions_follow_and_the_deleted_customers_do_not_merge_into_the_next(session, conn, ft):     # RS-01
    setup(conn, ft)
    repo_for(session, ft).delete_and_resequence(2)
    got = txns(conn, ft)
    assert (2, 30.0) in got and (3, 40.0) in got and (1, 10.0) in got            # Charlie is #2 now, Delta #3
    assert [a for c, a in got if c == 2] == [30.0]                                # Bravo's 20 did NOT join Charlie
    assert (-2, 20.0) in got                                                       # parked: still in the history, owned by nobody


@pytest.mark.parametrize("ft", FT)
def test_upi_ids_are_removed_for_the_deleted_customer_and_shift_for_the_rest(session, conn, ft):          # RS-02
    setup(conn, ft)
    other = "iop" if ft == "EDI" else "edi"
    t = ft.lower()
    for vpa, cid, ty in (("a@upi", 1, t), ("b@upi", 2, t), ("c@upi", 3, t), ("d@upi", 4, t), ("x@upi", 3, other)):
        conn.execute(text("INSERT INTO tbl_upi_vpa_mappings (upi_vpa, customer_id, customer_type, customer_name) VALUES (:v, :c, :t, 'n')"), {"v": vpa, "c": cid, "t": ty})
    repo_for(session, ft).delete_and_resequence(2)
    got = {v: c for v, c in conn.execute(text("SELECT upi_vpa, customer_id FROM tbl_upi_vpa_mappings")).all()}
    assert got == {"a@upi": 1, "c@upi": 2, "d@upi": 3, "x@upi": 3}                # b removed, same product shifted, the OTHER product untouched


@pytest.mark.parametrize("ft", FT)
def test_upi_payments_mapped_to_the_deleted_customer_are_unmapped_the_rest_shift(session, conn, ft):       # RS-03
    setup(conn, ft)
    t = ft.lower()
    other = "iop" if ft == "EDI" else "edi"
    rows = (("r1", 1, t), ("r2", 2, t), ("r3", 3, t), ("r4", 3, other), ("r5", None, None))
    for ref, cid, ty in rows:
        conn.execute(text("INSERT INTO tbl_upi_transactions (upi_ref_no, amount, transaction_type, transaction_date, source, mapped_customer_id, mapped_customer_type) "
                          "VALUES (:r, 100, 'credit', '2026-10-01', 'csv', :c, :t)"), {"r": ref, "c": cid, "t": ty})
    repo_for(session, ft).delete_and_resequence(2)
    got = {r: (c, ty) for r, c, ty in conn.execute(text("SELECT upi_ref_no, mapped_customer_id, mapped_customer_type FROM tbl_upi_transactions")).all()}
    assert got == {"r1": (1, t), "r2": (None, None), "r3": (2, t), "r4": (3, other), "r5": (None, None)}


@pytest.mark.parametrize("ft", FT)
@pytest.mark.parametrize("table", ["tbl_unclaimed_balance", "tbl_defaulted_balance"])
def test_unclaimed_and_defaulted_keep_their_amount_and_follow_the_id(session, conn, ft, table):            # RS-04
    setup(conn, ft)
    p = ft.lower()
    other = "iop" if ft == "EDI" else "edi"
    for cid, name, prod, amt in ((1, "Alpha", p, 5), (2, "Bravo", p, 6), (4, "Delta", p, 7), (4, "Other", other, 8)):
        conn.execute(text(f"INSERT INTO {table} (date, product, customer_id, customer_name, amount) VALUES ('2026-10-01', :p, :c, :n, :a)"),
                     {"p": prod, "c": cid, "n": name, "a": amt})
    repo_for(session, ft).delete_and_resequence(2)
    got = {n: (c, float(a)) for n, c, a in conn.execute(text(f"SELECT customer_name, customer_id, amount FROM {table}")).all()}
    assert got == {"Alpha": (1, 5.0), "Bravo": (None, 6.0), "Delta": (3, 7.0), "Other": (4, 8.0)}              # amounts all kept (dashboard totals unchanged)


@pytest.mark.parametrize("ft", FT)
def test_alias_and_ai_misread_names_follow_by_name(session, conn, ft):                                         # RS-05
    setup(conn, ft)
    add_mapping(conn, "alias", ft, "Charlie", "chaarli", cid=3)
    add_mapping(conn, "ai_mismatch", ft, "Delta", "delt4", cid=4)
    add_mapping(conn, "alias", ft, "Bravo", "braavo", cid=2)
    repo_for(session, ft).delete_and_resequence(2)
    assert pointer(conn, "alias", ft, "Charlie", "chaarli") == (2, True)
    assert pointer(conn, "ai_mismatch", ft, "Delta", "delt4") == (3, True)
    assert pointer(conn, "alias", ft, "Bravo", "braavo")[1] is False                                           # the deleted person's word is inactive


@pytest.mark.parametrize("ft", FT)
def test_deleting_the_last_customer_changes_nothing_else(session, conn, ft):                                   # RS-06
    setup(conn, ft)
    repo_for(session, ft).delete_and_resequence(4)
    assert txns(conn, ft) == [(-4, 40.0), (1, 10.0), (2, 20.0), (3, 30.0)]


@pytest.mark.parametrize("ft", FT)
def test_two_deletes_in_a_row_keep_everyone_right(session, conn, ft):                                          # RS-07
    setup(conn, ft)
    repo = repo_for(session, ft)
    repo.delete_and_resequence(1)                                               # Bravo is #1, Charlie #2, Delta #3
    repo.delete_and_resequence(1)                                               # Bravo gone: Charlie #1, Delta #2
    live = {c: a for c, a in txns(conn, ft) if c > 0}
    assert live == {1: 30.0, 2: 40.0}
    names = dict(conn.execute(text(f"SELECT customer_id, customer_name FROM tbl_{ft.lower()}_customer")).all())
    assert names == {1: "Charlie", 2: "Delta"}


@pytest.mark.parametrize("ft", FT)
def test_the_other_product_is_never_touched(session, conn, ft):                                                # RS-08
    setup(conn, ft)
    other = "IOP" if ft == "EDI" else "EDI"
    for i, n in enumerate(["Om", "Pm", "Qm"], start=1):
        add_customer(conn, other, i, n)
        txn(conn, other, i, i * 100)
    repo_for(session, ft).delete_and_resequence(1)
    assert txns(conn, other) == [(1, 100.0), (2, 200.0), (3, 300.0)]
