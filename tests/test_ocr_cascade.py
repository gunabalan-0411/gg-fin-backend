"""
OCR extract cascade + submit usage through the real routes (only the Gemini call is replaced).
R-01 … R-05, R-07, R-08, R-10, R-18/19, R-30, R-31   (T-01, T-02, T-03, T-04, T-06, T-18, T-34, T-35)
"""
import pytest
from sqlalchemy import text

from app.services import name_mapping_service as svc
from app.services import ocr_service
from tests.helpers import add_customer, add_mapping, set_closed

EXTRACT = "/api/ocr/extract"
SUBMIT = "/api/ocr/submit"


def rec(name, product="EDI", **kw):
    return {"collection_date": "01-10-2026", "customer_name": name, "product_type": product, "payment_mode": "CASH",
            "online_marker": None, "amount": 500, "raw_text": name, "confidence_score": 0.9, "page_number": 1,
            "notes": "", **kw}


@pytest.fixture()
def extract(client, monkeypatch):
    def run(*records):
        monkeypatch.setattr(ocr_service, "extract_page", lambda sid, idx, model="m": ("B64", [dict(r) for r in records]))
        r = client.post(EXTRACT, json={"session_id": "s", "page_index": 0, "model": "m"})
        assert r.status_code == 200, r.text
        return r.json()["records"]
    return run


@pytest.fixture()
def world(conn):
    """EDI: 1 Selvam, 2 Kannan, 3 Closed Guy(closed).  IOP: 11 Selvam."""
    add_customer(conn, "EDI", 1, "Selvam")
    add_customer(conn, "EDI", 2, "Kannan")
    add_customer(conn, "EDI", 3, "Closed Guy", closed=True)
    add_customer(conn, "IOP", 11, "Selvam")


def old_behaviour(session, name, product):
    """The matching code exactly as it was before this feature (kept here as the reference for T-01)."""
    from app.models.customer import EdiCustomer, IopCustomer
    from app.models.mapping import EdiNameMap, IopNameMap
    from sqlmodel import select
    model, nm = (IopCustomer, IopNameMap) if product == "IOP" else (EdiCustomer, EdiNameMap)
    active = {c.customer_id for c in session.exec(select(model).where(model.is_closed == False)).all()}  # noqa: E712
    pool = [{"id": r.customer_id, "name": r.customer_name_en} for r in session.exec(select(nm)).all()
            if r.customer_id in active and r.customer_name_en]
    matches = ocr_service.fuzzy_match(name, pool)
    best = matches[0] if matches else None
    return matches, (best["id"] if best and best["score"] >= 0.90 else None)


# ── T-01 step 1 is unchanged ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,product", [("Selvam", "EDI"), ("selvam", "EDI"), ("Selvm", "EDI"), ("Kanan", "EDI"),
                                           ("Selvan", "IOP"), ("Nobody Atall", "EDI"), ("Closed Guy", "EDI"), ("", "EDI")])
def test_step_one_output_is_identical_to_the_previous_behaviour(extract, world, session, name, product):
    got = extract(rec(name, product))[0]
    want_matches, want_id = old_behaviour(session, name, product)
    assert got["customer_suggestions"] == want_matches
    assert got["customer_id"] == want_id
    assert got["mapping_suggestions"] == [] and got["mapping_ref"] is None            # nothing mapped yet
    assert got["match_source"] == ("fuzzy" if want_id else None)


def test_extract_response_keeps_all_original_fields(extract, world):
    got = extract(rec("Selvam"))[0]
    for k in ("collection_date", "customer_name", "product_type", "payment_mode", "amount", "raw_text",
              "confidence_score", "customer_suggestions", "customer_id"):
        assert k in got
    assert got["customer_name"] == "Selvam"                                           # extracted text is never overwritten


# ── T-02 high confidence stops the cascade ─────────────────────────────────────────────────────────────────
def test_high_confidence_fuzzy_stops_the_cascade_but_column_two_still_offers_the_mapping(extract, world, conn):
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Selvam", cid=2)                # exact word for ANOTHER customer
    got = extract(rec("Selvam"))[0]
    assert got["customer_id"] == 1 and got["match_source"] == "fuzzy" and got["mapping_ref"] is None    # R-02
    assert [c["id"] for c in got["mapping_suggestions"]] == [2]                       # R-10: still listed for the user
    assert got["mapping_suggestions"][0]["exact"] is True


