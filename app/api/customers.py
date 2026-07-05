from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.customer import (
    EdiCustomerCreate, EdiCustomerRead, EdiCustomerUpdate,
    IopCustomerCreate, IopCustomerRead, IopCustomerUpdate,
)
from app.services.customer_service import EdiCustomerService, IopCustomerService
from app.services.transaction_service import EdiTransactionService, IopTransactionService
from app.models.mapping import EdiGroupMap, IopGroupMap

router = APIRouter()


# ── EDI ────────────────────────────────────────────────────────────────────
@router.get("/edi", response_model=dict)
def list_edi(
    skip: int = 0,
    limit: int = 50,
    search: str = "",
    segment_id: Optional[int] = None,
    sort_by: str = "customer_id",
    sort_dir: str = "asc",
    active_only: bool = False,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    svc = EdiCustomerService(session)
    customers = svc.list(skip, limit, search, segment_id, sort_by, sort_dir, active_only)
    total = svc.count(search, segment_id, active_only)
    customer_ids = [c.customer_id for c in customers]
    tamil_names = svc.get_tamil_names(customer_ids)
    data = []
    for c in customers:
        d = c.model_dump() if hasattr(c, "model_dump") else dict(c)
        d["tamil_name"] = tamil_names.get(c.customer_id, "")
        data.append(d)
    return {"data": data, "total": total}


@router.get("/edi/next-id")
def next_edi_id(session: Session = Depends(get_session), _=Depends(get_current_user)):
    return {"next_id": EdiCustomerService(session).next_id()}


@router.get("/edi/segments")
def edi_segments(session: Session = Depends(get_session), _=Depends(get_current_user)):
    rows = session.exec(select(EdiGroupMap)).all()
    return [{"segment_id": r.customer_segment_id, "name": r.customer_segment_name_en or ""} for r in rows]


@router.get("/edi/{customer_id}", response_model=EdiCustomerRead)
def get_edi(customer_id: int, session: Session = Depends(get_session), _=Depends(get_current_user)):
    customer = EdiCustomerService(session).get(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.post("/edi", response_model=EdiCustomerRead, status_code=201)
def create_edi(
    payload: EdiCustomerCreate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return EdiCustomerService(session).create(payload)


@router.patch("/edi/{customer_id}", response_model=EdiCustomerRead)
def update_edi(
    customer_id: int,
    payload: EdiCustomerUpdate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    result = EdiCustomerService(session).update(customer_id, payload)
    if not result:
        raise HTTPException(status_code=404, detail="Customer not found")
    return result


@router.delete("/edi/{customer_id}", status_code=204)
def delete_edi(
    customer_id: int,
    resequence: bool = False,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    svc = EdiCustomerService(session)
    if resequence:
        if not svc.delete_and_resequence(customer_id):
            raise HTTPException(status_code=404, detail="Customer not found")
    else:
        if not svc.delete(customer_id):
            raise HTTPException(status_code=404, detail="Customer not found")


# ── IOP ────────────────────────────────────────────────────────────────────
@router.get("/iop", response_model=dict)
def list_iop(
    skip: int = 0,
    limit: int = 50,
    search: str = "",
    segment_id: Optional[int] = None,
    sort_by: str = "customer_id",
    sort_dir: str = "asc",
    active_only: bool = False,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    svc = IopCustomerService(session)
    customers = svc.list(skip, limit, search, segment_id, sort_by, sort_dir, active_only)
    total = svc.count(search, segment_id, active_only)
    customer_ids = [c.customer_id for c in customers]
    tamil_names = svc.get_tamil_names(customer_ids)
    data = []
    for c in customers:
        d = c.model_dump() if hasattr(c, "model_dump") else dict(c)
        d["tamil_name"] = tamil_names.get(c.customer_id, "")
        data.append(d)
    return {"data": data, "total": total}


@router.get("/iop/next-id")
def next_iop_id(session: Session = Depends(get_session), _=Depends(get_current_user)):
    return {"next_id": IopCustomerService(session).next_id()}


@router.get("/iop/segments")
def iop_segments(session: Session = Depends(get_session), _=Depends(get_current_user)):
    rows = session.exec(select(IopGroupMap)).all()
    return [{"segment_id": r.customer_segment_id, "name": r.customer_segment_name_en or ""} for r in rows]


@router.get("/iop/{customer_id}", response_model=IopCustomerRead)
def get_iop(customer_id: int, session: Session = Depends(get_session), _=Depends(get_current_user)):
    customer = IopCustomerService(session).get(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.post("/iop", response_model=IopCustomerRead, status_code=201)
def create_iop(
    payload: IopCustomerCreate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return IopCustomerService(session).create(payload)


@router.patch("/iop/{customer_id}", response_model=IopCustomerRead)
def update_iop(
    customer_id: int,
    payload: IopCustomerUpdate,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    result = IopCustomerService(session).update(customer_id, payload)
    if not result:
        raise HTTPException(status_code=404, detail="Customer not found")
    return result


@router.delete("/iop/{customer_id}", status_code=204)
def delete_iop(
    customer_id: int,
    resequence: bool = False,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    svc = IopCustomerService(session)
    if resequence:
        if not svc.delete_and_resequence(customer_id):
            raise HTTPException(status_code=404, detail="Customer not found")
    else:
        if not svc.delete(customer_id):
            raise HTTPException(status_code=404, detail="Customer not found")


# ── Transliteration ─────────────────────────────────────────────────────────
class TransliterateRequest(BaseModel):
    text: str


@router.post("/transliterate")
def transliterate_name(
    payload: TransliterateRequest,
    _=Depends(get_current_user),
):
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate
        result = transliterate(payload.text, sanscript.ITRANS, sanscript.TAMIL)
        return {"tamil": result}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── Customer Transactions ────────────────────────────────────────────────────
@router.get("/edi/{customer_id}/transactions")
def edi_customer_transactions(
    customer_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return EdiTransactionService(session).list_by_customer(customer_id)


@router.get("/iop/{customer_id}/transactions")
def iop_customer_transactions(
    customer_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    return IopTransactionService(session).list_by_customer(customer_id)
