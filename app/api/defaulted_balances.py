from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, col

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.defaulted_balance import DefaultedBalance
from app.models.customer import EdiCustomer, IopCustomer

router = APIRouter()


@router.get("/lookup-name")
def lookup_customer_name(
    customer_id: int,
    product: str,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    if product == "edi":
        customer = session.get(EdiCustomer, customer_id)
    elif product == "iop":
        customer = session.get(IopCustomer, customer_id)
    else:
        raise HTTPException(status_code=400, detail="product must be 'edi' or 'iop'")

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    return {"customer_name": customer.customer_name or ""}


@router.get("")
def list_defaulted_balances(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    rows = session.exec(
        select(DefaultedBalance).order_by(col(DefaultedBalance.date).desc())
    ).all()
    return rows


@router.post("")
def create_defaulted_balance(
    data: dict,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    product = data.get("product", "")
    if product not in ("edi", "iop"):
        raise HTTPException(status_code=400, detail="product must be 'edi' or 'iop'")

    customer_id = data.get("customer_id")
    if product == "edi":
        customer = session.get(EdiCustomer, customer_id)
    else:
        customer = session.get(IopCustomer, customer_id)

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    row = DefaultedBalance(
        date=data["date"],
        product=product,
        customer_id=customer_id,
        customer_name=customer.customer_name or "",
        amount=data["amount"],
        notes=data.get("notes"),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.delete("/{record_id}")
def delete_defaulted_balance(
    record_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    row = session.get(DefaultedBalance, record_id)
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    session.delete(row)
    session.commit()
    return {"ok": True}
