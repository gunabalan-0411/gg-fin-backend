"""
IOP collections that are part interest, part principal (e.g. 10,000 = 500 interest + 9,500 principal).

* the TRANSACTION keeps the interest only (so IOP profit stays interest);
* the principal is added to the customer's existing `principal_paid` and the balance (loan - principal_paid) is recalculated;
* the principal is remembered on the transaction (`principal_amount`), so re-submitting the same sheet, correcting the amount or
  deleting the transaction moves `principal_paid` by the DIFFERENCE only - never twice;
* a principal larger than the remaining balance (or than the amount collected) is refused BEFORE anything is saved;
* the account is never closed automatically.

`plan` is used both by the live check on the OCR page and by the submit, so what the page shows is what the submit enforces.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlmodel import Session, select

from app.models.customer import IopCustomer
from app.models.transaction import IopTransaction


def _inr(n: Decimal | float | int) -> str:
    return f"₹{Decimal(str(n)):,.0f}"


def parse_date(text: Any) -> Optional[date]:
    if isinstance(text, date):                                  # already a date (the Voice page)
        return text
    try:
        return datetime.strptime(text, "%d-%m-%Y").date()
    except (TypeError, ValueError):
        return None


def plan(session: Session, records: Sequence[Any]) -> list[dict]:
    """
    One entry per IOP record that will be saved: {index, principal, interest, delta, balance_before, balance_after, error}.
    Records are looked at in order, so two rows of the same customer share the balance. `records` need customer_id, collection_date
    (dd-mm-yyyy), payment_mode, amount, is_paid, product_type and (optional) principal_amount.
    """
    out: list[dict] = []
    paid_now: dict[int, Decimal] = {}                       # customer -> principal_paid as it will be after the rows planned so far
    planned: dict[tuple, Decimal] = {}                      # (customer, date, mode) -> principal of the row planned for it
    customers: dict[int, Optional[IopCustomer]] = {}

    for i, rec in enumerate(records):
        if (rec.product_type or "").upper() != "IOP" or not rec.customer_id or not rec.amount:
            continue
        day = parse_date(rec.collection_date)
        if day is None:
            continue
        amount = Decimal(str(rec.amount))
        principal = Decimal(str(getattr(rec, "principal_amount", None) or 0)) if rec.is_paid else Decimal(0)
        error: Optional[str] = None
        if principal < 0:
            error = "The principal cannot be negative"
        elif principal > amount:
            error = f"The principal {_inr(principal)} is more than the amount collected {_inr(amount)}"

        cid = rec.customer_id
        if cid not in customers:
            customers[cid] = session.get(IopCustomer, cid)
        customer = customers[cid]
        key = (cid, day, rec.payment_mode)
        if key in planned:
            old = planned[key]
        else:
            existing = session.exec(select(IopTransaction).where(
                IopTransaction.customer_id == cid, IopTransaction.collection_date == day, IopTransaction.payment_mode == rec.payment_mode)).first()
            old = Decimal(str(existing.principal_amount or 0)) if existing else Decimal(0)
        planned[key] = principal
        delta = principal - old

        before = after = None
        if customer is not None:
            paid = paid_now.get(cid, Decimal(str(customer.principal_paid or 0)))
            if customer.loan_amount is not None:
                loan = Decimal(str(customer.loan_amount))
                before, after = loan - paid, loan - paid - delta
                if error is None and delta > 0 and after < 0:
                    error = f"The principal {_inr(principal)} is more than the balance {_inr(before)} of {customer.customer_name or 'this customer'}"
            paid_now[cid] = paid + delta

        out.append({
            "index": i, "principal": principal, "interest": amount - principal, "delta": delta,
            "balance_before": before, "balance_after": after, "error": error,
        })
    return out


def apply_delta(session: Session, customer_id: int, delta: Decimal) -> None:
    """Move the customer's principal_paid by `delta` and recalculate the balance. The account is NOT closed or reopened here."""
    if delta == 0:
        return
    customer = session.get(IopCustomer, customer_id)
    if customer is None:
        return
    customer.principal_paid = Decimal(str(customer.principal_paid or 0)) + delta
    if customer.loan_amount is not None:
        customer.outstanding_balance = Decimal(str(customer.loan_amount)) - customer.principal_paid
    session.add(customer)
