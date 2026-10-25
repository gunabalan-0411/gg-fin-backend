"""
Personal Finance records: savings accounts, friendly lends, gifts received. Every row belongs to one login. PR-01 ... PR-16.
"""
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


def saving(client, **over):
    body = {"amount": 25000, "bank": "HDFC", "account_name": "Salary", "balance_date": "2026-10-20", **over}
    return client.post(f"{P}/savings", json=body)


def lend(client, **over):
    body = {"to_whom": "Karthik", "amount": 5000, "follow_up_date": "2026-11-05", **over}
    return client.post(f"{P}/lends", json=body)


def gift(client, **over):
    body = {"gift_type": "money", "amount": 1000, "occasion": "Birthday", "gift_from": "Uncle", **over}
    return client.post(f"{P}/gifts", json=body)


# ── savings ───────────────────────────────────────────────────────────────────────────────────────────────
def test_a_saving_keeps_amount_bank_account_name_and_the_balance_date(client):                      # PR-01
    r = saving(client)
    assert r.status_code == 201, r.text
    got = client.get(f"{P}/savings").json()
    assert len(got) == 1
    assert (got[0]["amount"], got[0]["bank"], got[0]["account_name"], got[0]["balance_date"]) == ("25000.00", "HDFC", "Salary", "2026-10-20")


def test_a_saving_needs_a_bank_an_account_name_and_a_date_and_no_negative_amount(client):           # PR-02
    assert saving(client, bank="  ").status_code == 422
    assert saving(client, account_name="").status_code == 422
    assert saving(client, amount=-1).status_code == 422
    r = client.post(f"{P}/savings", json={"amount": 5, "bank": "SBI", "account_name": "x"})
    assert r.status_code == 422                                                                      # no balance date
    assert saving(client, amount=0).status_code == 201                                               # an empty account is allowed


def test_a_saving_can_be_updated_with_a_new_balance_and_date_and_deleted(client):                  # PR-03
    sid = saving(client).json()["id"]
    r = client.patch(f"{P}/savings/{sid}", json={"amount": 31000, "balance_date": "2026-10-25"})
    assert r.status_code == 200 and (r.json()["amount"], r.json()["balance_date"], r.json()["bank"]) == ("31000.00", "2026-10-25", "HDFC")
    assert client.patch(f"{P}/savings/{sid}", json={"bank": ""}).status_code == 422                 # still validated
    assert client.delete(f"{P}/savings/{sid}").status_code == 204
    assert client.get(f"{P}/savings").json() == []


# ── friendly lends ────────────────────────────────────────────────────────────────────────────────────────
def test_a_lend_keeps_to_whom_amount_and_the_follow_up_date(client):                                # PR-04
    r = lend(client)
    assert r.status_code == 201, r.text
    got = r.json()
    assert (got["to_whom"], got["amount"], got["follow_up_date"], got["is_settled"]) == ("Karthik", "5000.00", "2026-11-05", False)


def test_a_lend_needs_a_name_and_a_positive_amount_the_follow_up_is_optional(client):               # PR-05
    assert lend(client, to_whom=" ").status_code == 422
    assert lend(client, amount=0).status_code == 422
    assert lend(client, amount=-5).status_code == 422
    r = lend(client, follow_up_date=None)
    assert r.status_code == 201 and r.json()["follow_up_date"] is None


def test_lends_list_open_ones_first_by_the_nearest_follow_up_then_those_without_a_date_then_settled(client):   # PR-06
    lend(client, to_whom="Late", follow_up_date="2026-12-01")
    lend(client, to_whom="Soon", follow_up_date="2026-10-30")
    lend(client, to_whom="NoDate", follow_up_date=None)
    d = lend(client, to_whom="Paid", follow_up_date="2026-10-01").json()["id"]
    client.patch(f"{P}/lends/{d}", json={"is_settled": True})
    assert [x["to_whom"] for x in client.get(f"{P}/lends").json()] == ["Soon", "Late", "NoDate", "Paid"]


def test_a_lend_can_be_marked_settled_moved_to_a_new_follow_up_and_deleted(client):                  # PR-07
    lid = lend(client).json()["id"]
    r = client.patch(f"{P}/lends/{lid}", json={"follow_up_date": "2026-12-25", "amount": 4000})
    assert (r.json()["follow_up_date"], r.json()["amount"], r.json()["is_settled"]) == ("2026-12-25", "4000.00", False)
    assert client.patch(f"{P}/lends/{lid}", json={"is_settled": True}).json()["is_settled"] is True
    assert client.patch(f"{P}/lends/{lid}", json={"amount": 0}).status_code == 422
    assert client.delete(f"{P}/lends/{lid}").status_code == 204


