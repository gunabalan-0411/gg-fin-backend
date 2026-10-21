"""
Re-loan: a closed account is replaced by a new open one and its alias / AI-mismatch names are carried forward.
The words are stored by customer NAME: a new account with the SAME name gets them by itself (the database points them at the open
account); a new account with ANOTHER name gets a copy under its own name. CF-01 … CF-08.
"""
from tests.helpers import add_customer, add_mapping, pointer, rows, set_closed
from tests.test_name_mappings_api import URL, get, put, words

CF = f"{URL}/carry-forward"


def carry(client, frm, to, ft="EDI"):
    return client.post(CF, json={"finance_type": ft, "from_customer_id": frm, "to_customer_id": to})


def undo(client, cid, aliases=(), mismatches=(), ft="EDI"):
    return client.post(f"{CF}/undo", json={"finance_type": ft, "customer_id": cid, "aliases": list(aliases), "mismatches": list(mismatches)})


def preview(client, cid, ft="EDI"):
    return client.get(CF, params={"finance_type": ft, "customer_id": cid})


def old_and_new(conn, old="Suresh", new="Suresh Fast Food", ft="EDI"):
    add_customer(conn, ft, 1, old, closed=True)
    add_customer(conn, ft, 2, new)


def seed_old(conn, name="Suresh", aliases=("Sures", "Suri"), mismatches=("Suresh K", "Sursh"), ft="EDI"):
    for a in aliases:
        add_mapping(conn, "alias", ft, name, a, cid=1, use_count=4)
    for m in mismatches:
        add_mapping(conn, "ai_mismatch", ft, name, m, cid=1, use_count=2)


# ── another name: a copy under the new name ───────────────────────────────────────────────────────────────
def test_a_new_name_gets_a_copy_pointing_at_the_new_account(client, conn):                          # CF-01
    old_and_new(conn)
    seed_old(conn)
    r = carry(client, 1, 2)
    assert r.status_code == 200, r.text
    assert r.json() == {"same_name": False, "alias": ["Sures", "Suri"], "ai_mismatch": ["Suresh K", "Sursh"], "skipped": {"alias": 0, "ai_mismatch": 0}}
    assert pointer(conn, "alias", "EDI", "Suresh Fast Food", "Sures") == (2, True)               # live: the open account uses it
    assert pointer(conn, "ai_mismatch", "EDI", "Suresh Fast Food", "Sursh") == (2, True)
    d = get(client, 2).json()                                                                      # and the popup of the new account shows them
    assert words(d, "aliases") == ["Sures", "Suri"] and words(d, "mismatches") == ["Suresh K", "Sursh"]
    assert all(x["use_count"] == 0 for x in d["aliases"] + d["mismatches"])                        # a new account starts with fresh usage


def test_the_closed_account_keeps_its_own_words_and_they_are_not_active(client, conn):             # CF-02
    old_and_new(conn)
    seed_old(conn)
    carry(client, 1, 2)
    assert pointer(conn, "alias", "EDI", "Suresh", "Sures") == (1, False)                          # closed: kept as history, never matched
    assert [r[:2] for r in rows(conn, "alias", "EDI")] == [("Suresh", "Sures"), ("Suresh", "Suri"), ("Suresh Fast Food", "Sures"), ("Suresh Fast Food", "Suri")]


def test_carrying_twice_adds_nothing_more(client, conn):                                            # CF-03
    old_and_new(conn)
    seed_old(conn)
    carry(client, 1, 2)
    again = carry(client, 1, 2).json()
    assert again["alias"] == [] and again["ai_mismatch"] == []
    assert len(rows(conn, "alias", "EDI")) == 4 and len(rows(conn, "ai_mismatch", "EDI")) == 4


def test_words_the_new_name_already_has_are_kept_and_not_reported(client, conn):                   # CF-03
    old_and_new(conn)
    seed_old(conn)
    add_mapping(conn, "alias", "EDI", "Suresh Fast Food", "Sures", cid=2, use_count=9)
    r = carry(client, 1, 2).json()
    assert r["alias"] == ["Suri"]
    assert get(client, 2).json()["aliases"][0]["use_count"] == 9                                    # the existing row is untouched


