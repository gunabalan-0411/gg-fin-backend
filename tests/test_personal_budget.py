"""
Personal Finance > Monthly budget: one amount per TOP-LEVEL expense category, the same every month, against what the person spent
(sub-categories count towards their parent). Private to the login. PBG-01 ... PBG-14.
"""
from datetime import date

import pytest
from sqlalchemy import text

from app.api.deps import get_current_user
from app.core.database import engine
from app.main import app
from app.models.user import User

P = "/api/personal"
GROUPS = "/api/expense-groups"


def as_user(uid: int):
    app.dependency_overrides[get_current_user] = lambda: User(id=uid, username=f"user{uid}", hashed_password="x")


@pytest.fixture(autouse=True)
def _back_to_user_one(client):
    as_user(1)
    yield
    as_user(1)


def category(client, name, parent=None):
    r = client.post(f"{GROUPS}/categories", json={"name": name, "parent_id": parent})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def spend(client, amount, cat, day=None):
    r = client.post(f"{P}/expenses", json={"amount": amount, "date": (day or date.today()).isoformat(), "category_id": cat})
    assert r.status_code == 201, r.text


def set_budget(client, cat, amount):
    return client.put(f"{P}/budgets/{cat}", json={"amount": amount})


def view(client, month=None):
    r = client.get(f"{P}/budgets", params={"month": month} if month else None)
    assert r.status_code == 200, r.text
    return r.json()


def row(v, name):
    return next(c for c in v["categories"] if c["name"] == name)


def this_month():
    t = date.today()
    return f"{t.year:04d}-{t.month:02d}"


def test_with_nothing_set_every_main_category_is_listed_without_a_budget(client):                # PBG-01
    category(client, "Food")
    v = view(client)
    assert v["month"] == this_month()
    assert "Food" in [c["name"] for c in v["categories"]]
    assert all(c["budget"] is None and c["left"] is None and not c["over"] for c in v["categories"])
    assert float(v["total_budget"]) == 0 and v["budgeted_count"] == 0 and v["over_count"] == 0


def test_sub_categories_are_not_listed_as_rows_but_their_spending_counts_to_the_parent(client):  # PBG-02
    food = category(client, "Food")
    fish = category(client, "Fish", food)
    spend(client, 300, food)
    spend(client, 250, fish)
    v = view(client)
    assert "Fish" not in [c["name"] for c in v["categories"]]
    f = row(v, "Food")
    assert float(f["spent"]) == 550
    assert [(s["name"], float(s["total"])) for s in f["subs"]] == [("Fish", 250.0)]


def test_a_budget_shows_what_is_left_and_when_it_is_overspent(client):                          # PBG-03
    food, travel = category(client, "Food"), category(client, "Travel")
    assert set_budget(client, food, 5000).status_code == 200
    assert set_budget(client, travel, 1000).status_code == 200
    spend(client, 1200, food)
    spend(client, 1500, travel)
    v = view(client)
    f, t = row(v, "Food"), row(v, "Travel")
    assert float(f["budget"]) == 5000 and float(f["left"]) == 3800 and f["over"] is False
    assert float(t["left"]) == -500 and t["over"] is True
    assert v["over_count"] == 1 and v["budgeted_count"] == 2
    assert float(v["total_budget"]) == 6000
    assert float(v["spent_on_budgeted"]) == 2700


def test_spending_on_a_category_without_a_budget_is_in_the_total_spent_but_not_in_spent_on_budgeted(client):   # PBG-04
    food, fun = category(client, "Food"), category(client, "Fun")
    set_budget(client, food, 5000)
    spend(client, 400, food)
    spend(client, 900, fun)
    v = view(client)
    assert float(v["spent_on_budgeted"]) == 400
    assert float(v["total_spent"]) == 1300


def test_setting_again_replaces_the_budget_it_does_not_add_a_second_one(client):                # PBG-05
    food = category(client, "Food")
    set_budget(client, food, 5000)
    r = set_budget(client, food, 6500)
    assert r.status_code == 200 and float(r.json()["amount"]) == 6500
    assert float(row(view(client), "Food")["budget"]) == 6500
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM tbl_personal_budget WHERE category_id = :i"), {"i": food}).scalar() == 1


@pytest.mark.parametrize("empty", [0, None])
def test_an_empty_or_zero_amount_removes_the_budget(client, empty):                              # PBG-06
    food = category(client, "Food")
    set_budget(client, food, 5000)
    r = set_budget(client, food, empty)
    assert r.status_code == 200 and r.json()["amount"] is None
    assert row(view(client), "Food")["budget"] is None


