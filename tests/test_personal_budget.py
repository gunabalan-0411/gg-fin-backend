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


# ── carrying forward what is left over (or overspent) into the next month ─────────────────────────────────────
def began(category_id, month="2026-08-01", user_id=1):
    """Pretend the budget was first set in `month` (it is set today)."""
    with engine.begin() as c:
        c.execute(text("UPDATE tbl_personal_budget SET start_month = :m WHERE category_id = :c AND user_id = :u"), {"m": month, "c": category_id, "u": user_id})


def test_what_is_left_over_in_a_month_is_added_to_the_next_months_available_amount(client):      # PBG-15
    food = category(client, "Food")
    set_budget(client, food, 5000)
    began(food, "2026-08-01")
    spend(client, 4000, food, date(2026, 8, 12))                                                  # 1,000 left in August
    spend(client, 6000, food, date(2026, 9, 3))                                                   # 5,000 + 1,000 - 6,000 = 0 left in September
    spend(client, 1500, food, date(2026, 10, 9))
    aug, sep, octo, nov = (row(view(client, m), "Food") for m in ("2026-08", "2026-09", "2026-10", "2026-11"))
    assert (float(aug["carried_in"]), float(aug["available"]), float(aug["left"])) == (0, 5000, 1000)     # the first month has nothing carried in
    assert (float(sep["carried_in"]), float(sep["available"]), float(sep["left"])) == (1000, 6000, 0)
    assert (float(octo["carried_in"]), float(octo["available"]), float(octo["left"])) == (0, 5000, 3500)
    assert (float(nov["carried_in"]), float(nov["available"]), float(nov["left"])) == (3500, 8500, 8500)  # nothing spent yet in November
    assert float(nov["budget"]) == 5000                                                           # the amount itself stays the same


def test_overspending_is_carried_forward_too_and_reduces_the_next_month(client):                # PBG-16
    food = category(client, "Food")
    set_budget(client, food, 5000)
    began(food, "2026-10-01")
    spend(client, 9000, food, date(2026, 10, 5))
    octo, nov = row(view(client, "2026-10"), "Food"), row(view(client, "2026-11"), "Food")
    assert octo["over"] is True and float(octo["left"]) == -4000
    assert float(nov["carried_in"]) == -4000 and float(nov["available"]) == 1000
    spend(client, 1500, food, date(2026, 11, 2))
    nov = row(view(client, "2026-11"), "Food")
    assert nov["over"] is True and float(nov["left"]) == -500                                      # over against what is really available


def test_a_month_before_the_budget_began_carries_nothing(client):                                # PBG-17
    food = category(client, "Food")
    set_budget(client, food, 5000)
    began(food, "2026-09-01")
    spend(client, 100, food, date(2026, 7, 5))
    jul = row(view(client, "2026-07"), "Food")
    assert float(jul["carried_in"]) == 0 and float(jul["available"]) == 5000
    assert float(row(view(client, "2026-09"), "Food")["carried_in"]) == 0                          # the month it began


def test_sub_category_spending_counts_in_the_carry_and_other_categories_do_not(client):          # PBG-18
    food, fun = category(client, "Food"), category(client, "Fun")
    fish = category(client, "Fish", food)
    set_budget(client, food, 1000)
    began(food, "2026-09-01")
    spend(client, 300, fish, date(2026, 9, 4))
    spend(client, 200, food, date(2026, 9, 5))
    spend(client, 900, fun, date(2026, 9, 6))
    assert float(row(view(client, "2026-10"), "Food")["carried_in"]) == 500


def test_taking_a_budget_off_and_setting_it_again_starts_the_carry_afresh(client):               # PBG-19
    food = category(client, "Food")
    set_budget(client, food, 5000)
    began(food, "2026-08-01")
    assert float(row(view(client, "2026-11"), "Food")["carried_in"]) == 15000                      # three untouched months
    set_budget(client, food, 0)
    set_budget(client, food, 5000)
    assert float(row(view(client, "2026-11"), "Food")["carried_in"]) == 0                          # begun again this month
    set_budget(client, food, 6000)                                                                 # changing the amount does not reset it
    with engine.begin() as c:
        assert c.execute(text("SELECT start_month FROM tbl_personal_budget WHERE category_id = :c"), {"c": food}).scalar() == date.today().replace(day=1)


def test_the_totals_include_the_carry_and_the_dashboard_uses_what_is_available(client):          # PBG-20
    food, travel = category(client, "Food"), category(client, "Travel")
    set_budget(client, food, 5000)
    set_budget(client, travel, 1000)
    began(food, "2026-10-01")
    began(travel, "2026-10-01")
    spend(client, 3000, food, date(2026, 10, 7))                                                   # +2,000 into November
    spend(client, 1400, travel, date(2026, 10, 8))                                                 # -400 into November
    spend(client, 600, food, date(2026, 11, 1))
    v = view(client, "2026-11")
    assert float(v["total_budget"]) == 6000 and float(v["total_carried_in"]) == 1600 and float(v["total_available"]) == 7600
    assert float(v["spent_on_budgeted"]) == 600 and float(v["left"]) == 7000
    b = client.get(f"{P}/dashboard").json()["budget"]
    assert float(b["total_available"]) == float(view(client)["total_available"])


def test_the_carry_is_each_persons_own(client):                                                  # PBG-21
    food = category(client, "Food")
    set_budget(client, food, 5000)
    began(food, "2026-09-01")
    as_user(2)
    assert row(view(client, "2026-11"), "Food")["carried_in"] is None                              # nothing budgeted by this person
    set_budget(client, food, 100)
    began(food, "2026-10-01", user_id=2)
    assert float(row(view(client, "2026-11"), "Food")["carried_in"]) == 100
    as_user(1)
    assert float(row(view(client, "2026-11"), "Food")["carried_in"]) == 10000
