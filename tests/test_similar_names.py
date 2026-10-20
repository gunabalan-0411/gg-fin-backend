"""
A new alias / AI-mismatch word that looks like an EXISTING CUSTOMER NAME (90% or more) is reported, and has to be confirmed
("Add anyway") before it is saved.   SN-01 … SN-07  (B4-01 … B4-04, T4-01 … T4-04)
"""
import pytest

from app.services.name_mapping_service import SIMILAR_NAME_MIN, name_similarity
from tests.helpers import add_customer, add_mapping, rows, set_closed

URL = "/api/name-mappings"


def check(client, cid, items, ft="EDI"):
    return client.post(f"{URL}/check", json={"finance_type": ft, "customer_id": cid,
                                             "items": [{"kind": k, "word": w} for k, w in items]})


def put(client, cid, ft="EDI", aliases=(), mismatches=(), confirmed=(), resolutions=()):
    return client.put(f"{URL}/customer", json={
        "finance_type": ft, "customer_id": cid, "aliases": list(aliases), "mismatches": list(mismatches),
        "resolutions": list(resolutions), "confirmed_similar": [{"kind": k, "word": w} for k, w in confirmed],
    })


def remember(client, cid, word, ft="EDI"):
    return client.post(f"{URL}/remember", json={"finance_type": ft, "customer_id": cid, "word": word})


@pytest.fixture()
def people(conn):
    add_customer(conn, "EDI", 1, "Kannan")
    add_customer(conn, "EDI", 2, "Selvam Kumar")
    add_customer(conn, "EDI", 3, "Muthu")
    add_customer(conn, "IOP", 7, "Anbarasan")


def similar_of(resp):
    assert resp.status_code == 200, resp.text
    return {(s["kind"], s["word"]): s["matches"] for s in resp.json()["similar"]}


# ── the maths (SN-01) ───────────────────────────────────────────────────────────────────────────────────────
def test_threshold_is_ninety():
    assert SIMILAR_NAME_MIN == 90.0


@pytest.mark.parametrize("a,b,expected", [
    ("Selvam Kumar", "selvam  kumar", 100.0),             # case and spacing are ignored
    ("Kumar Selvam", "Selvam Kumar", 100.0),              # so is word order
    ("abcdefghij", "abcdefghix", 90.0),                   # exactly 90: one slip in ten letters
    ("abcdefghi", "abcdefghx", 88.9),                     # one slip in nine letters: below
    ("Kannan", "Kanan", 90.9),                            # a typical OCR slip
    ("Ra", "Rajesh Kumar", None),                         # a short word is NOT "similar" just because it is inside a name
    ("", "Kannan", 0.0),
])
def test_name_similarity(a, b, expected):
    score = name_similarity(a, b)
    if expected is None:
        assert score < SIMILAR_NAME_MIN
    else:
        assert score == expected


# ── B4-01 / T4-01: what is reported ─────────────────────────────────────────────────────────────────────────
def test_a_word_90_percent_like_another_customers_name_is_reported_with_the_customer_and_score(client, people):
    got = similar_of(check(client, 1, [("ai_mismatch", "Selvam Kumaar")]))              # for Kannan; looks like Selvam Kumar
    assert list(got) == [("ai_mismatch", "Selvam Kumaar")]
    (m,) = got[("ai_mismatch", "Selvam Kumaar")]
    assert m["customer_id"] == 2 and m["customer_name"] == "Selvam Kumar" and m["own"] is False and m["score"] >= 90


def test_below_ninety_is_not_reported(client, people):
    assert similar_of(check(client, 1, [("alias", "Selvam"), ("alias", "Kumar"), ("ai_mismatch", "Selvamkum")])) == {}


def test_the_edge_exactly_90_is_reported_89_is_not(client, conn):
    add_customer(conn, "EDI", 1, "Owner")
    add_customer(conn, "EDI", 2, "abcdefghij")                 # 10 letters
    add_customer(conn, "EDI", 3, "klmnopqrs")                  # 9 letters
    got = similar_of(check(client, 1, [("alias", "abcdefghix"), ("alias", "klmnopqrx")]))
    assert ("alias", "abcdefghix") in got and got[("alias", "abcdefghix")][0]["score"] == 90.0
    assert ("alias", "klmnopqrx") not in got                    # 88.9


