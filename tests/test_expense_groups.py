"""
Expense categories (two levels), "for whom", several lines per request, grouped totals, CSV export and grouping the old expenses.
EG-01 ... EG-14.
"""
import csv
import io
from datetime import date

import pytest
from sqlalchemy import text

from app.services import expense_guess as guess

API = "/api/expense-groups"
EXP = "/api/expenses"


def cats(client):
    return client.get(f"{API}/categories").json()


def by_name(rows, name, parent=None):
    return next(r for r in rows if r["name"] == name and r["parent_id"] == parent)


def persons(client):
    return {p["name"]: p["id"] for p in client.get(f"{API}/persons").json()}


def add(client, amount=100, d="2026-10-17", notes=None, category_id=None, person="Appa"):
    body = {"amount": amount, "date": d, "notes": notes, "category_id": category_id, "person_id": persons(client)[person]}
    return client.post(EXP, json=body)


def insert_old(conn, amount, d, notes):
    """An old expense: no category, no person (what every existing row looks like after the migration)."""
    conn.execute(text('INSERT INTO tbl_expense (amount, "date", notes) VALUES (:a, :d, :n)'), {"a": amount, "d": d, "n": notes})


# ── defaults ──────────────────────────────────────────────────────────────────────────────────────────────
def test_the_default_tree_people_and_miscellaneous_exist(client):                                  # EG-01
    rows = cats(client)
    tops = {r["name"] for r in rows if r["parent_id"] is None}
    assert {"Groceries", "Eating Out", "Vehicle & Fuel", "Bills & Recharge", "Home & Appliances", "Medical",
            "Wedding & Functions", "Clothing", "Gold & Jewellery", "Miscellaneous"} == tops
    groc = by_name(rows, "Groceries")
    assert {r["name"] for r in rows if r["parent_id"] == groc["id"]} >= {"Fish & Chicken", "Mutton & Meat", "Vegetables & Fruit", "Milk & Eggs", "Provisions"}
    eating = by_name(rows, "Eating Out")
    assert "Liquor" in {r["name"] for r in rows if r["parent_id"] == eating["id"]}                  # liquor is a sub-category of Eating Out
    assert by_name(rows, "Miscellaneous")["is_system"] is True
    assert sorted(persons(client)) == ["Amma", "Appa", "Guru", "Kamali", "On-house", "Suji"]


def test_defaults_are_created_once_and_user_deletions_stay_deleted(client):                       # EG-01
    rows = cats(client)
    client.delete(f"{API}/categories/{by_name(rows, 'Clothing')['id']}")
    assert "Clothing" not in {r["name"] for r in cats(client)}
    assert len(cats(client)) == len(rows) - 1


# ── categories ────────────────────────────────────────────────────────────────────────────────────────────
def test_create_rename_and_two_levels_only(client):                                               # EG-02
    top = client.post(f"{API}/categories", json={"name": "  Education  "}).json()
    assert top["name"] == "Education" and top["parent_id"] is None
    sub = client.post(f"{API}/categories", json={"name": "Books", "parent_id": top["id"]})
    assert sub.status_code == 201
    deeper = client.post(f"{API}/categories", json={"name": "Maths", "parent_id": sub.json()["id"]})
    assert deeper.status_code == 422 and deeper.json()["detail"]["code"] == "too_deep"
    assert client.post(f"{API}/categories", json={"name": "x", "parent_id": 99999}).status_code == 404
    r = client.patch(f"{API}/categories/{sub.json()['id']}", json={"name": "School books"})
    assert r.status_code == 200 and r.json()["name"] == "School books"


def test_names_are_unique_per_level_ignoring_case_and_the_existing_id_is_returned(client):        # EG-02
    first = client.post(f"{API}/categories", json={"name": "Education"}).json()
    dup = client.post(f"{API}/categories", json={"name": "education"})
    assert dup.status_code == 409 and dup.json()["detail"]["id"] == first["id"]
    other = client.post(f"{API}/categories", json={"name": "Books", "parent_id": first["id"]})
    again = client.post(f"{API}/categories", json={"name": "Books", "parent_id": by_name(cats(client), "Medical")["id"]})
    assert other.status_code == 201 and again.status_code == 201                                  # the same name under another parent is fine
    assert client.post(f"{API}/categories", json={"name": "   "}).status_code == 422
    assert client.post(f"{API}/categories", json={"name": "x" * 61}).status_code == 422


