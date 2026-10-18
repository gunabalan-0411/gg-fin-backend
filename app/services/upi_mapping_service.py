"""
UPI id (VPA) -> customer mappings, in the existing `tbl_upi_vpa_mappings` table (no new table).

* apply_pairings   — the OCR page pairs UPI transactions with customers; saved in one go when the page is submitted.
* move_vpa_mappings — "Use existing UPI ids" when a closed account is copied into a new one (and its undo).

History is never rewritten: a transaction that is already mapped keeps its customer.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.customer import EdiCustomer, IopCustomer
from app.models.upi import UpiTransaction, UpiVpaMapping
from app.schemas.upi_pairing import Pairing

_TYPES = ("edi", "iop")


def apply_pairings(session: Session, pairings: list[Pairing]) -> dict:
    result = {"paired": 0, "created": 0, "replaced": 0, "unchanged": 0, "stamped": 0, "skipped": []}
    for p in pairings:
        ctype = (p.customer_type or "").strip().lower()
        if ctype not in _TYPES:
            result["skipped"].append({"txn_id": p.txn_id, "reason": "bad_customer_type"})
            continue
        txn = session.get(UpiTransaction, p.txn_id)
        if txn is None:
            result["skipped"].append({"txn_id": p.txn_id, "reason": "txn_not_found"})
            continue

        vpa = (txn.sender_vpa or "").strip().lower()
        if vpa:
            existing = session.exec(
                select(UpiVpaMapping)
                .where(func.lower(UpiVpaMapping.upi_vpa) == vpa)
                .where(UpiVpaMapping.customer_type == ctype)
            ).first()
            if existing is None:
                session.add(UpiVpaMapping(upi_vpa=vpa, customer_id=p.customer_id, customer_type=ctype, customer_name=p.customer_name))
                result["created"] += 1
            elif existing.customer_id == p.customer_id:
                result["unchanged"] += 1
            elif p.replace:
                existing.customer_id = p.customer_id
                existing.customer_name = p.customer_name
                session.add(existing)
                result["replaced"] += 1
            else:
                result["skipped"].append({
                    "txn_id": p.txn_id, "reason": "vpa_mapped_to_other",
                    "mapped_customer_id": existing.customer_id, "mapped_customer_name": existing.customer_name,
                })
                continue
            session.flush()

            # Other transactions from the same UPI id that nobody has mapped yet follow the new mapping.
            # Already-mapped ones are history of whichever account they were mapped to — left alone.
            others = session.exec(
                select(UpiTransaction)
                .where(func.lower(UpiTransaction.sender_vpa) == vpa)
                .where(UpiTransaction.mapped_customer_id.is_(None))
                .where(UpiTransaction.id != txn.id)
            ).all()
            for o in others:
                o.mapped_customer_id = p.customer_id
                o.mapped_customer_type = ctype
                session.add(o)
                result["stamped"] += 1

        txn.mapped_customer_id = p.customer_id
        txn.mapped_customer_type = ctype
        session.add(txn)
        result["stamped"] += 1
        result["paired"] += 1
    session.commit()
    return result


def move_vpa_mappings(session: Session, customer_type: str, from_id: int, to_id: int) -> int:
    """
    Move every saved UPI id of one account to another. Only between a CLOSED and an OPEN account of the same product
    (either direction: copying a closed account forward, or undoing that copy).
    """
    ctype = (customer_type or "").strip().lower()
    if ctype not in _TYPES:
        raise ValueError("customer_type must be 'edi' or 'iop'")
    if from_id == to_id:
        raise ValueError("The two accounts are the same")
    model = EdiCustomer if ctype == "edi" else IopCustomer
    src, dst = session.get(model, from_id), session.get(model, to_id)
    if src is None or dst is None:
        raise LookupError("Customer not found")
    if bool(src.is_closed) == bool(dst.is_closed):
        raise ValueError("UPI ids can only move between a closed and an open account")

    rows = session.exec(
        select(UpiVpaMapping).where(UpiVpaMapping.customer_id == from_id).where(UpiVpaMapping.customer_type == ctype)
    ).all()
    for r in rows:
        r.customer_id = to_id
        r.customer_name = dst.customer_name
        session.add(r)
    session.commit()
    return len(rows)
