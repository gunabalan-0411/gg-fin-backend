"""
Personal Finance: the logged-in person's OWN expenses (tbl_personal_expense). PF-01 ... PF-14.
Same categories and 'for whom' list as the office expenses; never visible to another user; the dashboard's Personal view.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.api.deps import get_current_user
from app.main import app
from app.models.user import User

P = "/api/personal"
EXP = "/api/expenses"
GROUPS = "/api/expense-groups"


def as_user(uid: int):
    app.dependency_overrides[get_current_user] = lambda: User(id=uid, username=f"user{uid}", hashed_password="x")


@pytest.fixture(autouse=True)
def _back_to_user_one(client):
    as_user(1)
    yield
    as_user(1)


def cats(client):
    return client.get(f"{GROUPS}/categories").json()


def cat(client, name, parent=None):
    return next(c for c in cats(client) if c["name"] == name and c["parent_id"] == parent)


def persons(client):
    return {p["name"]: p["id"] for p in client.get(f"{GROUPS}/persons").json()}


def add(client, amount=100, d=None, notes=None, category=None, person=None):
    body = {"amount": amount, "date": (d or date.today()).isoformat(), "notes": notes, "category_id": category, "person_id": person}
    return client.post(f"{P}/expenses", json=body)


def month_start(d, back=0):
    idx = d.year * 12 + (d.month - 1) - back
    return date(idx // 12, idx % 12 + 1, 1)


# ── creating ──────────────────────────────────────────────────────────────────────────────────────────────
def test_a_personal_expense_needs_only_an_amount_and_a_date_the_category_defaults_to_miscellaneous(client):   # PF-01
    r = add(client, 250)
    assert r.status_code == 201, r.text
    e = r.json()
    assert (e["amount"], e["top_name"], e["sub_name"], e["person_id"], e["person_name"]) == ("250.00", "Miscellaneous", None, None, None)


def test_for_whom_is_optional_but_checked_when_given(client):                                       # PF-02
    ok = add(client, 80, person=persons(client)["Amma"])
    assert ok.status_code == 201 and ok.json()["person_name"] == "Amma"
    assert add(client, 80, person=99999).status_code == 404


def test_amount_and_category_are_validated(client):                                                 # PF-03
    assert client.post(f"{P}/expenses", json={"amount": 0, "date": "2026-10-17"}).status_code == 422
    assert client.post(f"{P}/expenses", json={"amount": -5, "date": "2026-10-17"}).status_code == 422
    assert add(client, 10, category=99999).status_code == 404
    groc = cat(client, "Groceries")
    fish = cat(client, "Fish & Chicken", groc["id"])
    e = add(client, 10, category=fish["id"], notes="  fish  ").json()
    assert (e["top_name"], e["sub_name"], e["notes"]) == ("Groceries", "Fish & Chicken", "fish")


# ── privacy ───────────────────────────────────────────────────────────────────────────────────────────────
def test_each_user_sees_only_their_own_personal_expenses(client):                                    # PF-04
    add(client, 100, notes="mine")
    as_user(2)
    assert client.get(f"{P}/expenses").json() == []
    add(client, 999, notes="theirs")
    assert [e["notes"] for e in client.get(f"{P}/expenses").json()] == ["theirs"]
    as_user(1)
    assert [e["notes"] for e in client.get(f"{P}/expenses").json()] == ["mine"]


def test_nobody_can_change_or_delete_somebody_elses_expense(client, conn):                          # PF-05
    e = add(client, 100, notes="mine").json()
    as_user(2)
    assert client.patch(f"{P}/expenses/{e['id']}", json={"amount": 1}).status_code == 404
    assert client.delete(f"{P}/expenses/{e['id']}").status_code == 404
    as_user(1)
    got = client.get(f"{P}/expenses").json()
    assert len(got) == 1 and got[0]["amount"] == "100.00"                                           # untouched
    assert client.delete(f"{P}/expenses/{e['id']}").status_code == 204                              # the owner can
    assert client.get(f"{P}/expenses").json() == []
    assert client.delete(f"{P}/expenses/{e['id']}").status_code == 404


def test_the_owner_can_edit(client):                                                                # PF-13
    e = add(client, 100).json()
    groc = cat(client, "Groceries")
    r = client.patch(f"{P}/expenses/{e['id']}", json={"amount": 120, "category_id": groc["id"], "person_id": persons(client)["Appa"], "notes": "milk"})
    assert r.status_code == 200
    assert (r.json()["amount"], r.json()["top_name"], r.json()["person_name"], r.json()["notes"]) == ("120.00", "Groceries", "Appa", "milk")
    assert client.patch(f"{P}/expenses/{e['id']}", json={"amount": 0}).status_code == 422
    assert client.patch(f"{P}/expenses/{e['id']}", json={"category_id": 99999}).status_code == 404
    assert client.patch(f"{P}/expenses/99999", json={"amount": 5}).status_code == 404


def test_personal_and_office_expenses_never_mix(client):                                            # PF-08
    add(client, 100, notes="personal")
    office = client.post(EXP, json={"amount": 500, "date": date.today().isoformat(), "person_id": persons(client)["Appa"]})
    assert office.status_code == 201
    assert [e["notes"] for e in client.get(f"{P}/expenses").json()] == ["personal"]
    assert [e["amount"] for e in client.get(EXP).json()] == ["500.00"]
    assert float(client.get(f"{EXP}/summary").json()["total"]) == 500.0                             # office totals are not touched
    assert float(client.get(f"{P}/expenses/summary").json()["total"]) == 100.0
    second = client.post(EXP, json={"amount": 9, "date": date.today().isoformat(), "person_id": persons(client)["Appa"]}).json()
    assert second["id"] != 1                                                                          # (the two tables count their ids separately)
    assert client.delete(f"{P}/expenses/{second['id']}").status_code == 404                          # an office expense is not a personal one
    assert len(client.get(EXP).json()) == 2


# ── filters, totals, export ───────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def spread(client):
    groc = cat(client, "Groceries")
    fish = cat(client, "Fish & Chicken", groc["id"])
    med = cat(client, "Medical")
    p = persons(client)
    add(client, 100, date(2026, 10, 1), "veg", groc["id"], p["Appa"])
    add(client, 300, date(2026, 10, 2), "fish", fish["id"], p["Amma"])
    add(client, 700, date(2026, 11, 3), "tablets", med["id"])
    add(client, 50, date(2026, 11, 4), None, None, p["Appa"])
    return {"groc": groc, "fish": fish, "med": med}


def test_filters(client, spread):                                                                   # PF-04
    amounts = lambda **q: sorted(float(e["amount"]) for e in client.get(f"{P}/expenses", params=q).json())
    assert amounts() == [50, 100, 300, 700]
    assert amounts(date_from="2026-11-01") == [50, 700]
    assert amounts(date_to="2026-10-31") == [100, 300]
    assert amounts(category_id=spread["groc"]["id"]) == [100, 300]                                  # includes the sub-category
    assert amounts(category_id=spread["fish"]["id"]) == [300]
    assert amounts(person_id=persons(client)["Appa"]) == [50, 100]
    assert amounts(search="FISH") == [300]


def test_summary_by_category_sub_category_and_person_with_the_grand_total(client, spread):          # PF-06
    s = client.get(f"{P}/expenses/summary").json()
    assert (s["count"], float(s["total"])) == (4, 1150.0)
    top = {c["name"]: c for c in s["categories"]}
    assert float(top["Groceries"]["total"]) == 400 and [x["name"] for x in top["Groceries"]["subs"]] == ["Fish & Chicken"]
    assert float(top["Medical"]["total"]) == 700 and float(top["Miscellaneous"]["total"]) == 50
    assert {p["name"]: float(p["total"]) for p in s["people"]} == {"Appa": 150.0, "Amma": 300.0, "Not specified": 700.0}
    as_user(2)
    assert client.get(f"{P}/expenses/summary").json()["count"] == 0                                 # somebody else sees nothing


def test_export_is_only_the_owners_with_a_total_at_the_end(client, spread):                          # PF-07
    import csv
    import io

    def parse(r):
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        return list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))

    rows = parse(client.get(f"{P}/expenses/export"))
    assert rows[0] == ["Date", "Amount", "Category", "Sub-category", "For whom", "Notes"]
    assert rows[-1] == ["Total", "1150.00", "", "", "", "4 expenses"] and len(rows) == 6
    assert "personal_expenses.csv" in client.get(f"{P}/expenses/export").headers["content-disposition"]
    summ = parse(client.get(f"{P}/expenses/export", params={"kind": "summary"}))
    assert summ[-1] == ["Total", "", "4", "1150.00"]
    assert client.get(f"{P}/expenses/export", params={"kind": "nope"}).status_code == 422
    as_user(2)
    other = parse(client.get(f"{P}/expenses/export"))
    assert other[-1] == ["Total", "0.00", "", "", "", "0 expenses"]


# ── shared categories and people ──────────────────────────────────────────────────────────────────────────
def test_a_category_created_for_office_expenses_is_usable_here_and_the_other_way_round(client):     # PF-09
    new = client.post(f"{GROUPS}/categories", json={"name": "Education"}).json()
    assert add(client, 10, category=new["id"]).json()["top_name"] == "Education"
    mine = client.post(f"{P}/expenses", json={"amount": 5, "date": date.today().isoformat(), "category_id": new["id"]})
    assert mine.status_code == 201
    office = client.post(EXP, json={"amount": 7, "date": date.today().isoformat(), "category_id": new["id"], "person_id": persons(client)["Appa"]})
    assert office.json()["top_name"] == "Education"


def test_deleting_a_category_moves_personal_expenses_of_every_user_to_miscellaneous_and_counts_them(client):   # PF-09
    groc = cat(client, "Groceries")
    add(client, 10, category=groc["id"])
    as_user(2)
    add(client, 20, category=groc["id"])
    as_user(1)
    assert cat(client, "Groceries")["used"] == 2                                                    # both users' expenses are counted
    r = client.delete(f"{GROUPS}/categories/{groc['id']}")
    assert r.json()["moved"] == 2
    assert [e["top_name"] for e in client.get(f"{P}/expenses").json()] == ["Miscellaneous"]
    as_user(2)
    assert [e["top_name"] for e in client.get(f"{P}/expenses").json()] == ["Miscellaneous"]


def test_deleting_a_person_makes_their_personal_expenses_not_specified(client):                      # PF-10
    amma = persons(client)["Amma"]
    add(client, 10, person=amma)
    r = client.delete(f"{GROUPS}/persons/{amma}")
    assert r.json()["unspecified"] == 1
    assert client.get(f"{P}/expenses").json()[0]["person_id"] is None


# ── the dashboard's Personal view ─────────────────────────────────────────────────────────────────────────
def test_the_dashboard_has_this_month_last_month_by_category_and_twelve_months(client):              # PF-11
    today = date.today()
    first = month_start(today)
    last_month_day = first - timedelta(days=1)
    groc = cat(client, "Groceries")
    med = cat(client, "Medical")
    add(client, 400, first, "a", groc["id"])
    add(client, 100, today, "b", med["id"])
    add(client, 250, last_month_day, "c", groc["id"])
    add(client, 999, month_start(today, back=13), "too old")                                         # outside the 12 months
    d = client.get(f"{P}/dashboard").json()
    assert d["month"] == f"{first.year:04d}-{first.month:02d}"
    assert (float(d["this_month"]["total"]), d["this_month"]["count"]) == (500.0, 2)
    assert (float(d["last_month"]["total"]), d["last_month"]["count"]) == (250.0, 1)
    assert [(c["name"], float(c["total"])) for c in d["categories"]] == [("Groceries", 400.0), ("Medical", 100.0)]
    assert len(d["monthly"]) == 12 and d["monthly"][-1]["month"] == d["month"]
    assert [m["month"] for m in d["monthly"]] == sorted(m["month"] for m in d["monthly"])
    assert float(d["monthly"][-1]["total"]) == 500.0 and float(d["monthly"][-2]["total"]) == 250.0
    assert sum(float(m["total"]) for m in d["monthly"]) == 750.0                                      # the too-old one is not in the series


def test_the_dashboard_is_the_owners_alone_and_empty_months_are_zero(client):                        # PF-11
    add(client, 400)
    as_user(2)
    d = client.get(f"{P}/dashboard").json()
    assert float(d["this_month"]["total"]) == 0.0 and d["categories"] == []
    assert len(d["monthly"]) == 12 and all(float(m["total"]) == 0.0 for m in d["monthly"])


def test_the_dashboard_handles_january_and_the_year_boundary():                                      # PF-12
    from app.services.expense_group_service import _month_start
    assert _month_start(date(2026, 1, 15)) == date(2026, 1, 1)
    assert _month_start(date(2026, 1, 15), back=1) == date(2025, 12, 1)
    assert _month_start(date(2026, 1, 15), back=11) == date(2025, 2, 1)
    assert _month_start(date(2026, 12, 31), back=-1) == date(2027, 1, 1)


def test_login_is_required():                                                                       # PF-14
    from fastapi.testclient import TestClient
    app.dependency_overrides.pop(get_current_user, None)
    anon = TestClient(app)
    for path in (f"{P}/expenses", f"{P}/expenses/summary", f"{P}/expenses/export", f"{P}/dashboard"):
        assert anon.get(path).status_code == 401, path
    assert anon.post(f"{P}/expenses", json={"amount": 1, "date": "2026-10-17"}).status_code == 401
    assert anon.delete(f"{P}/expenses/1").status_code == 401
