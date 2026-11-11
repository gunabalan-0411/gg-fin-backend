"""
A NEW account asked to be OPEN must never silently become a closed one. NAB-01 ... NAB-16.

The bug (re-loan): duplicating an account whose balance had gone negative (an overpaid account) copied that negative balance; the server then
created the copy already CLOSED (balance <= 0), so it vanished from the active list, and the carry-over (UPI ids move closed -> open only)
could not run. Now: an explicit "open" with nothing left to pay is refused with a clear 422; "closed" is honoured; no flag still derives it.
"""
import pytest

API = "/api/customers"

EDI = {"customer_name": "Selvam", "customer_name_ta": "செல்வம்", "customer_segment_id": 1, "loan_amount": 10000, "disbursed_amount": 9000,
       "interest": 1000, "loan_start_date": "2026-01-01"}
IOP = {"customer_name": "Gopal", "customer_name_ta": "கோபால்", "customer_segment_id": 1, "loan_amount": 100000, "interest": 2000,
       "interest_payment_frequency": 1}


def make(client, product, cid, **over):
    base = EDI if product == "edi" else IOP
    return client.post(f"{API}/{product}", json={"customer_id": cid, **base, **over})


def get(client, product, cid):
    return client.get(f"{API}/{product}/{cid}").json()


def next_id(client, product):
    return client.get(f"{API}/{product}/next-id").json()["next_id"]


# ── EDI ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_nab01_open_copy_with_a_negative_balance_is_refused_not_created_closed(client):
    r = make(client, "edi", 1, outstanding_balance=-2000, is_closed=False)
    assert r.status_code == 422
    assert "zero or less" in r.json()["detail"] and "Account Closed" in r.json()["detail"]
    assert client.get(f"{API}/edi/1").status_code == 404                     # nothing half-created
    assert next_id(client, "edi") == 1                                       # and no id was used up


def test_nab02_open_with_a_zero_balance_is_refused_too(client):
    assert make(client, "edi", 1, outstanding_balance=0, is_closed=False).status_code == 422


def test_nab03_explicitly_closed_with_a_negative_balance_is_allowed(client):
    r = make(client, "edi", 1, outstanding_balance=-2000, is_closed=True)
    assert r.status_code == 201 and r.json()["is_closed"] is True


def test_nab04_no_flag_still_derives_closed_from_the_balance(client):
    r = make(client, "edi", 1, outstanding_balance=0)
    assert r.status_code == 201 and r.json()["is_closed"] is True            # unchanged behaviour for plain API / import callers


def test_nab05_open_with_a_balance_left_is_open(client):
    r = make(client, "edi", 1, outstanding_balance=8000, is_closed=False)
    assert r.status_code == 201 and r.json()["is_closed"] is False and r.json()["outstanding_balance"] == "8000.00"


def test_nab06_open_without_a_balance_takes_the_loan_as_the_balance(client):
    r = make(client, "edi", 1, is_closed=False)
    assert r.status_code == 201 and r.json()["is_closed"] is False and r.json()["outstanding_balance"] == "10000.00"


def test_nab07_explicit_closed_with_a_balance_left_is_honoured(client):
    r = make(client, "edi", 1, outstanding_balance=8000, is_closed=True)
    assert r.status_code == 201 and r.json()["is_closed"] is True            # the switch said closed; it is no longer ignored


def test_nab08_no_loan_amount_is_not_a_re_loan_so_nothing_is_refused(client):
    body = {"customer_id": 1, "customer_name": "Bare", "is_closed": False}
    r = client.post(f"{API}/edi", json=body)
    assert r.status_code == 201


# ── IOP ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_nab09_iop_open_with_principal_paid_over_the_loan_is_refused(client):
    r = make(client, "iop", 1, principal_paid=120000, is_closed=False)
    assert r.status_code == 422 and "Principal paid" in r.json()["detail"]
    assert next_id(client, "iop") == 1


def test_nab10_iop_open_with_principal_paid_equal_to_the_loan_is_refused(client):
    assert make(client, "iop", 1, principal_paid=100000, is_closed=False).status_code == 422


def test_nab11_iop_closed_and_derived_cases(client):
    assert make(client, "iop", 1, principal_paid=120000, is_closed=True).json()["is_closed"] is True
    assert make(client, "iop", 2, principal_paid=100000).json()["is_closed"] is True          # no flag: derived as before
    r = make(client, "iop", 3, principal_paid=25000, is_closed=False)
    assert r.status_code == 201 and r.json()["is_closed"] is False and r.json()["outstanding_balance"] == "75000.00"


def test_nab12_iop_a_fresh_loan_starts_with_nothing_paid(client):
    r = make(client, "iop", 1, principal_paid=0, is_closed=False)
    assert r.status_code == 201 and r.json()["outstanding_balance"] == "100000.00"


# ── the real flow ────────────────────────────────────────────────────────────────────────────────────────

def overpay(client, cid):
    for d in ("2026-02-01", "2026-02-02"):
        assert client.post("/api/transactions/edi", json={"customer_id": cid, "collection_date": d, "amount": 6000}).status_code == 201


def test_nab13_an_overpaid_open_account_stays_open_and_negative(client):
    make(client, "edi", 1)
    overpay(client, 1)
    old = get(client, "edi", 1)
    assert old["outstanding_balance"] == "-2000.00" and old["is_closed"] is False          # the state that started this


def test_nab14_re_loan_with_the_old_negative_balance_is_refused_and_the_old_account_is_untouched(client):
    make(client, "edi", 1)
    overpay(client, 1)
    old = get(client, "edi", 1)
    r = client.post(f"{API}/edi", json={**old, "customer_id": next_id(client, "edi"), "outstanding_balance": float(old["outstanding_balance"]), "is_closed": False})
    assert r.status_code == 422
    assert get(client, "edi", 1)["is_closed"] is False                                      # the old account is still there, nothing was closed
    assert client.get(f"{API}/edi/2").status_code == 404


def test_nab15_re_loan_with_a_fresh_balance_works_end_to_end(client):
    make(client, "edi", 1)
    overpay(client, 1)
    old = get(client, "edi", 1)
    new_id = next_id(client, "edi")
    r = client.post(f"{API}/edi", json={**old, "customer_id": new_id, "outstanding_balance": float(old["loan_amount"]), "is_closed": False})
    assert r.status_code == 201 and r.json()["is_closed"] is False and r.json()["outstanding_balance"] == "10000.00"
    assert client.patch(f"{API}/edi/1", json={"is_closed": True}).status_code == 200       # then the old one is closed, as the flow does
    active = client.get(f"{API}/edi", params={"active_only": True, "limit": 50}).json()["data"]
    assert [c["customer_id"] for c in active] == [new_id]                                   # the new account is on the active list


def test_nab16_the_refusal_names_what_to_do_in_plain_words(client):
    d = make(client, "edi", 1, outstanding_balance=-1, is_closed=False).json()["detail"]
    assert "new loan's balance" in d
    d = make(client, "iop", 1, principal_paid=200000, is_closed=False).json()["detail"]
    assert "new loan's figures" in d
