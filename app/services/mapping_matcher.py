"""
Pure matching / ranking logic for the alias and AI-mismatch mapping tables (no database access).

Reuses the existing fuzzy scorer (`app.utils.name_matching.get_similar_score`) and the existing OCR confidence rule
(score >= 0.90 auto-assigns). Everything tunable lives in the constants block below.

Cascade (see Documentation.md §6):
    1. fuzzy match against active customer names           (unchanged, lives in ocr_service.fuzzy_match)
    2. if low  -> AI-mismatch words                         (this module: pick_auto_assign, kind="ai_mismatch")
    3. if low  -> 2nd-alias words                           (this module: pick_auto_assign, kind="alias")
Column 2 of the dropdown always shows the merged, ranked candidates from both tables (build_suggestions).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from app.utils.name_matching import _normalize, get_similar_score

# ── Tunables ──────────────────────────────────────────────────────────────────────────────────────────────
HIGH_CONFIDENCE = 0.90        # same threshold the OCR page already uses to auto-assign a customer
MIN_SHOWN_SCORE = 0.60        # column 2 lists candidates from here up (exact matches are always listed)
MAX_CANDIDATES = 5            # rows shown in column 2

ALIAS_LIMIT = 3               # 2nd alias names per customer
MISMATCH_LIMIT = 10           # AI-mismatch names per customer

USAGE_BOOST_PER_USE = 0.01    # ranking nudge per saved use ...
USAGE_BOOST_CAP = 0.05        # ... never more than this
RECENT_DAYS = 90              # a boost is halved when the word has not been used within this many days
STALE_NEVER_USED_DAYS = 90    # "stale": never used and older than this
STALE_LAST_USED_DAYS = 180    # "stale": last used longer ago than this

KINDS = ("ai_mismatch", "alias")          # cascade order: mismatch is consulted before alias
KIND_LIMIT = {"alias": ALIAS_LIMIT, "ai_mismatch": MISMATCH_LIMIT}
_KIND_RANK = {k: i for i, k in enumerate(KINDS)}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Data ──────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class MappingRow:
    """One ACTIVE mapping row (its customer is open)."""
    id: int
    kind: str                      # "alias" | "ai_mismatch"
    finance_type: str              # "EDI" | "IOP"
    customer_id: int
    customer_name: str
    word: str
    use_count: int = 0
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class Candidate:
    customer_id: int
    customer_name: str
    source: str                    # kind of the best entry
    mapping_id: int
    matched_word: str
    score: float                   # raw fuzzy score 0..1 — the only value used for auto-assign decisions
    exact: bool
    rank_score: float              # score + capped usage boost — used for ordering only
    use_count: int = 0
    also: list[dict] = field(default_factory=list)   # other words of the same customer that matched

    def to_dict(self) -> dict:
        return {
            "id": self.customer_id,
            "name": self.customer_name,
            "score": round(self.score, 2),
            "source": self.source,
            "mapping_id": self.mapping_id,
            "matched_word": self.matched_word,
            "exact": self.exact,
            "use_count": self.use_count,
            "also": self.also,
        }


# ── Usage helpers ─────────────────────────────────────────────────────────────────────────────────────────
def usage_boost(use_count: int, last_used_at: Optional[datetime], now: Optional[datetime] = None) -> float:
    """Small, capped ranking bonus for words the user actually relies on (never lifts anything over HIGH_CONFIDENCE)."""
    now = now or _now()
    boost = min(USAGE_BOOST_CAP, USAGE_BOOST_PER_USE * max(0, use_count))
    last = _aware(last_used_at)
    if last is None or now - last > timedelta(days=RECENT_DAYS):
        boost *= 0.5
    return boost


def is_stale(use_count: int, last_used_at: Optional[datetime], created_at: Optional[datetime],
             now: Optional[datetime] = None) -> bool:
    """Good candidates to replace when a customer is at the limit."""
    now = now or _now()
    last, created = _aware(last_used_at), _aware(created_at)
    if last is None:
        return created is not None and now - created > timedelta(days=STALE_NEVER_USED_DAYS)
    return now - last > timedelta(days=STALE_LAST_USED_DAYS)


# ── Scoring ───────────────────────────────────────────────────────────────────────────────────────────────
def _score_rows(name: str, rows: list[MappingRow], now: datetime) -> list[Candidate]:
    """Score `name` against each row's WORD with the existing fuzzy module. Returns one Candidate per row."""
    if not name or not name.strip() or not rows:
        return []
    cands = [{"customer_id": i, "name": _normalize(r.word), "display_name": r.word} for i, r in enumerate(rows)]
    spoken = _normalize(name)
    out: list[Candidate] = []
    for s in get_similar_score(name, cands):
        r = rows[s["customer_id"]]
        score = s["score"] / 100.0
        out.append(Candidate(
            customer_id=r.customer_id, customer_name=r.customer_name, source=r.kind, mapping_id=r.id,
            matched_word=r.word, score=score, exact=(spoken == _normalize(r.word)),
            rank_score=score + usage_boost(r.use_count, r.last_used_at, now), use_count=r.use_count,
        ))
    return out


