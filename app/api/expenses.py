from datetime import date
from typing import Callable, Optional, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.expense import ExpenseBulkCreate, ExpenseCreate, ExpenseRead, ExpenseUpdate, LegacyApplyRequest
from app.services import expense_group_service as svc
from app.services.expense_group_service import ExpenseGroupError
from app.services.expense_service import ExpenseService

router = APIRouter()
T = TypeVar("T")


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ExpenseGroupError as e:
        raise HTTPException(status_code=e.status, detail=e.payload())


@router.get("", response_model=list[ExpenseRead])
def list_expenses(
    skip: int = 0,
    limit: int = 10000,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category_id: Optional[int] = None,
    person_id: Optional[int] = None,
    ungrouped: bool = False,
    search: Optional[str] = None,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Expenses with their category (top-level / sub-category) and 'for whom'. category_id also matches the sub-categories of a top-level one."""
    return svc.list_expenses(session, skip, limit, date_from, date_to, category_id, person_id, ungrouped, search)


@router.get("/summary")
def expense_summary(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category_id: Optional[int] = None,
    person_id: Optional[int] = None,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Totals by category > sub-category and by person, with the grand total."""
    return svc.summary(session, date_from, date_to, category_id, person_id)


@router.get("/export")
def export_expenses(
    kind: str = Query("rows", description="rows | summary"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category_id: Optional[int] = None,
    person_id: Optional[int] = None,
    search: Optional[str] = None,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """CSV download (UTF-8 with BOM so Excel shows Tamil text), with a Total line at the end."""
    body = _run(lambda: svc.export_csv(session, kind, date_from, date_to, category_id, person_id, search))
    name = "expenses_summary.csv" if kind == "summary" else "expenses.csv"
    return Response("﻿" + body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/legacy-groups")
def legacy_groups(session: Session = Depends(get_session), _=Depends(get_current_user)):
    """Old (ungrouped) expenses, one line per distinct notes text, with the suggested category / person (a guess to review)."""
    return svc.legacy_groups(session)


@router.post("/legacy-groups/apply")
def legacy_apply(body: LegacyApplyRequest, session: Session = Depends(get_session), _=Depends(get_current_user)):
    """Save the reviewed choices for the old expenses. Notes are never edited."""
    return _run(lambda: svc.legacy_apply(session, body.items))


@router.post("", response_model=ExpenseRead, status_code=201)
def create_expense(
    payload: ExpenseCreate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return _run(lambda: svc.create_expenses(session, [payload]))[0]


@router.post("/bulk", response_model=list[ExpenseRead], status_code=201)
def create_expenses_bulk(
    payload: ExpenseBulkCreate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Several expense lines at once (the OCR Expense step): all are saved or none."""
    return _run(lambda: svc.create_expenses(session, payload.items))


@router.patch("/{expense_id}", response_model=ExpenseRead)
def update_expense(
    expense_id: int,
    payload: ExpenseUpdate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    result = _run(lambda: svc.update_expense(session, expense_id, payload.model_dump(exclude_none=True)))
    if not result:
        raise HTTPException(status_code=404, detail="Expense not found")
    return result


@router.delete("/{expense_id}", status_code=204)
def delete_expense(
    expense_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    if not ExpenseService(session).delete(expense_id):
        raise HTTPException(status_code=404, detail="Expense not found")
