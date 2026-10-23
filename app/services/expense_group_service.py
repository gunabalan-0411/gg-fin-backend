"""
Expense categories (two levels), "for whom", grouped totals, CSV export and the one-time grouping of old expenses.

* tbl_expense.category_id points at the MOST SPECIFIC category (a sub-category when one was chosen, else the top-level one).
  NULL means an old expense that has not been grouped yet.
* tbl_expense.person_id is "for whom" (required for new expenses).
* Miscellaneous is the default category and is protected (cannot be renamed or deleted).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import text
from sqlmodel import Session

from app.services import expense_guess as guess

MAX_NAME = 60


@dataclass(frozen=True)
class Scope:
    """Which expenses a call works on: the OFFICE expenses (tbl_expense, shared) or ONE person's personal expenses (tbl_personal_expense)."""
    table: str = "tbl_expense"
    user_id: Optional[int] = None                 # personal: only this user's rows are ever read, changed or deleted
    need_person: bool = True                      # office: "for whom" is required; personal: optional


OFFICE = Scope()


def personal(user_id: int) -> Scope:
    return Scope("tbl_personal_expense", user_id, need_person=False)


class ExpenseGroupError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def _clean(name: Optional[str]) -> str:
    n = " ".join((name or "").split())
    if not n:
        raise ExpenseGroupError(422, "name_required", "A name is required")
    if len(n) > MAX_NAME:
        raise ExpenseGroupError(422, "name_too_long", f"A name can be at most {MAX_NAME} characters")
    return n


# ── defaults ──────────────────────────────────────────────────────────────────────────────────────────────
def ensure_defaults(session: Session) -> int:
    """Create the default categories / people the first time (only while there are none) and keep Miscellaneous. Returns the Miscellaneous id.

    The page asks for categories, people and totals at the same moment, so several requests can arrive on an empty database: an advisory
    lock lets exactly one of them create the defaults (the others wait, then find them)."""
    session.execute(text("SELECT pg_advisory_xact_lock(987654323)"))
    n_cat = session.execute(text("SELECT count(*) FROM tbl_expense_category")).scalar()
    if n_cat == 0:
        for top, subs in guess.DEFAULT_TREE.items():
            tid = session.execute(text("INSERT INTO tbl_expense_category (name, parent_id) VALUES (:n, NULL) RETURNING id"), {"n": top}).scalar()
            for sub in subs:
                session.execute(text("INSERT INTO tbl_expense_category (name, parent_id) VALUES (:n, :p)"), {"n": sub, "p": tid})
    misc = session.execute(
        text("SELECT id FROM tbl_expense_category WHERE parent_id IS NULL AND lower(name) = lower(:n)"), {"n": guess.MISC}).scalar()
    if misc is None:
        misc = session.execute(text("INSERT INTO tbl_expense_category (name, parent_id) VALUES (:n, NULL) RETURNING id"), {"n": guess.MISC}).scalar()
    if session.execute(text("SELECT count(*) FROM tbl_expense_person")).scalar() == 0:
        for p in guess.DEFAULT_PERSONS:
            session.execute(text("INSERT INTO tbl_expense_person (name) VALUES (:n)"), {"n": p})
    session.commit()
    return misc


def misc_id(session: Session) -> int:
    return ensure_defaults(session)


# ── categories ────────────────────────────────────────────────────────────────────────────────────────────
def _category(session: Session, cid: int) -> dict:
    r = session.execute(text("SELECT id, name, parent_id FROM tbl_expense_category WHERE id = :i"), {"i": cid}).first()
    if r is None:
        raise ExpenseGroupError(404, "category_not_found", "That category does not exist")
    return {"id": r[0], "name": r[1], "parent_id": r[2]}


def list_categories(session: Session) -> list[dict]:
    misc = ensure_defaults(session)
    rows = session.execute(text("""
        SELECT c.id, c.name, c.parent_id,
               (SELECT count(*) FROM tbl_expense e WHERE e.category_id = c.id
                   OR e.category_id IN (SELECT s.id FROM tbl_expense_category s WHERE s.parent_id = c.id))
             + (SELECT count(*) FROM tbl_personal_expense e WHERE e.category_id = c.id
                   OR e.category_id IN (SELECT s.id FROM tbl_expense_category s WHERE s.parent_id = c.id)) AS used
        FROM tbl_expense_category c
        ORDER BY (c.parent_id IS NOT NULL), lower(c.name)
    """)).all()
    return [{"id": r[0], "name": r[1], "parent_id": r[2], "is_system": r[0] == misc, "used": r[3]} for r in rows]


