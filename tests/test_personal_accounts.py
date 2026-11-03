"""
Personal Finance > Monthly budget > Expense accounts: accounts used only for expenses, funds put in, expenses paid from them, a month
view (opening, funds, spent, closing) and matching with the bank. Private to the login. PAC-01 ... PAC-20.
"""
from datetime import date

import pytest

from app.api.deps import get_current_user
from app.main import app
from app.models.user import User

P = "/api/personal"


def as_user(uid: int):
    app.dependency_overrides[get_current_user] = lambda: User(id=uid, username=f"user{uid}", hashed_password="x")


@pytest.fixture(autouse=True)
def _back_to_user_one(client):
    as_user(1)
    yield
    as_user(1)


def account(client, name="HDFC expenses", opening=10000, opening_date="2026-09-01"):
    r = client.post(f"{P}/accounts", json={"name": name, "opening_balance": opening, "opening_date": opening_date})
    assert r.status_code == 201, r.text
    return r.json()


def fund(client, aid, amount, day="2026-09-05", notes=None):
    r = client.post(f"{P}/accounts/{aid}/funds", json={"amount": amount, "date": day, "notes": notes})
    assert r.status_code == 201, r.text
    return r.json()


def spend(client, amount, aid, day="2026-09-10", **over):
    r = client.post(f"{P}/expenses", json={"amount": amount, "date": day, "account_id": aid, **over})
    assert r.status_code == 201, r.text
    return r.json()


def view(client, month=None):
    r = client.get(f"{P}/accounts", params={"month": month} if month else None)
    assert r.status_code == 200, r.text
    return r.json()


def one(client, aid, month=None):
    return next(a for a in view(client, month)["accounts"] if a["id"] == aid)


def f(v):
    return float(v)


def test_a_new_account_holds_its_opening_balance_and_opens_on_the_first_of_this_month_by_default(client):   # PAC-01
    r = client.post(f"{P}/accounts", json={"name": "Wallet", "opening_balance": 2500})
    assert r.status_code == 201
    a = r.json()
    t = date.today()
    assert a["opening_date"] == t.replace(day=1).isoformat()
    assert f(a["balance_now"]) == 2500 and f(a["month_opening"]) == 2500 and a["active"] is True


def test_the_name_is_required_trimmed_limited_and_unique_per_person_ignoring_case(client):                  # PAC-02
    assert client.post(f"{P}/accounts", json={"name": "   "}).status_code == 422
    assert client.post(f"{P}/accounts", json={"name": "x" * 61}).status_code == 422
    assert client.post(f"{P}/accounts", json={"name": "Wallet", "opening_balance": 1e20}).status_code == 422
    assert client.post(f"{P}/accounts", json={"name": "  Wallet "}).json()["name"] == "Wallet"
    assert client.post(f"{P}/accounts", json={"name": "WALLET"}).status_code == 409
    as_user(2)
    assert client.post(f"{P}/accounts", json={"name": "Wallet"}).status_code == 201          # another person may use the same name


def test_a_fund_adds_to_the_balance_and_must_be_more_than_zero(client):                                      # PAC-03
    a = account(client)
    assert f(fund(client, a["id"], 5000)["balance_now"]) == 15000
    for bad in (0, -5, 1e20):
        assert client.post(f"{P}/accounts/{a['id']}/funds", json={"amount": bad, "date": "2026-09-06"}).status_code == 422


def test_a_fund_without_a_date_is_dated_today(client):                                                       # PAC-04
    a = account(client)
    client.post(f"{P}/accounts/{a['id']}/funds", json={"amount": 100})
    e = client.get(f"{P}/accounts/{a['id']}/entries").json()
    assert e[0]["date"] == date.today().isoformat() and e[0]["kind"] == "fund"


def test_an_expense_paid_from_an_account_comes_off_its_balance_and_says_so(client):                          # PAC-05
    a = account(client)
    e = spend(client, 2000, a["id"])
    assert e["account_id"] == a["id"] and e["account_name"] == "HDFC expenses"
    assert f(one(client, a["id"])["balance_now"]) == 8000
    listed = client.get(f"{P}/expenses").json()
    assert listed[0]["account_name"] == "HDFC expenses"


def test_an_expense_needs_no_account_and_then_touches_none(client):                                          # PAC-06
    a = account(client)
    e = spend(client, 700, None)
    assert e["account_id"] is None and e["account_name"] is None
    assert f(one(client, a["id"])["balance_now"]) == 10000


def test_an_expense_cannot_name_an_account_that_is_not_yours(client):                                        # PAC-07
    mine = account(client)
    as_user(2)
    theirs = account(client, "Other")
    r = client.post(f"{P}/expenses", json={"amount": 5, "date": "2026-09-10", "account_id": mine["id"]})
    assert r.status_code == 422
    as_user(1)
    assert client.post(f"{P}/expenses", json={"amount": 5, "date": "2026-09-10", "account_id": 99999}).status_code == 422
    assert client.post(f"{P}/expenses", json={"amount": 5, "date": "2026-09-10", "account_id": theirs["id"]}).status_code == 422