def test_both_kinds_are_checked_and_each_word_is_reported_once(client, people):
    got = similar_of(check(client, 1, [("alias", "Muthu"), ("ai_mismatch", "Muthu"), ("alias", "Muthu")]))
    assert set(got) == {("alias", "Muthu"), ("ai_mismatch", "Muthu")}


def test_the_customers_own_name_is_reported_as_own(client, people):
    got = similar_of(check(client, 1, [("ai_mismatch", "Kanan")]))
    (m,) = got[("ai_mismatch", "Kanan")]
    assert m["customer_id"] == 1 and m["own"] is True


def test_the_other_product_and_closed_customers_are_ignored(client, conn, people):
    assert similar_of(check(client, 1, [("alias", "Anbarasan")])) == {}                 # an IOP customer; Kannan is EDI
    assert ("alias", "Anbarasan") in similar_of(check(client, 7, [("alias", "Anbarasan")], ft="IOP"))   # its own name
    set_closed(conn, "EDI", 3, True)
    assert similar_of(check(client, 1, [("alias", "Muthu")])) == {}                     # closed accounts are never matched


def test_two_accounts_with_the_same_name_are_one_line(client, conn, people):
    add_customer(conn, "EDI", 9, "Selvam Kumar", closed=True)
    add_customer(conn, "EDI", 10, "selvam kumar")
    (m,) = similar_of(check(client, 1, [("alias", "Selvam Kumar")]))[("alias", "Selvam Kumar")]
    assert m["customer_name"] in ("Selvam Kumar", "selvam kumar")


def test_check_never_writes(client, conn, people):
    check(client, 1, [("alias", "Muthu")])
    assert rows(conn, "alias") == [] and rows(conn, "ai_mismatch") == []


# ── T4-02: other customers' aliases / mismatch words are NOT fuzzy-compared ────────────────────────────────────
def test_other_customers_aliases_are_not_fuzzy_compared_but_exact_conflicts_still_are(client, conn, people):
    add_mapping(conn, "alias", "EDI", "Muthu", "Kandasamy", cid=3)
    r = check(client, 1, [("alias", "Kandasamyy"), ("alias", "Kandasamy")])             # a near copy, and the exact word
    assert r.status_code == 200
    assert similar_of(r) == {}                                                           # no fuzzy look-alike warning ...
    assert [c["word"] for c in r.json()["conflicts"]] == ["Kandasamy"]                   # ... the exact-match check is unchanged


# ── B4-03 / T4-03: Save ─────────────────────────────────────────────────────────────────────────────────────
def test_save_refuses_an_unconfirmed_look_alike_and_writes_nothing(client, conn, people):
    r = put(client, 1, mismatches=["Kanan", "Muthu"])                       # "Muthu" is another customer's name
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "similar" and d["conflicts"] == []
    assert [(s["kind"], s["word"]) for s in d["similar"]] == [("ai_mismatch", "Muthu")]
    assert rows(conn, "ai_mismatch") == []                                  # not even the harmless word was saved


def test_save_accepts_it_once_confirmed(client, conn, people):
    r = put(client, 1, mismatches=["Kanan", "Muthu"], confirmed=[("ai_mismatch", "Muthu")])
    assert r.status_code == 200, r.text
    assert [w for _n, w, _c in rows(conn, "ai_mismatch", "EDI")] == ["Kanan", "Muthu"]


def test_a_confirmation_only_covers_its_own_word_and_kind(client, conn, people):
    r = put(client, 1, aliases=["Muthu"], mismatches=["Selvam Kumar"], confirmed=[("ai_mismatch", "Muthu"), ("alias", "Selvam Kumar")])
    assert r.status_code == 409
    assert {(s["kind"], s["word"]) for s in r.json()["detail"]["similar"]} == {("alias", "Muthu"), ("ai_mismatch", "Selvam Kumar")}
    assert rows(conn, "alias") == [] and rows(conn, "ai_mismatch") == []