def create_category(session: Session, name: str, parent_id: Optional[int]) -> dict:
    ensure_defaults(session)
    n = _clean(name)
    if parent_id is not None:
        parent = _category(session, parent_id)
        if parent["parent_id"] is not None:
            raise ExpenseGroupError(422, "too_deep", "A sub-category cannot have its own sub-categories (two levels only)")
        if parent["id"] == misc_id(session):
            raise ExpenseGroupError(422, "misc_has_no_subs", "Miscellaneous cannot have sub-categories")
    dup = session.execute(
        text("SELECT id FROM tbl_expense_category WHERE COALESCE(parent_id, 0) = COALESCE(:p, 0) AND lower(name) = lower(:n)"),
        {"p": parent_id, "n": n}).scalar()
    if dup is not None:
        raise ExpenseGroupError(409, "exists", f"'{n}' already exists here", id=dup)
    cid = session.execute(text("INSERT INTO tbl_expense_category (name, parent_id) VALUES (:n, :p) RETURNING id"), {"n": n, "p": parent_id}).scalar()
    session.commit()
    return {"id": cid, "name": n, "parent_id": parent_id, "is_system": False, "used": 0}


def rename_category(session: Session, cid: int, name: str) -> dict:
    misc = ensure_defaults(session)
    c = _category(session, cid)
    if cid == misc:
        raise ExpenseGroupError(409, "protected", "Miscellaneous is the default category and cannot be renamed")
    n = _clean(name)
    dup = session.execute(
        text("SELECT id FROM tbl_expense_category WHERE COALESCE(parent_id, 0) = COALESCE(:p, 0) AND lower(name) = lower(:n) AND id <> :i"),
        {"p": c["parent_id"], "n": n, "i": cid}).scalar()
    if dup is not None:
        raise ExpenseGroupError(409, "exists", f"'{n}' already exists here", id=dup)
    session.execute(text("UPDATE tbl_expense_category SET name = :n WHERE id = :i"), {"n": n, "i": cid})
    session.commit()
    return {"id": cid, "name": n, "parent_id": c["parent_id"]}


def delete_category(session: Session, cid: int) -> dict:
    """A top-level category: its expenses (and those of its sub-categories) move to Miscellaneous. A sub-category: its expenses move to its parent."""
    misc = ensure_defaults(session)
    c = _category(session, cid)
    if cid == misc:
        raise ExpenseGroupError(409, "protected", "Miscellaneous is the default category and cannot be deleted")
    if c["parent_id"] is None:
        subs = [r[0] for r in session.execute(text("SELECT id FROM tbl_expense_category WHERE parent_id = :i"), {"i": cid})]
        ids = [cid, *subs]
        moved = sum(session.execute(text(f"UPDATE {t} SET category_id = :m WHERE category_id = ANY(:ids)"), {"m": misc, "ids": ids}).rowcount
                    for t in ("tbl_expense", "tbl_personal_expense"))
        session.execute(text("DELETE FROM tbl_expense_category WHERE id = ANY(:ids)"), {"ids": ids})
    else:
        moved = sum(session.execute(text(f"UPDATE {t} SET category_id = :p WHERE category_id = :i"), {"p": c["parent_id"], "i": cid}).rowcount
                    for t in ("tbl_expense", "tbl_personal_expense"))
        session.execute(text("DELETE FROM tbl_expense_category WHERE id = :i"), {"i": cid})
    session.commit()
    return {"deleted": cid, "moved": moved}


# ── people ────────────────────────────────────────────────────────────────────────────────────────────────
def _person(session: Session, pid: int) -> dict:
    r = session.execute(text("SELECT id, name FROM tbl_expense_person WHERE id = :i"), {"i": pid}).first()
    if r is None:
        raise ExpenseGroupError(404, "person_not_found", "That person does not exist")
    return {"id": r[0], "name": r[1]}


