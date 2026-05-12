from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select, col, func

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.mapping import EdiNameMap, IopNameMap, EdiGroupMap, IopGroupMap

router = APIRouter()


class NameMapUpsert(BaseModel):
    customer_name_en: Optional[str] = None
    customer_name_ta: Optional[str] = None


class SegmentMapUpsert(BaseModel):
    customer_segment_name_en: Optional[str] = None
    customer_segment_name_ta: Optional[str] = None


# ── EDI ─────────────────────────────────────────────────────────────────────
@router.get("/edi")
def list_edi(
    skip: int = 0,
    limit: int = 100,
    search: str = "",
    sort_dir: str = "asc",
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    query = select(EdiNameMap)
    if search:
        query = query.where(
            col(EdiNameMap.customer_name_en).contains(search)
            | col(EdiNameMap.customer_name_ta).contains(search)
        )
    total = len(session.exec(query).all())
    order = col(EdiNameMap.customer_id).desc() if sort_dir == "desc" else col(EdiNameMap.customer_id).asc()
    rows = session.exec(query.order_by(order).offset(skip).limit(limit)).all()
    return {"data": [r.model_dump() for r in rows], "total": total}


@router.put("/edi/{customer_id}")
def upsert_edi(
    customer_id: int,
    payload: NameMapUpsert,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    row = session.get(EdiNameMap, customer_id)
    if row:
        if payload.customer_name_en is not None:
            row.customer_name_en = payload.customer_name_en
        if payload.customer_name_ta is not None:
            row.customer_name_ta = payload.customer_name_ta
    else:
        row = EdiNameMap(
            customer_id=customer_id,
            customer_name_en=payload.customer_name_en,
            customer_name_ta=payload.customer_name_ta,
        )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.model_dump()


@router.delete("/edi/{customer_id}", status_code=204)
def delete_edi(
    customer_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    row = session.get(EdiNameMap, customer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    session.delete(row)
    session.commit()


# ── IOP ─────────────────────────────────────────────────────────────────────
@router.get("/iop")
def list_iop(
    skip: int = 0,
    limit: int = 100,
    search: str = "",
    sort_dir: str = "asc",
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    query = select(IopNameMap)
    if search:
        query = query.where(
            col(IopNameMap.customer_name_en).contains(search)
            | col(IopNameMap.customer_name_ta).contains(search)
        )
    total = len(session.exec(query).all())
    order = col(IopNameMap.customer_id).desc() if sort_dir == "desc" else col(IopNameMap.customer_id).asc()
    rows = session.exec(query.order_by(order).offset(skip).limit(limit)).all()
    return {"data": [r.model_dump() for r in rows], "total": total}


@router.put("/iop/{customer_id}")
def upsert_iop(
    customer_id: int,
    payload: NameMapUpsert,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    row = session.get(IopNameMap, customer_id)
    if row:
        if payload.customer_name_en is not None:
            row.customer_name_en = payload.customer_name_en
        if payload.customer_name_ta is not None:
            row.customer_name_ta = payload.customer_name_ta
    else:
        row = IopNameMap(
            customer_id=customer_id,
            customer_name_en=payload.customer_name_en,
            customer_name_ta=payload.customer_name_ta,
        )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.model_dump()


@router.delete("/iop/{customer_id}", status_code=204)
def delete_iop(
    customer_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    row = session.get(IopNameMap, customer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    session.delete(row)
    session.commit()


# ── EDI Segments ─────────────────────────────────────────────────────────────
@router.get("/edi/segments")
def list_edi_segments(
    search: str = "",
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    query = select(EdiGroupMap)
    if search:
        query = query.where(
            col(EdiGroupMap.customer_segment_name_en).contains(search)
            | col(EdiGroupMap.customer_segment_name_ta).contains(search)
        )
    rows = session.exec(query.order_by(EdiGroupMap.customer_segment_id)).all()
    return {"data": [r.model_dump() for r in rows], "total": len(rows)}


@router.put("/edi/segments/{segment_id}")
def upsert_edi_segment(
    segment_id: int,
    payload: SegmentMapUpsert,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    row = session.get(EdiGroupMap, segment_id)
    if row:
        if payload.customer_segment_name_en is not None:
            row.customer_segment_name_en = payload.customer_segment_name_en
        if payload.customer_segment_name_ta is not None:
            row.customer_segment_name_ta = payload.customer_segment_name_ta
    else:
        row = EdiGroupMap(
            customer_segment_id=segment_id,
            customer_segment_name_en=payload.customer_segment_name_en,
            customer_segment_name_ta=payload.customer_segment_name_ta,
        )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.model_dump()


@router.delete("/edi/segments/{segment_id}", status_code=204)
def delete_edi_segment(
    segment_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    row = session.get(EdiGroupMap, segment_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    session.delete(row)
    session.commit()


# ── IOP Segments ─────────────────────────────────────────────────────────────
@router.get("/iop/segments")
def list_iop_segments(
    search: str = "",
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    query = select(IopGroupMap)
    if search:
        query = query.where(
            col(IopGroupMap.customer_segment_name_en).contains(search)
            | col(IopGroupMap.customer_segment_name_ta).contains(search)
        )
    rows = session.exec(query.order_by(IopGroupMap.customer_segment_id)).all()
    return {"data": [r.model_dump() for r in rows], "total": len(rows)}


@router.put("/iop/segments/{segment_id}")
def upsert_iop_segment(
    segment_id: int,
    payload: SegmentMapUpsert,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    row = session.get(IopGroupMap, segment_id)
    if row:
        if payload.customer_segment_name_en is not None:
            row.customer_segment_name_en = payload.customer_segment_name_en
        if payload.customer_segment_name_ta is not None:
            row.customer_segment_name_ta = payload.customer_segment_name_ta
    else:
        row = IopGroupMap(
            customer_segment_id=segment_id,
            customer_segment_name_en=payload.customer_segment_name_en,
            customer_segment_name_ta=payload.customer_segment_name_ta,
        )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.model_dump()


@router.delete("/iop/segments/{segment_id}", status_code=204)
def delete_iop_segment(
    segment_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    row = session.get(IopGroupMap, segment_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    session.delete(row)
    session.commit()
