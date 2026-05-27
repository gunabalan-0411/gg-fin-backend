from datetime import date
from sqlmodel import Session

from app.repositories.transaction_repo import EdiTransactionRepo, IopTransactionRepo
from app.schemas.transaction import (
    EdiTransactionCreate, EdiTransactionUpdate,
    IopTransactionCreate, IopTransactionUpdate,
)


class EdiTransactionService:
    def __init__(self, session: Session):
        self.repo = EdiTransactionRepo(session)

    def list_by_date(self, collection_date: date):
        return self.repo.get_by_date(collection_date)

    def list_by_customer(self, customer_id: int):
        return self.repo.get_by_customer(customer_id)

    def get(self, transaction_id: int):
        return self.repo.get_by_id(transaction_id)

    def create(self, payload: EdiTransactionCreate):
        return self.repo.create(payload.model_dump())

    def update(self, transaction_id: int, payload: EdiTransactionUpdate):
        txn = self.repo.get_by_id(transaction_id)
        if not txn:
            return None
        return self.repo.update(txn, payload.model_dump(exclude_none=True))

    def delete(self, transaction_id: int) -> bool:
        txn = self.repo.get_by_id(transaction_id)
        if not txn:
            return False
        self.repo.delete(txn)
        return True

    def upsert(self, customer_id: int, collection_date: date, amount: float, payment_mode: str = "CASH", is_paid: bool = True):
        status = "PAID" if is_paid else "UNPAID"
        existing = self.repo.get_by_customer_date_and_mode(customer_id, collection_date, payment_mode)
        if existing:
            return self.repo.update(existing, {"amount": amount, "payment_status": status})
        return self.repo.create({
            "customer_id": customer_id,
            "collection_date": collection_date,
            "amount": amount,
            "payment_mode": payment_mode,
            "payment_status": status,
        })


class IopTransactionService:
    def __init__(self, session: Session):
        self.repo = IopTransactionRepo(session)

    def list_by_date(self, collection_date: date):
        return self.repo.get_by_date(collection_date)

    def list_by_customer(self, customer_id: int):
        return self.repo.get_by_customer(customer_id)

    def get(self, transaction_id: int):
        return self.repo.get_by_id(transaction_id)

    def create(self, payload: IopTransactionCreate):
        return self.repo.create(payload.model_dump())

    def update(self, transaction_id: int, payload: IopTransactionUpdate):
        txn = self.repo.get_by_id(transaction_id)
        if not txn:
            return None
        return self.repo.update(txn, payload.model_dump(exclude_none=True))

    def delete(self, transaction_id: int) -> bool:
        txn = self.repo.get_by_id(transaction_id)
        if not txn:
            return False
        self.repo.delete(txn)
        return True

    def upsert(self, customer_id: int, collection_date: date, amount: float, payment_mode: str = "CASH", is_paid: bool = True):
        status = "PAID" if is_paid else "UNPAID"
        existing = self.repo.get_by_customer_date_and_mode(customer_id, collection_date, payment_mode)
        if existing:
            return self.repo.update(existing, {"amount": amount, "payment_status": status})
        return self.repo.create({
            "customer_id": customer_id,
            "collection_date": collection_date,
            "amount": amount,
            "payment_mode": payment_mode,
            "payment_status": status,
        })
