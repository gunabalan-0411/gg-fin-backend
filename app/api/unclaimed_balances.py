from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, col

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.unclaimed_balance import UnclaimedBalance
from app.models.customer import EdiCustomer, IopCustomer

router = APIRouter()


@router.get("/lookup-name")
def lookup_customer_name(
    customer_id: int,
    product: str,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    """Fetch customer name from the customer table by ID and product."""
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
def list_unclaimed_balances(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    rows = session.exec(
        select(UnclaimedBalance).order_by(col(UnclaimedBalance.date).desc())
    ).all()
    return rows


@router.post("")
def create_unclaimed_balance(
    data: dict,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    product = data.get("product", "")
    if product not in ("edi", "iop"):
        raise HTTPException(status_code=400, detail="product must be 'edi' or 'iop'")

    customer_id = data.get("customer_id") or None
    customer_name = data.get("customer_name") or None

    if customer_id:
        if product == "edi":
            customer = session.get(EdiCustomer, customer_id)
        else:
            customer = session.get(IopCustomer, customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        customer_name = customer.customer_name or None

    row = UnclaimedBalance(
        date=data["date"],
        product=product,
        customer_id=customer_id,
        customer_name=customer_name,
        amount=data["amount"],
        notes=data.get("notes"),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.delete("/{record_id}")
def delete_unclaimed_balance(
    record_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    row = session.get(UnclaimedBalance, record_id)
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    session.delete(row)
    session.commit()
    return {"ok": True}