def test_budgets_are_for_main_categories_known_ones_and_never_negative(client):                  # PBG-07
    food = category(client, "Food")
    fish = category(client, "Fish", food)
    assert set_budget(client, fish, 100).status_code == 422
    assert set_budget(client, 999999, 100).status_code == 404
    assert set_budget(client, food, -5).status_code == 422
    assert client.put(f"{P}/budgets/{food}", json={"amount": "abc"}).status_code == 422
    assert set_budget(client, food, 1e20).status_code == 422


def test_the_same_budget_applies_to_every_month_and_only_that_months_spending_counts(client):    # PBG-08
    food = category(client, "Food")
    set_budget(client, food, 5000)
    spend(client, 700, food, date(2026, 3, 10))
    spend(client, 100, food, date(2026, 4, 2))
    mar, apr = row(view(client, "2026-03"), "Food"), row(view(client, "2026-04"), "Food")
    assert (float(mar["budget"]), float(mar["spent"]), float(mar["left"])) == (5000, 700, 4300)
    assert (float(apr["budget"]), float(apr["spent"])) == (5000, 100)
    assert float(row(view(client, "2031-01"), "Food")["budget"]) == 5000          # a month far ahead still has it
    assert view(client, "2026-03")["month"] == "2026-03"


@pytest.mark.parametrize("bad", ["2026", "2026-13", "March", "2026-3-1", "x-y"])
def test_a_bad_month_is_refused(client, bad):                                                      # PBG-09
    assert client.get(f"{P}/budgets", params={"month": bad}).status_code == 422


def test_each_login_has_its_own_budget_and_spending(client):                                       # PBG-10
    food = category(client, "Food")
    set_budget(client, food, 5000)
    spend(client, 800, food)
    as_user(2)
    v = view(client)
    f = row(v, "Food")
    assert f["budget"] is None and float(f["spent"]) == 0
    set_budget(client, food, 2000)
    spend(client, 50, food)
    as_user(1)
    f = row(view(client), "Food")
    assert float(f["budget"]) == 5000 and float(f["spent"]) == 800
    as_user(2)
    assert set_budget(client, food, 0).status_code == 200
    as_user(1)
    assert float(row(view(client), "Food")["budget"]) == 5000                          # user 2 clearing theirs left mine alone


def test_an_old_ungrouped_expense_is_shown_apart_and_cannot_hold_a_budget(client):                # PBG-11
    view(client)                                                                                # seeds the default categories
    with engine.begin() as c:
        c.execute(text('INSERT INTO tbl_personal_expense (user_id, amount, "date", category_id) VALUES (1, 75, :d, NULL)'), {"d": date.today()})
    v = view(client)
    assert v["ungrouped"]["name"] == "Ungrouped" and float(v["ungrouped"]["spent"]) == 75
    assert float(v["total_spent"]) == 75


def test_deleting_a_main_category_deletes_its_budget(client):                                      # PBG-12
    food, fun = category(client, "Food"), category(client, "Fun")
    set_budget(client, food, 5000)
    set_budget(client, fun, 700)
    assert client.delete(f"{GROUPS}/categories/{food}").status_code == 200
    with engine.begin() as c:
        left = c.execute(text("SELECT category_id FROM tbl_personal_budget")).scalars().all()
    assert left == [fun]


def test_the_personal_dashboard_carries_this_months_budget_numbers(client):                        # PBG-13
    food, travel = category(client, "Food"), category(client, "Travel")
    d0 = client.get(f"{P}/dashboard").json()["budget"]
    assert float(d0["total_budget"]) == 0 and d0["budgeted_count"] == 0
    set_budget(client, food, 5000)
    set_budget(client, travel, 1000)
    spend(client, 1200, food)
    spend(client, 1500, travel)
    spend(client, 99, category(client, "Fun"))
    b = client.get(f"{P}/dashboard").json()["budget"]
    assert float(b["total_budget"]) == 6000 and float(b["spent_on_budgeted"]) == 2700
    assert b["over_count"] == 1 and b["budgeted_count"] == 2


def test_login_is_required(client):                                                                # PBG-14
    app.dependency_overrides.pop(get_current_user, None)
    assert client.get(f"{P}/budgets").status_code in (401, 403)
    assert client.put(f"{P}/budgets/1", json={"amount": 5}).status_code in (401, 403)