def test_the_customers_own_name_needs_no_confirmation(client, conn, people):
    r = put(client, 1, mismatches=["Kanan", "Kannan K"])
    assert r.status_code == 200, r.text
    assert len(rows(conn, "ai_mismatch", "EDI")) == 2


def test_words_already_saved_are_not_checked_again(client, conn, people):
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Muthu", cid=1)       # saved earlier (before this rule, or confirmed back then)
    r = put(client, 1, mismatches=["Muthu"])
    assert r.status_code == 200, r.text
    r = put(client, 1, mismatches=["Muthu", "Kanan"])                       # adding another harmless word keeps it
    assert r.status_code == 200, r.text


def test_editing_a_saved_word_into_a_look_alike_is_checked(client, conn, people):
    assert put(client, 1, mismatches=["Kanan"]).status_code == 200
    r = put(client, 1, mismatches=["Selvam Kumar"])                         # 'Kanan' edited into another customer's name
    assert r.status_code == 409 and r.json()["detail"]["code"] == "similar"
    assert [w for _n, w, _c in rows(conn, "ai_mismatch", "EDI")] == ["Kanan"]   # unchanged


def test_an_exact_conflict_and_a_look_alike_are_reported_together(client, conn, people):
    add_mapping(conn, "ai_mismatch", "EDI", "Muthu", "Mut", cid=3)
    r = put(client, 1, mismatches=["Mut", "Selvam Kumar"])
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "conflict"
    assert [c["word"] for c in d["conflicts"]] == ["Mut"]
    assert [s["word"] for s in d["similar"]] == ["Selvam Kumar"]


def test_resolving_the_conflict_and_confirming_saves_both(client, conn, people):
    add_mapping(conn, "ai_mismatch", "EDI", "Muthu", "Mut", cid=3)
    r = put(client, 1, mismatches=["Mut", "Selvam Kumar"], resolutions=[{"kind": "ai_mismatch", "word": "Mut", "action": "keep_both"}],
            confirmed=[("ai_mismatch", "Selvam Kumar")])
    assert r.status_code == 200, r.text
    assert {w for _n, w, _c in rows(conn, "ai_mismatch", "EDI")} == {"Mut", "Selvam Kumar"}


def test_save_without_the_new_field_still_works_for_ordinary_words(client, conn, people):
    r = client.put(f"{URL}/customer", json={"finance_type": "EDI", "customer_id": 1, "aliases": ["Kan"], "mismatches": []})
    assert r.status_code == 200, r.text


# ── B4-04 / T4-04: Remember this ────────────────────────────────────────────────────────────────────────────
def test_remember_does_not_save_a_look_alike_silently(client, conn, people):
    r = remember(client, 1, "Selvam Kumaar")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "similar" and d["word"] == "Selvam Kumaar"
    assert d["similar"][0]["matches"][0]["customer_name"] == "Selvam Kumar"
    assert rows(conn, "ai_mismatch") == []


def test_remember_still_adds_an_ordinary_word_and_a_misspelling_of_the_own_name(client, conn, people):
    assert remember(client, 1, "Kannam").json()["status"] == "added"                    # 83: not similar to anyone
    assert remember(client, 1, "Kanan").json()["status"] == "added"                     # 90.9, but it is the customer's OWN name
    assert [w for _n, w, _c in rows(conn, "ai_mismatch", "EDI")] == ["Kanan", "Kannam"]


def test_remember_keeps_its_other_statuses(client, conn, people):
    assert remember(client, 1, "Kanan").json()["status"] == "added"
    assert remember(client, 1, "Kanan").json()["status"] == "exists"
    add_mapping(conn, "ai_mismatch", "EDI", "Muthu", "Mut", cid=3)
    assert remember(client, 1, "Mut").json()["status"] == "conflict"                    # exact conflict wins, as before
