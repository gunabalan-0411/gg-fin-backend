from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.expense import ExpenseCreate, ExpenseRead, ExpenseUpdate
from app.services.expense_service import ExpenseService

router = APIRouter()


@router.get("", response_model=list[ExpenseRead])
def list_expenses(
    skip: int = 0,
    limit: int = 10000,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return ExpenseService(session).list(skip, limit)


@router.post("", response_model=ExpenseRead, status_code=201)
def create_expense(
    payload: ExpenseCreate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return ExpenseService(session).create(payload)


@router.patch("/{expense_id}", response_model=ExpenseRead)
def update_expense(
    expense_id: int,
    payload: ExpenseUpdate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    result = ExpenseService(session).update(expense_id, payload)
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
