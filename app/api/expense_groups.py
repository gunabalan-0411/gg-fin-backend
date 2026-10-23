"""Expense categories (two levels) and 'for whom' people. All rules live in app/services/expense_group_service.py."""
from typing import Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.expense import CategoryCreate, NameUpdate, PersonCreate
from app.services import expense_group_service as svc
from app.services.expense_group_service import ExpenseGroupError

router = APIRouter()
T = TypeVar("T")


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ExpenseGroupError as e:
        raise HTTPException(status_code=e.status, detail=e.payload())


@router.get("/categories")
def list_categories(session: Session = Depends(get_session), _=Depends(get_current_user)):
    """All categories (top-level and sub-categories, flat with parent_id), how many expenses use each, and which one is protected."""
    return svc.list_categories(session)


@router.post("/categories", status_code=201)
def create_category(body: CategoryCreate, session: Session = Depends(get_session), _=Depends(get_current_user)):
    return _run(lambda: svc.create_category(session, body.name, body.parent_id))


@router.patch("/categories/{category_id}")
def rename_category(category_id: int, body: NameUpdate, session: Session = Depends(get_session), _=Depends(get_current_user)):
    return _run(lambda: svc.rename_category(session, category_id, body.name))


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, session: Session = Depends(get_session), _=Depends(get_current_user)):
    return _run(lambda: svc.delete_category(session, category_id))


@router.get("/persons")
def list_persons(session: Session = Depends(get_session), _=Depends(get_current_user)):
    return svc.list_persons(session)


@router.post("/persons", status_code=201)
def create_person(body: PersonCreate, session: Session = Depends(get_session), _=Depends(get_current_user)):
    return _run(lambda: svc.create_person(session, body.name))


@router.patch("/persons/{person_id}")
def rename_person(person_id: int, body: NameUpdate, session: Session = Depends(get_session), _=Depends(get_current_user)):
    return _run(lambda: svc.rename_person(session, person_id, body.name))


@router.delete("/persons/{person_id}")
def delete_person(person_id: int, session: Session = Depends(get_session), _=Depends(get_current_user)):
    return _run(lambda: svc.delete_person(session, person_id))