def test_the_account_of_an_expense_can_be_changed_and_taken_off(client):                                     # PAC-08
    a, b = account(client), account(client, "Wallet", 500)
    e = spend(client, 300, a["id"])
    r = client.patch(f"{P}/expenses/{e['id']}", json={"account_id": b["id"]})
    assert r.status_code == 200 and r.json()["account_name"] == "Wallet"
    assert (f(one(client, a["id"])["balance_now"]), f(one(client, b["id"])["balance_now"])) == (10000, 200)
    r = client.patch(f"{P}/expenses/{e['id']}", json={"account_id": None})
    assert r.status_code == 200 and r.json()["account_id"] is None
    assert f(one(client, b["id"])["balance_now"]) == 500
    r = client.patch(f"{P}/expenses/{e['id']}", json={"amount": 120})                                         # not mentioning it leaves it alone
    assert r.json()["account_id"] is None
    assert client.patch(f"{P}/expenses/{e['id']}", json={"account_id": 99999}).status_code == 422


def test_editing_an_expense_without_naming_its_account_keeps_the_account(client):                           # PAC-09
    a = account(client)
    e = spend(client, 300, a["id"])
    r = client.patch(f"{P}/expenses/{e['id']}", json={"amount": 450})
    assert r.json()["account_id"] == a["id"] and f(one(client, a["id"])["balance_now"]) == 9550


def test_the_month_view_carries_the_balance_from_month_to_month(client):                                     # PAC-10
    a = account(client, opening=10000, opening_date="2026-09-01")
    fund(client, a["id"], 5000, "2026-09-05")
    spend(client, 2000, a["id"], "2026-09-10")
    fund(client, a["id"], 1000, "2026-10-03")
    spend(client, 500, a["id"], "2026-10-20")
    sep, octo = one(client, a["id"], "2026-09"), one(client, a["id"], "2026-10")
    assert (f(sep["month_opening"]), f(sep["funds"]), f(sep["spent"]), f(sep["closing"])) == (10000, 5000, 2000, 13000)
    assert (f(octo["month_opening"]), f(octo["funds"]), f(octo["spent"]), f(octo["closing"])) == (13000, 1000, 500, 13500)
    assert f(octo["balance_now"]) == 13500


def test_a_month_before_the_account_opened_is_marked_not_active(client):                                    # PAC-11
    a = account(client, opening_date="2026-09-01")
    aug = one(client, a["id"], "2026-08")
    assert aug["active"] is False and aug["closing"] is None
    v = view(client, "2026-08")
    assert f(v["totals"]["closing"]) == 0


def test_money_dated_before_the_opening_day_does_not_count(client):                                          # PAC-12
    a = account(client, opening=1000, opening_date="2026-09-15")
    spend(client, 300, a["id"], "2026-09-14")
    fund(client, a["id"], 50, "2026-09-10")
    assert f(one(client, a["id"], "2026-09")["closing"]) == 1000
    spend(client, 100, a["id"], "2026-09-15")                                                                  # the opening day itself counts
    assert f(one(client, a["id"], "2026-09")["closing"]) == 900


def test_the_totals_add_up_the_active_accounts(client):                                                      # PAC-13
    a, b = account(client, "A", 1000), account(client, "B", 2000)
    fund(client, a["id"], 500)
    spend(client, 300, b["id"])
    t = view(client, "2026-09")["totals"]
    assert (f(t["month_opening"]), f(t["funds"]), f(t["spent"]), f(t["closing"])) == (3000, 500, 300, 3200)


def test_matching_with_the_bank_shows_the_difference_and_changes_nothing_until_it_is_recorded(client):       # PAC-14
    a = account(client)
    spend(client, 500, a["id"])
    r = client.post(f"{P}/accounts/{a['id']}/reconcile", json={"bank_balance": 9400, "as_of": "2026-09-30"})
    body = r.json()
    assert r.status_code == 200 and f(body["tickbook_balance"]) == 9500 and f(body["difference"]) == -100
    assert body["matched"] is False and body["recorded"] is False
    assert client.get(f"{P}/accounts/{a['id']}/entries").json() == []
    ok = client.post(f"{P}/accounts/{a['id']}/reconcile", json={"bank_balance": 9500, "as_of": "2026-09-30", "record": True}).json()
    assert ok["matched"] is True and ok["recorded"] is False                                                    # nothing to record


def test_recording_the_difference_makes_tickbook_match_the_bank(client):                                      # PAC-15
    a = account(client)
    spend(client, 500, a["id"])
    body = client.post(f"{P}/accounts/{a['id']}/reconcile", json={"bank_balance": 9400, "as_of": "2026-09-30", "record": True}).json()
    assert body["recorded"] is True
    entries = client.get(f"{P}/accounts/{a['id']}/entries").json()
    assert len(entries) == 1 and entries[0]["kind"] == "adjustment" and f(entries[0]["amount"]) == -100 and entries[0]["date"] == "2026-09-30"
    assert f(one(client, a["id"], "2026-09")["closing"]) == 9400
    again = client.post(f"{P}/accounts/{a['id']}/reconcile", json={"bank_balance": 9400, "as_of": "2026-09-30"}).json()
    assert again["matched"] is True
    assert f(one(client, a["id"], "2026-09")["adjustments"]) == -100


