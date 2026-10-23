"""
The Type / Voice Entry page's submit with IOP principal + interest, the same rules as the OCR page (see test_iop_split.py).
VS-01 ... VS-08.
"""
from sqlalchemy import text

from tests.helpers import add_customer

URL = "/api/voice/submit"
DAY = "2026-10-17"


def iop(conn, cid=1, name="Ravi", loan=20000, paid=0):
    add_customer(conn, "IOP", cid, name)
    conn.execute(text("UPDATE tbl_iop_customer SET loan_amount = :l, principal_paid = :p, outstanding_balance = :o WHERE customer_id = :i"),
                 {"l": loan, "p": paid, "o": loan - paid, "i": cid})


def entry(cid=1, amount=10000, principal=None, mode="CASH"):
    e = {"customer_id": cid, "amount": amount, "payment_mode": mode}
    if principal is not None:
        e["principal_amount"] = principal
    return e


def submit(client, *entries, product="iop"):
    return client.post(URL, json=list(entries), params={"collection_date": DAY, "product": product})


def customer(conn, cid=1):
    r = conn.execute(text("SELECT principal_paid, outstanding_balance, is_closed FROM tbl_iop_customer WHERE customer_id = :i"), {"i": cid}).first()
    return float(r[0]), float(r[1]), bool(r[2])


def txns(conn, cid=1):
    return [(float(a), float(p)) for a, p in conn.execute(
        text("SELECT amount, principal_amount FROM tbl_iop_transactions WHERE customer_id = :i ORDER BY transaction_id"), {"i": cid}).all()]


def test_the_transaction_is_the_interest_and_the_principal_goes_to_principal_paid(client, conn):   # VS-01
    iop(conn, loan=20000, paid=3000)
    r = submit(client, entry(amount=10000, principal=9500))
    assert r.status_code == 200, r.text
    assert r.json()["submitted"] == 1 and r.json()["principal_added"] == 9500
    assert txns(conn) == [(500.0, 9500.0)]
    assert customer(conn) == (12500.0, 7500.0, False)                               # 3,000 + 9,500; balance 7,500; account left open


def test_an_entry_without_a_split_behaves_as_before_and_the_answer_is_unchanged(client, conn):     # VS-02
    iop(conn, loan=20000, paid=3000)
    r = submit(client, entry(amount=500))
    assert r.status_code == 200
    assert "principal_added" not in r.json()
    assert txns(conn) == [(500.0, 0.0)] and customer(conn)[0] == 3000.0


def test_edi_ignores_a_principal_amount(client, conn):                                             # VS-02
    add_customer(conn, "EDI", 9, "Selvam")
    r = submit(client, entry(cid=9, amount=1000, principal=900), product="edi")
    assert r.status_code == 200 and "principal_added" not in r.json()
    amount = conn.execute(text("SELECT amount FROM tbl_edi_transactions WHERE customer_id = 9")).scalar()
    assert float(amount) == 1000.0                                                                 # the whole amount, nothing split


def test_submitting_again_adds_nothing_twice_and_a_correction_moves_the_difference(client, conn):  # VS-03
    iop(conn, loan=20000)
    for _ in range(2):
        assert submit(client, entry(amount=10000, principal=9500)).status_code == 200
    assert customer(conn)[0] == 9500.0 and txns(conn) == [(500.0, 9500.0)]
    submit(client, entry(amount=10000, principal=8000))
    assert customer(conn)[0] == 8000.0 and txns(conn) == [(2000.0, 8000.0)]


def test_the_page_must_send_a_split_it_loaded_or_the_principal_is_taken_back(client, conn):         # VS-04
    """Why the page loads the split with the row: re-submitting the interest alone is a correction to 'no principal'."""
    iop(conn, loan=20000)
    submit(client, entry(amount=10000, principal=9500))
    submit(client, entry(amount=500))                                                               # the interest only, no principal sent
    assert customer(conn)[0] == 0.0
    submit(client, entry(amount=10000, principal=9500))                                             # the row as the page now shows it (total + split)
    submit(client, entry(amount=10000, principal=9500))
    assert customer(conn)[0] == 9500.0


def test_a_principal_above_the_balance_or_the_amount_is_refused_and_nothing_at_all_is_saved(client, conn):   # VS-05
    iop(conn, 1, "Ravi", loan=20000, paid=15000)                                                    # 5,000 left
    iop(conn, 2, "Kumar", loan=20000)
    r = submit(client, entry(cid=2, amount=300), entry(cid=1, amount=10000, principal=9500))
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "bad_principal" and "₹5,000" in d["message"] and "Ravi" in d["message"]
    assert d["rows"][0]["index"] == 1
    assert txns(conn, 1) == [] and txns(conn, 2) == []                                              # the good entry was not saved either
    over = submit(client, entry(cid=2, amount=500, principal=600))
    assert over.status_code == 422 and "more than the amount collected" in over.json()["detail"]["message"]
    assert submit(client, entry(cid=2, amount=500, principal=-1)).status_code == 422


def test_exactly_the_remaining_balance_is_allowed_and_the_account_stays_open(client, conn):          # VS-06
    iop(conn, loan=20000, paid=15000)
    assert submit(client, entry(amount=5200, principal=5000)).status_code == 200
    assert customer(conn) == (20000.0, 0.0, False)


def test_a_zero_or_missing_customer_is_skipped_as_before(client, conn):                              # VS-07
    iop(conn)
    r = submit(client, {"customer_id": None, "amount": 100}, entry(amount=0), entry(amount=500))
    assert r.status_code == 200 and r.json()["submitted"] == 1


def test_the_transaction_list_carries_the_principal_so_the_page_can_show_the_split(client, conn):    # VS-08
    iop(conn, loan=20000)
    submit(client, entry(amount=10000, principal=9500))
    got = client.get("/api/transactions/iop", params={"collection_date": DAY}).json()
    assert len(got) == 1 and float(got[0]["amount"]) == 500.0 and float(got[0]["principal_amount"]) == 9500.0