def list_persons(session: Session) -> list[dict]:
    ensure_defaults(session)
    rows = session.execute(text("""
        SELECT p.id, p.name, (SELECT count(*) FROM tbl_expense e WHERE e.person_id = p.id) + (SELECT count(*) FROM tbl_personal_expense e WHERE e.person_id = p.id)
        FROM tbl_expense_person p ORDER BY lower(p.name)
    """)).all()
    return [{"id": r[0], "name": r[1], "used": r[2]} for r in rows]


def create_person(session: Session, name: str) -> dict:
    ensure_defaults(session)
    n = _clean(name)
    dup = session.execute(text("SELECT id FROM tbl_expense_person WHERE lower(name) = lower(:n)"), {"n": n}).scalar()
    if dup is not None:
        raise ExpenseGroupError(409, "exists", f"'{n}' already exists", id=dup)
    pid = session.execute(text("INSERT INTO tbl_expense_person (name) VALUES (:n) RETURNING id"), {"n": n}).scalar()
    session.commit()
    return {"id": pid, "name": n, "used": 0}


def rename_person(session: Session, pid: int, name: str) -> dict:
    _person(session, pid)
    n = _clean(name)
    dup = session.execute(text("SELECT id FROM tbl_expense_person WHERE lower(name) = lower(:n) AND id <> :i"), {"n": n, "i": pid}).scalar()
    if dup is not None:
        raise ExpenseGroupError(409, "exists", f"'{n}' already exists", id=dup)
    session.execute(text("UPDATE tbl_expense_person SET name = :n WHERE id = :i"), {"n": n, "i": pid})
    session.commit()
    return {"id": pid, "name": n}


def delete_person(session: Session, pid: int) -> dict:
    """Its expenses become 'not specified' (person NULL) - they are never deleted."""
    _person(session, pid)
    moved = sum(session.execute(text(f"UPDATE {t} SET person_id = NULL WHERE person_id = :i"), {"i": pid}).rowcount
                for t in ("tbl_expense", "tbl_personal_expense"))
    session.execute(text("DELETE FROM tbl_expense_person WHERE id = :i"), {"i": pid})
    session.commit()
    return {"deleted": pid, "unspecified": moved}


# ── reading expenses ──────────────────────────────────────────────────────────────────────────────────────
_SELECT_TMPL = """
    SELECT e.id, e.amount, e."date", e.notes, e.category_id,
           COALESCE(c.parent_id, c.id) AS top_id,
           CASE WHEN c.parent_id IS NULL THEN c.name ELSE tp.name END AS top_name,
           CASE WHEN c.parent_id IS NULL THEN NULL ELSE c.name END AS sub_name,
           e.person_id, pe.name AS person_name
    FROM {table} e
    LEFT JOIN tbl_expense_category c  ON c.id = e.category_id
    LEFT JOIN tbl_expense_category tp ON tp.id = c.parent_id
    LEFT JOIN tbl_expense_person pe   ON pe.id = e.person_id
"""
_COLS = ["id", "amount", "date", "notes", "category_id", "top_id", "top_name", "sub_name", "person_id", "person_name"]


def _select(scope: Scope) -> str:
    return _SELECT_TMPL.replace("{table}", scope.table)           # the table name is one of our two constants, never user input


def _where(date_from: Optional[date], date_to: Optional[date], category_id: Optional[int], person_id: Optional[int],
           ungrouped: bool = False, search: Optional[str] = None, scope: Scope = OFFICE) -> tuple[str, dict]:
    parts, params = [], {}
    if scope.user_id is not None:
        parts.append("e.user_id = :uid"); params["uid"] = scope.user_id
    if date_from:
        parts.append('e."date" >= :df'); params["df"] = date_from
    if date_to:
        parts.append('e."date" <= :dt'); params["dt"] = date_to
    if category_id is not None:
        parts.append("(e.category_id = :cat OR c.parent_id = :cat)"); params["cat"] = category_id
    if person_id is not None:
        parts.append("e.person_id = :per"); params["per"] = person_id
    if ungrouped:
        parts.append("e.category_id IS NULL")
    if search and search.strip():
        parts.append("lower(coalesce(e.notes, '')) LIKE :q"); params["q"] = f"%{search.strip().lower()}%"
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def list_expenses(session: Session, skip: int = 0, limit: int = 10000, date_from: Optional[date] = None, date_to: Optional[date] = None,
                  category_id: Optional[int] = None, person_id: Optional[int] = None, ungrouped: bool = False,
                  search: Optional[str] = None, scope: Scope = OFFICE) -> list[dict]:
    where, params = _where(date_from, date_to, category_id, person_id, ungrouped, search, scope)
    rows = session.execute(text(_select(scope) + where + ' ORDER BY e."date" DESC, e.id DESC OFFSET :o LIMIT :l'), {**params, "o": skip, "l": limit}).all()
    return [dict(zip(_COLS, r)) for r in rows]


