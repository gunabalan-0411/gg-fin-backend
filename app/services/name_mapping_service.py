"""
Database side of the alias / AI-mismatch name mappings: loading, saving (with limits and conflict handling),
"remember this", usage counting and the large-table fallback.

The matching maths lives in `mapping_matcher` (pure). The customer<->mapping bookkeeping (re-pointing, renaming) is done
by database triggers — see app/db/mapping_schema.py — so nothing in here has to be called when customers change.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.services import mapping_matcher as mm
from app.services.mapping_matcher import MappingRow

# Above this many mapping rows the matcher stops loading everything into memory and asks Postgres for a
# pre-filtered candidate set per name instead (requirement R-31).
MEMORY_LIMIT = int(os.environ.get("NAME_MAPPING_MEMORY_LIMIT", "5000"))
MAX_WORD_LEN = 200

CUSTOMER_TABLE = {"EDI": "tbl_edi_customer", "IOP": "tbl_iop_customer"}
# kind -> (table, live view, word column)
KIND_META = {
    "alias": ("tbl_customer_alias", "vw_customer_alias", "alias_name"),
    "ai_mismatch": ("tbl_customer_ai_mismatch", "vw_customer_ai_mismatch", "mismatch_name"),
}
RESOLUTIONS = ("keep_both", "move")


class MappingError(Exception):
    """Business-rule failure; the API layer turns it into an HTTP error with a machine-readable `code`."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


# ── Small helpers ─────────────────────────────────────────────────────────────────────────────────────────
def normalize_type(value: str) -> str:
    v = (value or "").strip().upper()
    if v not in CUSTOMER_TABLE:
        raise MappingError(422, "bad_finance_type", f"finance_type must be EDI or IOP, got {value!r}")
    return v


def normalize_kind(value: str) -> str:
    if value not in KIND_META:
        raise MappingError(422, "bad_kind", f"kind must be 'alias' or 'ai_mismatch', got {value!r}")
    return value


def clean_word(word: Optional[str]) -> str:
    """Stored exactly as typed — only leading/trailing whitespace is trimmed (R-06: no other normalization)."""
    w = (word or "").strip()
    if len(w) > MAX_WORD_LEN:
        raise MappingError(422, "word_too_long", f"A name can be at most {MAX_WORD_LEN} characters", word=w[:30])
    return w


