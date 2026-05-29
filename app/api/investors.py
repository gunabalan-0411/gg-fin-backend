from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, col

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.investor import Investor

router = APIRouter()


def _to_dict(inv: Investor) -> dict:
    return {
        "id": inv.id,
        "date": str(inv.date),
        "investor_name": inv.investor_name,
        "amount": float(inv.amount),
        "return_amount": float(inv.return_amount),
        "notes": inv.notes,
    }


@router.get("")
def list_investors(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    rows = session.exec(select(Investor).order_by(col(Investor.date).desc())).all()
    return [_to_dict(r) for r in rows]


@router.post("", status_code=201)
def create_investor(
    data: dict,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    inv = Investor(
        date=data["date"],
        investor_name=data["investor_name"],
        amount=data["amount"],
        return_amount=data.get("return_amount", 0),
        notes=data.get("notes"),
    )
    session.add(inv)
    session.commit()
    session.refresh(inv)
    return _to_dict(inv)


@router.patch("/{investor_id}")
def update_investor(
    investor_id: int,
    data: dict,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    inv = session.get(Investor, investor_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investor not found")
    for field in ("date", "investor_name", "amount", "return_amount", "notes"):
        if field in data:
            setattr(inv, field, data[field])
    session.add(inv)
    session.commit()
    session.refresh(inv)
    return _to_dict(inv)


@router.delete("/{investor_id}", status_code=204)
def delete_investor(
    investor_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    inv = session.get(Investor, investor_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investor not found")
    session.delete(inv)
    session.commit()