# ── T-03 mismatch first, then alias ────────────────────────────────────────────────────────────────────────
def test_low_confidence_uses_the_ai_mismatch_mapping(extract, world, conn, session):
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Thiru", cid=2)
    got = extract(rec("Thiru"))[0]
    assert got["customer_id"] == 2 and got["match_source"] == "ai_mismatch"
    assert got["matched_customer_name"] == "Kannan"
    assert got["mapping_ref"] == {"kind": "ai_mismatch", "id": got["mapping_suggestions"][0]["mapping_id"]}
    assert got["customer_suggestions"] == old_behaviour(session, "Thiru", "EDI")[0]   # column 1 stays the plain fuzzy result


def test_low_confidence_falls_back_to_the_alias_mapping(extract, world, conn):
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Anbarasan", cid=2)            # "Anbu" ~ 85.7 < 0.90 -> not enough
    add_mapping(conn, "alias", "EDI", "Selvam", "Anbu", cid=1)
    got = extract(rec("Anbu"))[0]
    assert got["customer_id"] == 1 and got["match_source"] == "alias"
    assert got["mapping_ref"]["kind"] == "alias"


def test_mismatch_wins_over_alias_when_both_qualify(extract, world, conn):
    add_mapping(conn, "alias", "EDI", "Selvam", "Thirumalai", cid=1)                 # exact alias for customer 1 (100)
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Thirumalay", cid=2)           # strong but not exact mismatch (94.7)
    got = extract(rec("Thirumalai"))[0]
    assert got["customer_id"] == 2 and got["match_source"] == "ai_mismatch"          # mismatch is consulted first
    assert sorted(c["id"] for c in got["mapping_suggestions"]) == [1, 2]             # both visible in column 2


def test_nothing_qualifies_leaves_the_row_unassigned(extract, world, conn):
    add_mapping(conn, "alias", "EDI", "Kannan", "Laxmi", cid=2)                      # "Lakshmi" ~ 85 -> shown, not assigned
    got = extract(rec("Lakshmi"))[0]
    assert got["customer_id"] is None and got["match_source"] is None and got["mapping_ref"] is None
    assert [c["id"] for c in got["mapping_suggestions"]] == [2]                       # but offered in column 2


# ── T-04 threshold / exact order at endpoint level ─────────────────────────────────────────────────────────
def test_column_two_lists_exact_first_then_by_score_with_all_fields(extract, world, conn):
    add_mapping(conn, "ai_mismatch", "EDI", "Selvam", "Thirux", cid=1)
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "thiru", cid=2)                # exact (case-insensitive like the scorer)
    got = extract(rec("Thiru"))[0]
    sug = got["mapping_suggestions"]
    assert [c["id"] for c in sug] == [2, 1] and sug[0]["exact"] and not sug[1]["exact"]
    assert set(sug[0]) == {"id", "name", "score", "source", "mapping_id", "matched_word", "exact", "use_count", "also"}


# ── T-06 inactive customers never used ─────────────────────────────────────────────────────────────────────
def test_mappings_of_closed_or_missing_customers_are_never_used_or_shown(extract, world, conn):
    add_mapping(conn, "ai_mismatch", "EDI", "Closed Guy", "Thiru", cid=3)           # customer 3 is closed
    add_mapping(conn, "ai_mismatch", "EDI", "Gone Away", "Thiru", cid=77)            # no such customer at all
    got = extract(rec("Thiru"))[0]
    assert got["customer_id"] is None and got["mapping_suggestions"] == []
    set_closed(conn, "EDI", 3, False)                                                # account re-opened -> usable again
    got = extract(rec("Thiru"))[0]
    assert got["customer_id"] == 3 and got["match_source"] == "ai_mismatch"


def test_edi_and_iop_mappings_stay_separate(extract, world, conn):
    add_mapping(conn, "ai_mismatch", "IOP", "Selvam", "Thiru", cid=11)
    assert extract(rec("Thiru", "EDI"))[0]["customer_id"] is None
    got = extract(rec("Thiru", "IOP"))[0]
    assert got["customer_id"] == 11