def test_miscellaneous_is_protected_and_has_no_sub_categories(client):                            # EG-03
    misc = by_name(cats(client), "Miscellaneous")
    assert client.patch(f"{API}/categories/{misc['id']}", json={"name": "Other"}).status_code == 409
    assert client.delete(f"{API}/categories/{misc['id']}").status_code == 409
    assert client.post(f"{API}/categories", json={"name": "Odd", "parent_id": misc["id"]}).status_code == 422


def test_deleting_a_top_level_category_moves_its_expenses_to_miscellaneous(client):               # EG-04
    rows = cats(client)
    groc, fish = by_name(rows, "Groceries"), by_name(rows, "Fish & Chicken", by_name(rows, "Groceries")["id"])
    a = add(client, 100, category_id=groc["id"]).json()
    b = add(client, 200, category_id=fish["id"]).json()
    r = client.delete(f"{API}/categories/{groc['id']}")
    assert r.json() == {"deleted": groc["id"], "moved": 2}
    names = {r["name"] for r in cats(client)}
    assert "Groceries" not in names and "Fish & Chicken" not in names
    for e in (a, b):
        got = client.get(EXP, params={"category_id": by_name(cats(client), "Miscellaneous")["id"]}).json()
        assert e["id"] in {x["id"] for x in got}


def test_deleting_a_sub_category_moves_its_expenses_to_its_parent(client):                        # EG-04
    rows = cats(client)
    groc = by_name(rows, "Groceries")
    fish = by_name(rows, "Fish & Chicken", groc["id"])
    e = add(client, 100, category_id=fish["id"]).json()
    assert e["top_name"] == "Groceries" and e["sub_name"] == "Fish & Chicken"
    client.delete(f"{API}/categories/{fish['id']}")
    got = client.get(EXP).json()[0]
    assert got["id"] == e["id"] and got["top_name"] == "Groceries" and got["sub_name"] is None


# ── people ────────────────────────────────────────────────────────────────────────────────────────────────
def test_people_crud_and_deleting_one_keeps_the_expenses(client):                                 # EG-05
    new = client.post(f"{API}/persons", json={"name": "Ravi"}).json()
    assert client.post(f"{API}/persons", json={"name": "ravi"}).status_code == 409
    assert client.patch(f"{API}/persons/{new['id']}", json={"name": "Ravi K"}).json()["name"] == "Ravi K"
    e = client.post(EXP, json={"amount": 50, "date": "2026-10-17", "person_id": new["id"]}).json()
    assert e["person_name"] == "Ravi K"
    assert client.delete(f"{API}/persons/{new['id']}").json() == {"deleted": new["id"], "unspecified": 1}
    got = client.get(EXP).json()[0]
    assert got["id"] == e["id"] and got["person_id"] is None and got["person_name"] is None
    assert client.delete(f"{API}/persons/99999").status_code == 404


# ── creating expenses ─────────────────────────────────────────────────────────────────────────────────────
def test_for_whom_is_required_and_the_category_defaults_to_miscellaneous(client):                 # EG-06
    assert client.post(EXP, json={"amount": 10, "date": "2026-10-17"}).status_code == 422
    assert client.post(EXP, json={"amount": 10, "date": "2026-10-17", "person_id": 99999}).status_code == 404
    e = add(client, 10).json()
    assert e["top_name"] == "Miscellaneous" and e["sub_name"] is None and e["person_name"] == "Appa"
    assert client.post(EXP, json={"amount": 0, "date": "2026-10-17", "person_id": persons(client)["Appa"]}).status_code == 422
    assert client.post(EXP, json={"amount": 5, "date": "2026-10-17", "person_id": persons(client)["Appa"], "category_id": 99999}).status_code == 404


def test_several_lines_for_one_date_are_saved_together_or_not_at_all(client):                      # EG-07
    p = persons(client)
    rows = cats(client)
    groc, med = by_name(rows, "Groceries"), by_name(rows, "Medical")
    ok = client.post(f"{EXP}/bulk", json={"items": [
        {"amount": 300, "date": "2026-10-19", "category_id": groc["id"], "person_id": p["Appa"], "notes": " fish "},
        {"amount": 700, "date": "2026-10-19", "category_id": med["id"], "person_id": p["Amma"]},
        {"amount": 50, "date": "2026-10-19", "person_id": p["Suji"]},
    ]})
    assert ok.status_code == 201 and [e["amount"] for e in ok.json()] == ["300.00", "700.00", "50.00"]
    assert ok.json()[0]["notes"] == "fish" and ok.json()[2]["top_name"] == "Miscellaneous"
    bad = client.post(f"{EXP}/bulk", json={"items": [
        {"amount": 1, "date": "2026-10-20", "person_id": p["Appa"]},
        {"amount": 2, "date": "2026-10-20", "person_id": 99999},                                  # the second line is invalid
    ]})
    assert bad.status_code == 404
    assert len(client.get(EXP).json()) == 3                                                       # nothing from the failed request was saved
    assert client.post(f"{EXP}/bulk", json={"items": []}).status_code == 422


