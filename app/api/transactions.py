from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.transaction import (
    EdiTransactionCreate, EdiTransactionRead, EdiTransactionUpdate,
    IopTransactionCreate, IopTransactionRead, IopTransactionUpdate,
)
from app.services.transaction_service import EdiTransactionService, IopTransactionService

router = APIRouter()


# ── EDI ────────────────────────────────────────────────────────────────────
@router.get("/edi", response_model=list[EdiTransactionRead])
def list_edi(
    collection_date: date,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return EdiTransactionService(session).list_by_date(collection_date)


@router.post("/edi", response_model=EdiTransactionRead, status_code=201)
def create_edi(
    payload: EdiTransactionCreate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return EdiTransactionService(session).create(payload)


@router.patch("/edi/{transaction_id}", response_model=EdiTransactionRead)
def update_edi(
    transaction_id: int,
    payload: EdiTransactionUpdate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    result = EdiTransactionService(session).update(transaction_id, payload)
    if not result:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result


@router.delete("/edi/{transaction_id}", status_code=204)
def delete_edi(
    transaction_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    if not EdiTransactionService(session).delete(transaction_id):
        raise HTTPException(status_code=404, detail="Transaction not found")


# ── IOP ────────────────────────────────────────────────────────────────────
@router.get("/iop", response_model=list[IopTransactionRead])
def list_iop(
    collection_date: date,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return IopTransactionService(session).list_by_date(collection_date)


@router.post("/iop", response_model=IopTransactionRead, status_code=201)
def create_iop(
    payload: IopTransactionCreate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return IopTransactionService(session).create(payload)


@router.patch("/iop/{transaction_id}", response_model=IopTransactionRead)
def update_iop(
    transaction_id: int,
    payload: IopTransactionUpdate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    result = IopTransactionService(session).update(transaction_id, payload)
    if not result:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return result


@router.delete("/iop/{transaction_id}", status_code=204)
def delete_iop(
    transaction_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    if not IopTransactionService(session).delete(transaction_id):
        raise HTTPException(status_code=404, detail="Transaction not found")
