"""
Pure matching / ranking rules (no database).   R-03 … R-05, R-08, R-10, R-16 (T-03, T-04, T-07, T-09, T-15)

Reference scores from the existing fuzzy module (checked by hand, asserted below so a future change to the scorer is noticed):
    "Selvam" vs "selvam "      exact (module's own definition: equal after lower-case / trim / collapse)
    "Selvam" vs "Selvamm"      100.0  (substring) — NOT exact
    "Selvam" vs "Selvm"        90.9   (>= 0.90, auto-assign)
    "Lakshmi" vs "Laxmi"       85.0   (shown, but below 0.90)
    "Raja" vs "Rani"           66.7   (shown, below 0.90)
    "Selvam" vs "Kannan"       25.0   (hidden)
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import mapping_matcher as mm
from app.services.mapping_matcher import MappingRow
from app.utils.name_matching import _normalize, get_similar_score

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
_ids = iter(range(1, 10_000))


def row(kind, cid, cname, word, use=0, last=None, created=None, ftype="EDI"):
    return MappingRow(id=next(_ids), kind=kind, finance_type=ftype, customer_id=cid, customer_name=cname, word=word,
                      use_count=use, last_used_at=last, created_at=created)


def raw(a, b):
    return get_similar_score(a, [{"customer_id": 1, "name": _normalize(b), "display_name": b}])[0]["score"]


def test_reference_scores_of_the_existing_scorer_are_what_these_tests_assume():
    assert raw("Selvam", "Selvamm") == 100.0
    assert 90.0 <= raw("Selvam", "Selvm") < 100.0
    assert 60.0 <= raw("Lakshmi", "Laxmi") < 90.0
    assert 60.0 <= raw("Raja", "Rani") < 90.0
    assert raw("Selvam", "Kannan") < 60.0


# ── T-04 threshold, exact first ────────────────────────────────────────────────────────────────────────────
def test_exact_match_ranks_above_a_non_exact_100():
    rows = [row("ai_mismatch", 1, "Cust One", "Selvamm"),            # 100 but not exact
            row("ai_mismatch", 2, "Cust Two", "selvam ")]            # exact
    per = mm.candidates_by_kind("Selvam", rows, NOW)
    assert [c.customer_id for c in per["ai_mismatch"]] == [2, 1]
    assert per["ai_mismatch"][0].exact and not per["ai_mismatch"][1].exact


def test_candidate_scores_are_on_a_0_to_1_scale_and_use_the_existing_scorer():
    per = mm.candidates_by_kind("Selvam", [row("alias", 1, "C", "Selvm")], NOW)
    assert per["alias"][0].score == pytest.approx(raw("Selvam", "Selvm") / 100.0)


def test_below_min_score_is_hidden_but_exact_is_always_kept():
    rows = [row("alias", 1, "A", "Qwerty"), row("alias", 2, "B", "Raja")]
    assert raw("Rani", "Qwerty") < 60.0 <= raw("Rani", "Raja")
    assert [c.customer_id for c in mm.candidates_by_kind("Selvam", rows, NOW)["alias"]] == []
    shown = mm.candidates_by_kind("Rani", rows, NOW)["alias"]                 # "Raja" ~ 66.7: shown, "Qwerty" hidden
    assert [c.customer_id for c in shown] == [2]
    # exact is kept even if a caller raises the bar above its score
    kept = mm.candidates_by_kind("qwerty", rows, NOW, min_score=1.01)["alias"]
    assert [c.customer_id for c in kept] == [1] and kept[0].exact


def test_auto_assign_needs_the_existing_0_90_threshold():
    below = [row("ai_mismatch", 1, "A", "Laxmi")]                              # 85
    assert mm.build_suggestions("Lakshmi", below, NOW)[1] is None
    above = [row("ai_mismatch", 1, "A", "Selvm")]                              # 90.9
    assert mm.build_suggestions("Selvam", above, NOW)[1].customer_id == 1
    assert mm.HIGH_CONFIDENCE == 0.90


def test_usage_boost_never_lifts_a_weak_match_over_the_threshold():
    rows = [row("ai_mismatch", 1, "A", "Laxmi", use=500, last=NOW)]            # raw 85, huge usage
    cands, auto = mm.build_suggestions("Lakshmi", rows, NOW)
    assert auto is None                                                        # decided on the RAW score
    assert cands[0].rank_score == pytest.approx(cands[0].score + mm.USAGE_BOOST_CAP)   # boost is applied for ordering…
    assert cands[0].rank_score >= mm.HIGH_CONFIDENCE and cands[0].score < mm.HIGH_CONFIDENCE   # …yet it would cross 0.90


# ── T-03 cascade order ─────────────────────────────────────────────────────────────────────────────────────
def test_mismatch_is_consulted_before_alias_even_if_the_alias_scores_higher():
    rows = [row("alias", 10, "Alias Owner", "Selvamm"),                        # 100
            row("ai_mismatch", 20, "Mismatch Owner", "Selvm")]                 # 90.9
    _cands, auto = mm.build_suggestions("Selvam", rows, NOW)
    assert (auto.customer_id, auto.source) == (20, "ai_mismatch")


def test_alias_is_used_when_no_mismatch_reaches_the_threshold():
    rows = [row("ai_mismatch", 20, "M", "Laxmi"),                              # 85 -> not enough
            row("alias", 10, "A", "Lakshmi")]                                  # exact
    _cands, auto = mm.build_suggestions("Lakshmi", rows, NOW)
    assert (auto.customer_id, auto.source) == (10, "alias")


def test_nothing_matches_means_no_auto_assign_and_empty_column():
    cands, auto = mm.build_suggestions("Zzzzzz", [row("alias", 1, "A", "Kannan")], NOW)
    assert cands == [] and auto is None
    assert mm.build_suggestions("", [row("alias", 1, "A", "Kannan")], NOW) == ([], None)


# ── T-07 same word on two customers ────────────────────────────────────────────────────────────────────────
def test_same_word_mapped_to_two_customers_lists_both():
    rows = [row("ai_mismatch", 1, "Selvam A", "Selvm"), row("ai_mismatch", 2, "Selvam B", "Selvm")]
    cands, auto = mm.build_suggestions("Selvam", rows, NOW)
    assert sorted(c.customer_id for c in cands) == [1, 2]
    assert auto is not None                                                    # one is auto-picked, both are listed


def test_tie_is_broken_by_usage():
    rows = [row("ai_mismatch", 1, "Rarely", "Selvm", use=0), row("ai_mismatch", 2, "Often", "Selvm", use=4, last=NOW)]
    cands, auto = mm.build_suggestions("Selvam", rows, NOW)
    assert [c.customer_id for c in cands] == [2, 1]
    assert auto.customer_id == 2


# ── T-09 column-2 ordering and merging ─────────────────────────────────────────────────────────────────────
def test_column_two_merges_one_entry_per_customer_and_remembers_the_other_words():
    rows = [row("ai_mismatch", 1, "Same", "Selvm"), row("alias", 1, "Same", "Selvam"), row("alias", 1, "Same", "Selvamm")]
    cands, _ = mm.build_suggestions("Selvam", rows, NOW)
    assert len(cands) == 1
    c = cands[0]
    assert c.exact and c.matched_word == "Selvam"                              # best entry wins (exact)
    assert {a["word"] for a in c.also} == {"Selvm", "Selvamm"}                 # nothing lost while merging


def test_column_two_order_exact_then_score_plus_usage():
    rows = [row("alias", 1, "Low score", "Raja"),                              # 66.7
            row("alias", 2, "High score", "Rani", use=0),
            row("ai_mismatch", 3, "Exact", "rani"),
            row("ai_mismatch", 4, "Used a lot", "Rana", use=5, last=NOW)]
    cands, _ = mm.build_suggestions("Rani", rows, NOW)
    assert [c.exact for c in cands][:2] == [True, True]                         # both exacts come before anything else
    assert {c.customer_id for c in cands[:2]} == {2, 3}
    assert all(not c.exact for c in cands[2:])
    rest = [c for c in cands[2:]]
    assert [c.rank_score for c in rest] == sorted((c.rank_score for c in rest), reverse=True)   # then score + usage


def test_column_two_is_limited():
    rows = [row("alias", i, f"C{i}", f"Rani{'a' * i}") for i in range(1, 12)]
    cands, _ = mm.build_suggestions("Rani", rows, NOW)
    assert len(cands) == mm.MAX_CANDIDATES


def test_to_dict_shape_for_the_frontend():
    cands, _ = mm.build_suggestions("Selvam", [row("alias", 7, "Cust", "Selvm", use=2)], NOW)
    d = cands[0].to_dict()
    assert set(d) == {"id", "name", "score", "source", "mapping_id", "matched_word", "exact", "use_count", "also"}
    assert d["id"] == 7 and d["name"] == "Cust" and d["source"] == "alias" and d["use_count"] == 2


# ── usage boost / stale ────────────────────────────────────────────────────────────────────────────────────
def test_usage_boost_is_capped_and_halved_when_not_recent():
    assert mm.usage_boost(0, None, NOW) == 0
    assert mm.usage_boost(3, NOW, NOW) == pytest.approx(0.03)
    assert mm.usage_boost(100, NOW, NOW) == pytest.approx(mm.USAGE_BOOST_CAP)
    old = NOW - timedelta(days=mm.RECENT_DAYS + 1)
    assert mm.usage_boost(100, old, NOW) == pytest.approx(mm.USAGE_BOOST_CAP / 2)
    assert mm.usage_boost(100, None, NOW) == pytest.approx(mm.USAGE_BOOST_CAP / 2)


@pytest.mark.parametrize("use,last_days,created_days,stale", [
    (0, None, 10, False),                      # new, never used yet
    (0, None, 91, True),                       # never used in > 90 days since creation
    (0, None, 89, False),
    (5, 179, 400, False),                      # used recently enough
    (5, 181, 400, True),                       # last used > 180 days ago
    (1, 0, 400, False),
])
def test_stale_flag(use, last_days, created_days, stale):
    last = None if last_days is None else NOW - timedelta(days=last_days)
    assert mm.is_stale(use, last, NOW - timedelta(days=created_days), NOW) is stale


def test_naive_datetimes_from_the_database_are_handled():
    naive = datetime(2026, 1, 1)
    assert mm.is_stale(0, None, naive, NOW) is True
    assert mm.usage_boost(1, naive, NOW) == pytest.approx(0.005)