def test_update_changes_category_and_person_and_checks_them(client):                              # EG-06
    e = add(client, 10).json()
    groc = by_name(cats(client), "Groceries")
    r = client.patch(f"{EXP}/{e['id']}", json={"category_id": groc["id"], "person_id": persons(client)["Guru"], "amount": 55})
    assert r.status_code == 200 and r.json()["top_name"] == "Groceries" and r.json()["person_name"] == "Guru" and r.json()["amount"] == "55.00"
    assert client.patch(f"{EXP}/{e['id']}", json={"category_id": 99999}).status_code == 404
    assert client.patch(f"{EXP}/{e['id']}", json={"amount": 0}).status_code == 422
    assert client.patch(f"{EXP}/99999", json={"amount": 5}).status_code == 404


# ── filters, totals, export ───────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def spread(client):
    rows = cats(client)
    groc = by_name(rows, "Groceries")
    fish = by_name(rows, "Fish & Chicken", groc["id"])
    med = by_name(rows, "Medical")
    add(client, 100, "2026-10-01", "veg", groc["id"], "Appa")
    add(client, 300, "2026-10-02", "fish", fish["id"], "Amma")
    add(client, 700, "2026-11-03", "tablets", med["id"], "Appa")
    add(client, 50, "2026-11-04", None, None, "Suji")
    return {"groc": groc, "fish": fish, "med": med}


def test_filters_by_date_category_person_and_search(client, spread):                              # EG-08
    amounts = lambda **p: sorted(float(e["amount"]) for e in client.get(EXP, params=p).json())
    assert amounts() == [50, 100, 300, 700]
    assert amounts(date_from="2026-11-01") == [50, 700]
    assert amounts(date_to="2026-10-31") == [100, 300]
    assert amounts(category_id=spread["groc"]["id"]) == [100, 300]                                # a top-level category includes its sub-categories
    assert amounts(category_id=spread["fish"]["id"]) == [300]
    assert amounts(person_id=persons(client)["Appa"]) == [100, 700]
    assert amounts(search="FISH") == [300]
    assert amounts(category_id=spread["groc"]["id"], person_id=persons(client)["Amma"]) == [300]


def test_summary_groups_by_category_sub_category_and_person_with_the_grand_total(client, spread, conn):   # EG-09
    insert_old(conn, 25, "2026-10-05", "old note")
    s = client.get(f"{EXP}/summary").json()
    assert (s["count"], float(s["total"])) == (5, 1175.0)
    top = {c["name"]: c for c in s["categories"]}
    assert float(top["Groceries"]["total"]) == 400 and top["Groceries"]["count"] == 2
    assert [(x["name"], float(x["total"])) for x in top["Groceries"]["subs"]] == [("Fish & Chicken", 300.0)]
    assert float(top["Medical"]["total"]) == 700 and float(top["Miscellaneous"]["total"]) == 50
    assert top["Ungrouped"]["ungrouped"] is True and float(top["Ungrouped"]["total"]) == 25
    assert [c["name"] for c in s["categories"]][0] == "Medical"                                   # biggest first
    ppl = {p["name"]: float(p["total"]) for p in s["people"]}
    assert ppl == {"Appa": 800.0, "Amma": 300.0, "Suji": 50.0, "Not specified": 25.0}
    assert float(sum(p["total"] if not isinstance(p["total"], str) else float(p["total"]) for p in s["people"])) == float(s["total"])
    filtered = client.get(f"{EXP}/summary", params={"date_from": "2026-11-01"}).json()
    assert (filtered["count"], float(filtered["total"])) == (2, 750.0)


def parse(resp):
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text_ = resp.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text_)))