# ── T-07 two customers, same word ──────────────────────────────────────────────────────────────────────────
def test_same_word_on_two_customers_lists_both_in_column_two(extract, world, conn):
    add_mapping(conn, "ai_mismatch", "EDI", "Selvam", "Thiru", cid=1)
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Thiru", cid=2)
    got = extract(rec("Thiru"))[0]
    assert sorted(c["id"] for c in got["mapping_suggestions"]) == [1, 2]
    assert got["customer_id"] in (1, 2)


# ── T-18 ongoing collection ────────────────────────────────────────────────────────────────────────────────
def test_a_mapping_saved_through_the_popup_is_used_by_the_next_extract(client, extract, world):
    assert extract(rec("Thiru"))[0]["customer_id"] is None
    r = client.put("/api/name-mappings/customer", json={"finance_type": "EDI", "customer_id": 2, "aliases": [],
                                                       "mismatches": ["Thiru"], "resolutions": []})
    assert r.status_code == 200
    got = extract(rec("Thiru"))[0]
    assert got["customer_id"] == 2 and got["match_source"] == "ai_mismatch"
    # …and so is one added by "Remember this"
    client.post("/api/name-mappings/remember", json={"finance_type": "EDI", "customer_id": 1, "word": "Selwam Kumarr"})
    assert extract(rec("Selwam Kumarr"))[0]["customer_id"] == 1


def test_extract_degrades_gracefully_with_a_visible_warning_if_mappings_cannot_be_read(client, world, monkeypatch):
    def boom(session, memory_limit=None):
        raise RuntimeError("table missing")
    monkeypatch.setattr(svc, "MappingIndex", boom)
    monkeypatch.setattr(ocr_service, "extract_page", lambda sid, idx, model="m": ("B64", [rec("Selvam")]))
    r = client.post(EXTRACT, json={"session_id": "s", "page_index": 0, "model": "m"})
    assert r.status_code == 200
    body = r.json()
    assert body["records"][0]["customer_id"] == 1 and body["records"][0]["mapping_suggestions"] == []
    assert "unavailable" in body["mapping_warning"]


# ── T-34 usage is counted only for saved rows ──────────────────────────────────────────────────────────────
def usage(conn, kind="ai_mismatch"):
    t = "tbl_customer_ai_mismatch" if kind == "ai_mismatch" else "tbl_customer_alias"
    return conn.execute(text(f"SELECT id, use_count, last_used_at IS NOT NULL FROM {t} ORDER BY id")).all()


def submit_row(cid, ref=None, amount=500, product="EDI", date="01-10-2026"):
    return {"collection_date": date, "customer_name": "x", "customer_id": cid, "product_type": product,
            "payment_mode": "CASH", "is_paid": True, "amount": amount, **({"mapping_ref": ref} if ref else {})}


def test_usage_counts_only_when_the_row_is_saved(client, extract, world, conn):
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Thiru", cid=2)
    got = extract(rec("Thiru"))[0]
    assert usage(conn) == [(1, 0, False)]                                             # extract does not count
    client.post("/api/name-mappings/suggest", json={"finance_type": "EDI", "query": "Thiru"})
    assert usage(conn) == [(1, 0, False)]                                             # suggest does not count either

    ref = got["mapping_ref"]
    r = client.post(SUBMIT, json={"records": [submit_row(2, ref)]})
    assert r.status_code == 200 and r.json() == {"submitted": 1, "mapping_usage": 1}
    assert usage(conn) == [(1, 1, True)]

    # rows that are skipped (no amount / no customer / bad date) are not counted
    r = client.post(SUBMIT, json={"records": [submit_row(2, ref, amount=0), submit_row(None, ref),
                                              submit_row(2, ref, date="not-a-date")]})
    assert r.json() == {"submitted": 0, "mapping_usage": 0}
    assert usage(conn) == [(1, 1, True)]


def test_usage_counts_each_saved_row_and_ignores_a_ref_that_does_not_match_the_customer(client, world, conn):
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Thiru", cid=2)
    ref = {"kind": "ai_mismatch", "id": usage(conn)[0][0]}
    r = client.post(SUBMIT, json={"records": [submit_row(2, ref, date="01-10-2026"), submit_row(2, ref, date="02-10-2026"),
                                              submit_row(1, ref, date="03-10-2026")]})     # last: user switched customer
    assert r.json() == {"submitted": 3, "mapping_usage": 2}
    assert usage(conn)[0][1] == 2


