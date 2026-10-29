"""
Personal Finance: friendly borrows (money the person OWES) and investments. Every row belongs to one login. PB-01 ... PB-20.
"""
from datetime import date, timedelta

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


def d(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def borrow(client, **over):
    body = {"from_whom": "Murugan", "amount": 4000, "pay_back_date": "2026-11-10", **over}
    return client.post(f"{P}/borrows", json=body)


def invest(client, **over):
    body = {"name": "Nifty 50 index fund", "inv_type": "mutual_fund", "amount": 50000, "current_value": 56000, "invested_date": "2026-01-15", **over}
    return client.post(f"{P}/investments", json=body)


def owe(client):
    return client.get(f"{P}/dashboard").json()["holdings"]["borrows"]


# ── friendly borrows ──────────────────────────────────────────────────────────────────────────────────────
def test_a_borrow_keeps_from_whom_amount_and_the_pay_back_date(client):                              # PB-01
    r = borrow(client)
    assert r.status_code == 201, r.text
    got = r.json()
    assert (got["from_whom"], got["amount"], got["pay_back_date"], got["is_settled"]) == ("Murugan", "4000.00", "2026-11-10", False)


def test_a_borrow_needs_a_name_and_a_positive_amount_the_date_is_optional(client):                  # PB-02
    assert borrow(client, from_whom=" ").status_code == 422
    assert borrow(client, amount=0).status_code == 422
    assert borrow(client, amount=-5).status_code == 422
    r = borrow(client, pay_back_date=None)
    assert r.status_code == 201 and r.json()["pay_back_date"] is None


def test_a_borrow_can_be_marked_paid_edited_and_deleted(client):                                      # PB-03
    bid = borrow(client).json()["id"]
    r = client.patch(f"{P}/borrows/{bid}", json={"is_settled": True})
    assert r.status_code == 200 and r.json()["is_settled"] is True and r.json()["from_whom"] == "Murugan"
    r = client.patch(f"{P}/borrows/{bid}", json={"amount": 4500, "notes": "  wedding  "})
    assert r.json()["amount"] == "4500.00" and r.json()["notes"] == "wedding"
    assert client.patch(f"{P}/borrows/{bid}", json={"amount": 0}).status_code == 422
    assert client.delete(f"{P}/borrows/{bid}").status_code == 204
    assert client.get(f"{P}/borrows").json() == []


def test_borrows_list_unpaid_first_by_date_then_the_paid_ones(client):                                # PB-04
    paid = borrow(client, from_whom="Paid", pay_back_date=d(-9)).json()["id"]
    borrow(client, from_whom="NoDate", pay_back_date=None)
    borrow(client, from_whom="Later", pay_back_date=d(20))
    borrow(client, from_whom="Soon", pay_back_date=d(2))
    client.patch(f"{P}/borrows/{paid}", json={"is_settled": True})
    assert [r["from_whom"] for r in client.get(f"{P}/borrows").json()] == ["Soon", "Later", "NoDate", "Paid"]


def test_somebody_elses_borrow_is_not_found_for_read_change_and_delete(client):                       # PB-05
    bid = borrow(client).json()["id"]
    as_user(2)
    assert client.get(f"{P}/borrows").json() == []
    assert client.patch(f"{P}/borrows/{bid}", json={"amount": 1}).status_code == 404
    assert client.delete(f"{P}/borrows/{bid}").status_code == 404
    as_user(1)
    assert len(client.get(f"{P}/borrows").json()) == 1


# ── what the person owes (Personal dashboard) ─────────────────────────────────────────────────────────────
def test_nothing_owed_gives_zeros_not_an_error(client):                                               # PB-06
    h = owe(client)
    assert float(h["to_pay"]) == 0 and h["people"] == 0 and h["overdue"] == 0
    assert h["next_pay_back"] is None and h["next_pay_back_to"] is None


def test_you_owe_counts_unpaid_borrows_only_a_paid_one_drops_out(client):                             # PB-07
    a = borrow(client, from_whom="A", amount=1000).json()["id"]
    borrow(client, from_whom="B", amount=250.5, pay_back_date=None)
    assert float(owe(client)["to_pay"]) == 1250.5
    client.patch(f"{P}/borrows/{a}", json={"is_settled": True})
    h = owe(client)
    assert float(h["to_pay"]) == 250.5 and h["people"] == 1


def test_overdue_pay_backs_are_counted_with_their_amount_today_is_not_overdue(client):                # PB-08
    borrow(client, from_whom="Late1", amount=1000, pay_back_date=d(-5))
    borrow(client, from_whom="Late2", amount=500, pay_back_date=d(-1))
    borrow(client, from_whom="Today", amount=300, pay_back_date=d(0))
    borrow(client, from_whom="Later", amount=200, pay_back_date=d(9))
    borrow(client, from_whom="NoDate", amount=100, pay_back_date=None)
    h = owe(client)
    assert h["overdue"] == 2 and float(h["overdue_amount"]) == 1500
    assert h["people"] == 5 and float(h["to_pay"]) == 2100


def test_the_next_pay_back_is_the_nearest_from_today_on_and_names_the_person(client):                # PB-09
    borrow(client, from_whom="Past", pay_back_date=d(-3))
    borrow(client, from_whom="Far", pay_back_date=d(30))
    borrow(client, from_whom="Near", pay_back_date=d(4))
    done = borrow(client, from_whom="Done", pay_back_date=d(1)).json()["id"]
    client.patch(f"{P}/borrows/{done}", json={"is_settled": True})                                    # a paid one is never "next"
    h = owe(client)
    assert h["next_pay_back"] == d(4) and h["next_pay_back_to"] == "Near"


def test_each_login_owes_only_its_own_borrows_and_lends_are_not_mixed_in(client):                     # PB-10
    borrow(client, amount=700)
    client.post(f"{P}/lends", json={"to_whom": "Friend", "amount": 9000})
    assert float(owe(client)["to_pay"]) == 700                                                        # the 9,000 lend is money coming IN
    assert float(client.get(f"{P}/dashboard").json()["holdings"]["lends"]["to_collect"]) == 9000
    as_user(2)
    assert float(owe(client)["to_pay"]) == 0
    as_user(1)
    assert float(owe(client)["to_pay"]) == 700


# ── investments ───────────────────────────────────────────────────────────────────────────────────────────
def test_an_investment_keeps_name_type_amount_current_value_and_date(client):                         # PB-11
    r = invest(client)
    assert r.status_code == 201, r.text
    got = r.json()
    assert (got["name"], got["inv_type"], got["amount"], got["current_value"], got["invested_date"]) == (
        "Nifty 50 index fund", "mutual_fund", "50000.00", "56000.00", "2026-01-15")


def test_current_value_and_date_are_optional_name_and_amount_are_not(client):                         # PB-12
    r = invest(client, current_value=None, invested_date=None)
    assert r.status_code == 201 and r.json()["current_value"] is None and r.json()["invested_date"] is None
    assert invest(client, name="  ").status_code == 422
    assert invest(client, amount=0).status_code == 422
    assert invest(client, amount=-1).status_code == 422
    assert invest(client, current_value=-1).status_code == 422
    assert invest(client, current_value=0).status_code == 201                                         # worth nothing now is a real answer


def test_only_the_six_types_are_accepted(client):                                                     # PB-13
    for t in ("stocks", "mutual_fund", "fixed_deposit", "gold", "chit", "other"):
        assert invest(client, inv_type=t).status_code == 201, t
    assert invest(client, inv_type="crypto").status_code == 422
    assert invest(client, inv_type="").status_code == 422


def test_an_investment_can_be_updated_with_a_new_value_and_deleted(client):                           # PB-14
    iid = invest(client).json()["id"]
    r = client.patch(f"{P}/investments/{iid}", json={"current_value": 61000})
    assert r.status_code == 200 and r.json()["current_value"] == "61000.00" and r.json()["amount"] == "50000.00"
    assert client.patch(f"{P}/investments/{iid}", json={"inv_type": "bitcoin"}).status_code == 422
    assert client.patch(f"{P}/investments/{iid}", json={"name": ""}).status_code == 422
    assert client.delete(f"{P}/investments/{iid}").status_code == 204
    assert client.get(f"{P}/investments").json() == []


def test_investments_list_newest_first_no_date_last(client):                                          # PB-15
    invest(client, name="Old", invested_date="2025-01-01")
    invest(client, name="NoDate", invested_date=None)
    invest(client, name="New", invested_date="2026-09-01")
    assert [r["name"] for r in client.get(f"{P}/investments").json()] == ["New", "Old", "NoDate"]


def test_somebody_elses_investment_is_not_found_and_not_listed(client):                               # PB-16
    iid = invest(client).json()["id"]
    as_user(2)
    assert client.get(f"{P}/investments").json() == []
    assert client.patch(f"{P}/investments/{iid}", json={"amount": 1}).status_code == 404
    assert client.delete(f"{P}/investments/{iid}").status_code == 404
    as_user(1)
    assert len(client.get(f"{P}/investments").json()) == 1


def test_the_new_routes_need_a_login(client):                                                         # PB-17
    app.dependency_overrides.pop(get_current_user, None)
    for path in ("borrows", "investments"):
        assert client.get(f"{P}/{path}").status_code == 401
        assert client.post(f"{P}/{path}", json={}).status_code == 401


def test_the_existing_holdings_and_spending_parts_are_unchanged(client):                              # PB-18
    d_ = client.get(f"{P}/dashboard").json()
    assert {"savings", "lends", "borrows"} == set(d_["holdings"])
    assert {"month", "this_month", "last_month", "categories", "people", "monthly", "holdings"} <= set(d_)