def get_expense(session: Session, expense_id: int, scope: Scope = OFFICE) -> Optional[dict]:
    where, params = _where(None, None, None, None, scope=scope)
    clause = (where + " AND " if where else " WHERE ") + "e.id = :i"
    r = session.execute(text(_select(scope) + clause), {**params, "i": expense_id}).first()
    return dict(zip(_COLS, r)) if r else None


# ── writing expenses ──────────────────────────────────────────────────────────────────────────────────────
def _resolve(session: Session, category_id: Optional[int], person_id: Optional[int], need_person: bool) -> tuple[int, Optional[int]]:
    cat = misc_id(session) if category_id is None else _category(session, category_id)["id"]
    if person_id is None:
        if need_person:
            raise ExpenseGroupError(422, "person_required", "Choose who the expense was for")
        return cat, None
    return cat, _person(session, person_id)["id"]


def create_expenses(session: Session, items: Sequence[Any], scope: Scope = OFFICE) -> list[dict]:
    """All or nothing: every line is checked first, then all are saved in one transaction."""
    prepared = []
    for it in items:
        cat, per = _resolve(session, it.category_id, it.person_id, need_person=scope.need_person)
        prepared.append({"amount": it.amount, "date": it.date, "notes": (it.notes or "").strip() or None, "cat": cat, "per": per})
    ids = []
    try:
        for p in prepared:
            if scope.user_id is None:
                sql = 'INSERT INTO tbl_expense (amount, "date", notes, category_id, person_id) VALUES (:amount, :date, :notes, :cat, :per) RETURNING id'
            else:
                sql = ('INSERT INTO tbl_personal_expense (user_id, amount, "date", notes, category_id, person_id) '
                       'VALUES (:uid, :amount, :date, :notes, :cat, :per) RETURNING id')
                p = {**p, "uid": scope.user_id}
            ids.append(session.execute(text(sql), p).scalar())
        session.commit()
    except Exception:
        session.rollback()
        raise
    return [get_expense(session, i, scope) for i in ids]


def update_expense(session: Session, expense_id: int, data: dict, scope: Scope = OFFICE) -> Optional[dict]:
    if get_expense(session, expense_id, scope) is None:                 # also: somebody else's personal expense is "not found"
        return None
    sets, params = [], {"i": expense_id}
    if data.get("amount") is not None:
        if Decimal(str(data["amount"])) <= 0:
            raise ExpenseGroupError(422, "bad_amount", "The amount must be more than zero")
        sets.append("amount = :amount"); params["amount"] = data["amount"]
    if data.get("date") is not None:
        sets.append('"date" = :date'); params["date"] = data["date"]
    if data.get("notes") is not None:
        sets.append("notes = :notes"); params["notes"] = data["notes"].strip() or None
    if data.get("category_id") is not None:
        sets.append("category_id = :cat"); params["cat"] = _category(session, data["category_id"])["id"]
    if data.get("person_id") is not None:
        sets.append("person_id = :per"); params["per"] = _person(session, data["person_id"])["id"]
    if sets:
        session.execute(text(f"UPDATE {scope.table} SET " + ", ".join(sets) + " WHERE id = :i"), params)
        session.commit()
    return get_expense(session, expense_id, scope)


def delete_personal_expense(session: Session, expense_id: int, scope: Scope) -> bool:
    """Only the owner's own personal expense can be deleted (anybody else's is 'not found')."""
    if scope.user_id is None or get_expense(session, expense_id, scope) is None:
        return False
    session.execute(text(f"DELETE FROM {scope.table} WHERE id = :i AND user_id = :u"), {"i": expense_id, "u": scope.user_id})
    session.commit()
    return True