def test_the_bank_can_hold_more_than_tickbook_too(client):                                                    # PAC-16
    a = account(client)
    body = client.post(f"{P}/accounts/{a['id']}/reconcile", json={"bank_balance": 10250, "as_of": "2026-09-30", "record": True}).json()
    assert f(body["difference"]) == 250
    assert f(one(client, a["id"], "2026-09")["closing"]) == 10250


def test_matching_on_a_day_counts_only_what_happened_by_then(client):                                         # PAC-17
    a = account(client)
    spend(client, 500, a["id"], "2026-09-10")
    spend(client, 700, a["id"], "2026-09-25")
    body = client.post(f"{P}/accounts/{a['id']}/reconcile", json={"bank_balance": 9500, "as_of": "2026-09-15"}).json()
    assert body["matched"] is True and f(body["tickbook_balance"]) == 9500
    early = client.post(f"{P}/accounts/{a['id']}/reconcile", json={"bank_balance": 9500, "as_of": "2026-08-31"})
    assert early.status_code == 422                                                                              # before the account opened


def test_a_fund_or_an_adjustment_can_be_taken_back(client):                                                   # PAC-18
    a = account(client)
    fund(client, a["id"], 400)
    eid = client.get(f"{P}/accounts/{a['id']}/entries").json()[0]["id"]
    r = client.delete(f"{P}/accounts/{a['id']}/entries/{eid}")
    assert r.status_code == 200 and f(r.json()["balance_now"]) == 10000
    assert client.delete(f"{P}/accounts/{a['id']}/entries/{eid}").status_code == 404


def test_renaming_and_changing_the_opening_balance(client):                                                    # PAC-19
    a, b = account(client), account(client, "Wallet", 5)
    r = client.patch(f"{P}/accounts/{a['id']}", json={"name": "Salary account", "opening_balance": 12000})
    assert r.status_code == 200 and r.json()["name"] == "Salary account" and f(r.json()["balance_now"]) == 12000
    assert client.patch(f"{P}/accounts/{a['id']}", json={"name": "wallet"}).status_code == 409
    assert client.patch(f"{P}/accounts/{a['id']}", json={"name": " "}).status_code == 422
    assert client.patch(f"{P}/accounts/99999", json={"name": "x"}).status_code == 404


def test_deleting_an_account_keeps_the_expenses_and_removes_its_funds(client):                               # PAC-20
    a = account(client)
    fund(client, a["id"], 400)
    e = spend(client, 300, a["id"])
    r = client.delete(f"{P}/accounts/{a['id']}")
    assert r.status_code == 200 and r.json()["expenses_kept"] == 1 and r.json()["entries_removed"] == 1
    assert view(client)["accounts"] == []
    kept = next(x for x in client.get(f"{P}/expenses").json() if x["id"] == e["id"])
    assert kept["account_id"] is None and f(kept["amount"]) == 300


def test_each_login_sees_and_changes_only_its_own_accounts(client):                                           # PAC-21
    a = account(client)
    fund(client, a["id"], 100)
    as_user(2)
    assert view(client)["accounts"] == []
    for call in (
        lambda: client.patch(f"{P}/accounts/{a['id']}", json={"name": "mine now"}),
        lambda: client.delete(f"{P}/accounts/{a['id']}"),
        lambda: client.post(f"{P}/accounts/{a['id']}/funds", json={"amount": 5}),
        lambda: client.get(f"{P}/accounts/{a['id']}/entries"),
        lambda: client.post(f"{P}/accounts/{a['id']}/reconcile", json={"bank_balance": 1}),
    ):
        assert call().status_code == 404
    as_user(1)
    assert f(one(client, a["id"])["balance_now"]) == 10100


@pytest.mark.parametrize("bad", ["2026", "2026-13", "March", "x-y"])
def test_a_bad_month_is_refused(client, bad):                                                                  # PAC-22
    assert client.get(f"{P}/accounts", params={"month": bad}).status_code == 422


def test_login_is_required(client):                                                                            # PAC-23
    app.dependency_overrides.pop(get_current_user, None)
    assert client.get(f"{P}/accounts").status_code in (401, 403)
    assert client.post(f"{P}/accounts/1/reconcile", json={"bank_balance": 1}).status_code in (401, 403)


def test_office_expenses_are_not_touched_by_accounts(client):                                                  # PAC-24
    a = account(client)
    r = client.post("/api/expenses", json={"amount": 10, "date": "2026-09-10", "person_id": 1})
    assert r.status_code in (201, 422)                                                                         # (office needs a person; not what is tested)
    assert f(one(client, a["id"])["balance_now"]) == 10000
