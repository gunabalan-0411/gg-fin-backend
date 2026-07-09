import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, col

from app.api.deps import get_current_user
from app.core.database import get_session
from app.models.debt import Debt, DebtRepayment

router = APIRouter()


def _repayments_for(session: Session, debt_id: int) -> list[DebtRepayment]:
    return list(
        session.exec(
            select(DebtRepayment)
            .where(DebtRepayment.debt_id == debt_id)
            .order_by(col(DebtRepayment.date), col(DebtRepayment.id))
        ).all()
    )


def _total_repaid(session: Session, debt_id: int) -> float:
    rows = _repayments_for(session, debt_id)
    return float(sum(r.amount for r in rows))


def _balance_ref_date() -> datetime.date:
    """Most recent 5th-of-month on or before today."""
    today = datetime.date.today()
    if today.day >= 5:
        return today.replace(day=5)
    if today.month == 1:
        return datetime.date(today.year - 1, 12, 5)
    return today.replace(month=today.month - 1, day=5)


def _emi_balance(debt: Debt, as_of: datetime.date) -> float:
    """Outstanding balance via PV-of-remaining-payments amortization formula."""
    r = float(debt.interest_rate_pa) / 12 / 100
    n = debt.tenure_months
    pmt = float(debt.emi_amount)
    start = debt.date

    emis_paid = (as_of.year - start.year) * 12 + (as_of.month - start.month)
    emis_paid = max(0, min(emis_paid, n))
    n_remaining = n - emis_paid

    if n_remaining <= 0:
        return 0.0
    if r == 0:
        return round(pmt * n_remaining, 2)
    return round(pmt * (1 - (1 + r) ** (-n_remaining)) / r, 2)


def _debt_to_dict(debt: Debt, session: Session) -> dict:
    total_repaid = _total_repaid(session, debt.id)

    has_emi_data = (
        debt.emi_amount is not None
        and debt.interest_rate_pa is not None
        and debt.tenure_months is not None
    )
    if has_emi_data:
        balance = _emi_balance(debt, _balance_ref_date())
    else:
        balance = float(debt.amount) - total_repaid

    return {
        "id": debt.id,
        "date": str(debt.date),
        "lender_name": debt.lender_name,
        "borrower_name": debt.borrower_name,
        "amount": float(debt.amount),
        "emi_amount": float(debt.emi_amount) if debt.emi_amount is not None else None,
        "interest_rate_pa": float(debt.interest_rate_pa) if debt.interest_rate_pa is not None else None,
        "tenure_months": debt.tenure_months,
        "end_date": str(debt.end_date) if debt.end_date else None,
        "paid_by": debt.paid_by,
        "total_repaid": total_repaid,
        "balance": balance,
        "notes": debt.notes,
    }


def _repayment_to_dict(repayment: DebtRepayment, running_balance: float) -> dict:
    return {
        "id": repayment.id,
        "debt_id": repayment.debt_id,
        "date": str(repayment.date),
        "amount": float(repayment.amount),
        "balance": running_balance,
        "notes": repayment.notes,
    }


# ── Debts (Lender Records) ────────────────────────────────────────────────

@router.get("")
def list_debts(
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    debts = session.exec(select(Debt).order_by(col(Debt.date).desc())).all()
    return [_debt_to_dict(d, session) for d in debts]


@router.post("")
def create_debt(
    data: dict,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    debt = Debt(
        date=data["date"],
        lender_name=data["lender_name"],
        borrower_name=data.get("borrower_name"),
        amount=data["amount"],
        emi_amount=data.get("emi_amount"),
        interest_rate_pa=data.get("interest_rate_pa"),
        tenure_months=data.get("tenure_months"),
        end_date=data.get("end_date"),
        paid_by=data.get("paid_by"),
        notes=data.get("notes"),
    )
    session.add(debt)
    session.commit()
    session.refresh(debt)
    return _debt_to_dict(debt, session)


@router.patch("/{debt_id}")
def update_debt(
    debt_id: int,
    data: dict,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    debt = session.get(Debt, debt_id)
    if not debt:
        raise HTTPException(status_code=404, detail="Debt not found")
    for field in ("date", "lender_name", "borrower_name", "amount", "emi_amount",
                  "interest_rate_pa", "tenure_months", "end_date", "paid_by", "notes"):
        if field in data:
            setattr(debt, field, data[field])
    session.add(debt)
    session.commit()
    session.refresh(debt)
    return _debt_to_dict(debt, session)


@router.delete("/{debt_id}")
def delete_debt(
    debt_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    debt = session.get(Debt, debt_id)
    if not debt:
        raise HTTPException(status_code=404, detail="Debt not found")
    # Delete repayments first
    for r in _repayments_for(session, debt_id):
        session.delete(r)
    session.delete(debt)
    session.commit()
    return {"ok": True}


# ── Repayments ────────────────────────────────────────────────────────────

@router.get("/{debt_id}/repayments")
def list_repayments(
    debt_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    debt = session.get(Debt, debt_id)
    if not debt:
        raise HTTPException(status_code=404, detail="Debt not found")

    rows = _repayments_for(session, debt_id)
    total = float(debt.amount)
    cumulative = 0.0
    result = []
    for r in rows:
        cumulative += float(r.amount)
        result.append(_repayment_to_dict(r, total - cumulative))
    return result


@router.post("/{debt_id}/repayments")
def create_repayment(
    debt_id: int,
    data: dict,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    debt = session.get(Debt, debt_id)
    if not debt:
        raise HTTPException(status_code=404, detail="Debt not found")

    repayment = DebtRepayment(
        debt_id=debt_id,
        date=data["date"],
        amount=data["amount"],
        notes=data.get("notes"),
    )
    session.add(repayment)
    session.commit()
    session.refresh(repayment)

    # Recompute running balance for this repayment
    rows = _repayments_for(session, debt_id)
    cumulative = 0.0
    balance = float(debt.amount)
    for r in rows:
        cumulative += float(r.amount)
        if r.id == repayment.id:
            balance = float(debt.amount) - cumulative
            break
    return _repayment_to_dict(repayment, balance)


@router.patch("/{debt_id}/repayments/{repayment_id}")
def update_repayment(
    debt_id: int,
    repayment_id: int,
    data: dict,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    repayment = session.get(DebtRepayment, repayment_id)
    if not repayment or repayment.debt_id != debt_id:
        raise HTTPException(status_code=404, detail="Repayment not found")

    for field in ("date", "amount", "notes"):
        if field in data:
            setattr(repayment, field, data[field])
    session.add(repayment)
    session.commit()
    session.refresh(repayment)

    debt = session.get(Debt, debt_id)
    rows = _repayments_for(session, debt_id)
    cumulative = 0.0
    balance = float(debt.amount)
    for r in rows:
        cumulative += float(r.amount)
        if r.id == repayment_id:
            balance = float(debt.amount) - cumulative
            break
    return _repayment_to_dict(repayment, balance)


@router.delete("/{debt_id}/repayments/{repayment_id}")
def delete_repayment(
    debt_id: int,
    repayment_id: int,
    session: Session = Depends(get_session),
    _=Depends(get_current_user),
):
    repayment = session.get(DebtRepayment, repayment_id)
    if not repayment or repayment.debt_id != debt_id:
        raise HTTPException(status_code=404, detail="Repayment not found")
    session.delete(repayment)
    session.commit()
    return {"ok": True}