# ── totals ────────────────────────────────────────────────────────────────────────────────────────────────
def summary(session: Session, date_from: Optional[date] = None, date_to: Optional[date] = None, category_id: Optional[int] = None,
            person_id: Optional[int] = None, scope: Scope = OFFICE) -> dict:
    """Totals by category > sub-category and by person, with the grand total. Old, ungrouped expenses form their own 'Ungrouped' group."""
    ensure_defaults(session)
    where, params = _where(date_from, date_to, category_id, person_id, scope=scope)
    rows = session.execute(text(f"""
        SELECT COALESCE(c.parent_id, c.id) AS top_id,
               CASE WHEN c.parent_id IS NULL THEN c.name ELSE tp.name END AS top_name,
               c.id AS cat_id, CASE WHEN c.parent_id IS NULL THEN NULL ELSE c.name END AS sub_name,
               count(*) AS n, COALESCE(sum(e.amount), 0) AS total
        FROM {scope.table} e
        LEFT JOIN tbl_expense_category c  ON c.id = e.category_id
        LEFT JOIN tbl_expense_category tp ON tp.id = c.parent_id
        {where}
        GROUP BY 1, 2, 3, 4
    """), params).all()
    tops: dict[Any, dict] = {}
    for top_id, top_name, cat_id, sub_name, n, total in rows:
        t = tops.setdefault(top_id, {"id": top_id, "name": top_name or "Ungrouped", "ungrouped": top_id is None, "count": 0, "total": Decimal(0), "subs": []})
        t["count"] += n
        t["total"] += total
        if sub_name:
            t["subs"].append({"id": cat_id, "name": sub_name, "count": n, "total": total})
    cats = sorted(tops.values(), key=lambda t: (-t["total"], t["name"]))
    for t in cats:
        t["subs"].sort(key=lambda s: (-s["total"], s["name"]))
    prows = session.execute(text(f"""
        SELECT e.person_id, pe.name, count(*), COALESCE(sum(e.amount), 0)
        FROM {scope.table} e
        LEFT JOIN tbl_expense_category c ON c.id = e.category_id
        LEFT JOIN tbl_expense_person pe ON pe.id = e.person_id
        {where}
        GROUP BY 1, 2
    """), params).all()
    people = sorted(({"id": r[0], "name": r[1] or "Not specified", "count": r[2], "total": r[3]} for r in prows), key=lambda p: (-p["total"], p["name"]))
    grand = sum((t["total"] for t in cats), Decimal(0))
    n_all = sum(t["count"] for t in cats)
    return {"categories": cats, "people": people, "total": grand, "count": n_all}