def test_the_per_customer_limits_still_apply(client, conn):                                         # CF-04
    old_and_new(conn)
    seed_old(conn, aliases=("a1", "a2", "a3"), mismatches=tuple(f"m{i}" for i in range(10)))
    add_mapping(conn, "alias", "EDI", "Suresh Fast Food", "x1", cid=2)
    add_mapping(conn, "alias", "EDI", "Suresh Fast Food", "x2", cid=2)
    r = carry(client, 1, 2).json()
    assert r["alias"] == ["a1"] and r["skipped"] == {"alias": 2, "ai_mismatch": 0}                  # room for 1 more of 3
    assert len(r["ai_mismatch"]) == 10
    d = get(client, 2).json()
    assert len(d["aliases"]) == 3 and len(d["mismatches"]) == 10


# ── the same name: nothing to copy ────────────────────────────────────────────────────────────────────────
def test_the_same_name_follows_by_itself_and_nothing_is_copied(client, conn):                       # CF-05
    add_customer(conn, "EDI", 1, "Suresh")                      # still open when the copy is created ...
    add_customer(conn, "EDI", 2, "Suresh")
    seed_old(conn)
    assert pointer(conn, "alias", "EDI", "Suresh", "Sures") == (1, True)                           # ... so the words stay with the old one
    set_closed(conn, "EDI", 1, True)                                                                # ... until it is closed
    assert pointer(conn, "alias", "EDI", "Suresh", "Sures") == (2, True)                            # then the open account has them
    r = carry(client, 1, 2).json()
    assert r["same_name"] is True and r["alias"] == [] and r["ai_mismatch"] == []
    assert len(rows(conn, "alias", "EDI")) == 2                                                     # no duplicate rows
    assert words(get(client, 2).json(), "aliases") == ["Sures", "Suri"]


# ── rules ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_only_from_a_closed_account_to_an_open_one(client, conn):                                   # CF-06
    add_customer(conn, "EDI", 1, "Suresh")                      # open
    add_customer(conn, "EDI", 2, "Other")                       # open
    add_customer(conn, "EDI", 3, "Done", closed=True)
    add_customer(conn, "EDI", 4, "Done Two", closed=True)
    seed_old(conn)
    assert carry(client, 1, 2).status_code == 409               # open -> open
    assert carry(client, 3, 4).status_code == 409               # closed -> closed
    assert carry(client, 2, 3).status_code == 409               # open -> closed
    assert carry(client, 1, 1).status_code == 400
    assert carry(client, 1, 99).status_code == 404 and carry(client, 99, 2).status_code == 404
    assert len(rows(conn, "alias", "EDI")) == 2                 # nothing written by the refusals


def test_a_new_account_without_a_name_and_a_bad_product_are_refused(client, conn):                  # CF-06
    add_customer(conn, "EDI", 1, "Suresh", closed=True)
    add_customer(conn, "EDI", 2, None)
    assert carry(client, 1, 2).status_code == 422
    assert carry(client, 1, 2, ft="LOAN").status_code == 422


def test_products_are_separate(client, conn):                                                       # CF-07
    old_and_new(conn)
    add_customer(conn, "IOP", 1, "Suresh", closed=True)
    add_customer(conn, "IOP", 2, "Suresh Fast Food")
    seed_old(conn, ft="EDI")
    add_mapping(conn, "alias", "IOP", "Suresh", "iop-only", cid=1)
    carry(client, 1, 2, ft="EDI")
    assert [r[:2] for r in rows(conn, "alias", "IOP")] == [("Suresh", "iop-only")]                  # IOP untouched
    carry(client, 1, 2, ft="IOP")
    assert [r[:2] for r in rows(conn, "alias", "IOP")] == [("Suresh", "iop-only"), ("Suresh Fast Food", "iop-only")]
    assert "iop-only" not in [r[1] for r in rows(conn, "alias", "EDI")]


