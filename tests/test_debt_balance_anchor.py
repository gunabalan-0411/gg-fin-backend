"""
Capital > Borrowings: the balance starts from what the lender reported (balance_anchor on balance_anchor_date) and is
rolled forward one EMI per 5th at the loan's own EMI and rate. DB-01 ... DB-12.
"""
from datetime import date

import pytest

from app.api import debts

URL = "/api/debts"

# the six loans of the bank sheet taken on 2025-10-24: (start, lender, borrower, amount, emi, rate, tenure, lender balance)
SHEET = [
    ("2024-09-07", "HDFC PL", "Gu", 1180000, 25568, 10.9, 60, 975455),
    ("2023-02-01", "HDFC Home L", "Gu", 3200000, 29170, 8.0, 240, 3041416),
    ("2023-01-07", "ICICI PL", "Kam", 1000000, 21489, 10.5, 60, 514822),
    ("2025-02-01", "RE Bike Loan", "Kam", 219704, 10995, 10.1, 24, 155114),
    ("2022-01-07", "ICICI PL", "Suji", 450000, 10000, 13.0, 60, 202000),
    ("2023-01-07", "HDFC PL", "Suji", 500000, 11000, 13.0, 60, 324000),
]


@pytest.fixture(autouse=True)
def _ref_5_oct_2026(monkeypatch):
    monkeypatch.setattr(debts, "_balance_ref_date", lambda: date(2026, 10, 5))


def loan(client, **over):
    body = {"date": "2022-01-07", "lender_name": "ICICI PL", "borrower_name": "Suji", "amount": 450000,
            "emi_amount": 10000, "interest_rate_pa": 13, "tenure_months": 60,
            "balance_anchor": 202000, "balance_anchor_date": "2025-10-24", **over}
    r = client.post(URL, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_4_5_lakh_loan_is_rolled_forward_from_the_lenders_balance(client):          # DB-01
    # the lender said 2,02,000 on 24 Oct 2025; 12 EMIs of 10,000 at 13% later (5 Nov 2025 ... 5 Oct 2026)
    assert loan(client)["balance"] == pytest.approx(102468, abs=1)


@pytest.mark.parametrize("row, expected", list(zip(SHEET, [764638, 2930688, 300910, 33304, 102468, 228566])))
def test_every_loan_of_the_bank_sheet_on_5_oct_2026(client, row, expected):               # DB-02
    start, lender, borrower, amount, emi, rate, tenure, bal = row
    got = loan(client, date=start, lender_name=lender, borrower_name=borrower, amount=amount, emi_amount=emi,
               interest_rate_pa=rate, tenure_months=tenure, balance_anchor=bal)
    assert got["balance"] == pytest.approx(expected, abs=2)


def test_the_tenure_does_not_decide_the_balance_any_more(client):                         # DB-03
    short = loan(client, tenure_months=60)["balance"]
    long = loan(client, tenure_months=240)["balance"]
    assert short == long


def test_emis_are_counted_on_the_5th_after_the_anchor_date():                              # DB-04
    ref = date(2026, 10, 5)
    assert debts._emis_since(date(2025, 10, 24), ref) == 12        # first one 5 Nov 2025
    assert debts._emis_since(date(2025, 10, 5), ref) == 12         # taken ON the 5th: that EMI is already in it
    assert debts._emis_since(date(2025, 10, 4), ref) == 13         # taken the day before: the 5 Oct 2025 EMI still to come
    assert debts._emis_since(date(2026, 10, 5), ref) == 0
    assert debts._emis_since(date(2026, 12, 1), ref) == 0          # a lender figure from the future: nothing to roll


def test_a_balance_is_never_negative_and_stops_at_zero(client):                           # DB-05
    got = loan(client, balance_anchor=20000)                       # two EMIs from the end, twelve rolled
    assert got["balance"] == 0


def test_without_a_rate_the_emi_just_comes_off(client):                                    # DB-06
    got = loan(client, interest_rate_pa=None, tenure_months=None, balance_anchor=150000)
    assert got["balance"] == 150000 - 12 * 10000


def test_without_an_emi_only_repayments_after_the_lender_date_come_off(client):          # DB-07
    got = loan(client, emi_amount=None, interest_rate_pa=None, tenure_months=None, balance_anchor=100000)
    did = got["id"]
    client.post(f"{URL}/{did}/repayments", json={"date": "2025-09-01", "amount": 5000})    # before the lender figure: already in it
    client.post(f"{URL}/{did}/repayments", json={"date": "2026-02-01", "amount": 7000})
    rows = {d["id"]: d for d in client.get(URL).json()}
    assert rows[did]["balance"] == 93000


def test_a_loan_without_a_lender_balance_keeps_the_old_formula(client):                    # DB-08
    got = loan(client, balance_anchor=None, balance_anchor_date=None)
    assert got["balance"] == pytest.approx(29362, abs=1)           # the schedule-based figure, unchanged
    assert got["balance_anchor"] is None and got["balance_anchor_date"] is None


def test_the_balance_and_its_date_come_back_in_the_list(client):                          # DB-09
    loan(client)
    row = client.get(URL).json()[0]
    assert row["balance_anchor"] == 202000
    assert row["balance_anchor_date"] == "2025-10-24"


def test_a_newer_lender_balance_replaces_the_old_one(client):                              # DB-10
    did = loan(client)["id"]
    r = client.patch(f"{URL}/{did}", json={"balance_anchor": 90000, "balance_anchor_date": "2026-09-30"})
    assert r.status_code == 200
    assert r.json()["balance"] == pytest.approx(90000 * (1 + 0.13 / 12) - 10000, abs=1)   # one EMI (5 Oct 2026) since 30 Sep


def test_the_balance_and_the_date_go_together(client):                                     # DB-11
    r = client.post(URL, json={"date": "2024-01-01", "lender_name": "X", "amount": 1000, "balance_anchor": 500})
    assert r.status_code == 422
    did = loan(client)["id"]
    assert client.patch(f"{URL}/{did}", json={"balance_anchor_date": None}).status_code == 422
    assert client.patch(f"{URL}/{did}", json={"balance_anchor": -5}).status_code == 422


def test_clearing_both_goes_back_to_the_schedule(client):                                  # DB-12
    did = loan(client)["id"]
    r = client.patch(f"{URL}/{did}", json={"balance_anchor": None, "balance_anchor_date": None})
    assert r.status_code == 200
    assert r.json()["balance"] == pytest.approx(29362, abs=1)