def test_usage_ignores_unknown_refs_and_never_fails_the_submit(client, world, conn):
    r = client.post(SUBMIT, json={"records": [submit_row(2, {"kind": "ai_mismatch", "id": 99999})]})
    assert r.status_code == 200 and r.json() == {"submitted": 1, "mapping_usage": 0}
    assert client.post(SUBMIT, json={"records": [submit_row(2, {"kind": "bogus", "id": 1})]}).status_code == 422


def test_alias_usage_and_ranking_effect(client, extract, world, conn):
    add_mapping(conn, "alias", "EDI", "Selvam", "Thirum", cid=1)
    add_mapping(conn, "alias", "EDI", "Kannan", "Thirum", cid=2)
    first = extract(rec("Thiru"))[0]["mapping_suggestions"]
    assert len(first) == 2
    ref_for_2 = next(c for c in first if c["id"] == 2)
    for _ in range(3):
        client.post(SUBMIT, json={"records": [submit_row(2, {"kind": "alias", "id": ref_for_2["mapping_id"]})]})
    after = extract(rec("Thiru"))[0]
    assert [c["id"] for c in after["mapping_suggestions"]][0] == 2                    # the used one now ranks first
    assert after["mapping_suggestions"][0]["use_count"] == 3


# ── T-35 large-table fallback ──────────────────────────────────────────────────────────────────────────────
def test_beyond_the_row_limit_matching_is_done_by_the_database_and_agrees(client, session, world, conn, monkeypatch):
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Thiru", cid=2)
    add_mapping(conn, "alias", "EDI", "Selvam", "Thirumalai", cid=1)
    for i in range(40):                                                               # filler rows for other customers
        add_mapping(conn, "ai_mismatch", "EDI", "Kannan", f"filler{i}", cid=2)

    small = svc.MappingIndex(session, memory_limit=10_000)
    big = svc.MappingIndex(session, memory_limit=5)
    assert small.in_memory is True and big.in_memory is False                        # R-31: switches to DB-side
    for name in ("Thiru", "Thirumalai", "thiru", "Thirumalay"):
        a_c, a_auto = small.suggest("EDI", name)
        b_c, b_auto = big.suggest("EDI", name)
        assert [(c.customer_id, c.matched_word) for c in b_c] == [(c.customer_id, c.matched_word) for c in a_c], name
        assert (a_auto and a_auto.customer_id) == (b_auto and b_auto.customer_id), name
    # the database path only ever returns a candidate subset, not the whole table
    assert len(big.rows_for("EDI", "Thiru")) < 42


def test_the_database_path_is_used_by_the_real_endpoint_once_the_limit_is_crossed(client, extract, world, conn, monkeypatch):
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Thiru", cid=2)
    monkeypatch.setattr(svc, "MEMORY_LIMIT", 0)
    got = extract(rec("Thiru"))[0]
    assert got["customer_id"] == 2 and got["match_source"] == "ai_mismatch"


def test_database_prefilter_survives_awkward_characters(session, world, conn):
    add_mapping(conn, "alias", "EDI", "Kannan", "100% real_name", cid=2)
    add_mapping(conn, "alias", "EDI", "Kannan", "Plain", cid=2)
    idx = svc.MappingIndex(session, memory_limit=0)
    assert idx.in_memory is False
    assert idx.rows_for("EDI", "100% real_name")[0].word == "100% real_name"
    # LIKE wildcards in the typed text are literals, not wildcards: "%" / "_" must not match unrelated words like "Plain"
    for q in ("%", "_", "%%", "_%"):
        assert "Plain" not in [r.word for r in idx.rows_for("EDI", q)], q


def test_database_path_without_the_trigram_extension_still_finds_obvious_candidates(session, world, conn, monkeypatch):
    monkeypatch.setattr(svc, "_has_trigram", lambda s: False)
    add_mapping(conn, "ai_mismatch", "EDI", "Kannan", "Thirumalai", cid=2)
    idx = svc.MappingIndex(session, memory_limit=0)
    assert [r.word for r in idx.rows_for("EDI", "Thirumalai Kumar")] == ["Thirumalai"]       # shared token
    assert [r.word for r in idx.rows_for("EDI", "Thirumalai")] == ["Thirumalai"]             # equal
    assert [r.word for r in idx.rows_for("EDI", "xx Thirumalai yy")] == ["Thirumalai"]       # word inside the name