# ── undo ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_undo_removes_exactly_what_was_added(client, conn):                                         # CF-08
    old_and_new(conn)
    seed_old(conn)
    add_mapping(conn, "alias", "EDI", "Suresh Fast Food", "kept", cid=2)                             # the new account's own word
    r = carry(client, 1, 2).json()
    u = undo(client, 2, r["alias"], r["ai_mismatch"])
    assert u.status_code == 200 and u.json() == {"removed": {"alias": 2, "ai_mismatch": 2}}
    assert [x[:2] for x in rows(conn, "alias", "EDI")] == [("Suresh", "Sures"), ("Suresh", "Suri"), ("Suresh Fast Food", "kept")]
    assert len(rows(conn, "ai_mismatch", "EDI")) == 2                                               # the closed account's own words stay


def test_undo_is_harmless_when_repeated_or_empty(client, conn):                                     # CF-08
    old_and_new(conn)
    seed_old(conn)
    r = carry(client, 1, 2).json()
    undo(client, 2, r["alias"], r["ai_mismatch"])
    again = undo(client, 2, r["alias"], r["ai_mismatch"]).json()
    assert again == {"removed": {"alias": 0, "ai_mismatch": 0}}
    assert undo(client, 2).json() == {"removed": {"alias": 0, "ai_mismatch": 0}}
    assert undo(client, 99).status_code == 404


def test_undo_never_touches_another_accounts_words(client, conn):                                   # CF-08
    old_and_new(conn)
    seed_old(conn)
    undo(client, 2, ["Sures"], ["Sursh"])                       # the new account has none of them: the closed one's rows must survive
    assert len(rows(conn, "alias", "EDI")) == 2 and len(rows(conn, "ai_mismatch", "EDI")) == 2


# ── preview (for the form) ────────────────────────────────────────────────────────────────────────────────
def test_preview_lists_the_words_of_a_closed_or_an_open_account(client, conn):                      # CF-01
    old_and_new(conn)
    seed_old(conn)
    closed = preview(client, 1).json()
    assert closed["customer"] == {"id": 1, "name": "Suresh", "is_closed": True, "finance_type": "EDI"}
    assert closed["aliases"] == ["Sures", "Suri"] and closed["mismatches"] == ["Suresh K", "Sursh"]
    assert get(client, 1).status_code == 409                    # (the popup refuses a closed account — that is why preview exists)
    assert preview(client, 2).json()["aliases"] == []
    assert preview(client, 99).status_code == 404
    put(client, 2, aliases=["Own"])
    assert preview(client, 2).json()["aliases"] == ["Own"]


def test_preview_of_an_account_without_a_name_is_empty(client, conn):
    add_customer(conn, "EDI", 5, None, closed=True)
    d = preview(client, 5).json()
    assert d["aliases"] == [] and d["mismatches"] == []


# ── choosing the kinds (the form has one switch per kind) ─────────────────────────────────────────────────
def test_only_the_chosen_kinds_are_carried(client, conn):                                           # CF-09
    old_and_new(conn)
    seed_old(conn)
    r = client.post(CF, json={"finance_type": "EDI", "from_customer_id": 1, "to_customer_id": 2, "aliases": True, "mismatches": False}).json()
    assert r["alias"] == ["Sures", "Suri"] and r["ai_mismatch"] == []
    assert len(rows(conn, "ai_mismatch", "EDI")) == 2                                               # nothing copied for mismatches
    r = client.post(CF, json={"finance_type": "EDI", "from_customer_id": 1, "to_customer_id": 2, "aliases": False, "mismatches": True}).json()
    assert r["alias"] == [] and r["ai_mismatch"] == ["Suresh K", "Sursh"]
    none = client.post(CF, json={"finance_type": "EDI", "from_customer_id": 1, "to_customer_id": 2, "aliases": False, "mismatches": False})
    assert none.status_code == 200 and none.json()["alias"] == [] and none.json()["ai_mismatch"] == []