def test_export_rows_has_every_expense_and_the_total_at_the_end(client, spread):                  # EG-10
    rows = parse(client.get(f"{EXP}/export", params={"kind": "rows"}))
    assert rows[0] == ["Date", "Amount", "Category", "Sub-category", "For whom", "Notes"]
    assert rows[1] == ["2026-11-04", "50.00", "Miscellaneous", "", "Suji", ""]                    # newest first
    assert ["2026-10-02", "300.00", "Groceries", "Fish & Chicken", "Amma", "fish"] in rows
    assert rows[-1] == ["Total", "1150.00", "", "", "", "4 expenses"]
    filtered = parse(client.get(f"{EXP}/export", params={"kind": "rows", "category_id": spread["groc"]["id"]}))
    assert filtered[-1] == ["Total", "400.00", "", "", "", "2 expenses"] and len(filtered) == 4
    assert "attachment" in client.get(f"{EXP}/export").headers["content-disposition"]


def test_export_summary_lists_categories_then_people_and_the_grand_total(client, spread):         # EG-10
    rows = parse(client.get(f"{EXP}/export", params={"kind": "summary"}))
    assert rows[0] == ["Category", "Sub-category", "Entries", "Total"]
    assert ["Groceries", "", "2", "400.00"] in rows and ["Groceries", "Fish & Chicken", "1", "300.00"] in rows
    assert ["For whom", "", "Entries", "Total"] in rows and ["Appa", "", "2", "800.00"] in rows
    assert rows[-1] == ["Total", "", "4", "1150.00"]
    assert client.get(f"{EXP}/export", params={"kind": "nope"}).status_code == 422


def test_export_keeps_tamil_text_and_commas_intact(client):                                       # EG-10
    add(client, 10, notes='அரிசி, "special" rice')
    rows = parse(client.get(f"{EXP}/export"))
    assert rows[1][5] == 'அரிசி, "special" rice'


# ── the old expenses ──────────────────────────────────────────────────────────────────────────────────────
def test_old_expenses_are_listed_once_per_notes_text_with_a_guess(client, conn):                  # EG-11
    insert_old(conn, 100, "2022-08-16", "groceries, appa")
    insert_old(conn, 50, "2022-08-17", "Groceries, Appa ")                                        # same text apart from case / spaces
    insert_old(conn, 600, "2022-08-19", "eye glass, amma")
    insert_old(conn, 500, "2022-08-20", "day expense")
    insert_old(conn, 70, "2022-08-21", None)
    g = client.get(f"{EXP}/legacy-groups").json()
    assert g["count"] == 5 and float(g["total"]) == 1320.0 and len(g["groups"]) == 4
    rows = cats(client)
    name_of = {r["id"]: r["name"] for r in rows}
    p_of = {v: k for k, v in persons(client).items()}
    first = g["groups"][0]
    assert first["notes"].lower().startswith("eye glass") and float(first["total"]) == 600.0   # biggest total first
    by_notes = {(x["notes"] or "").strip().lower(): x for x in g["groups"]}
    gro = by_notes["groceries, appa"]
    assert gro["count"] == 2 and name_of[gro["category_id"]] == "Groceries" and p_of[gro["person_id"]] == "Appa"
    eye = by_notes["eye glass, amma"]
    assert name_of[eye["category_id"]] == "Dental & Eye" and p_of[eye["person_id"]] == "Amma"
    day = by_notes["day expense"]
    assert name_of[day["category_id"]] == "Miscellaneous" and day["person_id"] is None
    assert by_notes[""]["category_id"] == by_category_id(rows, "Miscellaneous")


def by_category_id(rows, name):
    return next(r["id"] for r in rows if r["name"] == name and r["parent_id"] is None)


def test_old_expenses_only_change_when_the_reviewed_choices_are_applied(client, conn):             # EG-12
    insert_old(conn, 100, "2022-08-16", "groceries, appa")
    insert_old(conn, 50, "2022-08-17", "Groceries, Appa ")
    insert_old(conn, 500, "2022-08-20", "day expense")
    before = client.get(EXP).json()
    assert all(e["category_id"] is None and e["top_name"] is None for e in before)                # nothing is guessed behind the user's back
    g = client.get(f"{EXP}/legacy-groups").json()
    assert all(e["category_id"] is None for e in client.get(EXP).json())                          # listing the guess saved nothing

    groc = by_name(cats(client), "Groceries")
    r = client.post(f"{EXP}/legacy-groups/apply", json={"items": [
        {"notes": "groceries, appa", "category_id": groc["id"], "person_id": persons(client)["Appa"]}]})
    assert r.json() == {"updated": 2}
    got = {e["notes"].strip(): e for e in client.get(EXP).json()}
    assert got["day expense"]["category_id"] is None                                              # not in the applied list: untouched
    for n in ("groceries, appa", "Groceries, Appa"):
        assert got[n]["top_name"] == "Groceries" and got[n]["person_name"] == "Appa"
    assert {e["notes"] for e in client.get(EXP).json()} == {"groceries, appa", "Groceries, Appa ", "day expense"}   # notes are never edited
    assert len(client.get(f"{EXP}/legacy-groups").json()["groups"]) == 1                          # only the unapplied one is left


