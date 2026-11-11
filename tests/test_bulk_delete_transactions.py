"""
Delete several of a customer's transactions at once (the customer's transaction list: select rows -> Delete selected). BD-01 ... BD-12.
  * EDI: the rows go, the balance is recalculated from what is still PAID;
  * IOP: the rows go and the principal they had added comes back off principal_paid / the balance, once, by the total;
  * all or nothing: an id that is not this customer's (or is gone) refuses the whole request and deletes nothing;
  * only the named customer's rows are touched.
"""
from decimal import Decimal

from sqlalchemy import text

from tests.helpers import add_customer

EDI = "/api/transactions/edi/bulk-delete"
IOP = "/api/transactions/iop/bulk-delete"


def edi_customer(conn, cid=1, loan=10000):
    add_customer(conn, "EDI", cid, f"Edi{cid}")
    conn.execute(text("UPDATE tbl_edi_customer SET loan_amount=:l, outstanding_balance=:l WHERE customer_id=:c"), {"l": loan, "c": cid})


def edi_txn(conn, cid, day, amount, status="PAID", mode="CASH"):
    return conn.execute(text("INSERT INTO tbl_edi_transactions (customer_id, collection_date, amount, payment_mode, payment_status) VALUES (:c,:d,:a,:m,:s) RETURNING transaction_id"),
                        {"c": cid, "d": day, "a": amount, "m": mode, "s": status}).scalar()


def edi_sync(conn, cid):
    conn.execute(text("UPDATE tbl_edi_customer SET outstanding_balance = loan_amount - COALESCE((SELECT sum(amount) FROM tbl_edi_transactions WHERE customer_id=:c AND payment_status='PAID'),0) WHERE customer_id=:c"), {"c": cid})


def edi_state(conn, cid):
    bal = conn.execute(text("SELECT outstanding_balance FROM tbl_edi_customer WHERE customer_id=:c"), {"c": cid}).scalar()
    n = conn.execute(text("SELECT count(*) FROM tbl_edi_transactions WHERE customer_id=:c"), {"c": cid}).scalar()
    return float(bal), n


def iop_customer(conn, cid=1, loan=20000, paid=0):
    add_customer(conn, "IOP", cid, f"Iop{cid}")
    conn.execute(text("UPDATE tbl_iop_customer SET loan_amount=:l, principal_paid=:p, outstanding_balance=:o WHERE customer_id=:c"), {"l": loan, "p": paid, "o": loan - paid, "c": cid})


def iop_txn(conn, cid, day, amount, principal=0, status="PAID"):
    return conn.execute(text("INSERT INTO tbl_iop_transactions (customer_id, collection_date, amount, payment_mode, payment_status, principal_amount) VALUES (:c,:d,:a,'CASH',:s,:p) RETURNING transaction_id"),
                        {"c": cid, "d": day, "a": amount, "s": status, "p": principal}).scalar()


def iop_state(conn, cid):
    r = conn.execute(text("SELECT principal_paid, outstanding_balance FROM tbl_iop_customer WHERE customer_id=:c"), {"c": cid}).first()
    n = conn.execute(text("SELECT count(*) FROM tbl_iop_transactions WHERE customer_id=:c"), {"c": cid}).scalar()
    return float(r[0]), float(r[1]), n


def test_bd01_edi_selected_rows_are_deleted_and_the_balance_follows(client, conn):
    edi_customer(conn, 1, 10000)
    a = edi_txn(conn, 1, "2026-10-01", 200)
    b = edi_txn(conn, 1, "2026-10-02", 200)
    c = edi_txn(conn, 1, "2026-10-03", 1500)
    edi_sync(conn, 1)
    assert edi_state(conn, 1) == (8100.0, 3)
    r = client.post(EDI, json={"customer_id": 1, "ids": [a, c]})
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    assert edi_state(conn, 1) == (9800.0, 1)                               # only the 200 on the 2nd is still paid: 10,000 - 200


def test_bd02_edi_an_unpaid_row_can_go_too_without_moving_the_balance(client, conn):
    edi_customer(conn, 1, 10000)
    a = edi_txn(conn, 1, "2026-10-01", 500)
    u = edi_txn(conn, 1, "2026-10-02", 500, status="UNPAID")
    edi_sync(conn, 1)
    assert client.post(EDI, json={"customer_id": 1, "ids": [u]}).json() == {"deleted": 1}
    assert edi_state(conn, 1) == (9500.0, 1)
    assert a


