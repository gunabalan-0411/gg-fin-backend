"""
Personal Finance > Monthly budget: what the logged-in person plans to spend each month on every TOP-LEVEL expense category (the same
amount every month), set against what they actually spent. Spending is the person's own personal expenses (sub-categories count
towards their parent). Every row belongs to one login and is never shown to another.
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


def _budgets(session: Session, user_id: int) -> dict[int, Decimal]:
    rows = session.execute(text("SELECT category_id, amount FROM tbl_personal_budget WHERE user_id = :u"), {"u": user_id}).all()
    return {r[0]: r[1] for r in rows}


def budget_view(session: Session, user_id: int, first: date) -> dict:
    """Every top-level category with its budget and what was spent in the month starting `first`."""
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    spent = svc.summary(session, first, last, None, None, svc.personal(user_id))     # also seeds the default categories
    by_cat = {c["id"]: c for c in spent["categories"]}
    budgets = _budgets(session, user_id)
    tops = session.execute(text("SELECT id, name FROM tbl_expense_category WHERE parent_id IS NULL ORDER BY lower(name)")).all()

    rows = []
    for cid, name in tops:
        c = by_cat.get(cid)
        total = c["total"] if c else Decimal(0)
        budget = budgets.get(cid)
        rows.append({
            "category_id": cid, "name": name, "budget": budget, "spent": total,
            "left": (budget - total) if budget is not None else None,
            "over": budget is not None and total > budget,
            "subs": [{"id": s["id"], "name": s["name"], "total": s["total"]} for s in (c["subs"] if c else [])],
        })
    ungrouped = by_cat.get(None)
    total_budget = sum((r["budget"] for r in rows if r["budget"] is not None), Decimal(0))
    spent_budgeted = sum((r["spent"] for r in rows if r["budget"] is not None), Decimal(0))
    return {
        "month": f"{first.year:04d}-{first.month:02d}",
        "categories": rows,
        "ungrouped": {"name": "Ungrouped", "spent": ungrouped["total"]} if ungrouped else None,
        "total_budget": total_budget,
        "spent_on_budgeted": spent_budgeted,
        "total_spent": spent["total"],
        "over_count": sum(1 for r in rows if r["over"]),
        "budgeted_count": sum(1 for r in rows if r["budget"] is not None),
    }


def budget_summary(session: Session, user_id: int, today: date) -> dict:
    """The few numbers the Personal dashboard shows for this month."""
    v = budget_view(session, user_id, today.replace(day=1))
    return {k: v[k] for k in ("total_budget", "spent_on_budgeted", "over_count", "budgeted_count")}


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
            text("INSERT INTO tbl_personal_budget (user_id, category_id, amount) VALUES (:u, :c, :a)"),
            {"u": user.id, "c": category_id, "a": amount},
        )
    session.commit()
    return {"category_id": category_id, "amount": amount}