def test_apply_never_overwrites_a_group_that_was_already_grouped_or_a_known_person(client, conn):   # EG-12
    insert_old(conn, 100, "2022-08-16", "tire")
    med = by_name(cats(client), "Medical")
    e = add(client, 40, "2026-10-17", "tire", None, "Guru").json()                                # a NEW expense with the same notes text
    client.post(f"{EXP}/legacy-groups/apply", json={"items": [{"notes": "tire", "category_id": med["id"]}]})
    mine = client.get(EXP, params={"search": "tire"}).json()
    new = next(x for x in mine if x["id"] == e["id"])
    assert new["top_name"] == "Miscellaneous" and new["person_name"] == "Guru"                    # untouched
    assert next(x for x in mine if x["id"] != e["id"])["top_name"] == "Medical"
    assert client.post(f"{EXP}/legacy-groups/apply", json={"items": [{"notes": "x", "category_id": 99999}]}).status_code == 404


def test_the_old_expense_can_be_given_a_person_only_when_one_is_chosen(client, conn):             # EG-12
    insert_old(conn, 100, "2022-08-16", "milk")
    groc = by_name(cats(client), "Groceries")
    client.post(f"{EXP}/legacy-groups/apply", json={"items": [{"notes": "milk", "category_id": groc["id"]}]})
    e = client.get(EXP).json()[0]
    assert e["top_name"] == "Groceries" and e["person_id"] is None                                # still 'not specified' - shown so it can be fixed


# ── the keyword guess itself ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("notes, top, sub", [
    ("groceries", "Groceries", None),
    ("groceries, appa, from fund", "Groceries", None),
    ("fish, petrol, milk, eggs, banana", "Groceries", "Fish & Chicken"),          # a shopping trip with petrol stays groceries
    ("chicken", "Groceries", "Fish & Chicken"),
    ("mutton, appa", "Groceries", "Mutton & Meat"),
    ("milk, appa", "Groceries", "Milk & Eggs"),
    ("vegies, oils", "Groceries", "Vegetables & Fruit"),
    ("my marriage, gold", "Wedding & Functions", "Marriage"),
    ("kam marital, family, pooja", "Wedding & Functions", "Marriage"),
    ("gift, family", "Wedding & Functions", "Birthday & Gifts"),
    ("eye glass, amma", "Medical", "Dental & Eye"),
    ("hospital", "Medical", "Hospital"),
    ("medicine, family", "Medical", "Medicine"),
    ("recharge, family", "Bills & Recharge", "Mobile Recharge"),
    ("current bill, family", "Bills & Recharge", "Electricity"),
    ("tv purchase", "Home & Appliances", "Appliances"),
    ("electrician, family", "Home & Appliances", "Repairs"),
    ("petrol", "Vehicle & Fuel", "Petrol"),
    ("tire", "Vehicle & Fuel", "Service & Repair"),
    ("bus fee, me", "Vehicle & Fuel", "Bus & Auto"),
    ("family car", "Vehicle & Fuel", None),
    ("dress, amma", "Clothing", None),
    ("gold, amma", "Gold & Jewellery", None),
    ("biriyani, appa", "Eating Out", "Hotel & Tiffin"),
    ("food sunday zomota", "Eating Out", "Online Food"),
    ("icecream, appa", "Eating Out", "Snacks & Sweets"),
    ("beer, appa", "Eating Out", "Liquor"),
    ("tasmac", "Eating Out", "Liquor"),
    ("day expense", "Miscellaneous", None),
    ("kishan", "Miscellaneous", None),
    ("", "Miscellaneous", None),
    (None, "Miscellaneous", None),
    ("car park", "Vehicle & Fuel", None),
    ("carrot", "Miscellaneous", None),                                            # 'car' inside a word does not count
    ("barbecue", "Miscellaneous", None),                                          # nor 'bar'
])
def test_the_keyword_guess(notes, top, sub):                                                      # EG-13
    assert guess.guess_category(notes) == (top, sub)


