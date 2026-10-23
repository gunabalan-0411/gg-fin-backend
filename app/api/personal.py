"""
Personal Finance: the logged-in person's OWN expenses (never visible to another user). The categories and the 'for whom' list are the
same as the office expenses'. All rules live in app/services/expense_group_service.py (Scope = personal(user.id)).
"""
from datetime import date
from typing import Callable, Optional, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.user import User
from app.schemas.expense import ExpenseRead, ExpenseUpdate, PersonalExpenseCreate
from app.services import expense_group_service as svc
from app.services.expense_group_service import ExpenseGroupError

router = APIRouter()
T = TypeVar("T")


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ExpenseGroupError as e:
        raise HTTPException(status_code=e.status, detail=e.payload())


@router.get("/expenses", response_model=list[ExpenseRead])
def list_personal(
    skip: int = 0,
    limit: int = 10000,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category_id: Optional[int] = None,
    person_id: Optional[int] = None,
    search: Optional[str] = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return svc.list_expenses(session, skip, limit, date_from, date_to, category_id, person_id, False, search, svc.personal(user.id))


@router.get("/expenses/summary")
def personal_summary(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category_id: Optional[int] = None,
    person_id: Optional[int] = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return svc.summary(session, date_from, date_to, category_id, person_id, svc.personal(user.id))


@router.get("/expenses/export")
def personal_export(
    kind: str = Query("rows", description="rows | summary"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category_id: Optional[int] = None,
    person_id: Optional[int] = None,
    search: Optional[str] = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    body = _run(lambda: svc.export_csv(session, kind, date_from, date_to, category_id, person_id, search, svc.personal(user.id)))
    name = "personal_expenses_summary.csv" if kind == "summary" else "personal_expenses.csv"
    return Response("﻿" + body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/dashboard")
def personal_dashboard(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """The Personal view of the main dashboard: this month vs last month, this month by category, the last 12 months."""
    return _run(lambda: svc.personal_dashboard(session, svc.personal(user.id), date.today()))


@router.post("/expenses", response_model=ExpenseRead, status_code=201)
def create_personal(
    payload: PersonalExpenseCreate,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return _run(lambda: svc.create_expenses(session, [payload], svc.personal(user.id)))[0]


@router.patch("/expenses/{expense_id}", response_model=ExpenseRead)
def update_personal(
    expense_id: int,
    payload: ExpenseUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    result = _run(lambda: svc.update_expense(session, expense_id, payload.model_dump(exclude_none=True), svc.personal(user.id)))
    if not result:
        raise HTTPException(status_code=404, detail="Expense not found")
    return result


@router.delete("/expenses/{expense_id}", status_code=204)
def delete_personal(
    expense_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if not svc.delete_personal_expense(session, expense_id, svc.personal(user.id)):
        raise HTTPException(status_code=404, detail="Expense not found")