def test_bd03_edi_all_or_nothing_when_one_id_is_not_this_customers(client, conn):
    edi_customer(conn, 1)
    edi_customer(conn, 2)
    a = edi_txn(conn, 1, "2026-10-01", 200)
    other = edi_txn(conn, 2, "2026-10-01", 200)
    edi_sync(conn, 1); edi_sync(conn, 2)
    r = client.post(EDI, json={"customer_id": 1, "ids": [a, other]})
    assert r.status_code == 400 and "Nothing was deleted" in r.json()["detail"]
    assert edi_state(conn, 1)[1] == 1 and edi_state(conn, 2)[1] == 1       # neither customer lost anything


def test_bd04_edi_an_id_that_is_already_gone_refuses_the_request(client, conn):
    edi_customer(conn, 1)
    a = edi_txn(conn, 1, "2026-10-01", 200)
    assert client.post(EDI, json={"customer_id": 1, "ids": [a, a + 999]}).status_code == 400
    assert edi_state(conn, 1)[1] == 1


def test_bd05_edi_the_same_id_twice_is_fine(client, conn):
    edi_customer(conn, 1)
    a = edi_txn(conn, 1, "2026-10-01", 200)
    edi_sync(conn, 1)
    assert client.post(EDI, json={"customer_id": 1, "ids": [a, a]}).json() == {"deleted": 1}


def test_bd06_an_empty_or_huge_list_is_rejected(client, conn):
    edi_customer(conn, 1)
    assert client.post(EDI, json={"customer_id": 1, "ids": []}).status_code == 422
    assert client.post(EDI, json={"customer_id": 1, "ids": list(range(1, 2002))}).status_code == 422
    assert client.post(EDI, json={"ids": [1]}).status_code == 422


def test_bd07_iop_the_principal_of_deleted_rows_comes_back_off_once(client, conn):
    iop_customer(conn, 1, loan=20000, paid=12500)                           # 3,000 earlier + 9,500 from the row below
    a = iop_txn(conn, 1, "2026-10-01", 500, principal=9500)
    b = iop_txn(conn, 1, "2026-10-02", 500, principal=0)
    c = iop_txn(conn, 1, "2026-10-03", 600, principal=2000)
    conn.execute(text("UPDATE tbl_iop_customer SET principal_paid = 14500, outstanding_balance = 5500 WHERE customer_id = 1"))
    r = client.post(IOP, json={"customer_id": 1, "ids": [a, c]})
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    assert iop_state(conn, 1) == (3000.0, 17000.0, 1)                       # 14,500 - 9,500 - 2,000; balance = 20,000 - 3,000


def test_bd08_iop_plain_interest_rows_only_leave(client, conn):
    iop_customer(conn, 1, loan=20000, paid=1000)
    a = iop_txn(conn, 1, "2026-10-01", 500)
    b = iop_txn(conn, 1, "2026-10-02", 500)
    assert client.post(IOP, json={"customer_id": 1, "ids": [a, b]}).json() == {"deleted": 2}
    assert iop_state(conn, 1) == (1000.0, 19000.0, 0)                       # nothing to take back


def test_bd09_iop_all_or_nothing_and_other_customers_untouched(client, conn):
    iop_customer(conn, 1, loan=20000, paid=9500)
    iop_customer(conn, 2, loan=20000, paid=9500)
    a = iop_txn(conn, 1, "2026-10-01", 500, principal=9500)
    other = iop_txn(conn, 2, "2026-10-01", 500, principal=9500)
    r = client.post(IOP, json={"customer_id": 1, "ids": [a, other]})
    assert r.status_code == 400
    assert iop_state(conn, 1) == (9500.0, 10500.0, 1) and iop_state(conn, 2) == (9500.0, 10500.0, 1)


def test_bd10_iop_an_account_is_not_closed_or_reopened_by_deleting(client, conn):
    iop_customer(conn, 1, loan=10000, paid=10000)
    conn.execute(text("UPDATE tbl_iop_customer SET is_closed = true WHERE customer_id = 1"))
    a = iop_txn(conn, 1, "2026-10-01", 500, principal=10000)
    assert client.post(IOP, json={"customer_id": 1, "ids": [a]}).status_code == 200
    closed = conn.execute(text("SELECT is_closed FROM tbl_iop_customer WHERE customer_id = 1")).scalar()
    assert closed is True                                                    # still closed (the user decides)


def test_bd11_the_single_delete_still_works_as_before(client, conn):
    edi_customer(conn, 1)
    a = edi_txn(conn, 1, "2026-10-01", 200)
    edi_sync(conn, 1)
    assert client.delete(f"/api/transactions/edi/{a}").status_code == 204
    assert edi_state(conn, 1) == (10000.0, 0)


def test_bd12_it_needs_a_login(conn):
    from fastapi.testclient import TestClient
    from app.api.deps import get_current_user
    from app.main import app
    app.dependency_overrides.pop(get_current_user, None)
    r = TestClient(app).post(EDI, json={"customer_id": 1, "ids": [1]})
    assert r.status_code in (401, 403)