@pytest.mark.parametrize("notes, person", [
    ("groceries, appa, from fund", "Appa"),
    ("eye glass, amma", "Amma"),
    ("groceries, family", "On-house"),
    ("dress, amma, appa", "Amma"),                                                # the first one written
    ("kamali docs, groceries", "Kamali"),
    ("suji bus fee", "Suji"),
    ("guru bike", "Guru"),
    ("day expense", None),
    ("bus fee, me", None),                                                        # 'me' is not one of the six: left for the review
    ("amma.", "Amma"),
    (None, None),
])
def test_the_person_guess(notes, person):                                                         # EG-13
    assert guess.guess_person(notes) == person


def test_every_guessed_name_exists_in_the_default_tree():                                         # EG-13
    tree = guess.DEFAULT_TREE
    samples = ["groceries", "fish", "mutton", "milk", "veg", "rice", "my marriage", "pooja", "gift", "hospital", "dental", "medicine",
               "recharge", "current bill", "net", "water can", "tv", "bed", "electrician", "petrol", "tire", "bus", "zomato", "snacks", "biriyani",
               "beer", "dress", "gold"]
    for s in samples:
        top, sub = guess.guess_category(s)
        assert top in tree, s
        assert sub is None or sub in tree[top], (s, top, sub)
    assert set(guess.DEFAULT_PERSONS) == {"Amma", "Appa", "On-house", "Suji", "Kamali", "Guru"}


def test_a_guessed_category_that_was_deleted_falls_back_to_a_level_that_exists(client, conn):      # EG-14
    insert_old(conn, 100, "2022-08-16", "fish")
    rows = cats(client)
    client.delete(f"{API}/categories/{by_name(rows, 'Fish & Chicken', by_name(rows, 'Groceries')['id'])['id']}")
    g = client.get(f"{EXP}/legacy-groups").json()["groups"][0]
    assert next(r["name"] for r in cats(client) if r["id"] == g["category_id"]) == "Groceries"      # the sub-category is gone: its parent
    client.delete(f"{API}/categories/{by_name(cats(client), 'Groceries')['id']}")
    g = client.get(f"{EXP}/legacy-groups").json()["groups"][0]
    assert next(r["name"] for r in cats(client) if r["id"] == g["category_id"]) == "Miscellaneous"


def test_auth_is_required(client):                                                                # EG-14
    from fastapi.testclient import TestClient
    from app.api.deps import get_current_user
    from app.main import app
    app.dependency_overrides.pop(get_current_user, None)
    try:
        anon = TestClient(app)
        for path in (f"{API}/categories", f"{API}/persons", f"{EXP}/summary", f"{EXP}/export", f"{EXP}/legacy-groups"):
            assert anon.get(path).status_code == 401, path
        assert anon.post(f"{EXP}/bulk", json={"items": []}).status_code == 401
    finally:
        pass


# ── the first requests on an empty database ───────────────────────────────────────────────────────────────
def test_concurrent_first_requests_create_the_defaults_once(client, monkeypatch):                  # EG-15
    """The Expenses page asks for categories, people and totals at the same moment on a database that has none yet."""
    import threading

    from sqlmodel import Session

    from app.core.database import engine
    from app.services import expense_group_service as svc

    import time

    class SlowTree(dict):
        """Pauses while the defaults are being written, so every request really overlaps the others."""
        def items(self):
            time.sleep(0.3)
            return super().items()

    monkeypatch.setattr(guess, "DEFAULT_TREE", SlowTree(guess.DEFAULT_TREE))
    errors, ids = [], []

    def worker():
        try:
            with Session(engine) as s:
                ids.append(svc.ensure_defaults(s))
        except Exception as exc:                       # pragma: no cover - the failure we are guarding against
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(set(ids)) == 1                                                                   # everybody got the same Miscellaneous
    names = [(r["name"], r["parent_id"]) for r in cats(client)]
    assert len(names) == len(set((n.lower(), p) for n, p in names))                             # no duplicates
    assert sum(1 for r in cats(client) if r["name"] == "Miscellaneous") == 1
    assert len(persons(client)) == 6


def test_the_database_itself_refuses_a_duplicate_name(client, conn):                               # EG-15
    from sqlalchemy.exc import IntegrityError
    cats(client)                                                                                # defaults exist
    with pytest.raises(IntegrityError):
        conn.execute(text("INSERT INTO tbl_expense_category (name, parent_id) VALUES ('GROCERIES', NULL)"))
    with pytest.raises(IntegrityError):
        conn.execute(text("INSERT INTO tbl_expense_person (name) VALUES ('appa')"))
