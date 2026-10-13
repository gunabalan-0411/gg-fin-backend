"""
/api/name-mappings endpoints and the service underneath: popup data, Save, limits, conflicts, Remember, suggest,
stale flags, closed customers, resync.   R-06, R-07, R-08, R-11, R-14 … R-19, R-29 (T-05, T-06, T-10, T-13 … T-18)
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tests.helpers import add_customer, add_mapping, pointer, rows, set_closed

URL = "/api/name-mappings"


def put(client, cid, ft="EDI", aliases=(), mismatches=(), resolutions=()):
    return client.put(f"{URL}/customer", json={"finance_type": ft, "customer_id": cid, "aliases": list(aliases),
                                              "mismatches": list(mismatches), "resolutions": list(resolutions)})


def get(client, cid, ft="EDI"):
    return client.get(URL, params={"finance_type": ft, "customer_id": cid})


def words(data, key):
    return [x["word"] for x in data[key]]


@pytest.fixture()
def two(conn):
    add_customer(conn, "EDI", 1, "Alpha")
    add_customer(conn, "EDI", 2, "Beta")


# ── T-13 popup contents / Save is what persists ────────────────────────────────────────────────────────────
def test_popup_data_shape_and_limits(client, two):
    r = get(client, 1)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["customer"] == {"id": 1, "name": "Alpha", "finance_type": "EDI"}
    assert d["aliases"] == [] and d["mismatches"] == []
    assert d["limits"] == {"alias": 3, "ai_mismatch": 10}


def test_nothing_is_stored_until_save_and_save_replaces_the_lists(client, conn, two):
    assert client.post(f"{URL}/check", json={"finance_type": "EDI", "customer_id": 1,
                                             "items": [{"kind": "alias", "word": "Al"}]}).status_code == 200
    assert rows(conn, "alias") == []                                           # check never writes
    r = put(client, 1, aliases=["Al", "Alph"], mismatches=["Alfa", "Alpa"])
    assert r.status_code == 200, r.text
    d = get(client, 1).json()
    assert words(d, "aliases") == ["Al", "Alph"] and words(d, "mismatches") == ["Alfa", "Alpa"]
    # saving a shorter list deletes the removed words
    d = put(client, 1, aliases=["Al"], mismatches=["Alpa", "Alfaa"]).json()
    assert words(d, "aliases") == ["Al"] and sorted(words(d, "mismatches")) == ["Alfaa", "Alpa"]
    assert [r[1] for r in rows(conn, "alias")] == ["Al"]
    # empty lists clear everything
    d = put(client, 1).json()
    assert d["aliases"] == [] and d["mismatches"] == []
    assert rows(conn, "alias") == [] and rows(conn, "ai_mismatch") == []


def test_saved_rows_carry_the_current_name_type_and_active_id(client, conn, two):
    put(client, 2, aliases=["Bee"])
    assert rows(conn, "alias", "EDI") == [("Beta", "Bee", 2)]
    assert pointer(conn, "alias", "EDI", "Beta", "Bee") == (2, True)


def test_edit_resets_usage_unchanged_words_keep_theirs(client, conn, two):
    put(client, 1, mismatches=["keepme", "fixme"])
    conn.execute(text("UPDATE tbl_customer_ai_mismatch SET use_count = 7"))
    d = put(client, 1, mismatches=["keepme", "fixed"]).json()                  # 'fixme' edited to 'fixed'
    by_word = {x["word"]: x for x in d["mismatches"]}
    assert by_word["keepme"]["use_count"] == 7 and by_word["fixed"]["use_count"] == 0


# ── T-05 stored as typed ───────────────────────────────────────────────────────────────────────────────────
def test_words_are_stored_exactly_as_typed_apart_from_outer_whitespace(client, conn, two):
    d = put(client, 1, mismatches=["  Selvam  ", "selvam", "Sel  vam", "SELVAM"]).json()
    assert words(d, "mismatches") == ["Selvam", "selvam", "Sel  vam", "SELVAM"]     # no case folding, inner spaces kept
    assert put(client, 1, mismatches=["A", "A", " A "]).json()["mismatches"][0]["word"] == "A"   # exact dups collapse


def test_blank_words_are_ignored_and_overlong_words_rejected(client, two):
    assert words(put(client, 1, aliases=["", "   ", "ok"]).json(), "aliases") == ["ok"]
    r = put(client, 1, aliases=["x" * 201])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "word_too_long"


# ── T-14 limits ────────────────────────────────────────────────────────────────────────────────────────────
def test_limits_3_aliases_10_mismatches_enforced_by_the_server(client, conn, two):
    assert put(client, 1, aliases=["a1", "a2", "a3"], mismatches=[f"m{i}" for i in range(10)]).status_code == 200
    r = put(client, 1, aliases=["a1", "a2", "a3", "a4"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "limit_exceeded"
    assert r.json()["detail"]["kind"] == "alias" and r.json()["detail"]["limit"] == 3
    r = put(client, 1, mismatches=[f"m{i}" for i in range(11)])
    assert r.status_code == 422 and r.json()["detail"]["kind"] == "ai_mismatch" and r.json()["detail"]["limit"] == 10
    assert len(rows(conn, "alias")) == 3 and len(rows(conn, "ai_mismatch")) == 10      # failed saves changed nothing


def test_at_the_limit_the_user_can_edit_or_delete_an_old_value(client, conn, two):
    put(client, 1, aliases=["a1", "a2", "a3"])
    d = put(client, 1, aliases=["a1", "a2", "a3-fixed"]).json()                # edit one
    assert words(d, "aliases") == ["a1", "a2", "a3-fixed"]
    d = put(client, 1, aliases=["a1", "a2", "brand-new"]).json()               # replace one
    assert words(d, "aliases") == ["a1", "a2", "brand-new"]
    d = put(client, 1, aliases=["a1", "a2", "x"]).json()
    assert len(d["aliases"]) == 3


def test_limits_are_per_customer(client, two):
    assert put(client, 1, aliases=["a1", "a2", "a3"]).status_code == 200
    assert put(client, 2, aliases=["b1", "b2", "b3"]).status_code == 200


def test_a_merged_group_over_the_limit_must_be_trimmed_before_saving(client, conn, two):
    for i in range(12):                                                         # as left behind by a rename merge
        add_mapping(conn, "ai_mismatch", "EDI", "Alpha", f"w{i}", cid=1)
    assert len(get(client, 1).json()["mismatches"]) == 12                       # nothing was silently deleted
    r = put(client, 1, mismatches=[f"w{i}" for i in range(12)])
    assert r.status_code == 422                                                 # cannot save 12
    assert put(client, 1, mismatches=[f"w{i}" for i in range(10)]).status_code == 200


# ── T-15 stale ─────────────────────────────────────────────────────────────────────────────────────────────
def test_stale_flags_in_popup(client, conn, two):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=120)).isoformat()
    add_mapping(conn, "ai_mismatch", "EDI", "Alpha", "fresh", cid=1)
    add_mapping(conn, "ai_mismatch", "EDI", "Alpha", "never-used-old", cid=1, created=old)
    add_mapping(conn, "ai_mismatch", "EDI", "Alpha", "used-long-ago", cid=1, use_count=3,
                last_used=(now - timedelta(days=200)).isoformat(), created=old)
    add_mapping(conn, "ai_mismatch", "EDI", "Alpha", "used-lately", cid=1, use_count=3,
                last_used=(now - timedelta(days=5)).isoformat(), created=old)
    by = {x["word"]: x["stale"] for x in get(client, 1).json()["mismatches"]}
    assert by == {"fresh": False, "never-used-old": True, "used-long-ago": True, "used-lately": False}


# ── T-16 conflicts ─────────────────────────────────────────────────────────────────────────────────────────
def test_conflict_must_be_decided_and_names_the_other_customer(client, conn, two):
    put(client, 2, mismatches=["Zed"])
    r = put(client, 1, mismatches=["Zed"])
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "conflict"
    assert detail["conflicts"] == [{"kind": "ai_mismatch", "word": "Zed",
                                    "customers": [{"mapping_id": detail["conflicts"][0]["customers"][0]["mapping_id"],
                                                   "customer_id": 2, "customer_name": "Beta"}]}]
    assert rows(conn, "ai_mismatch") == [("Beta", "Zed", 2)]                    # nothing changed


def test_keep_both(client, conn, two):
    put(client, 2, mismatches=["Zed"])
    r = put(client, 1, mismatches=["Zed"], resolutions=[{"kind": "ai_mismatch", "word": "Zed", "action": "keep_both"}])
    assert r.status_code == 200, r.text
    assert sorted(rows(conn, "ai_mismatch")) == [("Alpha", "Zed", 1), ("Beta", "Zed", 2)]


def test_move_deletes_the_other_customers_row(client, conn, two):
    put(client, 2, mismatches=["Zed", "Other"])
    r = put(client, 1, mismatches=["Zed"], resolutions=[{"kind": "ai_mismatch", "word": "Zed", "action": "move"}])
    assert r.status_code == 200, r.text
    assert sorted(rows(conn, "ai_mismatch")) == [("Alpha", "Zed", 1), ("Beta", "Other", 2)]


def test_cancel_is_simply_not_saving(client, conn, two):
    put(client, 2, mismatches=["Zed"])
    c = client.post(f"{URL}/check", json={"finance_type": "EDI", "customer_id": 1,
                                          "items": [{"kind": "ai_mismatch", "word": "Zed"}]})
    assert c.status_code == 200 and c.json()["conflicts"][0]["customers"][0]["customer_name"] == "Beta"
    assert rows(conn, "ai_mismatch") == [("Beta", "Zed", 2)]


def test_conflict_check_is_per_kind_and_ignores_inactive_customers(client, conn, two):
    put(client, 2, aliases=["Zed"])
    items = [{"kind": "ai_mismatch", "word": "Zed"}]                            # same word, other kind: no conflict
    assert client.post(f"{URL}/check", json={"finance_type": "EDI", "customer_id": 1, "items": items}).json()["conflicts"] == []
    items = [{"kind": "alias", "word": "Zed"}]
    assert len(client.post(f"{URL}/check", json={"finance_type": "EDI", "customer_id": 1, "items": items}).json()["conflicts"]) == 1
    set_closed(conn, "EDI", 2, True)                                            # Beta closes -> never shown (R-07)
    assert client.post(f"{URL}/check", json={"finance_type": "EDI", "customer_id": 1, "items": items}).json()["conflicts"] == []
    assert put(client, 1, aliases=["Zed"]).status_code == 200                   # and no decision is needed


def test_conflict_ignores_other_finance_type(client, conn, two):
    add_customer(conn, "IOP", 5, "Gamma")
    put(client, 5, ft="IOP", aliases=["Zed"])
    assert put(client, 1, aliases=["Zed"]).status_code == 200


def test_unchanged_existing_words_never_raise_conflicts(client, conn, two):
    put(client, 1, mismatches=["Zed"])
    put(client, 2, mismatches=["Zed"], resolutions=[{"kind": "ai_mismatch", "word": "Zed", "action": "keep_both"}])
    assert put(client, 1, mismatches=["Zed", "New"]).status_code == 200         # Zed already there: not a new word


# ── T-17 remember ──────────────────────────────────────────────────────────────────────────────────────────
def remember(client, cid, word, ft="EDI"):
    return client.post(f"{URL}/remember", json={"finance_type": ft, "customer_id": cid, "word": word})


def test_remember_adds_an_ai_mismatch_only(client, conn, two):
    r = remember(client, 1, " Alfa ")
    assert r.status_code == 200 and r.json() == {"status": "added", "word": "Alfa"}
    assert rows(conn, "ai_mismatch") == [("Alpha", "Alfa", 1)] and rows(conn, "alias") == []
    assert remember(client, 1, "Alfa").json()["status"] == "exists"
    assert len(rows(conn, "ai_mismatch")) == 1


def test_remember_at_the_limit_changes_nothing_and_says_so(client, conn, two):
    put(client, 1, mismatches=[f"m{i}" for i in range(10)])
    r = remember(client, 1, "eleventh").json()
    assert r == {"status": "limit_reached", "word": "eleventh", "limit": 10}
    assert len(rows(conn, "ai_mismatch")) == 10


def test_remember_on_a_conflict_changes_nothing_and_returns_who_has_it(client, conn, two):
    put(client, 2, mismatches=["Zed"])
    r = remember(client, 1, "Zed").json()
    assert r["status"] == "conflict" and r["conflicts"][0]["customers"][0]["customer_name"] == "Beta"
    assert rows(conn, "ai_mismatch") == [("Beta", "Zed", 2)]


def test_remember_rejects_empty_word(client, two):
    assert remember(client, 1, "   ").status_code == 422


# ── errors ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_unknown_closed_nameless_customers_and_bad_input(client, conn, two):
    assert get(client, 999).status_code == 404 and get(client, 999).json()["detail"]["code"] == "customer_not_found"
    set_closed(conn, "EDI", 2, True)
    r = get(client, 2)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "customer_closed"
    assert put(client, 2, aliases=["x"]).status_code == 409
    add_customer(conn, "EDI", 3, None)
    assert get(client, 3).status_code == 422 and get(client, 3).json()["detail"]["code"] == "customer_has_no_name"
    assert client.get(URL, params={"finance_type": "XYZ", "customer_id": 1}).status_code == 422
    assert client.get(URL, params={"finance_type": "edi", "customer_id": 1}).status_code == 200      # case-insensitive
    assert client.post(f"{URL}/check", json={"finance_type": "EDI", "customer_id": 1,
                                             "items": [{"kind": "nope", "word": "x"}]}).status_code == 422


def test_requires_login():
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get(URL, params={"finance_type": "EDI", "customer_id": 1}).status_code == 401


def test_same_name_accounts_share_one_group_and_pointer_is_the_first_open_one(client, conn):
    add_customer(conn, "EDI", 1, "Twin", closed=True)
    add_customer(conn, "EDI", 2, "Twin")
    add_customer(conn, "EDI", 3, "Twin")
    put(client, 3, aliases=["T"])
    assert pointer(conn, "alias", "EDI", "Twin", "T") == (2, True)             # first open account, not the one edited from
    assert words(get(client, 2).json(), "aliases") == ["T"]                    # every account sees the same list


# ── T-10 suggest while typing, T-06 inactive excluded, T-07 two customers ──────────────────────────────────
def suggest(client, q, ft="EDI"):
    return client.post(f"{URL}/suggest", json={"finance_type": ft, "query": q})


def test_suggest_returns_ranked_candidates_for_typed_text(client, conn, two):
    put(client, 1, mismatches=["Selvm"], aliases=["Selvamm"])
    put(client, 2, mismatches=["Kannan"])
    r = suggest(client, "Selvam")
    assert r.status_code == 200
    cands = r.json()["candidates"]
    assert [c["id"] for c in cands] == [1] and cands[0]["name"] == "Alpha"
    assert cands[0]["source"] in ("alias", "ai_mismatch") and 0 < cands[0]["score"] <= 1
    assert suggest(client, "Qwertyuiop").json()["candidates"] == []
    assert suggest(client, "").json()["candidates"] == []
    assert suggest(client, "Selvam", ft="XYZ").status_code == 422


def test_suggest_never_returns_closed_customers_and_does_not_count_usage(client, conn, two):
    put(client, 1, mismatches=["Selvm"])
    put(client, 2, mismatches=["Selvm"], resolutions=[{"kind": "ai_mismatch", "word": "Selvm", "action": "keep_both"}])
    assert sorted(c["id"] for c in suggest(client, "Selvam").json()["candidates"]) == [1, 2]      # R-08 both listed
    set_closed(conn, "EDI", 2, True)
    assert [c["id"] for c in suggest(client, "Selvam").json()["candidates"]] == [1]               # R-07
    assert conn.execute(text("SELECT coalesce(sum(use_count),0) FROM tbl_customer_ai_mismatch")).scalar() == 0


def test_suggest_is_separate_per_finance_type(client, conn, two):
    add_customer(conn, "IOP", 9, "Iota")
    put(client, 9, ft="IOP", mismatches=["Selvm"])
    assert suggest(client, "Selvam", "EDI").json()["candidates"] == []
    assert [c["id"] for c in suggest(client, "Selvam", "IOP").json()["candidates"]] == [9]


# ── T-33 resync endpoint ───────────────────────────────────────────────────────────────────────────────────
def test_resync_endpoint_repairs_pointers(client, conn, two):
    put(client, 1, aliases=["a"], mismatches=["m"])
    conn.execute(text("UPDATE tbl_customer_alias SET active_customer_id = 42"))
    conn.execute(text("UPDATE tbl_customer_ai_mismatch SET active_customer_id = 42"))
    r = client.post(f"{URL}/resync")
    assert r.status_code == 200 and r.json() == {"repointed": 2, "alias_rows": 1, "mismatch_rows": 1}
    assert pointer(conn, "alias", "EDI", "Alpha", "a") == (1, True)
    assert client.post(f"{URL}/resync").json()["repointed"] == 0