def _order_key(c: Candidate):
    # exact first, then score(+usage); ties: mismatch before alias, then stable by name/word
    return (not c.exact, -c.rank_score, _KIND_RANK.get(c.source, 9), c.customer_name, c.matched_word)


def _shown(c: Candidate, min_score: float) -> bool:
    return c.exact or c.score >= min_score


# ── Public API ────────────────────────────────────────────────────────────────────────────────────────────
def candidates_by_kind(name: str, rows: Iterable[MappingRow], now: Optional[datetime] = None,
                       min_score: float = MIN_SHOWN_SCORE) -> dict[str, list[Candidate]]:
    """Per-kind candidate lists (ordered: exact first, then score + usage boost)."""
    now = now or _now()
    rows = list(rows)
    out: dict[str, list[Candidate]] = {}
    for kind in KINDS:
        scored = _score_rows(name, [r for r in rows if r.kind == kind], now)
        out[kind] = sorted((c for c in scored if _shown(c, min_score)), key=_order_key)
    return out


def merge_for_display(per_kind: dict[str, list[Candidate]], limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """
    Column 2: one entry per customer (the best of its alias / mismatch words), ordered exact-first then by
    score + usage boost. The same word mapped to two customers yields two entries.
    """
    best: dict[int, Candidate] = {}
    for kind in KINDS:
        for c in per_kind.get(kind, []):
            cur = best.get(c.customer_id)
            if cur is None:
                best[c.customer_id] = Candidate(**{**c.__dict__, "also": []})
            else:
                keep, other = (c, cur) if _order_key(c) < _order_key(cur) else (cur, c)
                # `cur.also` holds the losers collected so far (a fresh `c.also` is always empty)
                merged = Candidate(**{**keep.__dict__, "also": list(cur.also)})
                merged.also.append({"source": other.source, "word": other.matched_word, "mapping_id": other.mapping_id})
                best[c.customer_id] = merged
    return sorted(best.values(), key=_order_key)[:limit]


def pick_auto_assign(per_kind: dict[str, list[Candidate]], threshold: float = HIGH_CONFIDENCE) -> Optional[Candidate]:
    """
    Cascade step 2 then 3: the first AI-mismatch candidate at/over the threshold, else the first alias candidate.
    Decided on the RAW score — a usage boost can never promote a weak match.
    """
    for kind in KINDS:                                   # ai_mismatch first, then alias
        for c in per_kind.get(kind, []):                 # already ordered: exact first, then rank
            if c.score >= threshold:
                return c
    return None


def build_suggestions(name: str, rows: Iterable[MappingRow], now: Optional[datetime] = None,
                      limit: int = MAX_CANDIDATES) -> tuple[list[Candidate], Optional[Candidate]]:
    """(column-2 candidates, auto-assign candidate for the cascade)."""
    per_kind = candidates_by_kind(name, rows, now)
    return merge_for_display(per_kind, limit), pick_auto_assign(per_kind)