def _clean_list(words: Iterable[Optional[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        w = clean_word(w)
        if w and w not in seen:                     # exact duplicates collapse; case differences are kept (R-06)
            seen.add(w)
            out.append(w)
    return out


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# ── Customers ─────────────────────────────────────────────────────────────────────────────────────────────
def get_customer(session: Session, ftype: str, customer_id: int) -> dict:
    row = session.execute(
        text(f"SELECT customer_id, customer_name, is_closed FROM {CUSTOMER_TABLE[ftype]} WHERE customer_id = :id"),
        {"id": customer_id},
    ).first()
    if row is None:
        raise MappingError(404, "customer_not_found", f"No {ftype} customer with id {customer_id}")
    return {"id": row[0], "name": row[1], "is_closed": bool(row[2]), "finance_type": ftype}


def _open_customer_with_name(session: Session, ftype: str, customer_id: int) -> dict:
    c = get_customer(session, ftype, customer_id)
    if c["is_closed"]:
        raise MappingError(409, "customer_closed", "This customer's account is closed — closed customers are not used for matching")
    if not (c["name"] or "").strip():
        raise MappingError(422, "customer_has_no_name", "This customer has no name, so names cannot be mapped to it")
    return c


# ── Loading rows for matching ─────────────────────────────────────────────────────────────────────────────
_ROW_COLS = "id, finance_type, customer_name, {word}, active_customer_id, use_count, last_used_at, created_at"


def _to_rows(kind: str, result) -> list[MappingRow]:
    return [
        MappingRow(id=r[0], kind=kind, finance_type=r[1], customer_name=r[2], word=r[3], customer_id=r[4],
                   use_count=r[5] or 0, last_used_at=r[6], created_at=r[7])
        for r in result
    ]


def total_rows(session: Session) -> int:
    n = 0
    for kind, (table, _view, _col) in KIND_META.items():
        n += int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar() or 0)
    return n


def load_active_rows(session: Session, ftype: Optional[str] = None) -> list[MappingRow]:
    """All mapping rows whose customer is active (read live through the views). Inactive customers never appear (R-07)."""
    rows: list[MappingRow] = []
    for kind, (_table, view, col) in KIND_META.items():
        sql = f"SELECT {_ROW_COLS.format(word=col)} FROM {view} WHERE is_customer_active"
        params: dict[str, Any] = {}
        if ftype:
            sql += " AND finance_type = :t"
            params["t"] = ftype
        rows += _to_rows(kind, session.execute(text(sql), params))
    return rows


def _like_patterns(name: str) -> list[str]:
    tokens = [t for t in re.split(r"\s+", name.strip().lower()) if len(t) >= 3] or [name.strip().lower()]
    esc = lambda s: s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # noqa: E731
    return ["%" + esc(t) + "%" for t in tokens if t]


TRIGRAM_MIN_SIMILARITY = 0.2     # deliberately generous: this only pre-filters, the fuzzy scorer decides
_trgm_available: Optional[bool] = None


def _has_trigram(session: Session) -> bool:
    global _trgm_available
    if _trgm_available is None:
        _trgm_available = session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")).first() is not None
    return _trgm_available


def fetch_candidate_rows(session: Session, ftype: str, name: str) -> list[MappingRow]:
    """
    Large-table path: let Postgres pre-filter to words that
      * equal the name, or
      * are trigram-similar to it (pg_trgm, when installed — catches a single misspelt letter), or
      * contain one of its tokens (>= 3 letters), or
      * are themselves contained in the name (the scorer treats "Thiru" inside "Thirumalai" as a match),
    and let the fuzzy scorer rank only those. Without pg_trgm a word with every token misspelt is not a candidate on this
    path; the in-memory path has no such limit.
    """
    name = (name or "").strip()
    if not name:
        return []
    trigram = _has_trigram(session)
    rows: list[MappingRow] = []
    for kind, (_table, view, col) in KIND_META.items():
        conds = [f"lower({col}) = lower(:name)",
                 f"lower({col}) LIKE ANY (:patterns)",
                 f"(length({col}) >= 3 AND strpos(lower(:name), lower({col})) > 0)"]
        params: dict[str, Any] = {"t": ftype, "name": name, "patterns": _like_patterns(name)}
        if trigram:
            conds.append(f"similarity(lower({col}), lower(:name)) >= :sim")
            params["sim"] = TRIGRAM_MIN_SIMILARITY
        sql = (f"SELECT {_ROW_COLS.format(word=col)} FROM {view} "
               f"WHERE is_customer_active AND finance_type = :t AND (" + " OR ".join(conds) + ")")
        rows += _to_rows(kind, session.execute(text(sql), params))
    return rows


class MappingIndex:
    """
    Mapping rows prepared once per OCR extract. Small tables are matched fully in memory; once the tables hold more than
    `memory_limit` rows each lookup is pre-filtered by the database instead.
    """

    def __init__(self, session: Session, memory_limit: Optional[int] = None):
        self.session = session
        limit = MEMORY_LIMIT if memory_limit is None else memory_limit
        self.in_memory = total_rows(session) <= limit
        self._by_type: dict[str, list[MappingRow]] = {"EDI": [], "IOP": []}
        if self.in_memory:
            for r in load_active_rows(session):
                self._by_type.setdefault(r.finance_type, []).append(r)

    def rows_for(self, ftype: str, name: str) -> list[MappingRow]:
        if self.in_memory:
            return self._by_type.get(ftype, [])
        return fetch_candidate_rows(self.session, ftype, name)

    def suggest(self, ftype: str, name: str, now: Optional[datetime] = None):
        """(column-2 candidates, auto-assign candidate) for one extracted name."""
        return mm.build_suggestions(name, self.rows_for(ftype, name), now)


# ── Popup data ────────────────────────────────────────────────────────────────────────────────────────────
def _group_rows(session: Session, kind: str, ftype: str, customer_name: str) -> list[dict]:
    table, _view, col = KIND_META[kind]
    now = datetime.now(timezone.utc)
    res = session.execute(
        text(f"SELECT id, {col}, use_count, last_used_at, created_at FROM {table} "
             f"WHERE finance_type = :t AND customer_name = :n ORDER BY id"),
        {"t": ftype, "n": customer_name},
    )
    return [
        {"id": r[0], "word": r[1], "use_count": r[2], "last_used_at": _iso(r[3]), "created_at": _iso(r[4]),
         "stale": mm.is_stale(r[2], r[3], r[4], now)}
        for r in res
    ]


def popup_data(session: Session, ftype: str, customer_id: int) -> dict:
    ftype = normalize_type(ftype)
    c = _open_customer_with_name(session, ftype, customer_id)
    return {
        "customer": {"id": c["id"], "name": c["name"], "finance_type": ftype},
        "aliases": _group_rows(session, "alias", ftype, c["name"]),
        "mismatches": _group_rows(session, "ai_mismatch", ftype, c["name"]),
        "limits": dict(mm.KIND_LIMIT),
    }


# ── Conflicts ─────────────────────────────────────────────────────────────────────────────────────────────
def find_conflicts(session: Session, ftype: str, own_name: str, items: Sequence[dict]) -> list[dict]:
    """Words in `items` that are already mapped to ANOTHER active customer (inactive customers are never shown, R-07)."""
    out: list[dict] = []
    by_kind: dict[str, list[str]] = {}
    for it in items:
        by_kind.setdefault(normalize_kind(it["kind"]), []).append(clean_word(it["word"]))
    for kind, words in by_kind.items():
        words = [w for w in words if w]
        if not words:
            continue
        _table, view, col = KIND_META[kind]
        res = session.execute(
            text(f"SELECT id, {col}, active_customer_id, customer_name FROM {view} "
                 f"WHERE is_customer_active AND finance_type = :t AND customer_name <> :me AND {col} = ANY(:words) "
                 f"ORDER BY {col}, customer_name"),
            {"t": ftype, "me": own_name, "words": words},
        )
        grouped: dict[str, list[dict]] = {}
        for r in res:
            grouped.setdefault(r[1], []).append({"mapping_id": r[0], "customer_id": r[2], "customer_name": r[3]})
        for word, customers in grouped.items():
            out.append({"kind": kind, "word": word, "customers": customers})
    return out


def check(session: Session, ftype: str, customer_id: int, items: Sequence[dict]) -> list[dict]:
    ftype = normalize_type(ftype)
    c = _open_customer_with_name(session, ftype, customer_id)
    return find_conflicts(session, ftype, c["name"], items)


# ── Save (popup) ──────────────────────────────────────────────────────────────────────────────────────────
def _repoint(session: Session, ftype: str, name: str) -> None:
    session.execute(text("SELECT fn_ggfin_repoint_mappings(:t, :n)"), {"t": ftype, "n": name})


def save_customer_mappings(session: Session, ftype: str, customer_id: int, aliases: Sequence[str],
                           mismatches: Sequence[str], resolutions: Sequence[dict] = ()) -> dict:
    """
    Make the customer's alias / AI-mismatch lists exactly `aliases` / `mismatches` (the popup's Save).
    Editing a word = removing the old one and adding the new one, so an edited word starts with fresh usage stats.
    """
    ftype = normalize_type(ftype)
    c = _open_customer_with_name(session, ftype, customer_id)
    name = c["name"]
    final = {"alias": _clean_list(aliases), "ai_mismatch": _clean_list(mismatches)}

    for kind, words in final.items():                                    # R-15: limits, server side
        if len(words) > mm.KIND_LIMIT[kind]:
            raise MappingError(422, "limit_exceeded",
                               f"At most {mm.KIND_LIMIT[kind]} {'alias' if kind == 'alias' else 'AI-mismatch'} names per customer "
                               f"— remove or edit an existing one",
                               kind=kind, limit=mm.KIND_LIMIT[kind], count=len(words))

    existing = {k: {r["word"]: r["id"] for r in _group_rows(session, k, ftype, name)} for k in KIND_META}
    to_add = {k: [w for w in final[k] if w not in existing[k]] for k in KIND_META}
    to_delete = {k: [i for w, i in existing[k].items() if w not in final[k]] for k in KIND_META}

    # R-17: a new word that already belongs to another active customer needs an explicit decision
    wanted = [{"kind": k, "word": w} for k in KIND_META for w in to_add[k]]
    conflicts = find_conflicts(session, ftype, name, wanted)
    decisions = {(r.get("kind"), clean_word(r.get("word"))): r.get("action") for r in resolutions}
    unresolved = []
    for cf in conflicts:
        action = decisions.get((cf["kind"], cf["word"]))
        if action not in RESOLUTIONS:
            unresolved.append(cf)
    if unresolved:
        raise MappingError(409, "conflict", "Some names are already mapped to another customer", conflicts=unresolved)

    try:
        for cf in conflicts:                                             # "move": delete the other customer's row
            if decisions[(cf["kind"], cf["word"])] == "move":
                table, _v, _c = KIND_META[cf["kind"]]
                session.execute(text(f"DELETE FROM {table} WHERE id = ANY(:ids)"),
                                {"ids": [x["mapping_id"] for x in cf["customers"]]})
        for kind, ids in to_delete.items():
            if ids:
                table, _v, _c = KIND_META[kind]
                session.execute(text(f"DELETE FROM {table} WHERE id = ANY(:ids)"), {"ids": ids})
        for kind, words in to_add.items():
            table, _v, col = KIND_META[kind]
            for w in words:
                session.execute(
                    text(f"INSERT INTO {table} (finance_type, customer_name, {col}, active_customer_id) "
                         f"VALUES (:t, :n, :w, :cid)"),
                    {"t": ftype, "n": name, "w": w, "cid": c["id"]},
                )
        _repoint(session, ftype, name)           # same-name accounts: point at the first open one
        session.commit()
    except IntegrityError as exc:                # two saves racing on the same word
        session.rollback()
        raise MappingError(409, "duplicate", "That name was just added by someone else — reload and try again") from exc
    except Exception:
        session.rollback()
        raise
    return popup_data(session, ftype, customer_id)


# ── Remember this (one click, AI-mismatch only) ───────────────────────────────────────────────────────────
def remember(session: Session, ftype: str, customer_id: int, word: str) -> dict:
    """
    Add `word` as an AI-mismatch name of the customer. Never overwrites anything: when the customer is at the limit or the
    word belongs to another customer the caller gets a status back and opens the popup so the user decides.
    """
    ftype = normalize_type(ftype)
    c = _open_customer_with_name(session, ftype, customer_id)
    w = clean_word(word)
    if not w:
        raise MappingError(422, "empty_word", "Nothing to remember")
    current = _group_rows(session, "ai_mismatch", ftype, c["name"])
    if any(r["word"] == w for r in current):
        return {"status": "exists", "word": w}
    if len(current) >= mm.MISMATCH_LIMIT:
        return {"status": "limit_reached", "word": w, "limit": mm.MISMATCH_LIMIT}
    conflicts = find_conflicts(session, ftype, c["name"], [{"kind": "ai_mismatch", "word": w}])
    if conflicts:
        return {"status": "conflict", "word": w, "conflicts": conflicts}
    try:
        session.execute(
            text("INSERT INTO tbl_customer_ai_mismatch (finance_type, customer_name, mismatch_name, active_customer_id) "
                 "VALUES (:t, :n, :w, :cid)"),
            {"t": ftype, "n": c["name"], "w": w, "cid": c["id"]},
        )
        _repoint(session, ftype, c["name"])
        session.commit()
    except IntegrityError:
        session.rollback()
        return {"status": "exists", "word": w}
    return {"status": "added", "word": w}


# ── Usage (R-30) ──────────────────────────────────────────────────────────────────────────────────────────
def record_usage(session: Session, refs: Sequence[dict]) -> int:
    """
    Count a use for each mapping row the user picked and whose row was SAVED. `refs` items:
    {kind, mapping_id, finance_type, customer_id}. A ref only counts when the row really points at that customer.
    """
    n = 0
    for r in refs:
        kind = r.get("kind")
        if kind not in KIND_META:
            continue
        table, _v, _c = KIND_META[kind]
        res = session.execute(
            text(f"UPDATE {table} SET use_count = use_count + 1, last_used_at = now() "
                 f"WHERE id = :id AND finance_type = :t AND active_customer_id = :cid"),
            {"id": r["mapping_id"], "t": r["finance_type"], "cid": r["customer_id"]},
        )
        n += res.rowcount or 0
    if n:
        session.commit()
    return n


# ── Maintenance ───────────────────────────────────────────────────────────────────────────────────────────
def resync(session: Session) -> dict:
    """Manual repair: re-point every mapping row (the same function the triggers use)."""
    changed = int(session.execute(text("SELECT fn_ggfin_resync_all_mappings()")).scalar() or 0)
    session.commit()
    return {"repointed": changed, "alias_rows": int(session.execute(text("SELECT count(*) FROM tbl_customer_alias")).scalar()),
            "mismatch_rows": int(session.execute(text("SELECT count(*) FROM tbl_customer_ai_mismatch")).scalar())}
