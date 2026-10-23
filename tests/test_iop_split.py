"""
IOP collections that are part interest, part principal (10,000 = 500 interest + 9,500 principal) submitted from the OCR page.
IS-01 ... IS-14.
  * the transaction is the INTEREST only; the principal is added to the customer's principal_paid and the balance recalculated;
  * re-submitting / correcting / deleting moves principal_paid by the difference only;
  * a principal above the amount or above the remaining balance is refused and nothing is saved;
  * the account is never closed automatically.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.helpers import add_customer

SUBMIT = "/api/ocr/submit"
CHECK = "/api/ocr/iop-split-check"


def iop(conn, cid=1, name="Ravi", loan=20000, paid=0, closed=False):
    add_customer(conn, "IOP", cid, name, closed=closed)
    conn.execute(text("UPDATE tbl_iop_customer SET loan_amount = :l, principal_paid = :p, outstanding_balance = :o WHERE customer_id = :i"),
                 {"l": loan, "p": paid, "o": loan - paid, "i": cid})


def rec(cid=1, amount=10000, principal=0, date="17-10-2026", mode="CASH", paid=True, product="IOP"):
    return {"collection_date": date, "customer_name": "x", "customer_id": cid, "product_type": product, "payment_mode": mode,
            "is_paid": paid, "amount": amount, "principal_amount": principal}


def submit(client, *records):
    return client.post(SUBMIT, json={"records": list(records)})


def customer(conn, cid=1):
    r = conn.execute(text("SELECT principal_paid, outstanding_balance, is_closed FROM tbl_iop_customer WHERE customer_id = :i"), {"i": cid}).first()
    return float(r[0]), float(r[1]), bool(r[2])


def txns(conn, cid=1):
    rows = conn.execute(text("SELECT amount, principal_amount, payment_status, payment_mode, collection_date FROM tbl_iop_transactions "
                             "WHERE customer_id = :i ORDER BY transaction_id"), {"i": cid}).all()
    return [(float(a), float(p), s, m) for a, p, s, m, _d in rows]


# ── the basic split ───────────────────────────────────────────────────────────────────────────────────────
def test_the_transaction_is_the_interest_and_the_principal_goes_to_principal_paid(client, conn):   # IS-01
    iop(conn, loan=20000, paid=3000)
    r = submit(client, rec(amount=10000, principal=9500))
    assert r.status_code == 200, r.text
    assert r.json()["submitted"] == 1 and r.json()["principal_added"] == 9500
    assert txns(conn) == [(500.0, 9500.0, "PAID", "CASH")]                    # 500 interest; the 10,000 is not stored as the transaction
    assert customer(conn) == (12500.0, 7500.0, False)                          # 3,000 + 9,500 added; balance = 20,000 - 12,500


def test_a_row_without_a_split_behaves_exactly_as_before(client, conn):                           # IS-02
    iop(conn, loan=20000, paid=3000)
    submit(client, rec(amount=500))
    assert txns(conn) == [(500.0, 0.0, "PAID", "CASH")]
    assert customer(conn) == (3000.0, 17000.0, False)


def test_edi_rows_and_unpaid_rows_never_move_principal(client, conn):                             # IS-02
    iop(conn, loan=20000)
    add_customer(conn, "EDI", 9, "Selvam")
    r = submit(client, rec(cid=9, amount=1000, principal=900, product="EDI"),
               rec(amount=10000, principal=9500, paid=False, mode="ONLINE"))
    assert r.status_code == 200 and "principal_added" not in r.json()                  # nothing moved: the answer is the old one
    assert customer(conn) == (0.0, 20000.0, False)
    assert txns(conn) == [(10000.0, 0.0, "UNPAID", "ONLINE")]                  # unpaid: saved as before, no principal


def test_the_whole_amount_can_be_principal_with_zero_interest(client, conn):                       # IS-03
    iop(conn, loan=20000)
    assert submit(client, rec(amount=5000, principal=5000)).status_code == 200
    assert txns(conn) == [(0.0, 5000.0, "PAID", "CASH")]
    assert customer(conn)[0] == 5000.0


# ── re-submitting never adds twice ────────────────────────────────────────────────────────────────────────
def test_submitting_the_same_sheet_twice_adds_the_principal_once(client, conn):                    # IS-04
    iop(conn, loan=20000)
    for _ in range(3):
        assert submit(client, rec(amount=10000, principal=9500)).status_code == 200
    assert txns(conn) == [(500.0, 9500.0, "PAID", "CASH")]
    assert customer(conn) == (9500.0, 10500.0, False)


def test_a_corrected_principal_moves_principal_paid_by_the_difference(client, conn):               # IS-05
    iop(conn, loan=20000)
    submit(client, rec(amount=10000, principal=9500))
    submit(client, rec(amount=10000, principal=8000))                          # corrected down
    assert customer(conn)[0] == 8000.0 and txns(conn) == [(2000.0, 8000.0, "PAID", "CASH")]
    submit(client, rec(amount=10000, principal=0))                             # the split removed
    assert customer(conn) == (0.0, 20000.0, False) and txns(conn) == [(10000.0, 0.0, "PAID", "CASH")]


def test_a_row_marked_unpaid_later_takes_its_principal_back(client, conn):                         # IS-05
    iop(conn, loan=20000)
    submit(client, rec(amount=10000, principal=9500, mode="ONLINE"))
    submit(client, rec(amount=10000, principal=9500, mode="ONLINE", paid=False))
    assert customer(conn)[0] == 0.0


def test_two_rows_of_one_customer_share_the_balance_and_each_mode_is_its_own_transaction(client, conn):   # IS-06
    iop(conn, loan=20000)
    r = submit(client, rec(amount=6000, principal=5000, mode="CASH"), rec(amount=4500, principal=4000, mode="ONLINE"))
    assert r.status_code == 200 and r.json()["principal_added"] == 9000
    assert customer(conn)[0] == 9000.0
    assert sorted(txns(conn)) == [(500.0, 4000.0, "PAID", "ONLINE"), (1000.0, 5000.0, "PAID", "CASH")]


# ── refusals: nothing is saved ────────────────────────────────────────────────────────────────────────────
def test_a_principal_above_the_remaining_balance_is_refused_and_nothing_at_all_is_saved(client, conn):   # IS-07
    iop(conn, 1, "Ravi", loan=20000, paid=15000)                               # 5,000 left
    iop(conn, 2, "Kumar", loan=20000)
    r = submit(client, rec(cid=2, amount=300), rec(cid=1, amount=10000, principal=9500))
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "bad_principal" and "₹9,500" in d["message"] and "₹5,000" in d["message"] and "Ravi" in d["message"]
    assert d["rows"] == [{"index": 1, "message": d["message"]}]
    assert txns(conn, 2) == [] and txns(conn, 1) == []                         # the good row was NOT saved either
    assert customer(conn, 1) == (15000.0, 5000.0, False)


def test_the_refusal_counts_what_the_sheet_already_took_from_the_balance(client, conn):           # IS-07
    iop(conn, loan=20000, paid=10000)                                          # 10,000 left
    r = submit(client, rec(amount=6000, principal=6000, mode="CASH"), rec(amount=5000, principal=5000, mode="ONLINE"))
    assert r.status_code == 422 and r.json()["detail"]["rows"][0]["index"] == 1
    assert txns(conn) == []


def test_exactly_the_remaining_balance_is_allowed_and_the_account_is_not_closed(client, conn):    # IS-08
    iop(conn, loan=20000, paid=15000)
    assert submit(client, rec(amount=5200, principal=5000)).status_code == 200
    assert customer(conn) == (20000.0, 0.0, False)                                # balance 0, but the account is left open


def test_a_principal_above_the_amount_or_below_zero_is_refused(client, conn):                     # IS-09
    iop(conn, loan=20000)
    over = submit(client, rec(amount=500, principal=600))
    assert over.status_code == 422 and "more than the amount collected" in over.json()["detail"]["message"]
    neg = submit(client, rec(amount=500, principal=-1))
    assert neg.status_code == 422 and "negative" in neg.json()["detail"]["message"]
    assert txns(conn) == []


def test_a_resubmit_that_only_repeats_the_same_principal_is_never_refused_for_the_balance(client, conn):   # IS-10
    iop(conn, loan=10000)
    assert submit(client, rec(amount=10000, principal=9500)).status_code == 200      # balance now 500
    again = submit(client, rec(amount=10000, principal=9500))                         # the same 9,500 is already counted
    assert again.status_code == 200, again.text
    assert customer(conn)[0] == 9500.0


# ── the live check ────────────────────────────────────────────────────────────────────────────────────────
def test_the_check_reports_interest_and_the_balance_before_and_after_without_saving(client, conn):   # IS-11
    iop(conn, loan=20000, paid=3000)
    r = client.post(CHECK, json={"records": [rec(amount=10000, principal=9500)]})
    assert r.status_code == 200
    assert r.json()["rows"] == [{"index": 0, "principal": 9500.0, "interest": 500.0, "balance_before": 17000.0, "balance_after": 7500.0, "error": None}]
    assert txns(conn) == [] and customer(conn)[0] == 3000.0


def test_the_check_names_the_problem_and_skips_non_iop_and_unassigned_rows(client, conn):          # IS-11
    iop(conn, loan=1000)
    add_customer(conn, "EDI", 9, "Selvam")
    r = client.post(CHECK, json={"records": [rec(cid=9, amount=100, product="EDI"), rec(amount=900, principal=2000), {**rec(), "customer_id": None}]})
    rows = r.json()["rows"]
    assert [x["index"] for x in rows] == [1]
    assert "more than the amount collected" in rows[0]["error"]


def test_the_check_agrees_with_the_submit(client, conn):                                           # IS-11
    iop(conn, loan=20000, paid=15000)
    bad = rec(amount=10000, principal=9500)
    assert client.post(CHECK, json={"records": [bad]}).json()["rows"][0]["error"] is not None
    assert submit(client, bad).status_code == 422
    good = rec(amount=5200, principal=5000)
    assert client.post(CHECK, json={"records": [good]}).json()["rows"][0]["error"] is None
    assert submit(client, good).status_code == 200


# ── taking a transaction away ─────────────────────────────────────────────────────────────────────────────
def test_deleting_the_transaction_takes_its_principal_back(client, conn):                          # IS-12
    iop(conn, loan=20000, paid=1000)
    submit(client, rec(amount=10000, principal=9500))
    tid = conn.execute(text("SELECT transaction_id FROM tbl_iop_transactions")).scalar()
    assert customer(conn)[0] == 10500.0
    assert client.delete(f"/api/transactions/iop/{tid}").status_code == 204
    assert customer(conn) == (1000.0, 19000.0, False)


def test_marking_it_unpaid_on_the_collections_page_takes_the_principal_back(client, conn):         # IS-12
    iop(conn, loan=20000)
    submit(client, rec(amount=10000, principal=9500))
    tid = conn.execute(text("SELECT transaction_id FROM tbl_iop_transactions")).scalar()
    r = client.patch(f"/api/transactions/iop/{tid}", json={"payment_status": "UNPAID"})
    assert r.status_code == 200 and float(r.json()["principal_amount"]) == 0.0
    assert customer(conn)[0] == 0.0
    assert client.patch(f"/api/transactions/iop/{tid}", json={"amount": 700}).status_code == 200      # a plain edit leaves it alone
    assert customer(conn)[0] == 0.0


def test_a_normal_transaction_deletes_as_before(client, conn):                                    # IS-12
    iop(conn, loan=20000, paid=1000)
    submit(client, rec(amount=500))
    tid = conn.execute(text("SELECT transaction_id FROM tbl_iop_transactions")).scalar()
    assert client.delete(f"/api/transactions/iop/{tid}").status_code == 204
    assert customer(conn)[0] == 1000.0


def test_the_transaction_list_shows_the_principal_it_carried(client, conn):                        # IS-13
    iop(conn, loan=20000)
    submit(client, rec(amount=10000, principal=9500))
    got = client.get("/api/transactions/iop", params={"collection_date": "2026-10-17"}).json()
    assert len(got) == 1 and float(got[0]["amount"]) == 500.0 and float(got[0]["principal_amount"]) == 9500.0


def test_an_unknown_customer_does_not_break_the_submit(client, conn):                              # IS-14
    r = submit(client, rec(cid=999, amount=500, principal=100))
    assert r.status_code == 200


def test_login_is_required(client):                                                               # IS-14
    from fastapi.testclient import TestClient
    from app.api.deps import get_current_user
    from app.main import app
    app.dependency_overrides.pop(get_current_user, None)
    assert TestClient(app).post(CHECK, json={"records": []}).status_code == 401


# ── gaps found by the mutation checks ─────────────────────────────────────────────────────────────────────
def test_repeating_an_unchanged_principal_is_not_refused_even_when_the_loan_was_later_lowered(client, conn):   # IS-10
    iop(conn, loan=20000)
    assert submit(client, rec(amount=10000, principal=9500)).status_code == 200
    conn.execute(text("UPDATE tbl_iop_customer SET loan_amount = 5000, outstanding_balance = 5000 - principal_paid WHERE customer_id = 1"))   # now paid > loan
    again = submit(client, rec(amount=10000, principal=9500))                      # nothing changes: it must not be blocked
    assert again.status_code == 200, again.text
    assert customer(conn)[0] == 9500.0
    worse = submit(client, rec(amount=10000, principal=9600))                      # but ADDING more than the (negative) balance is
    assert worse.status_code == 422


def test_the_save_itself_ignores_principal_on_an_unpaid_row(client, conn, session):               # IS-02
    """Not only the OCR route: any caller of the service (the check above it is not the only guard)."""
    from datetime import date

    from app.services.transaction_service import IopTransactionService
    iop(conn, loan=20000)
    IopTransactionService(session).upsert(1, date(2026, 10, 17), 500, "ONLINE", False, 5000)
    assert customer(conn)[0] == 0.0
    assert txns(conn) == [(500.0, 0.0, "UNPAID", "ONLINE")]