def export_csv(session: Session, kind: str, date_from: Optional[date] = None, date_to: Optional[date] = None, category_id: Optional[int] = None,
               person_id: Optional[int] = None, search: Optional[str] = None, scope: Scope = OFFICE) -> str:
    """kind='rows': every expense (date, amount, category, sub-category, for whom, notes) and a Total line at the end.
       kind='summary': category > sub-category totals, then people totals, and a Total line at the end."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    if kind == "rows":
        rows = list_expenses(session, 0, 1_000_000, date_from, date_to, category_id, person_id, False, search, scope)
        w.writerow(["Date", "Amount", "Category", "Sub-category", "For whom", "Notes"])
        total = Decimal(0)
        for r in rows:
            total += r["amount"]
            w.writerow([r["date"].isoformat(), f'{r["amount"]:.2f}', r["top_name"] or "Ungrouped", r["sub_name"] or "", r["person_name"] or "Not specified", r["notes"] or ""])
        w.writerow(["Total", f"{total:.2f}", "", "", "", f"{len(rows)} expenses"])
    elif kind == "summary":
        s = summary(session, date_from, date_to, category_id, person_id, scope)
        w.writerow(["Category", "Sub-category", "Entries", "Total"])
        for c in s["categories"]:
            w.writerow([c["name"], "", c["count"], f'{c["total"]:.2f}'])
            for sub in c["subs"]:
                w.writerow([c["name"], sub["name"], sub["count"], f'{sub["total"]:.2f}'])
        w.writerow([])
        w.writerow(["For whom", "", "Entries", "Total"])
        for p in s["people"]:
            w.writerow([p["name"], "", p["count"], f'{p["total"]:.2f}'])
        w.writerow([])
        w.writerow(["Total", "", s["count"], f'{s["total"]:.2f}'])
    else:
        raise ExpenseGroupError(422, "bad_kind", "kind must be 'rows' or 'summary'")
    return out.getvalue()


# ── grouping the old expenses ─────────────────────────────────────────────────────────────────────────────
def _find(session: Session, name: str, parent_id: Optional[int]) -> Optional[int]:
    return session.execute(
        text("SELECT id FROM tbl_expense_category WHERE COALESCE(parent_id, 0) = COALESCE(:p, 0) AND lower(name) = lower(:n)"),
        {"p": parent_id, "n": name}).scalar()


def legacy_groups(session: Session) -> dict:
    """The old (ungrouped) expenses, one line per distinct notes text, each with the category / person the keyword guess suggests."""
    misc = ensure_defaults(session)
    rows = session.execute(text("""
        SELECT min(notes) AS notes, count(*) AS n, sum(amount) AS total, min("date") AS first, max("date") AS last
        FROM tbl_expense WHERE category_id IS NULL
        GROUP BY lower(trim(coalesce(notes, '')))
        ORDER BY sum(amount) DESC
    """)).all()
    persons = {r["name"].lower(): r["id"] for r in list_persons(session)}
    groups = []
    for notes, n, total, first, last in rows:
        top, sub = guess.guess_category(notes)
        top_id = _find(session, top, None)
        cat_id = _find(session, sub, top_id) if (sub and top_id) else None
        cat_id = cat_id or top_id or misc
        person = guess.guess_person(notes)
        groups.append({
            "notes": notes, "count": n, "total": total, "first": first, "last": last,
            "category_id": cat_id, "person_id": persons.get(person.lower()) if person else None,
        })
    return {"groups": groups, "count": sum(g["count"] for g in groups), "total": sum((g["total"] for g in groups), Decimal(0))}


def legacy_apply(session: Session, items: Sequence[Any]) -> dict:
    """Save the user's reviewed choices: only still-ungrouped expenses with exactly that notes text change; the notes are never edited."""
    updated = 0
    try:
        for it in items:
            cat, per = _resolve(session, it.category_id, it.person_id, need_person=False)
            res = session.execute(
                text("""UPDATE tbl_expense SET category_id = :cat, person_id = COALESCE(:per, person_id)
                        WHERE category_id IS NULL AND lower(trim(coalesce(notes, ''))) = lower(trim(coalesce(:notes, '')))"""),
                {"cat": cat, "per": per, "notes": it.notes})
            updated += res.rowcount
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"updated": updated}


# ── Personal Finance dashboard ────────────────────────────────────────────────────────────────────────────
def _month_start(d: date, back: int = 0) -> date:
    """The first day of the month `back` months before the month of `d` (negative: after)."""
    idx = d.year * 12 + (d.month - 1) - back
    return date(idx // 12, idx % 12 + 1, 1)


def personal_dashboard(session: Session, scope: Scope, today: date, months: int = 12) -> dict:
    """What one person spent: this month (against last month), this month by category, and the last `months` months (zeros included)."""
    if scope.user_id is None:
        raise ExpenseGroupError(400, "not_personal", "Not a personal scope")
    first = _month_start(today)
    next_first = _month_start(today, back=-1)
    prev_first = _month_start(today, back=1)
    start = _month_start(today, back=months - 1)

    def month_total(a: date, b: date) -> dict:
        r = session.execute(text(f'SELECT COALESCE(sum(amount), 0), count(*) FROM {scope.table} WHERE user_id = :u AND "date" >= :a AND "date" < :b'),
                            {"u": scope.user_id, "a": a, "b": b}).first()
        return {"total": r[0], "count": r[1]}

    this_month, last_month = month_total(first, next_first), month_total(prev_first, first)
    by_month = {r[0]: r[1] for r in session.execute(text(
        f"""SELECT to_char("date", 'YYYY-MM'), COALESCE(sum(amount), 0) FROM {scope.table}
            WHERE user_id = :u AND "date" >= :a AND "date" < :b GROUP BY 1"""), {"u": scope.user_id, "a": start, "b": next_first})}
    series = []
    for i in range(months - 1, -1, -1):
        m = _month_start(today, back=i)
        key = f"{m.year:04d}-{m.month:02d}"
        series.append({"month": key, "total": by_month.get(key, Decimal(0))})
    this = summary(session, first, next_first - timedelta(days=1), None, None, scope)
    return {
        "month": f"{first.year:04d}-{first.month:02d}",
        "this_month": this_month, "last_month": last_month,
        "categories": this["categories"], "people": this["people"],
        "monthly": series,
    }
