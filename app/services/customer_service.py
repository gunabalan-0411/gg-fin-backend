from __future__ import annotations
from decimal import Decimal
from typing import Optional, List, Dict
from sqlmodel import Session, select, func

from app.repositories.customer_repo import EdiCustomerRepo, IopCustomerRepo
from app.schemas.customer import (
    EdiCustomerCreate, EdiCustomerUpdate,
    IopCustomerCreate, IopCustomerUpdate,
)


class NewAccountWouldCloseError(ValueError):
    """A NEW account was asked to be open, but its figures say nothing is left to pay - it would be created already closed
    (and, for a re-loan copy, take the old account's negative / zero balance with it). Refused with a clear message instead."""


EDI_WOULD_CLOSE = ("The balance to pay on this new account is zero or less, so it would be created already closed. "
                   "Enter the new loan's balance, or switch 'Account Closed' on.")
IOP_WOULD_CLOSE = ("Principal paid is not less than the loan amount, so this new account would be created already closed. "
                   "Enter the new loan's figures, or switch 'Account Closed' on.")


class EdiCustomerService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = EdiCustomerRepo(session)

    def list(self, skip: int, limit: int, search: str, segment_id: Optional[int],
             sort_by: str = "customer_id", sort_dir: str = "asc", active_only: bool = False):
        return self.repo.get_all(skip, limit, search, segment_id, sort_by, sort_dir, active_only)

    def count(self, search: str, segment_id: Optional[int], active_only: bool = False) -> int:
        return self.repo.count(search, segment_id, active_only)

    def get(self, customer_id: int):
        return self.repo.get_by_id(customer_id)

    def next_id(self) -> int:
        return self.repo.max_id() + 1

    def create(self, payload: EdiCustomerCreate):
        data = payload.model_dump()
        # Set initial outstanding_balance = loan_amount (no transactions yet)
        if data.get("loan_amount") is not None and data.get("outstanding_balance") is None:
            data["outstanding_balance"] = data["loan_amount"]
        closed_by_balance = Decimal(str(data.get("outstanding_balance") or 0)) <= 0
        # An account explicitly asked to be OPEN with a loan but nothing left to pay (e.g. a re-loan copy that kept the old,
        # overpaid account's negative balance) must not silently become a closed one that vanishes from the active list.
        if payload.is_closed is False and closed_by_balance and Decimal(str(data.get("loan_amount") or 0)) > 0:
            raise NewAccountWouldCloseError(EDI_WOULD_CLOSE)
        data["is_closed"] = True if payload.is_closed else closed_by_balance
        return self.repo.create(data)

    def update(self, customer_id: int, payload: EdiCustomerUpdate):
        customer = self.repo.get_by_id(customer_id)
        if not customer:
            return None
        update_data = payload.model_dump(exclude_none=True)
        # If loan_amount is being updated, resync outstanding_balance from transactions
        if "loan_amount" in update_data:
            from app.models.transaction import EdiTransaction
            new_loan = Decimal(str(update_data["loan_amount"]))
            paid_sum = self.session.exec(
                select(func.coalesce(func.sum(EdiTransaction.amount), 0))
                .where(EdiTransaction.customer_id == customer_id)
                .where(EdiTransaction.payment_status == "PAID")
            ).one()
            update_data["outstanding_balance"] = new_loan - Decimal(str(paid_sum))
        return self.repo.update(customer, update_data)

    def delete(self, customer_id: int) -> bool:
        customer = self.repo.get_by_id(customer_id)
        if not customer:
            return False
        self.repo.delete(customer)
        return True

    def delete_and_resequence(self, customer_id: int) -> bool:
        return self.repo.delete_and_resequence(customer_id)

    def get_tamil_names(self, customer_ids: List[int]) -> Dict[int, str]:
        return self.repo.get_tamil_names(customer_ids)


class IopCustomerService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = IopCustomerRepo(session)

    def list(self, skip: int, limit: int, search: str, segment_id: Optional[int],
             sort_by: str = "customer_id", sort_dir: str = "asc", active_only: bool = False):
        return self.repo.get_all(skip, limit, search, segment_id, sort_by, sort_dir, active_only)

    def count(self, search: str, segment_id: Optional[int], active_only: bool = False) -> int:
        return self.repo.count(search, segment_id, active_only)

    def get(self, customer_id: int):
        return self.repo.get_by_id(customer_id)

    def next_id(self) -> int:
        return self.repo.max_id() + 1

    def _iop_balance(self, loan_amount, principal_paid) -> tuple[Decimal, bool]:
        loan = Decimal(str(loan_amount or 0))
        paid = Decimal(str(principal_paid or 0))
        balance = loan - paid
        return balance, balance <= 0

    def create(self, payload: IopCustomerCreate):
        data = payload.model_dump()
        balance, closed = self._iop_balance(data.get("loan_amount"), data.get("principal_paid"))
        if payload.is_closed is False and closed and Decimal(str(data.get("loan_amount") or 0)) > 0:
            raise NewAccountWouldCloseError(IOP_WOULD_CLOSE)           # same rule as EDI: never silently create a closed "new" account
        data["outstanding_balance"] = balance
        data["is_closed"] = True if payload.is_closed else closed
        return self.repo.create(data)

    def update(self, customer_id: int, payload: IopCustomerUpdate):
        customer = self.repo.get_by_id(customer_id)
        if not customer:
            return None
        update_data = payload.model_dump(exclude_none=True)
        new_loan = update_data.get("loan_amount", customer.loan_amount)
        new_paid = update_data.get("principal_paid", customer.principal_paid)
        balance, _ = self._iop_balance(new_loan, new_paid)
        update_data["outstanding_balance"] = balance
        return self.repo.update(customer, update_data)

    def delete(self, customer_id: int) -> bool:
        customer = self.repo.get_by_id(customer_id)
        if not customer:
            return False
        self.repo.delete(customer)
        return True

    def delete_and_resequence(self, customer_id: int) -> bool:
        return self.repo.delete_and_resequence(customer_id)

    def get_tamil_names(self, customer_ids: List[int]) -> Dict[int, str]:
        return self.repo.get_tamil_names(customer_ids)