# ── gifts ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_money_gift_needs_how_much_and_keeps_the_occasion_and_giver(client):                        # PR-08
    r = gift(client)
    assert r.status_code == 201, r.text
    g = r.json()
    assert (g["gift_type"], g["amount"], g["gold_grams"], g["item_name"], g["occasion"], g["gift_from"]) == ("money", "1000.00", None, None, "Birthday", "Uncle")
    assert gift(client, amount=None).status_code == 422
    assert gift(client, amount=0).status_code == 422


def test_a_gold_gift_needs_the_grams_and_a_gift_of_another_kind_needs_its_name(client):               # PR-09
    r = gift(client, gift_type="gold", amount=None, gold_grams=8, occasion="Wedding")
    assert r.status_code == 201 and (r.json()["gold_grams"], r.json()["amount"]) == ("8.000", None)
    assert gift(client, gift_type="gold", amount=None).status_code == 422
    r = gift(client, gift_type="other", amount=None, item_name="Wall clock")
    assert r.status_code == 201 and r.json()["item_name"] == "Wall clock"
    assert gift(client, gift_type="other", amount=None).status_code == 422
    assert gift(client, gift_type="silver", amount=None).status_code == 422                           # only money / gold / other


def test_only_the_detail_of_the_chosen_kind_is_kept(client):                                           # PR-10
    r = gift(client, gift_type="money", amount=500, gold_grams=3, item_name="clock")
    assert (r.json()["amount"], r.json()["gold_grams"], r.json()["item_name"]) == ("500.00", None, None)


def test_changing_the_kind_of_a_gift_drops_the_old_detail_and_asks_for_the_new_one(client):            # PR-11
    gid = gift(client, amount=500).json()["id"]
    assert client.patch(f"{P}/gifts/{gid}", json={"gift_type": "gold"}).status_code == 422             # grams missing
    r = client.patch(f"{P}/gifts/{gid}", json={"gift_type": "gold", "gold_grams": 4})
    assert r.status_code == 200 and (r.json()["gold_grams"], r.json()["amount"]) == ("4.000", None)
    r = client.patch(f"{P}/gifts/{gid}", json={"occasion": "  Diwali  ", "gift_from": ""})
    assert (r.json()["occasion"], r.json()["gift_from"], r.json()["gold_grams"]) == ("Diwali", None, "4.000")   # trimmed; empty clears
    assert client.delete(f"{P}/gifts/{gid}").status_code == 204


def test_gifts_are_listed_newest_first_and_those_without_a_date_last(client):                          # PR-12
    gift(client, occasion="Old", received_date="2025-01-01")
    gift(client, occasion="New", received_date="2026-09-01")
    gift(client, occasion="Undated")
    assert [g["occasion"] for g in client.get(f"{P}/gifts").json()] == ["New", "Old", "Undated"]


# ── privacy ───────────────────────────────────────────────────────────────────────────────────────────────
def test_another_login_sees_none_of_them(client):                                                       # PR-13
    saving(client)
    lend(client)
    gift(client)
    as_user(2)
    for kind in ("savings", "lends", "gifts"):
        assert client.get(f"{P}/{kind}").json() == []


def test_another_login_cannot_change_or_delete_them_it_gets_not_found(client):                          # PR-14
    sid = saving(client).json()["id"]
    lid = lend(client).json()["id"]
    gid = gift(client).json()["id"]
    as_user(2)
    for kind, rid in (("savings", sid), ("lends", lid), ("gifts", gid)):
        assert client.patch(f"{P}/{kind}/{rid}", json={"notes": "mine now"}).status_code == 404
        assert client.delete(f"{P}/{kind}/{rid}").status_code == 404
    as_user(1)
    assert len(client.get(f"{P}/savings").json()) == 1 and client.get(f"{P}/lends").json()[0]["notes"] is None
    assert len(client.get(f"{P}/gifts").json()) == 1


def test_unknown_ids_are_not_found(client):                                                              # PR-15
    for kind in ("savings", "lends", "gifts"):
        assert client.patch(f"{P}/{kind}/9999", json={"notes": "x"}).status_code == 404
        assert client.delete(f"{P}/{kind}/9999").status_code == 404


def test_login_is_required():                                                                            # PR-16
    from fastapi.testclient import TestClient
    app.dependency_overrides.pop(get_current_user, None)
    c = TestClient(app)
    for kind in ("savings", "lends", "gifts"):
        assert c.get(f"{P}/{kind}").status_code == 401
        assert c.post(f"{P}/{kind}", json={}).status_code == 401
