"""
The Personal dashboard's holdings: total in savings and what is still to be collected from friends. PH-01 ... PH-07.
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


def saving(client, amount, bank="HDFC", name="Salary"):
    assert client.post(f"{P}/savings", json={"amount": amount, "bank": bank, "account_name": name, "balance_date": d(0)}).status_code == 201


def lend(client, to, amount, follow=None):
    r = client.post(f"{P}/lends", json={"to_whom": to, "amount": amount, "follow_up_date": follow})
    assert r.status_code == 201
    return r.json()["id"]


def holdings(client):
    return client.get(f"{P}/dashboard").json()["holdings"]


def test_nothing_entered_gives_zeros_not_an_error(client):                                       # PH-01
    h = holdings(client)
    assert float(h["savings"]["total"]) == 0 and h["savings"]["accounts"] == 0
    assert float(h["lends"]["to_collect"]) == 0 and h["lends"]["people"] == 0 and h["lends"]["overdue"] == 0
    assert h["lends"]["next_follow_up"] is None and h["lends"]["next_follow_up_to"] is None


def test_total_in_savings_adds_every_account(client):                                            # PH-02
    saving(client, 25000)
    saving(client, 10000.5, "SBI", "Joint")
    h = holdings(client)["savings"]
    assert float(h["total"]) == 35000.5 and h["accounts"] == 2


def test_to_collect_counts_open_lends_only_a_repaid_one_drops_out(client):                      # PH-03
    a = lend(client, "Karthik", 5000, d(10))
    lend(client, "Suresh", 2000)
    assert float(holdings(client)["lends"]["to_collect"]) == 7000
    client.patch(f"{P}/lends/{a}", json={"is_settled": True})
    h = holdings(client)["lends"]
    assert float(h["to_collect"]) == 2000 and h["people"] == 1


def test_overdue_follow_ups_are_counted_with_their_amount_today_is_not_overdue(client):          # PH-04
    lend(client, "Late1", 1000, d(-5))
    lend(client, "Late2", 500, d(-1))
    lend(client, "Today", 300, d(0))
    lend(client, "Later", 200, d(9))
    lend(client, "NoDate", 100)
    h = holdings(client)["lends"]
    assert h["overdue"] == 2 and float(h["overdue_amount"]) == 1500
    assert h["people"] == 5 and float(h["to_collect"]) == 2100


def test_the_next_follow_up_is_the_nearest_one_from_today_on_and_names_the_person(client):       # PH-05
    lend(client, "Past", 100, d(-3))
    lend(client, "Far", 100, d(30))
    lend(client, "Near", 100, d(4))
    done = lend(client, "Done", 100, d(1))
    client.patch(f"{P}/lends/{done}", json={"is_settled": True})                                 # a repaid lend is never "next"
    h = holdings(client)["lends"]
    assert h["next_follow_up"] == d(4) and h["next_follow_up_to"] == "Near"


def test_each_login_sees_only_its_own_numbers(client):                                           # PH-06
    saving(client, 1000)
    lend(client, "Mine", 400, d(2))
    as_user(2)
    h = holdings(client)
    assert float(h["savings"]["total"]) == 0 and float(h["lends"]["to_collect"]) == 0
    saving(client, 7)
    as_user(1)
    assert float(holdings(client)["savings"]["total"]) == 1000


def test_the_spending_part_of_the_dashboard_is_unchanged(client):                                # PH-07
    d_ = client.get(f"{P}/dashboard").json()
    assert {"month", "this_month", "last_month", "categories", "people", "monthly", "holdings"} <= set(d_)
    assert len(d_["monthly"]) == 12
