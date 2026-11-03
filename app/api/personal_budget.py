"""
Personal Finance > Monthly budget: what the logged-in person plans to spend each month on every TOP-LEVEL expense category (the same
amount every month), set against what they actually spent. Spending is the person's own personal expenses (sub-categories count
towards their parent). What is left over (or overspent) in a month carries forward into the next month's available amount, starting
from the month the budget was first set. Every row belongs to one login and is never shown to another.
"""
import calendar
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.user import User
from app.services import expense_group_service as svc

router = APIRouter()

MAX_AMOUNT = Decimal("99999999999999.99")          # NUMERIC(14, 2)


class BudgetSet(BaseModel):
    amount: Optional[Decimal] = None                # empty or 0 removes the budget

    @field_validator("amount")
    @classmethod
    def _sane(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is None:
            return None
        if v < 0:
            raise ValueError("A budget cannot be negative")
        if v > MAX_AMOUNT:
            raise ValueError("That budget is too large")
        return v


def _month(value: Optional[str], today: date) -> date:
    """First day of 'YYYY-MM' (this month when absent)."""
    if not value:
        return today.replace(day=1)
    try:
        y, m = value.split("-")
        return date(int(y), int(m), 1)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="month must look like 2026-11")


def _budgets(session: Session, user_id: int) -> dict[int, tuple[Decimal, date]]:
    rows = session.execute(text("SELECT category_id, amount, start_month FROM tbl_personal_budget WHERE user_id = :u"), {"u": user_id}).all()
    return {r[0]: (r[1], r[2]) for r in rows}


def _spent_before(session: Session, user_id: int, first: date) -> dict[int, Decimal]:
    """Per budgeted main category: everything spent from the month its budget started up to (not including) `first`."""
    rows = session.execute(text("""
        SELECT b.category_id, COALESCE(sum(e.amount), 0)
        FROM tbl_personal_budget b
        JOIN tbl_expense_category c ON COALESCE(c.parent_id, c.id) = b.category_id
        JOIN tbl_personal_expense e ON e.category_id = c.id AND e.user_id = b.user_id AND e."date" >= b.start_month AND e."date" < :first
        WHERE b.user_id = :u
        GROUP BY b.category_id
    """), {"u": user_id, "first": first}).all()
    return {r[0]: r[1] for r in rows}


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def budget_view(session: Session, user_id: int, first: date) -> dict:
    """Every top-level category with its budget, what rolled in from earlier months, and what was spent in the month starting `first`."""
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    spent = svc.summary(session, first, last, None, None, svc.personal(user_id))     # also seeds the default categories
    by_cat = {c["id"]: c for c in spent["categories"]}
    budgets = _budgets(session, user_id)
    before = _spent_before(session, user_id, first)
    tops = session.execute(text("SELECT id, name FROM tbl_expense_category WHERE parent_id IS NULL ORDER BY lower(name)")).all()

    rows = []
    for cid, name in tops:
        c = by_cat.get(cid)
        total = c["total"] if c else Decimal(0)
        budget, start = budgets.get(cid, (None, None))
        carry = Decimal(0)
        if budget is not None:
            n = max(_months_between(start, first), 0)                    # whole months since the budget began, up to the one before this
            carry = budget * n - before.get(cid, Decimal(0)) if n else Decimal(0)
        available = (budget + carry) if budget is not None else None
        rows.append({
            "category_id": cid, "name": name, "budget": budget, "spent": total,
            "carried_in": carry if budget is not None else None,
            "available": available,
            "left": (available - total) if available is not None else None,       # this is what rolls into next month
            "over": available is not None and total > available,
            "subs": [{"id": s["id"], "name": s["name"], "total": s["total"]} for s in (c["subs"] if c else [])],
        })
    ungrouped = by_cat.get(None)
    budgeted = [r for r in rows if r["budget"] is not None]
    total_budget = sum((r["budget"] for r in budgeted), Decimal(0))
    total_carry = sum((r["carried_in"] for r in budgeted), Decimal(0))
    spent_budgeted = sum((r["spent"] for r in budgeted), Decimal(0))
    return {
        "month": f"{first.year:04d}-{first.month:02d}",
        "categories": rows,
        "ungrouped": {"name": "Ungrouped", "spent": ungrouped["total"]} if ungrouped else None,
        "total_budget": total_budget,
        "total_carried_in": total_carry,
        "total_available": total_budget + total_carry,
        "spent_on_budgeted": spent_budgeted,
        "left": total_budget + total_carry - spent_budgeted,                     # carries into next month
        "total_spent": spent["total"],
        "over_count": sum(1 for r in rows if r["over"]),
        "budgeted_count": len(budgeted),
    }


def budget_summary(session: Session, user_id: int, today: date) -> dict:
    """The few numbers the Personal dashboard shows for this month."""
    v = budget_view(session, user_id, today.replace(day=1))
    return {k: v[k] for k in ("total_budget", "total_available", "spent_on_budgeted", "over_count", "budgeted_count")}


@router.get("/budgets")
def get_budgets(
    month: Optional[str] = Query(None, description="YYYY-MM; this month when absent"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return budget_view(session, user.id, _month(month, date.today()))


@router.put("/budgets/{category_id}")
def set_budget(
    category_id: int,
    payload: BudgetSet,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    cat = session.execute(text("SELECT parent_id FROM tbl_expense_category WHERE id = :i"), {"i": category_id}).first()
    if cat is None:
        raise HTTPException(status_code=404, detail="That category does not exist")
    if cat[0] is not None:
        raise HTTPException(status_code=422, detail="Budgets are set on the main categories, not on a sub-category")
    amount = payload.amount
    if not amount:
        session.execute(text("DELETE FROM tbl_personal_budget WHERE user_id = :u AND category_id = :c"), {"u": user.id, "c": category_id})
        session.commit()
        return {"category_id": category_id, "amount": None}
    updated = session.execute(
        text("UPDATE tbl_personal_budget SET amount = :a WHERE user_id = :u AND category_id = :c"),
        {"a": amount, "u": user.id, "c": category_id},
    ).rowcount
    if not updated:
        session.execute(
            text("INSERT INTO tbl_personal_budget (user_id, category_id, amount, start_month) VALUES (:u, :c, :a, :s)"),
            {"u": user.id, "c": category_id, "a": amount, "s": date.today().replace(day=1)},
        )
    session.commit()
    return {"category_id": category_id, "amount": amount}
